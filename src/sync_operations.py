"""Sync operations for matching and managing local game saves."""
from pathlib import Path
from datetime import datetime
from typing import List
import json

from library_classes import Game


class SaveBackup:
    """Catalog of games from local sync folder, matched to RetroGameServer library."""

    def __init__(self, sync_folder: Path):
        self.sync_folder = sync_folder
        self.games: List[Game] = []

    def scan(self) -> None:
        """Scan sync folder and build local game catalog.

        Expected directory structure:
            sync_folder/
            ├── Game Boy Advance/
            │   ├── Pokemon Fire Red.srm
            │   └── Zelda Minish Cap.srm
            └── NES/
                ├── Super Mario Bros.sav
                └── Donkey Kong.sav

        All files in platform directories are treated as save files.
        Game name is derived from filename (without extension).
        """
        # Iterate through platform directories
        for platform_dir in self.sync_folder.iterdir():
            if not platform_dir.is_dir():
                continue

            platform_name = platform_dir.name

            # Discover all files in this platform directory
            for save_file in platform_dir.iterdir():
                if not save_file.is_file():
                    continue

                # Extract game name from filename (without extension)
                game_name = save_file.stem

                # Check if game already exists in catalog
                existing_game = next((g for g in self.games if g.name == game_name), None)
                if existing_game:
                    existing_game.add_local_save(save_file)
                else:
                    # Create new game entry
                    new_game = Game(name=game_name, platform=platform_name)
                    new_game.add_local_save(save_file)
                    self.games.append(new_game)

    def match_to_romm(self, romm_server) -> None:
        """Match local games to RetroGameServer library entries.

        Uses platform information to narrow search space for efficiency.
        Currently performs exact name matching within platform. Future enhancement: fuzzy matching.

        Args:
            romm_server: RetroGameServer instance containing library DataFrame
        """
        for game in self.games:
            # Narrow search to platform if available
            if game.platform:
                platform_games = romm_server.library[romm_server.library['platform_display_name'] == game.platform]
                matching_rows = platform_games[platform_games['name'] == game.name]
            else:
                # Fallback to searching all games if platform not available
                matching_rows = romm_server.library[romm_server.library['name'] == game.name]

            if not matching_rows.empty:
                # Convert DataFrame row to dictionary and populate ROMM data
                romm_row = matching_rows.iloc[0].to_dict()
                game.set_romm_data(romm_row)
            else:
                # Game not found in ROMM library
                game.is_matched = False

    def get_unmatched_games(self) -> List[Game]:
        """Return list of local games that couldn't be matched to ROMM."""
        return [game for game in self.games if not game.is_matched]

    def get_matched_games(self) -> List[Game]:
        """Return list of local games successfully matched to ROMM."""
        return [game for game in self.games if game.is_matched]

    def needs_sync(self, last_sync_time: datetime) -> List[Game]:
        """Return games with local changes since last sync."""
        return [game for game in self.games if game.needs_local_sync(last_sync_time)]

    def summary(self) -> None:
        """Print summary statistics of the catalog."""
        total = len(self.games)
        matched = sum(1 for g in self.games if g.is_matched)
        with_saves = sum(1 for g in self.games if g.local_save_files)

        print(f"Total games in sync folder: {total}")
        print(f"Matched to ROMM: {matched} ({matched/total*100:.1f}% if total > 0 else 0)")
        print(f"With local saves: {with_saves}")
        print(f"Unmatched: {total - matched}")

    def to_dict(self) -> list:
        """Serialize entire catalog to list of dictionaries for caching."""
        return [game.to_dict() for game in self.games]

    def to_json(self, filepath: Path) -> None:
        """Save catalog to JSON file for caching between runs."""
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

    @staticmethod
    def from_json(filepath: Path, sync_folder: Path) -> 'SaveBackup':
        """Load catalog from JSON file."""
        catalog = SaveBackup(sync_folder)
        with open(filepath, 'r') as f:
            data = json.load(f)
        catalog.games = [Game.from_dict(game_data) for game_data in data]
        return catalog
