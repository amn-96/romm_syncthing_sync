"""Sync operations for matching and managing local game saves."""
from pathlib import Path
from datetime import datetime
from typing import List
from collections import namedtuple
import json
import re
import logging

from .library_classes import Game, RetroGameServer
from .romm_api_func import RommUser

logger = logging.getLogger(__name__)


class SaveBackup:
    """Catalog of games from local sync folder, matched to RetroGameServer library."""

    def __init__(self, sync_folder: Path):
        self.sync_folder = sync_folder
        self.games: List[Game] = []

    #region Scan Functions
    @staticmethod
    def _update_games_dict(file_path: Path, platform_name: str, games_dict: dict) -> dict:
        """Process a file and return updated games_dict with its categorized content.

        Extracts game name and file type from filename, then adds the file to the
        appropriate category (save, state, or state_screen) in games_dict.

        Args:
            file_path: Path object for the file to process
            platform_name: Name of the platform directory
            games_dict: Dictionary to update with file information

        Returns:
            Updated games_dict with file categorized and added
        """
        filename = file_path.name

        # Extract base game name and file type
        # Patterns: game.state, game.state0, game.state1, game.state.auto
        #           game.state.png, game.state0.png, game.state.auto.png
        game_name = None
        file_type = None

        # Check for state screenshot (.state.png, .state0.png, .state.auto.png)
        state_screen_match = re.match(r'^(.+?)\.state(?:\d+|\.auto)?\.png$', filename)
        if state_screen_match:
            game_name = state_screen_match.group(1)
            file_type = 'state_screen'
        else:
            # Check for state file (.state, .state0, .state.auto)
            state_match = re.match(r'^(.+?)\.state(?:\d+|\.auto)?$', filename)
            if state_match:
                game_name = state_match.group(1)
                file_type = 'state'
            else:
                # Regular save file (.srm, .sav, etc.)
                game_name = file_path.stem
                file_type = 'save'

        # Initialize game entry if not seen before
        if game_name not in games_dict:
            games_dict[game_name] = {
                'platform': platform_name,
                'saves': [],
                'states': [],
                'screens': []
            }

        # Categorize file
        if file_type == 'save':
            games_dict[game_name]['saves'].append(file_path)
        elif file_type == 'state':
            games_dict[game_name]['states'].append(file_path)
        elif file_type == 'state_screen':
            games_dict[game_name]['screens'].append(file_path)

        return games_dict
    
    def _match_to_romm(self,
                      game: Game,
                      romm_server: RetroGameServer) -> None:
        """Match local games to RetroGameServer library entries.

        Uses platform information to narrow search space for efficiency.
        Currently performs exact name matching within platform. Future enhancement: fuzzy matching.

        FUTURE OPTIMIZATION: Implement LocalStateTracker
        - Track file modification times in cache to detect changes
        - Only re-scan files that have been modified since last sync
        - Currently, all files are scanned and matched every time
        - With caching: 10x faster for unchanged folders

        Args:
            romm_server: RetroGameServer instance containing library DataFrame
        """
        # Narrow search to platform if available
        if game.platform:
            platform_games = romm_server.library[romm_server.library['platform_slug'] == game.platform]
            # Logic here checks for a column match to *fs_name*, not "name" from romm's api output.
            # This is because ROMM strips regions and rewrites the filename for a cleaned up name, while Retroarch save files and states use the filename directly.
            # For example: retroarch save srm: "Castlevania - Symphony of the Night (USA).srm", romm['name']: "Castlevania: Symphony of the Night"
            # romm_server.library columns: 'fs_name' (full romm filename including region and extension i.e. "Metal Slug X (USA).chd")
            #   I went with this way because it'd be reliable and broad enough, but there are other objects I could use.
            matching_rows = platform_games[platform_games['fs_name'].str.contains(game.name, na=False, regex=False)]
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

    def _update_saves_and_states(self, new_game: Game, file_info: dict, romm_user: RommUser):
        """Creates Game class objects for each save and state file from 'games_dict'"""

        # Local
        for save_file in file_info['saves']:
            new_game.add_local_save(save_file)

        for state_file in file_info['states']:
            new_game.add_local_state(state_file)
    
    
    def scan_and_match(self,
                       romm_server: RetroGameServer,
                       romm_user: RommUser) -> dict:
        """Scan local (syncthing or otherwise) folder and build local game catalog. Public class method.

        Expected directory structure:
            sync_folder/
            ├── Game Boy Advance/
            │   ├── Pokemon Fire Red.srm
            │   ├── Pokemon Fire Red.state
            │   ├── Pokemon Fire Red.state.png
            │   └── Zelda Minish Cap.srm
            └── NES/
                ├── Super Mario Bros.sav
                └── Donkey Kong.sav

        Aggregates all save files and state files for each game
        into a single Game object that is then matched to a romm server-side entry. Game name is derived from base filename.
        """
        # Iterate through platform directories
        for platform_dir in self.sync_folder.iterdir():
            if not platform_dir.is_dir():
                continue

            platform_name = platform_dir.name
            games_dict = {}  # Dictionary to aggregate files by game name (re-created for each platform)
            found_games = []  # cache games that have been found to avoid duplicates

            # Discover all files in this platform directory
            for file_path in platform_dir.iterdir():
                if not file_path.is_file():
                    continue
                elif "sync-conflict" in file_path.name:
                    continue
                elif ".stignore" in file_path.name or ".stfolder" in file_path.name:  # ignore syncthing metadata
                    continue
                elif "._" in file_path.name or ".DS_Store" in file_path.name:  # ignore trash files
                    continue

                games_dict = self._update_games_dict(file_path, platform_name, games_dict)
            
            for game_name, file_info in games_dict.items():
                new_game = Game(name=game_name, platform=file_info['platform'])
                
                # update the romm information 
                self._match_to_romm(game=new_game, romm_server=romm_server)
                
                # update the saves and states. Server side info will be populated as well if applicable
                self._update_saves_and_states(new_game=new_game, file_info=file_info, romm_user=romm_user)

                # add to matched games list
                self.games.append(new_game)

        return games_dict
    #endregion

    #region Utility Functions
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
        print(f"Unmatched: {total - matched}.")

    def to_dict(self) -> list:
        """Serialize entire catalog to list of dictionaries for caching."""
        return [game.to_dict() for game in self.games]

    def to_json(self, filepath: Path) -> None:
        """Save catalog to JSON file for caching between runs."""
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

    @staticmethod
    def from_json(filepath: Path, sync_folder: Path) -> 'SaveBackup | None':
        """Load catalog from JSON file."""
        catalog = SaveBackup(sync_folder)
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
        except FileNotFoundError:
            logger.warning("No existing cache file found. Syncing from scratch.")
            return None
        except Exception as e:
            logger.error(f"{e}")
            return None
        
        catalog.games = [Game.from_dict(game_data) for game_data in data]
        return catalog
    #endregion

    #region Sync Functions (in general, these call the ROMM API)
    # Mix of static and instance methods
    def _add_romm_saves_and_states(self, games: List[Game], romm_user: RommUser):
        # Running this for every file takes a long time because of tons of API requests. 
        # This function will only be called when necessary (see cache_compare), so it does NOT use self.games
        for new_game in games:
            new_game.add_romm_saves( romm_user=romm_user)
            new_game.add_romm_states(romm_user=romm_user)

    def sync_all_states(self, romm_user: RommUser) -> dict:
        """Sync state files for all matched games to ROMM.

        Handles stopping/starting ROMM container around the sync operation.

        FUTURE OPTIMIZATION: Implement SyncStrategy pattern
        - Only sync games with modifications (needs_local_sync check)
        - Avoid re-uploading unchanged saves
        - Would reduce bandwidth and ROMM container downtime

        Args:
            romm_user: RommUser object with credentials and container info

        Returns:
            Dictionary with sync statistics:
            {
                'total_games': int,
                'games_synced': int,
                'total_files_copied': int,
                'errors': List[str]
            }
        """
        stats = {
            'total_games': 0,
            'games_synced': 0,
            'total_files_copied': 0,
            'errors': []
        }

        matched_games = self.get_matched_games()
        if not matched_games:
            logger.info("No matched games found to sync")
            return stats

        stats['total_games'] = len(matched_games)

        # for game in matched_games:
        #     success, copied = game.copy_states_to_romm(romm_user)
        #     if success:
        #         stats['games_synced'] += 1
        #         stats['total_files_copied'] += copied

        logger.info(f"Sync complete. Synced {stats['games_synced']}/{stats['total_games']} games, "
                   f"{stats['total_files_copied']} files copied")
        if stats['errors']:
            logger.warning(f"Encountered {len(stats['errors'])} errors during sync")

        return stats
    #endregion
