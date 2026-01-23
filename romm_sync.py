# external library imports
from watchdog.observers import Observer
from pathlib import Path
from typing import Callable
import signal
import sys
from dotenv import load_dotenv
# logging special import
from loguru import logger
# internal imports
from src.config import get_config
from src.sync_orchestrator import SyncOrchestrator, WatchdogSyncManager, PeriodicSyncManager, FileChangeHandler

# Load environment variables early
load_dotenv()


def initialize_romm_sync():
    # Get the app's configuration from docker env variables. See .env.example
    app_cfg = get_config()

    # First sync. Compares all save states
    logger.info("Performing initial sync. This may take a while...")
    srv, lcl = SyncOrchestrator.build_libraries(app_cfg=app_cfg)

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
    def watchdog(cls, sync_manager: WatchdogSyncManager, observer_stop_callback: Callable[[], None]):
        """Factory for watchdog sync mode exit handler"""
        def handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down gracefully...")
            sync_manager.cancel_pending()
            observer_stop_callback()
        return cls(handler)


def run_periodic_sync(srv, lcl):
    """Run ROMM sync in periodic mode, performing full syncs at regular intervals."""
    sync_manager = PeriodicSyncManager(srv=srv, lcl=lcl)

    AppExit.periodic(sync_manager.stop)  # register the exit signal handler

    sync_manager.run()


def run_watch_sync(srv, lcl):
    """Run ROMM sync in watch mode, syncing on file system changes.
    For this mode, all the sync functions are called and written in the sync_orchestrator module."""

    # Create sync manager to control the watchdog sync
    sync_manager = WatchdogSyncManager(srv=srv, lcl=lcl)


    # Create event handler with sync manager
    event_handler = FileChangeHandler(sync_manager)
    observer = Observer()

    AppExit.watchdog(sync_manager=sync_manager, observer_stop_callback=observer.stop)  # register the exit signal handler

    # Determine which directories to watch
    watch_dirs = lcl.sync_folders

    # Schedule observer for each directory
    for watch_dir in watch_dirs:
        observer.schedule(event_handler, str(watch_dir), recursive=True)
        logger.info(f"Watching directory: {watch_dir}")

    # Start observer
    observer.start()
    logger.info("Observer started.")

    try:
        # Keep the observer running
        observer.join()
    finally:
        observer.stop()
        observer.join()
        logger.info("Exiting ('Watch' sync mode)...")


def main():
    """Main orchestration function."""
    app_cfg, srv, lcl = initialize_romm_sync()

    logger.info("Romm_sync starting...")

    if app_cfg.SYNC_MODE == "periodic":
        run_periodic_sync(srv, lcl)
    elif app_cfg.SYNC_MODE == "watch":
        run_watch_sync(srv, lcl)
    else:
        logger.critical(f"Invalid sync mode {app_cfg.SYNC_MODE}. Allowed: 'periodic', 'watch'.\n\nExiting...")
        sys.exit(1)


if __name__ == "__main__":
    main()
