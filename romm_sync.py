# external library imports
from watchdog.observers import Observer
from pathlib import Path
from typing import Callable
import signal
import sys
from dotenv import load_dotenv
import requests
# logging special import
from loguru import logger
# internal imports
from src.config import get_config
from src.store import Store
from src.conflict_resolution import ConflictResolution
from src.two_way_sync import TwoWaySync
from src.sync_orchestrator import SyncOrchestrator, WatchdogSyncManager, PeriodicSyncManager, PullManager, FileChangeHandler, prune_periodically
from src.syncthing import SyncthingResolver
from src.webui import serve_in_thread

# Load environment variables early
load_dotenv()


def build_engine(app_cfg) -> TwoWaySync:
    resolver = None
    if app_cfg.SYNCTHING_URL:
        try:
            resolver = SyncthingResolver(app_cfg.SYNCTHING_URL, app_cfg.SYNCTHING_API_KEY, app_cfg.sync_dirs)
        except requests.RequestException as error:
            logger.warning(f"Syncthing unreachable ({error}). Files will show 'Syncthing' instead of device names.")
    if app_cfg.PUSH_ONLY:
        logger.warning("Push-only: nothing will be written to the sync folders.")
    return TwoWaySync(Store(app_cfg.DATA_DIR / "romm_sync.db"), app_cfg, resolver)


def initialize_romm_sync(engine: TwoWaySync | None = None):
    # Get the app's configuration from docker env variables. See .env.example
    app_cfg = get_config()

    # First sync. Compares all save states
    logger.info("Building local and romm libraries. This may take a while for large libraries...")
    if app_cfg.FULL_INITIAL_SYNC == "false":
        logger.info("Initial sync skipped ('FULL_INITIAL_SYNC' = false).")
        srv, lcl = SyncOrchestrator.build_libraries(app_cfg=app_cfg)
    else:
        logger.info("Performing initial sync.")
        init_state = SyncOrchestrator(local_library=None, romm_server=None, engine=engine)  # full_sync builds both libraries
        init_state.full_sync(force_sync=app_cfg.FULL_INITIAL_SYNC == "force")
        srv, lcl = init_state.srv, init_state.lcl

    return app_cfg, srv, lcl


class AppExit():
    def __init__(self, exit_handler):
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, exit_handler)
        signal.signal(signal.SIGINT, exit_handler)

    @classmethod
    def periodic(cls, stop_callback: Callable[[], None]):
        """Factory for periodic sync mode exit handler"""
        def handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down gracefully...")
            stop_callback()
        return cls(handler)
        
    @classmethod
    def watchdog(cls, sync_manager: WatchdogSyncManager, observer_stop_callback: Callable[[], None], pull_manager: PullManager | None):
        """Factory for watchdog sync mode exit handler"""
        def handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down gracefully...")
            sync_manager.cancel_pending()
            if pull_manager:
                pull_manager.stop()
            observer_stop_callback()
        return cls(handler)


def run_periodic_sync(srv, lcl, engine):
    """Run ROMM sync in periodic mode, performing full syncs at regular intervals."""
    sync_manager = PeriodicSyncManager(srv=srv, lcl=lcl, engine=engine)

    AppExit.periodic(sync_manager.stop)  # register the exit signal handler

    sync_manager.run()


def run_watch_sync(srv, lcl, engine):
    """Run ROMM sync in watch mode, syncing on file system changes.
    For this mode, all the sync functions are called and written in the sync_orchestrator module."""

    # Create sync manager to control the watchdog sync
    sync_manager = WatchdogSyncManager(srv=srv, lcl=lcl, engine=engine)

    # Create event handler with sync manager
    event_handler = FileChangeHandler(sync_manager)
    observer = Observer()

    pull_manager = PullManager(sync_manager.orchestrator) if engine.writable else None  # push-only never writes, so there is nothing to pull into
    AppExit.watchdog(sync_manager=sync_manager, observer_stop_callback=observer.stop, pull_manager=pull_manager)  # register the exit signal handler

    # Determine which directories to watch
    watch_dirs = lcl.sync_folders

    # Schedule observer for each directory
    for watch_dir in watch_dirs:
        observer.schedule(event_handler, str(watch_dir), recursive=True)
        logger.info(f"Watching directory: {watch_dir}")

    # Start observer
    observer.start()
    logger.info("Observer started.")
    if pull_manager:
        pull_manager.start()

    try:
        # Keep the observer running
        observer.join()
    finally:
        observer.stop()
        observer.join()
        logger.info("Exiting ('Watch' sync mode)...")


def main():
    """Main orchestration function."""
    engine = build_engine(get_config())
    conflicts = ConflictResolution(engine)
    serve_in_thread(engine, conflicts)
    prune_periodically(engine, conflicts, engine.cfg.LOG_DB_RETENTION_DAYS)
    app_cfg, srv, lcl = initialize_romm_sync(engine)

    logger.info("Romm_sync starting!")

    if app_cfg.SYNC_MODE == "periodic":
        run_periodic_sync(srv, lcl, engine)
    elif app_cfg.SYNC_MODE == "watch":
        run_watch_sync(srv, lcl, engine)
    else:
        logger.critical(f"Invalid sync mode {app_cfg.SYNC_MODE}. Allowed: 'periodic', 'watch'.\n\nExiting...")
        sys.exit(1)


if __name__ == "__main__":
    main()
