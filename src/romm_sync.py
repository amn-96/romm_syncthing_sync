"""Main orchestration for ROMM game save synchronization."""
import os
from pathlib import Path
import logging

from .config import Config
from .library_classes import RetroGameServer, RommUser
from .sync_operations import SaveBackup

# # Initialize Config instance
# config = Config()

logger = logging.getLogger(__name__)
# logger.setLevel(getattr(logging, config.LOG_LEVEL))
# handler = logging.StreamHandler()
# handler.setFormatter(logging.Formatter(config.LOG_FORMAT))
# logger.addHandler(handler)

def main(config: Config = Config()): 
    """Scan a local Syncthing folder and match games to ROMM library.
    Input classes will auto-populate based on environment variables.

    Directory structure:
        sync_folder/
        ├── Game Boy Advance/
        │   ├── Pokemon Fire Red.srm
        │   └── Zelda Minish Cap.srm
        └── NES/
            ├── Super Mario Bros.sav
            └── Donkey Kong.sav

    Workflow:
    1. Scans the sync folder and creates Game dataclasses for each unique game
    2. Loads the ROMM library
    3. Matches local games to ROMM entries
    4. Reports matched, unmatched, and sync-ready games
    """    
    # Step 0: Validate configuration
    try:
        config.validate()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return 1

    # Step 1: Use Config for paths
    sync_folder = config.SYNC_FOLDER
    cache_file = config.CACHE_FILE

    # Step 2: Load ROMM library
    logger.info("Initializing ROMM library...")
    try:
        romm = RetroGameServer.initialize_romm_map(config.ROMM_CREDENTIALS)
        logger.info(f"Loaded ROMM library: {romm}")
    except Exception as e:
        logger.error(f"Failed to load ROMM library: {e}")
        return 1

    # Step 3: Scan local sync folder
    logger.info(f"Scanning sync folder: {sync_folder}")
    if not sync_folder.exists():
        logger.error(f"Sync folder does not exist: {sync_folder}")
        return 1

    catalog = SaveBackup(sync_folder)
    catalog.scan_and_match(romm_server=romm, romm_user=config.ROMM_CREDENTIALS)
    logger.info(f"{catalog.summary()}")

    # Step 4: Get cached library to compare against current local data
    cached_catalog = SaveBackup.from_json(filepath=cache_file, sync_folder=sync_folder)
    if cached_catalog is None:  # no existing cache means start a sync from scratch
        catalog._add_romm_saves_and_states(games=catalog.games, romm_user=config.ROMM_CREDENTIALS)
    else:
        pass


    # Step 5: Report results
    print("\n" + "=" * 80)
    catalog.summary()
    print("=" * 80 + "\n")

    # Step 6: Show matched games
    matched = catalog.get_matched_games()
    if matched:
        print("MATCHED GAMES:")
        for game in sorted(matched, key=lambda g: g.platform or ""):
            print(f"  [{game.platform}] {game.name}")
            print(f"    └─ ROMM ID: {game.romm_id}, Saves: {len(game.local_save_files)}")

    # Step 7: Show unmatched games
    unmatched = catalog.get_unmatched_games()
    if unmatched:
        print("\nUNMATCHED GAMES (not in ROMM):")
        for game in sorted(unmatched, key=lambda g: g.platform or ""):
            print(f"  [{game.platform}] {game.name}")
            print(f"    └─ Saves: {len(game.local_save_files)}")

    # Step 8: Cache catalog to JSON for future use
    logger.info(f"Caching catalog to {cache_file}")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    catalog.to_json(cache_file)


if __name__ == "__main__":
    me = RommUser(romm_url = 'https://emu.local.amnserv.xyz',
                         romm_username="akshay", romm_password="inagalaxyfarfaraway",
                         romm_base_dir=Path("/mnt/d/serverdat/RetroGameServer"))
    cfg = Config(romm_credentials=me,
             sync_folder="/mnt/d/serverdat/EmuSync/SAVES",
             cache_file=Path.cwd() / "local_games.json",
             log_level="DEBUG")
    
    main(config=cfg)
