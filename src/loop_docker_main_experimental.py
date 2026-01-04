"""
Docker-friendly main event loop for continuous ROMM synchronization.

Supports two sync modes:
- Periodic: Runs sync on a fixed schedule (e.g., every 5 minutes)
- Watch: Monitors local sync folder for file changes and syncs immediately

Gracefully handles shutdown signals (SIGTERM, SIGINT) for clean container termination.
"""
import asyncio
import logging
import os
import signal
from datetime import datetime, timedelta
from typing import Optional

from .config import Config
from .library_classes import RetroGameServer
from .sync_operations import SaveBackup
from .romm_api_func import RommUser


logger = logging.getLogger(__name__)


class SyncStatistics:
    """Track sync operation statistics."""

    def __init__(self):
        self.last_sync_time: Optional[datetime] = None
        self.total_syncs: int = 0
        self.total_games_synced: int = 0
        self.total_files_copied: int = 0
        self.last_error: Optional[str] = None
        self.last_error_time: Optional[datetime] = None

    def record_sync(self, games_synced: int, files_copied: int, error: Optional[str] = None):
        """Record a sync operation's results."""
        self.last_sync_time = datetime.now()
        self.total_syncs += 1
        self.total_games_synced += games_synced
        self.total_files_copied += files_copied
        if error:
            self.last_error = error
            self.last_error_time = datetime.now()

    def summary(self) -> str:
        """Return a human-readable summary of sync statistics."""
        return (
            f"Syncs: {self.total_syncs} | "
            f"Games: {self.total_games_synced} | "
            f"Files: {self.total_files_copied} | "
            f"Last sync: {self.last_sync_time or 'Never'}"
        )


class RommSyncScheduler:
    """
    Main scheduler for ROMM synchronization.

    Orchestrates periodic and watch-mode syncing with proper cleanup and error handling.
    """

    def __init__(self, config: Config):
        self.config = config
        self.romm_user: Optional[RommUser] = None
        self.romm_library: Optional[RetroGameServer] = None
        self.catalog: Optional[SaveBackup] = None
        self.stats = SyncStatistics()
        self._running = False
        self._library_refresh_time: Optional[datetime] = None
        self._local_cache_time: Optional[datetime] = None

        # Set up signal handlers for graceful shutdown
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Set up handlers for SIGTERM and SIGINT (Docker container shutdown)."""
        def handle_shutdown(signum, _frame):
            logger.info(f"Received signal {signum}, initiating graceful shutdown...")
            self._running = False

        signal.signal(signal.SIGTERM, handle_shutdown)
        signal.signal(signal.SIGINT, handle_shutdown)

    async def initialize(self) -> bool:
        """
        Initialize scheduler: validate config, connect to ROMM, scan local folder.

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info("Initializing ROMM Sync Scheduler...")

            # Validate configuration
            self.config.validate()
            logger.debug("Configuration validated")

            # Initialize ROMM connection
            self.romm_user = self.config.ROMM_CREDENTIALS
            logger.info(f"Connecting to ROMM at {self.romm_user.url}")

            # Load ROMM library
            await self._refresh_romm_library()
            if not self.romm_library:
                logger.error("Failed to load ROMM library")
                return False

            # Scan local sync folder
            if not self.config.SYNC_FOLDER.exists():
                logger.error(f"Sync folder does not exist: {self.config.SYNC_FOLDER}")
                return False

            self.catalog = SaveBackup(self.config.SYNC_FOLDER)
            if not await self._scan_local_folder():
                logger.error("Failed to scan local folder")
                return False

            logger.info("Scheduler initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize scheduler: {e}")
            return False

    async def _refresh_romm_library(self, force: bool = False) -> bool:
        """
        Refresh ROMM library from server.

        Only fetches if cache is stale (configurable TTL, default 1 hour).
        Set force=True to bypass cache.

        Returns:
            True if successful, False otherwise
        """
        # FUTURE: Implement LibraryCache class to reduce API calls
        # This should:
        # 1. Check if cached library exists and is fresh (< ROMM_LIBRARY_TTL_HOURS)
        # 2. If fresh, load from cache file instead of API
        # 3. If stale or force=True, fetch from API and update cache
        # 4. Store library + timestamp in JSON

        try:
            now = datetime.now()

            # Check if we need to refresh
            if not force and self._library_refresh_time:
                # Default to 1 hour TTL, could be configurable
                ttl_hours = getattr(self.config, 'ROMM_LIBRARY_TTL_HOURS', 1)
                if now - self._library_refresh_time < timedelta(hours=ttl_hours):
                    logger.debug("ROMM library cache still fresh, skipping refresh")
                    return True

            logger.info("Refreshing ROMM library from server...")
            self.romm_library = RetroGameServer.initialize_romm_map(self.romm_user)
            self._library_refresh_time = now
            logger.info(f"Library refreshed: {self.romm_library}")
            return True

        except Exception as e:
            logger.error(f"Failed to refresh ROMM library: {e}")
            return False

    async def _scan_local_folder(self) -> bool:
        """
        Scan local sync folder and update game catalog.

        FUTURE: Optimize with LocalStateTracker to only process changed files.
        Currently scans all files every time.

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.debug(f"Scanning local folder: {self.config.SYNC_FOLDER}")
            self.catalog.scan_save_folder()
            logger.info(f"Found {len(self.catalog.games)} games in local folder")
            return True
        except Exception as e:
            logger.error(f"Failed to scan local folder: {e}")
            return False

    async def _match_local_to_romm(self) -> bool:
        """
        Match local games to ROMM library.

        Returns:
            True if successful, False otherwise
        """
        try:
            if not self.romm_library or not self.catalog:
                logger.error("ROMM library or catalog not initialized")
                return False

            logger.debug("Matching local games to ROMM library...")
            self.catalog.match_to_romm(self.romm_library)

            matched = len(self.catalog.get_matched_games())
            unmatched = len(self.catalog.get_unmatched_games())
            logger.info(f"Matching complete: {matched} matched, {unmatched} unmatched")
            return True

        except Exception as e:
            logger.error(f"Failed to match games: {e}")
            return False

    async def _perform_sync(self) -> bool:
        """
        Perform a single sync operation.

        FUTURE: Implement change detection to only sync games with modifications.
        Currently syncs all matched games every time.

        Returns:
            True if successful, False otherwise
        """
        try:
            if not self.catalog or not self.romm_user:
                logger.error("Catalog or ROMM user not initialized")
                return False

            logger.info("Starting sync operation...")
            stats = self.catalog.sync_all_states(self.romm_user)

            error_msg = None
            if stats['errors']:
                error_msg = "; ".join(stats['errors'])
                logger.warning(f"Sync completed with errors: {error_msg}")

            self.stats.record_sync(
                games_synced=stats['games_synced'],
                files_copied=stats['total_files_copied'],
                error=error_msg
            )

            logger.info(f"Sync operation complete: {self.stats.summary()}")
            return len(stats['errors']) == 0

        except Exception as e:
            logger.error(f"Failed to perform sync: {e}")
            self.stats.record_sync(0, 0, str(e))
            return False

    async def run_periodic_sync(self):
        """
        Run periodic sync at fixed intervals.

        Uses config.SYNC_INTERVAL_SECONDS to determine frequency.
        Each cycle: scan local → match to ROMM → sync changes
        """
        logger.info(f"Starting periodic sync (interval: {self.config.SYNC_INTERVAL_SECONDS}s)")

        while self._running:
            try:
                # Refresh ROMM library periodically (cache prevents excessive API calls)
                await self._refresh_romm_library()

                # Scan local folder
                await self._scan_local_folder()

                # Match and sync
                await self._match_local_to_romm()
                await self._perform_sync()

                # Wait before next cycle
                logger.debug(f"Next sync in {self.config.SYNC_INTERVAL_SECONDS} seconds")
                await asyncio.sleep(self.config.SYNC_INTERVAL_SECONDS)

            except Exception as e:
                logger.error(f"Error in periodic sync loop: {e}")
                self.stats.record_sync(0, 0, str(e))
                # Wait before retrying to avoid rapid error loops
                await asyncio.sleep(min(self.config.SYNC_INTERVAL_SECONDS, 10))

    async def run_watch_mode(self):
        """
        Monitor local sync folder for changes and sync on modification.

        FUTURE: Implement with watchdog library:
        1. Watch config.SYNC_FOLDER recursively
        2. Debounce rapid changes (coalesce into single sync)
        3. On file modify/create: trigger scan + match + sync
        4. Run in parallel with periodic ROMM library refresh

        For now, this is a placeholder for the architecture.
        """
        logger.info("Watch mode not yet implemented")
        logger.info("TODO: Implement with watchdog library for real-time sync")
        # Placeholder: just run periodic sync for now
        await self.run_periodic_sync()

    async def run(self, mode: str = "periodic"):
        """
        Start the main scheduler loop.

        Args:
            mode: 'periodic' for fixed-interval syncs, 'watch' for file-system monitoring

        Mode behavior:
        - 'periodic': Runs sync every SYNC_INTERVAL_SECONDS
        - 'watch': Monitors for file changes and syncs immediately (TODO)
        """
        self._running = True

        try:
            # Initialize on startup
            if not await self.initialize():
                logger.error("Failed to initialize scheduler")
                return 1

            logger.info(f"Starting ROMM Sync in {mode.upper()} mode")

            # Run selected mode
            if mode.lower() == "watch":
                await self.run_watch_mode()
            else:  # default to periodic
                await self.run_periodic_sync()

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        except Exception as e:
            logger.error(f"Fatal error in scheduler: {e}")
            return 1
        finally:
            await self.shutdown()
            logger.info("Scheduler shutdown complete")

        return 0

    async def shutdown(self):
        """Gracefully shutdown the scheduler."""
        logger.info("Shutting down scheduler...")
        self._running = False

        # Clean up resources here if needed
        # For example: close ROMM connections, save state, etc.

        logger.info(f"Final statistics: {self.stats.summary()}")


async def main(config: Optional[Config] = None, mode: str = "periodic"):
    """
    Entry point for the ROMM sync application.

    Args:
        config: Config object (if None, creates default from environment variables)
        mode: 'periodic' or 'watch' sync mode

    Returns:
        Exit code (0 for success, 1 for error)
    """
    if config is None:
        config = Config()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL),
        format=config.LOG_FORMAT
    )

    scheduler = RommSyncScheduler(config)
    return await scheduler.run(mode=mode)


if __name__ == "__main__":
    import sys

    # Get sync mode from environment or default to periodic
    sync_mode = os.getenv("SYNC_MODE", "periodic")
    config = Config()

    exit_code = asyncio.run(main(config, mode=sync_mode))
    sys.exit(exit_code)
