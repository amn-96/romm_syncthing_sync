"""Orchestration for coordinating synchronization between local library and ROMM server.

Includes orchestration logic, sync state management, and file system event handling.
"""
from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path
from loguru import logger
import threading
from collections import namedtuple

from .games_class import Game
from .library_classes import LocalLibrary, RetroGameServer
from .romm_api_func import RommUser
from .config import METADATA_FILE_FILTERS
from watchdog.events import FileSystemEventHandler


ValidEvent = namedtuple("ValidEvent", ['path', 'type'])


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

    def __init__(self, local_library: LocalLibrary, romm_server: RetroGameServer, romm_user: RommUser):
        """Initialize the orchestrator with required components.

        Args:
            local_library: LocalLibrary instance managing local game files
            romm_server: RetroGameServer instance managing ROMM library state
            romm_user: RommUser instance with ROMM API credentials
        """
        self.local_library = local_library
        self.romm_server = romm_server
        self.romm_user = romm_user

    def _fetch_romm_data(self, games: List[Game]) -> None:
        """Fetch current saves and states from ROMM for given games.

        Args:
            games: List of games to fetch ROMM state for
        """
        for game in games:
            if game.is_matched:
                logger.debug(f"Fetching ROMM data for {game.name}")
                try:
                    game.fetch_romm_saves()
                    game.fetch_romm_states()
                except Exception as e:
                    logger.error(f"Failed to fetch ROMM data for {game.name}: {e}")

    def _push_local_to_romm(self, games: List[Game]) -> tuple[List[Game], List[tuple[Game, Exception]]]:
        """Push local saves and states to ROMM.

        Args:
            games: List of games to sync to ROMM

        Returns:
            Tuple of (successful_games, failed_games_with_errors)
        """
        successful = []
        failed = []

        for game in games:
            if game.is_matched:
                logger.debug(f"Pushing local save data for {game.name} to ROMM.")
                try:
                    game.sync_local_saves_to_remote()
                    game.sync_local_states_to_remote()
                    successful.append(game)
                except Exception as e:
                    logger.error(f"Failed to sync {game.name} to ROMM: {e}")
                    failed.append((game, e))
        return successful, failed

    def _validate_games(self, games: List[Game]) -> tuple[List[Game], List[Game]]:
        """Separate matched and unmatched games.

        Args:
            games: List of games to validate

        Returns:
            Tuple of (matched_games, unmatched_games)
        """
        matched = [g for g in games if g.is_matched]
        unmatched = [g for g in games if not g.is_matched]
        return matched, unmatched

    def full_sync(self, cache_filepath: Optional[Path] = None) -> SyncResult:
        """Perform a complete synchronization between local library and ROMM.

        This is the main sync operation that:
        1. Fetches current ROMM state for all matched games
        2. Pushes local saves/states to ROMM
        3. Caches the updated library state

        Args:
            cache_filepath: Path to save the cache file after sync (optional)

        Returns:
            SyncResult with details of the operation
        """
        logger.info("---------- Starting full sync ----------")
        result = SyncResult()

        try:
            matched_games, unmatched_games = self._validate_games(
                list(self.local_library.games.values())
            )
            result.unmatched_games = unmatched_games

            if not matched_games:
                logger.debug("[FULLSYNC] No matched games found for syncing")
                result.success = False
                result.games_synced, result.games_failed = [], []
                result.error_message = "No matched games found for syncing"
                return result

            # Step 1: Fetch current ROMM state
            logger.info(f"[FULLSYNC] Fetching ROMM data for {len(matched_games)} games")
            self._fetch_romm_data(matched_games)

            # Step 2: Push local to ROMM
            logger.info(f"[FULLSYNC] Syncing local data to ROMM for {len(matched_games)} games")
            successful, failed = self._push_local_to_romm(matched_games)

            result.games_synced = successful
            result.games_failed = failed

            # if cache_filepath:
            #     logger.info(f"Caching library state to {cache_filepath}")
            #     self.local_library.to_json(cache_filepath)

            result.success = len(failed) == 0
            if len(successful) == 0 and len(failed) == 0:
                logger.debug("[FULLSYNC] No changes detected - libraries already in sync")
            logger.info("---------- Full sync completed ----------")

            return result

        except Exception as e:
            result.success = False
            result.error_message = str(e)
            logger.error(f"[FULLSYNC] Failed! {e}", exc_info=True)
            return result

    def watchdog_sync(self, event_paths: List[Path], event_types: List[str], cache_filepath: Optional[Path] = None) -> SyncResult:
        """Perform an incremental sync of all file changes gathered by the watchdog.

        This sync operation:
        1. Extracts affected games from watchdog events
        2. Rescans those games for updated files
        3. Fetches their current ROMM state
        4. Pushes local changes to ROMM
        """
        logger.info("---------- Starting watchdog sync ----------")
        result = SyncResult()

        try:
            if not event_paths:
                logger.info("[WATCHDOGSYNC] No events to process")
                result.success = True
                return result

            logger.debug("[WATCHDOGSYNC] The following file events will be processed in this sync operation:")
            for p, t in zip(event_paths, event_types):
                logger.debug(f"[WATCHDOGSYNC] --- File: {p}, Type: {t}")
            
            # Step 1: Extract affected games from watchdog events
            logger.info(f"[WATCHDOGSYNC] Processing {len(event_paths)} file change events...")
            games_to_sync = self.local_library.extract_games_from_watchdog_events(
                event_paths,
                self.romm_server
            )

            if not games_to_sync:
                logger.info("[WATCHDOGSYNC] No games in ROMM library found matching local library. Will not sync.")
                result.success = False
                result.games_synced, result.games_failed = [], []
                result.error_message = "No matched games found for syncing"
                return result

            logger.info(f"[WATCHDOGSYNC] Syncing save data for {len(games_to_sync)} games...")

            # Step 2: Fetch current ROMM state
            self._fetch_romm_data(games_to_sync)

            # Step 3: Push local to ROMM
            successful, failed = self._push_local_to_romm(games_to_sync)

            result.games_synced = successful
            result.games_failed = failed

            # if cache_filepath:
            #     logger.info(f"Caching library state to {cache_filepath}")
            #     self.local_library.to_json(cache_filepath)

            result.success = len(failed) == 0
            logger.info("---------- Watchdog sync completed ----------")

            return result

        except Exception as e:
            result.success = False
            result.error_message = str(e)
            logger.error(f"[WATCHDOGSYNC] Failed! {e}", exc_info=True)
            return result


class SyncManager:
    """Manages sync state and coordinates watchdog events with ROMM sync operations."""

    def __init__(self, srv: RetroGameServer, lcl: LocalLibrary, romm_user: RommUser,
                 watchdog_delay_seconds: int = 60, cache_filepath: Optional[Path] = None):
        """Initialize sync manager.

        Args:
            srv: RetroGameServer instance
            lcl: LocalLibrary instance
            romm_user: RommUser credentials
            watchdog_delay_seconds: Delay before executing sync after file changes
            cache_filepath: Path to cache file (saved after each sync)
        """
        self.orchestrator = SyncOrchestrator(lcl, srv, romm_user)
        self.cache_filepath = cache_filepath
        self.watchdog_delay_seconds = watchdog_delay_seconds
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
            result = self.orchestrator.watchdog_sync(event_paths, event_types, self.cache_filepath)

            if result.success:
                logger.info(f"Watchdog sync succeeded: {len(result.games_synced)} games synced")
            else:
                logger.error(f"Watchdog sync failed: {result.error_message}")
                if result.games_failed:
                    logger.warning(f"{len(result.games_failed)} games failed to sync")

        except Exception as e:
            logger.error(f"Failed to execute sync: {e}", exc_info=True)
        finally:
            with self.timer_lock:
                self.is_syncing = False

    def cancel_pending(self):
        """Cancel any pending timer."""
        with self.timer_lock:
            if self.timer_thread is not None and self.timer_thread.is_alive():
                self.timer_thread.cancel()


class FileChangeHandler(FileSystemEventHandler):
    """Handle filesystem events and trigger sync operations. Modified from watchdog library"""

    def __init__(self, sync_manager: SyncManager):
        self.sync_manager = sync_manager

    def _handle_event(self, event):
        """Common handler for all filesystem events."""
        if not event.is_directory:
            event_path = Path(event.src_path)
            # Filter out any metadata files
            if any(filter_str in event_path.name for filter_str in METADATA_FILE_FILTERS):
                logger.trace(f"Ignored file system event (METADATA): {event.event_type} - {event.src_path}")
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
