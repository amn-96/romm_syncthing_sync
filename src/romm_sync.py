"""Main orchestration for ROMM game save synchronization."""
from pathlib import Path
import logging

from .config import Config
from .library_classes import RetroGameServer
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
        romm = RetroGameServer.initialize_romm_map(
            romm_url=config.ROMM_URL,
            username=config.ROMM_USERNAME,
            passwd=config.ROMM_PASSWORD
        )
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
    catalog.scan()
    logger.info(f"Found {len(catalog.games)} games in sync folder")

    # Step 4: Match to ROMM
    logger.info("Matching local games to ROMM library...")
    catalog.match_to_romm(romm)

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
            print(f"    └─ Last modified: {game.local_last_modified}")

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

    # Step 9: Show example of retrieving a specific game
    print("\nEXAMPLE: Accessing game data:")
    if catalog.games:
        example_game = catalog.games[0]
        print(f"  Game: {example_game.name}")
        print(f"  Platform: {example_game.platform}")
        print(f"  Matched: {example_game.is_matched}")
        print(f"  Local saves: {example_game.local_save_files}")
        if example_game.is_matched:
            print(f"  ROMM ID: {example_game.romm_id}")
            print(f"  ROMM size: {example_game.romm_fs_size_bytes} bytes")
            print(f"  Summary: {example_game.summary()}")


if __name__ == "__main__":
    main()
