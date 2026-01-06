#!/usr/bin/env python3
"""
Photography Organizer Entrypoint
Monitors a directory and triggers a script once file changes have ceased for DELAY_SECONDS
Uses watchdog library instead of inotifywait
"""

import os
import sys
import signal
import logging
import subprocess
import threading
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


# Configuration
DIRECTORY = "/mnt/TDAS/Photography/__ORGANIZED/"
RUN_PY_SCRIPT = "/app/src/pictures_toeditsfolder_g9.py"
DELAY_SECONDS = 60

# Global state
timer_thread = None
timer_lock = threading.Lock()

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


def run_script():
    """Execute the photography organizer script"""
    logger.info(f"---------- Running {RUN_PY_SCRIPT} ----------")
    try:
        subprocess.run([sys.executable, RUN_PY_SCRIPT], check=True)
        logger.info("---------- FINISHED ----------")
    except subprocess.CalledProcessError as e:
        logger.error(f"Script failed with exit code {e.returncode}")
    except Exception as e:
        logger.error(f"Failed to run script: {e}")


def schedule_script_execution():
    """Schedule the script to run after DELAY_SECONDS of inactivity"""
    global timer_thread  # global var for persistence

    with timer_lock:
        # Kill existing timer if one is running
        if timer_thread is not None and timer_thread.is_alive():
            timer_thread.join(timeout=0)  # Non-blocking check

        # Create and start new timer thread
        timer_thread = threading.Timer(DELAY_SECONDS, run_script)
        timer_thread.daemon = False
        timer_thread.start()


class FileChangeHandler(FileSystemEventHandler):
    """Handle filesystem events"""

    def on_modified(self, event):
        if not event.is_directory:
            logger.info(f"Filesystem Event: {event.event_type} - {event.src_path}")
            schedule_script_execution()

    def on_created(self, event):
        if not event.is_directory:
            logger.info(f"Filesystem Event: {event.event_type} - {event.src_path}")
            schedule_script_execution()

    def on_deleted(self, event):
        if not event.is_directory:
            logger.info(f"Filesystem Event: {event.event_type} - {event.src_path}")
            schedule_script_execution()

    def on_moved(self, event):
        if not event.is_directory:
            logger.info(f"Filesystem Event: {event.event_type} - {event.src_path}")
            schedule_script_execution()


def cleanup(signum, frame):
    """Handle graceful shutdown"""
    logger.info("Shutting down gracefully...")

    # Cancel any pending timer
    with timer_lock:
        if timer_thread is not None and timer_thread.is_alive():
            timer_thread.cancel()

    sys.exit(0)


def main():
    """Main entry point"""
    logger.info(f"Photography Organizer initialized - watching {DIRECTORY}")

    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    # Verify directory exists
    if not Path(DIRECTORY).exists():
        logger.error(f"Directory does not exist: {DIRECTORY}")
        sys.exit(1)

    # Create observer and handler
    event_handler = FileChangeHandler()
    observer = Observer()
    observer.schedule(event_handler, DIRECTORY, recursive=True)

    # Start observer
    observer.start()
    logger.info("Observer started")

    try:
        # Keep the observer running
        observer.join()
    except KeyboardInterrupt:
        cleanup(None, None)
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
