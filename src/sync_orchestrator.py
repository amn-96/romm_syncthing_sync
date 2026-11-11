"""Orchestration for coordinating synchronization between local library and ROMM server.

Includes orchestration logic, sync state management, and file system event handling.
"""
from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path
from loguru import logger
import threading
import time
from functools import wraps
from collections import Counter, defaultdict, namedtuple

from time import perf_counter as tpc

from .games_class import Game
from .conflict_resolution import ConflictResolution
from .two_way_sync import TwoWaySync
from .library_classes import LocalLibrary, RetroGameServer, LibraryScanner
from .config import get_config, METADATA_FILE_FILTERS
from watchdog.events import FileSystemEventHandler


ValidEvent = namedtuple("ValidEvent", ['path', 'type'])

PRUNE_INTERVAL_SECONDS = 24 * 3600
SYNC_LOCK = threading.Lock()  # held for every sync and every resolve action from the web UI


class SyncProgress:
    """Steps of the latest full sync or pull, for the web UI's progress bar. A full sync has one per RomM library page,
    one for the pull pass and two per local game (fetch, push); the page count is only known once RomM answers the
    first page. A pull replaces the estimate with one step per game once it knows which games it will pull."""

    def __init__(self):
        self.done = self.total = 0
        self.pages_counted = False

    def start(self, local_games: int) -> None:
        self.done, self.total, self.pages_counted = 0, 2 * local_games + 1, False

    def romm_page(self, page_count: int) -> None:
        if not self.pages_counted:
            self.total += page_count
            self.pages_counted = True
        self.done += 1

    def games_to_pull(self, count: int) -> None:
        self.total = self.done + count

    def advance(self) -> None:
        self.done = min(self.done + 1, self.total)

    def finish(self) -> None:
        self.done = self.total


SYNC_PROGRESS = SyncProgress()


def synchronized(method):
    @wraps(method)
    def locked(*args, **kwargs):
        with SYNC_LOCK:
            return method(*args, **kwargs)
    return locked


@dataclass
class SyncResult:
    """Result of a sync operation."""
    games_synced: List[Game] = field(default_factory=list)
    games_failed: List[tuple[Game, Exception]] = field(default_factory=list)
    unmatched_games: List[Game] = field(default_factory=list)
    success: bool = False
    error_message: Optional[str] = None

    def summary(self) -> str:
        """Return a human-readable summary of sync results."""
        lines = []
        lines.append(f"Status: {'SUCCESS' if self.success else 'FAILED'}")
        lines.append(f"Games synced: {len(self.games_synced)}")
        lines.append(f"Games failed: {len(self.games_failed)}")
        lines.append(f"Unmatched games: {len(self.unmatched_games)}")
        if self.error_message:
            lines.append(f"Error: {self.error_message}")
        return "\n".join(lines)


class SyncOrchestrator:
    """Orchestrates synchronization operations between local library and ROMM server.
    """

    def __init__(self, local_library: LocalLibrary, romm_server: RetroGameServer, engine: TwoWaySync | None = None):
        """Orchestrator needs to keep and update a single instance of LocalLibrary and RetroGameServer.
        Without an engine there is no history DB or two-way sync: files are only pushed to RomM.
        """
        self.lcl = local_library
        self.srv = romm_server
        self.engine = engine
        self.app_cfg = get_config()
    
    @staticmethod
    def build_libraries(app_cfg, progress: SyncProgress | None = None):
        """Build fresh local and romm libraries in one step. For iniitialization."""
        lcl = LocalLibrary(app_cfg.sync_dirs)
        lcl.build_local_library()
        if progress:
            progress.start(len(lcl.games))
        srv = RetroGameServer.build_romm_library(app_cfg.ROMM_CREDENTIALS, on_page=progress and progress.romm_page)
        LibraryScanner.match_to_romm(list(lcl.games.values()), srv)
        
        return srv, lcl

    @staticmethod
    def _fetch_romm_data(games: List[Game]) -> None:
        """Fetch current saves and states from ROMM for given games.

        Args:
            games: List of games to fetch ROMM state for
        """
        for game in games:
            SYNC_PROGRESS.advance()
            if game.is_matched:
                logger.debug(f"Fetching ROMM data for {game.name}")
                try:
                    game.fetch_romm_saves()
                    game.fetch_romm_states()
                except Exception as e:
                    logger.error(f"Failed to fetch ROMM data for {game.name}: {e}")

    @staticmethod
    def _push_local_to_romm(games: List[Game], force: bool, engine: TwoWaySync | None = None) -> tuple[List[Game], List[tuple[Game, Exception]]]:
        """Push local saves and states to ROMM.

        Args:
            games: List of games to sync to ROMM

        Returns:
            Tuple of (successful_games, failed_games_with_errors)
        """
        successful = []
        failed = []

        for game in games:
            SYNC_PROGRESS.advance()
            if game.is_matched:
                logger.debug(f"Pushing local save data for {game.name} to ROMM.")
                try:
                    game.sync_local_saves_to_remote(force, engine)
                    game.sync_local_states_to_remote(force, engine)
                    successful.append(game)
                except Exception as e:
                    logger.error(f"Failed to sync {game.name} to ROMM: {e}")
                    failed.append((game, e))
        return successful, failed

    def _record_run(self, games: List[Game], failed: list, partial: bool, unmatched: List[Game] = ()) -> None:
        """Snapshot the library into the DB after a sync pass. Games pushed or pulled are re-fetched so the snapshot shows RomM's new state.
        Unmatched games add no rows; they're passed so the UI can list them and the games held for matching several RomM games."""
        self._fetch_romm_data([game for game in games if game.romm_id in self.engine.touched_rom_ids])
        self.engine.store.snapshot(self.engine, [*games, *unmatched], self.srv.library, partial)
        self.engine.finish_run(checked=len(games), errors=len(failed))

    @staticmethod
    def _validate_games(games: List[Game]) -> tuple[List[Game], List[Game]]:
        """Separate matched and unmatched games.

        Args:
            games: List of games to validate

        Returns:
            Tuple of (matched_games, unmatched_games)
        """
        matched = [g for g in games if g.is_matched]
        unmatched = [g for g in games if not g.is_matched]
        return matched, unmatched

    def _managed_platforms(self) -> dict[str, str]:
        """Map RomM platform slug -> local folder name for every platform folder under the sync dirs."""
        managed = {}
        for sync_dir in self.lcl.sync_folders:
            for platform_dir in sync_dir.iterdir():
                if platform_dir.is_dir() and not any(f in platform_dir.name for f in METADATA_FILE_FILTERS):
                    managed.setdefault(self.srv.get_romm_platform(platform_dir.name) or platform_dir.name, platform_dir.name)
        return managed

    def _new_local_game(self, romm_game: dict, local_platform: str) -> Game:
        """Register a RomM game that has no local files yet, so its saves and states can be downloaded."""
        name = Path(romm_game['fs_name']).stem
        game = self.lcl.games.get((local_platform, name))
        if game is None:
            game = Game(name=name, path=self.app_cfg.sync_dir_for("save") / local_platform, platform=local_platform)
            self.lcl.games[(local_platform, name)] = game
        game.set_romm_data(romm_game)
        return game

    def _pull_pass(self, progress: SyncProgress | None = None) -> tuple[List[Game], List[tuple[Game, Exception]]]:
        """Bring RomM-side changes and RomM-only files into the sync folders, for the platforms that have a folder there.
        All of the user's saves and states are fetched in two requests rather than two per game."""
        managed = self._managed_platforms()
        user = self.app_cfg.ROMM_CREDENTIALS
        saves_by_rom, states_by_rom = defaultdict(list), defaultdict(list)
        for save in user.saves.get():
            saves_by_rom[save['rom_id']].append(save)
        for state in user.states.get():
            states_by_rom[state['rom_id']].append(state)

        stems = Counter((g['platform_slug'], Path(g['fs_name']).stem) for g in self.srv.library)
        # A name two RomM games share can't be paired safely with local files (see LibraryScanner.match_to_romm)
        romm_games = {g['id']: g for g in self.srv.library
                      if g['platform_slug'] in managed and stems[(g['platform_slug'], Path(g['fs_name']).stem)] == 1}
        local_games = {g.romm_id: g for g in self.lcl.games.values() if g.is_matched}
        games = []
        for rom_id in (saves_by_rom.keys() | states_by_rom.keys()) & romm_games.keys():
            game = local_games.get(rom_id) or self._new_local_game(romm_games[rom_id], managed[romm_games[rom_id]['platform_slug']])
            game.set_romm_saves(saves_by_rom[rom_id])
            game.set_romm_states(states_by_rom[rom_id])
            games.append(game)

        self.engine.negotiate(games)
        if progress:
            progress.games_to_pull(len(games))
        failed = []
        for game in games:
            try:
                game.pull_from_romm(self.engine)
            except Exception as e:
                logger.error(f"[PULL] Failed to pull {game.name}: {e}")
                failed.append((game, e))
            if progress:
                progress.advance()
        return games, failed

    @synchronized
    def pull_from_romm(self) -> SyncResult:
        """Scheduled pull (watch mode): nothing else notices changes made on the RomM side. Never pushes."""
        logger.info("---------- Starting pull from ROMM ----------")
        result = SyncResult()
        try:
            self.srv, self.lcl = self.build_libraries(self.app_cfg, SYNC_PROGRESS)
            self.engine.begin_run("scheduled_pull")
            games, failed = self._pull_pass(SYNC_PROGRESS)
            self._record_run(games, failed, partial=True)
            SYNC_PROGRESS.finish()
            result.games_synced, result.games_failed, result.success = games, failed, not failed
        except Exception as e:
            result.error_message = str(e)
            logger.error(f"[PULL] Failed! {e}", exc_info=True)
        return result

    @synchronized
    def full_sync(self, force_sync: bool = False) -> SyncResult:
        """Perform a complete synchronization between local library and ROMM.
        This is the main sync operation that:
        1. Fetches current ROMM state for all matched games
        2. Pushes local saves/states to ROMM
        3. Caches the updated library state
        Returns:
            SyncResult with details of the operation
        """
        logger.info("---------- Starting full sync ----------")
        result = SyncResult()

        # Initialize a fresh state for the romm library and local library
        self.srv, self.lcl = self.build_libraries(self.app_cfg, SYNC_PROGRESS)
        if self.engine:
            self.engine.begin_run("full")

        try:
            # first, so files that only exist in RomM join the library before the push
            pull_failed = self._pull_pass()[1] if self.engine and self.engine.writable else []
            SYNC_PROGRESS.advance()
            matched_games, unmatched_games = self._validate_games(
                list(self.lcl.games.values())
            )
            result.unmatched_games = unmatched_games

            if not matched_games:
                logger.debug("[FULLSYNC] No matched games found for syncing")
                if self.engine:
                    self._record_run([], pull_failed, partial=False, unmatched=unmatched_games)
                result.success = False
                result.games_synced, result.games_failed = [], []
                result.error_message = "No matched games found for syncing"
                return result

            # Step 1: Fetch current ROMM state
            logger.info(f"[FULLSYNC] Fetching ROMM data for {len(matched_games)} games")
            self._fetch_romm_data(matched_games)
            if self.engine:
                self.engine.negotiate(matched_games)

            # Step 2: Push local to ROMM
            logger.info(f"[FULLSYNC] Syncing local data to ROMM for {len(matched_games)} games")
            successful, failed = self._push_local_to_romm(matched_games, force=force_sync, engine=self.engine)
            failed = pull_failed + failed
            if self.engine:
                self._record_run(matched_games, failed, partial=False, unmatched=unmatched_games)

            result.games_synced = successful
            result.games_failed = failed

            result.success = len(failed) == 0
            
            if len(successful) == 0 and len(failed) == 0:
                logger.debug("[FULLSYNC] No changes detected - libraries already in sync")
            
            if not result.success:
                logger.warning(f"[FULLSYNC] Encountered errors: {result.error_message}")
            if result.games_failed:
                logger.warning(f"[FULLSYNC] {len(result.games_failed)} games failed to sync")
            if result.unmatched_games:
                logger.info(f"[FULLSYNC] {len(result.unmatched_games)} games could not be matched to ROMM")
                logger.info(f"[FULLSYNC] Unmatched games: {', '.join([ug.name for ug in result.unmatched_games])}")

            logger.info("---------- Full sync completed ----------")
            SYNC_PROGRESS.finish()

            return result

        except Exception as e:
            result.success = False
            result.error_message = str(e)
            logger.error(f"[FULLSYNC] Failed! {e}", exc_info=True)
            return result

    @synchronized
    def watchdog_sync(self, event_paths: List[Path], event_types: List[str]) -> SyncResult:
        """Perform an incremental sync of all file changes gathered by the watchdog.

        This sync operation:
        1. Extracts affected games from watchdog events
        2. Rescans those games for updated files
        3. Fetches their current ROMM state
        4. Pushes local changes to ROMM
        """
        logger.info("---------- Starting watchdog sync ----------")

        # First, refresh the romm library.
        logger.debug("[WATCHDOGSYNC] Updating state of Romm library.")
        # updates the romm server object with a fresh, rebuilt instance. This is needed so that newly added games are not missed.
        self.srv = RetroGameServer.build_romm_library(self.app_cfg.ROMM_CREDENTIALS)
        if self.engine:
            self.engine.begin_run("watch")

        result = SyncResult()
        # next, partial sync of only modified files. 
        # This saves a lot of time because building the local library requires api calls for every single game to fetch the saves and states.
        try:
            if not event_paths:
                logger.info("[WATCHDOGSYNC] No events to process")
                result.success = True
                return result

            logger.debug("[WATCHDOGSYNC] The following file events will be processed in this sync operation:")
            for p, t in zip(event_paths, event_types):
                logger.debug(f"[WATCHDOGSYNC] --- File: {p}, Type: {t}")
            
            # Extract the games using the file paths recorded by watchdog
            logger.info(f"[WATCHDOGSYNC] Processing {len(event_paths)} file change events...")
            games_to_sync = self.lcl.update_from_watchdog_events(
                event_paths,
                self.srv
            )

            if not games_to_sync:
                logger.info("[WATCHDOGSYNC] No games in ROMM library found matching local library. Will not sync.")
                result.success = False
                result.games_synced, result.games_failed = [], []
                result.error_message = "No matched games found for syncing"
                return result

            logger.info(f"[WATCHDOGSYNC] Syncing save data for {len(games_to_sync)} games...")

            # Fetch current ROMM state
            self._fetch_romm_data(games_to_sync)
            if self.engine:
                self.engine.negotiate(games_to_sync)

            # Step 3: Push local to ROMM
            successful, failed = self._push_local_to_romm(games_to_sync, force=self.app_cfg.FORCE_PUSH_SYNC, engine=self.engine)
            if self.engine:
                self._record_run(games_to_sync, failed, partial=True)

            result.games_synced = successful
            result.games_failed = failed

            result.success = len(failed) == 0
            
            logger.info("---------- Watchdog sync completed ----------")

            return result

        except Exception as e:
            result.success = False
            result.error_message = str(e)
            logger.error(f"[WATCHDOGSYNC] Failed! {e}", exc_info=True)
            return result


class WatchdogSyncManager:
    """Manages watchdog sync state and coordinates filesystem events with ROMM sync operations."""

    def __init__(self, srv: RetroGameServer, lcl: LocalLibrary, engine: TwoWaySync | None = None):
        """Initialize. Called in main to kick off the watchdog sync.
        """
        self.orchestrator = SyncOrchestrator(lcl, srv, engine)
        self.watchdog_delay_seconds = get_config().WATCHDOG_DELAY_SECONDS
        self.pending_events: List[ValidEvent] = []
        self.event_lock = threading.Lock()
        self.timer_thread = None
        self.timer_lock = threading.Lock()
        self.is_syncing = False  # Track if sync is currently in progress

    def schedule_sync(self):
        """Schedule sync to run after watchdog_delay_seconds of file system inactivity.

        If a sync is already in progress, events are queued for the next sync cycle.
        If a timer is already scheduled, it's cancelled and restarted if an event happens within WATCHDOG_DELAY_SECONDS debounce period.
        """
        with self.timer_lock:
            # Don't schedule if sync is already running; events will be processed next cycle
            if self.is_syncing:
                logger.debug("Sync already in progress; events queued for next cycle")
                return

            # Cancel any existing timer to restart the delay
            if self.timer_thread is not None and self.timer_thread.is_alive():
                self.timer_thread.cancel()

            self.timer_thread = threading.Timer(self.watchdog_delay_seconds, self.execute_sync)
            self.timer_thread.daemon = False
            self.timer_thread.start()

    def add_event(self, event_path: str, event_type: str):
        """Add a filesystem event to the pending queue."""
        with self.event_lock:
            self.pending_events.append(ValidEvent(path=event_path, type=event_type))

    def execute_sync(self):
        """Execute the sync operation for pending events."""
        try:
            t0 = tpc()
            with self.timer_lock:
                self.is_syncing = True

            with self.event_lock:
                if not self.pending_events:
                    logger.debug("No pending events to process")
                    return

                event_paths = [Path(p_e.path) for p_e in self.pending_events]
                event_types = [p_e.type for p_e in self.pending_events]
                self.pending_events.clear()

            # Use orchestrator to handle watchdog sync
            result = self.orchestrator.watchdog_sync(event_paths, event_types)

            if result.success:
                logger.info(f"Watchdog sync succeeded! {len(result.games_synced)} games synced in {round(tpc() - t0, 2)} s.")
            else:
                logger.error(f"Watchdog sync had errors: {result.error_message}")
                if result.games_failed:
                    logger.warning(f"{len(result.games_failed)} games failed to sync")

        except Exception as e:
            logger.error(f"Failed to execute sync: {e}", exc_info=True)
        finally:

            logger.info("---------- Continuing to watch for file changes ----------")
            with self.timer_lock:
                self.is_syncing = False
            if self.pending_events:  # arrived mid-sync, when schedule_sync declined to start a timer
                self.schedule_sync()

    def cancel_pending(self):
        """Cancel any pending timer."""
        with self.timer_lock:
            if self.timer_thread is not None and self.timer_thread.is_alive():
                self.timer_thread.cancel()


class PeriodicSyncManager:
    """Manages periodic sync, performing full syncs at regular intervals."""

    def __init__(self, srv: RetroGameServer, lcl: LocalLibrary, engine: TwoWaySync | None = None):
        self.orchestrator = SyncOrchestrator(lcl, srv, engine)
        self.sync_interval = get_config().SYNC_INTERVAL_SECONDS
        self.force_sync = get_config().FORCE_PUSH_SYNC
        self.stop_event = threading.Event()

    def run(self):
        """Run the periodic sync loop. Blocks until stop() is called."""
        try:
            # startup already ran the initial sync (or skipped it per FULL_INITIAL_SYNC), so wait first
            while not self.stop_event.wait(timeout=self.sync_interval):
                logger.info("Running periodic sync...")
                try:
                    t0 = tpc()
                    self.orchestrator.full_sync(force_sync=self.force_sync)
                    logger.info(f"Periodic sync completed in {round(tpc() - t0, 2)} s.")
                except Exception as e:
                    logger.error(f"Sync failed: {e}")
        finally:
            logger.info("Exiting ('Periodic' sync mode)...")

    def stop(self):
        """Signal the sync loop to stop."""
        self.stop_event.set()


class PullManager:
    """Watch mode only: asks RomM for changes every PULL_INTERVAL_SECONDS on a background thread, since file events
    only cover the local side."""

    def __init__(self, orchestrator: SyncOrchestrator):
        self.orchestrator = orchestrator
        self.interval = get_config().PULL_INTERVAL_SECONDS
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="romm-pull", daemon=True)

    def start(self):
        self.thread.start()
        logger.info(f"Pulling from ROMM every {self.interval} s.")

    def run(self):
        while not self.stop_event.wait(timeout=self.interval):
            self.orchestrator.pull_from_romm()

    def stop(self):
        self.stop_event.set()


def prune_periodically(engine: TwoWaySync, conflicts: ConflictResolution, retention_days: int) -> None:
    """Prune the history and the backups now and then every 24 h on a daemon thread."""
    def run():
        while True:
            engine.store.prune(retention_days)
            conflicts.prune_backups(retention_days)
            time.sleep(PRUNE_INTERVAL_SECONDS)

    threading.Thread(target=run, name="db-prune", daemon=True).start()


class FileChangeHandler(FileSystemEventHandler):
    """Handle filesystem events and trigger sync operations. Modified from watchdog library"""

    def __init__(self, sync_manager: WatchdogSyncManager):
        self.sync_manager = sync_manager

    def _handle_event(self, event):
        """Common handler for all filesystem events."""
        if not event.is_directory:
            event_path = Path(event.src_path)
            # Filter out any metadata files
            if any(filter_str in event_path.name for filter_str in METADATA_FILE_FILTERS):
                logger.trace(f"Ignored file system event (METADATA): {event.event_type} - {event.src_path}")
                return

            engine = self.sync_manager.orchestrator.engine
            if engine and engine.is_own_write(event_path):
                logger.trace(f"Ignored file system event (own write): {event.event_type} - {event.src_path}")
                return

            logger.trace(f"Filesystem Event: {event.event_type} - {event.src_path}")
            self.sync_manager.add_event(event.src_path, event.event_type)
            self.sync_manager.schedule_sync()

    def on_modified(self, event):
        self._handle_event(event)

    def on_created(self, event):
        self._handle_event(event)

    def on_deleted(self, event):
        self._handle_event(event)

    def on_moved(self, event):
        self._handle_event(event)