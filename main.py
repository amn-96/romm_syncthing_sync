# external library imports
from watchdog.observers import Observer
from pathlib import Path
import signal
import sys
from time import perf_counter as tpc
import threading
from dotenv import load_dotenv
# logging special import
from loguru import logger
# internal imports
from src.romm_sync import initialize_romm_sync, full_sync, cleanup
from src.sync_orchestrator import SyncManager, FileChangeHandler

# Load environment variables early
load_dotenv()


def run_periodic_sync(app_cfg):
    """Run ROMM sync in periodic mode, performing full syncs at regular intervals."""
    logger.info(f"Periodic sync mode (interval: {app_cfg.SYNC_INTERVAL_SECONDS}s)")

    stop_event = threading.Event()

    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        stop_event.set()

    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        while not stop_event.is_set():
            logger.info("Running periodic sync...")
            try:
                t0 = tpc()
                srv, lcl = full_sync(app_cfg)
                t1 = tpc()
                logger.info(f"Periodic Sync Completed in {round(t1-t0, 2)} s.")
            except Exception as e:
                logger.error(f"Sync failed: {e}")

            # Sleep for the interval, but check stop_event periodically
            stop_event.wait(timeout=int(app_cfg.SYNC_INTERVAL_SECONDS))
    finally:
        logger.info(f"Exiting ({app_cfg.SYNC_INTERVAL_SECONDS} second periodic)...")


def run_watch_sync(srv, lcl, app_cfg):
    """Run ROMM sync in watch mode, syncing on file system changes."""
    
    # Create sync manager with configurable delay
    sync_manager = SyncManager(
        srv, lcl, app_cfg.ROMM_CREDENTIALS,
        watchdog_delay_seconds=int(app_cfg.WATCHDOG_DELAY_SECONDS),
    )

    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        cleanup(sync_manager, signum, frame)

    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Create event handler with sync manager
    event_handler = FileChangeHandler(sync_manager)
    observer = Observer()
    observer.schedule(event_handler, str(app_cfg.SYNC_DIR), recursive=True)

    # Start observer
    observer.start()
    logger.info("Observer started")

    try:
        # Keep the observer running
        observer.join()
    finally:
        observer.stop()
        observer.join()
        logger.info("Exiting (watch mode)...")


def main():
    """Main orchestration function."""
    app_cfg, srv, lcl = initialize_romm_sync()

    logger.info("Romm_sync starting...")

    if app_cfg.SYNC_MODE == "periodic":
        run_periodic_sync(app_cfg)
    elif app_cfg.SYNC_MODE == "watch":
        run_watch_sync(srv, lcl, app_cfg)
    else:
        logger.critical(f"Invalid sync mode {app_cfg.SYNC_MODE}. Exiting...")
        sys.exit(1)


if __name__ == "__main__":
    main()
