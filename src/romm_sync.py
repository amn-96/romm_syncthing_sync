#!/usr/bin/env python3
"""
romm_sync main module. Initializes sync system and manages sync modes (full sync and watchdog sync).
"""
import sys
import logging
from pathlib import Path

from .config import Config, get_config
from .library_classes import RetroGameServer, LocalLibrary
from .sync_orchestrator import SyncOrchestrator, SyncManager

logger = logging.getLogger(__name__)


def cleanup(sync_manager: SyncManager, signum, frame):
    """Handle graceful shutdown"""
    logger.info("Shutting down gracefully...")
    sync_manager.cancel_pending()
    sys.exit(0)


def get_local_library(srv: RetroGameServer, library_path: Path) -> LocalLibrary:
    logger.info("Building local game library and matching to contents of your ROMM server.")
    local_library = LocalLibrary(library_path)
    local_library.build_local_library()
    # Match games to ROMM
    local_library.match_to_romm(list(local_library.games.values()), srv)
    
    # Cache the library
    # cfg = get_config()
    # local_library.to_json(filepath=cfg.CACHE_FILEPATH)

    return local_library


def full_sync(app_cfg: Config) -> tuple[RetroGameServer, LocalLibrary]:
    """Performs a full sync between local library and ROM. For use with initial sync and periodic sync modes.
    This will:
    - build both local and romm libraries from scratch
    - check local and romm state of every detected game
    - perform the sync using the SyncOrchestrator."""

    # Initialize a fresh state for the romm library and local library
    romm_library = RetroGameServer.initialize_romm_map(app_cfg.ROMM_CREDENTIALS)
    lcl = get_local_library(srv=romm_library, library_path=app_cfg.SYNC_DIR)

    # Use orchestrator for full sync
    orchestrator = SyncOrchestrator(lcl, romm_library, app_cfg.ROMM_CREDENTIALS)
    result = orchestrator.full_sync(cache_filepath=app_cfg.CACHE_FILEPATH)

    if not result.success:
        logger.warning(f"Full sync encountered errors: {result.error_message}")
    if result.games_failed:
        logger.warning(f"{len(result.games_failed)} games failed to sync")
    if result.unmatched_games:
        logger.info(f"{len(result.unmatched_games)} games could not be matched to ROMM")

    return romm_library, lcl


def initialize_romm_sync():
    # Get the app's configuration from docker env variables. See .env.example
    app_cfg = get_config()
    app_cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)

    # First sync. Compares all save states
    logger.info("Performing initial sync. This may take a while...")
    srv, lcl = full_sync(app_cfg)

    return app_cfg, srv, lcl
