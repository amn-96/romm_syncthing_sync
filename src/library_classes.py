"""Classes for ROMM library and Local Library (saves/states).
In general, these are one step abstracted over Game, so they operate on groups of Game at once."""
from pathlib import Path
from typing import List, Dict, Optional
import pandas as pd
import logging
import re
import json

# program imports
from .games_class import Game
from .romm_api_func import RommUser

logger = logging.getLogger(__name__)


# Glue Functions: the key pieces that exchange the info needed between LocalLibrary and RetroGameServer
def match_romm_saves_and_states(games: List[Game]):
    """Given a list of games, fetch the saves and states present in ROMM for those games.
        - Running this for every file may take a long time because it requires 2 unique API requests per game.
        - This function will only be called when necessary, so it is not associated with any library class."""
    for new_game in games:
        if new_game.is_matched:
            new_game.fetch_romm_saves()
            new_game.fetch_romm_states()
        else:
            logger.warning(f"{new_game.name} is not in ROMM library. Upload this game to your ROMM server to push the local savedata.")


def push_saves_and_states_to_romm(games: List[Game]):
    """Given a list of games that have already had romm saves and states added, check local against remote and upload to ROMM as needed.
        - 1 API request per file!
        - Written so it can be called only for necessary games i.e. from local repo watchdog list of changes files."""
    for new_game in games:
        if new_game.is_matched:
            new_game.sync_local_saves_to_remote()
            new_game.sync_local_states_to_remote()
        else:
            logger.warning(f"{new_game.name} is not in ROMM library. Upload this game to your ROMM server to push the local savedata.")


class RetroGameServer:
    def __init__(self, raw_database, library, by_id, by_platform, games):
        self.raw_database: dict = raw_database
        self.library: pd.DataFrame = library
        self.by_id: dict = by_id
        self.by_platform: dict = by_platform
        self.games: list = games

    @classmethod
    def initialize_romm_map(cls,
                            romm_user: RommUser):
        """Use this factory method to initialize the sync with a snapshot of the ROMM database.

        Args:
            romm_url: ROMM API base URL. Defaults to config.ROMM_URL
            username: ROMM username. Defaults to config.ROMM_USERNAME
            passwd: ROMM password. Defaults to config.ROMM_PASSWORD
        """
        data = romm_user.get_full_library()

        # represent the library as a dataframe for ease of use
        library = pd.json_normalize(data['items'])

        # build a map of ROM ID : ROM NAME
        id_map = dict(zip(library['id'], library['name']))
        platform_map = library.groupby('platform_slug')['name'].apply(list).to_dict()
        games_list = library['name'].tolist()

        return cls(raw_database=data, library=library, by_id=id_map, by_platform=platform_map, games=games_list)

    def count(self):
        return len(self.games)

    def size_gb(self):
        return round(self.library['fs_size_bytes'].sum() / 1e9, 2)

    def __repr__(self):
        return (f"RetroGameServer(games={self.count()}, "
                f"size_gb={self.size_gb()}, "
                f"platforms={len(self.by_platform)})")

    def summary(self, verbose=True):
        print(f"Games: {self.count()}, Library Size: {self.size_gb()} GB")
        print()
        platforms = list(self.by_platform.items())
        for idx, (platform, games) in enumerate(platforms):
            print(f"┌─ {platform}: {len(games)} game(s)")
            if verbose:
                for game in games:
                    print(f"│  - {game}")
                if idx < len(platforms) - 1:
                    print("│")
                    print("├" + "─" * 40)
                    print("│")


class LocalLibrary:
    """Catalog of games from local sync folder, matched to RetroGameServer library."""

    def __init__(self, sync_folder: Path):
        self.sync_folder = sync_folder
        self.games: Dict[str, Game] = {}

    # # Initialize from a previously-built JSON file to save time.   
    # @classmethod
    # def from_json(cls, filepath: Path, sync_folder: Path) -> 'LocalLibrary':
    #     """Load catalog from JSON file."""
    #     local_library = cls(sync_folder)
    #     try:
    #         with open(filepath, 'r') as f:
    #             data = json.load(f)
    #     except FileNotFoundError as fe:
    #         logger.info("No existing cache file found.")
    #         raise (fe)
    #     except Exception as e:
    #         logger.error(f"{e}")
    #         raise (e)
        
    #     local_library.games = {game_data['name']: Game.from_dict(game_data) for game_data in data}
    #     return local_library
    
    # region Scan Functions
    @staticmethod
    def _collect_games(platform_dir: Path) -> List[Path]:
        """Discover and filter all game files in a platform directory.

        Iterates through files in the platform directory and returns a list of valid game files,
        excluding metadata files (sync-conflict, .stignore, .stfolder, ._, .DS_Store).
        """
        files = []

        for file_path in platform_dir.iterdir():
            if not file_path.is_file():
                continue
            # Skip various metadata files
            if "sync-conflict" in file_path.name:
                continue
            if ".stignore" in file_path.name or ".stfolder" in file_path.name:
                continue
            if "._" in file_path.name or ".DS_Store" in file_path.name:
                continue
            files.append(file_path)

        return files
        
    @staticmethod
    def _add_game_dict_entry(file_path: Path, platform_name: str, games_dict: dict) -> dict:
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

    @staticmethod
    def _build_games_dict(files: List[Path], platform_name: str) -> dict:
        """Build games_dict by categorizing files into saves, states, and screenshots.

        Args:
            files: List of file paths to categorize
            platform_name: Name of the platform (for passing to _add_game_dict_entry)

        Returns:
            Dictionary with structure: {game_name: {'platform': str, 'saves': [], 'states': [], 'screens': []}}
        """
        games_dict = {}

        for file_path in files:
            games_dict = LocalLibrary._add_game_dict_entry(file_path, platform_name, games_dict)

        return games_dict

    def match_to_romm(self,
                       games: List[Game],
                       romm_library: RetroGameServer) -> None:
        """Match local games to RetroGameServer library entries.

        Uses platform information to narrow search space for efficiency.
        Currently performs exact name matching within platform. Future enhancement: fuzzy matching.
        """
        for game in games:
            # Narrow search to platform if available
            if game.platform:
                platform_games = romm_library.library[romm_library.library['platform_slug'] == game.platform]
                # Logic here checks for a column match to *fs_name*, not "name" from romm's api output.
                # This is because ROMM strips regions and rewrites the filename for a cleaned up name, while Retroarch save files and states use the filename directly.
                # For example: retroarch save srm: "Castlevania - Symphony of the Night (USA).srm", romm['name']: "Castlevania: Symphony of the Night"
                # romm_library.library columns: 'fs_name' (full romm filename including region and extension i.e. "Metal Slug X (USA).chd")
                #   I went with this way because it'd be reliable and broad enough, but there are other objects I could use.
                matching_rows = platform_games[platform_games['fs_name'].str.contains(game.name, na=False, regex=False)]
            else:
                # Fallback to searching all games if platform not available
                matching_rows = romm_library.library[romm_library.library['name'] == game.name]

            if not matching_rows.empty:
                # Convert DataFrame row to dictionary and populate ROMM data
                romm_row = matching_rows.iloc[0].to_dict()
                game.set_romm_data(romm_row)
            else:
                # Game not found in ROMM library
                game.is_matched = False

    @staticmethod
    def _load_local_saves_and_states(new_game: Game, file_info: dict):
        """Loads and populates local save and state files from file_info dict into a Game object."""

        # Local saves
        for save_file in file_info['saves']:
            new_game.add_local_save(save_file)

        # Local states
        for state_file in file_info['states']:
            new_game.add_local_state(state_file)

    def _rescan_local_saves_and_states(self, game: Game) -> None:
        """Rescans and refreshes the existing list of save and state files for a particular game"""
        # Clear existing saves and states
        game.local_save_files = []
        game.local_state_files = []

        # Rescan platform directory and repopulate
        platform_dir = self.sync_folder / game.platform
        files = self._collect_games(platform_dir)
        games_dict = self._build_games_dict(files, game.platform)

        if game.name in games_dict:
            file_info = games_dict[game.name]
            self._load_local_saves_and_states(new_game=game, file_info=file_info)

    def build_local_library(self) -> dict:
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

            # Discover and filter all game files in this platform directory
            files = self._collect_games(platform_dir)

            # Build games dictionary from files
            games_dict = self._build_games_dict(files, platform_name)

            # Create Game objects for all games in this platform
            games = []
            for game_name, file_info in games_dict.items():
                logger.debug(f"Processing game: {game_name} ({len(file_info['saves'])} saves, {len(file_info['states'])} states)")
                new_game = Game(name=game_name, platform=file_info['platform'])
                self._load_local_saves_and_states(new_game=new_game, file_info=file_info)
                games.append(new_game)
                # Add to games dict
                self.games[game_name] = new_game
                logger.debug(f"  Added {game_name} to library")

        return games_dict
    # endregion

    # region Utility Functions
    def get_game_by_name(self, game_name: str) -> Optional[Game]:
        """Get a game from the library by name."""
        return self.games.get(game_name)

    def get_unmatched_games(self) -> List[Game]:
        """Return list of local games that couldn't be matched to ROMM."""
        return [game for game in self.games.values() if not game.is_matched]

    def get_matched_games(self) -> List[Game]:
        """Return list of local games successfully matched to ROMM."""
        return [game for game in self.games.values() if game.is_matched]

    def summary(self) -> None:
        """Print summary statistics of the catalog."""
        total = len(self.games)
        matched = sum(1 for g in self.games.values() if g.is_matched)
        with_saves = sum(1 for g in self.games.values() if g.local_save_files)

        logging.debug(f"Total games in sync folder: {total}")
        if total > 0:
            logging.debug(f"Matched to ROMM: {matched} ({matched/total*100:.1f}%)")
            logging.debug(f"With local saves: {with_saves}")
            logging.debug(f"Unmatched: {total - matched}.")
        else:
            logging.warning(f"No saves or states were found in {self.sync_folder}.")

    # def _to_dict(self) -> list:
    #     """Serialize entire catalog to list of dictionaries for caching."""
    #     return [game.to_dict() for game in self.games.values()]

    # def to_json(self, filepath: Path) -> None:
    #     """Save catalog to JSON file for caching between runs."""
    #     with open(filepath, 'w') as f:
    #         json.dump(self._to_dict(), f, indent=2)
    # endregion

    # region File Watcher / Sync Functions
    def _add_new_game_from_watchdog_event(self,
                                          event_file_path: Path,
                                          romm_library: RetroGameServer) -> Game:
        """Detect and add a new game based on a watchdog event file path.

        Expects the directory structure: sync_folder/platform_name/game_name.ext
        Follows the same initialization pattern as build_from_scratch for a single game.

        Args:
            event_file_path: Path to the file that triggered the watchdog event
            romm_library: RetroGameServer instance for ROMM matching
            romm_user: RommUser credentials for API calls

        Returns:
            The newly created Game object, or None if it couldn't be created
        """
        try:
            # Extract platform and game name from path
            # Expected structure: sync_folder/platform_name/game_name.ext
            platform_dir = event_file_path.parent
            platform_name = platform_dir.name
            game_name = event_file_path.stem

            # Scan platform directory and collect files for this game
            files = self._collect_games(platform_dir)
            games_dict = self._build_games_dict(files, platform_name)

            # Create the Game object
            file_info = games_dict[game_name]
            new_game = Game(name=game_name, platform=file_info['platform'])

            # Match to ROMM
            self.match_to_romm([new_game], romm_library)

            # Load local saves and states
            self._load_local_saves_and_states(new_game=new_game, file_info=file_info)

            # Add to games dict
            self.games[game_name] = new_game
            logger.info(f"Added new game to library: {game_name}")

            return new_game
        except Exception as e:
            logger.error(f"Error adding new game from watchdog event: {e}")
            return None

    def extract_games_from_watchdog_events(self,
                                           event_paths: List[str],
                                           romm_library: RetroGameServer) -> List[Game]:
        """Parse watchdog filesystem events and identify affected games for syncing.

        This method identifies which games have had file changes and ensures they're in the
        library (adding new games if needed). The affected games are then rescanned to pick up
        any new save/state files before being returned for sync operations.

        Args:
            event_paths: List of file paths from watchdog events (event.src_path)
            romm_library: RetroGameServer instance for matching new games to ROMM

        Returns:
            List of affected Game objects (deduplicated) ready for sync operations
        """
        affected_games_dict = {}  # game_name -> Game object

        for event_path in event_paths:
            path_obj = Path(event_path)
            game_name = path_obj.name.split('.')[0]

            # Skip if we've already processed this game
            if game_name in affected_games_dict:
                continue

            # Find or create the game
            matching_game = self.get_game_by_name(game_name)
            if not matching_game:
                logger.info(f"New game detected: {game_name}")
                matching_game = self._add_new_game_from_watchdog_event(path_obj, romm_library)
                if not matching_game:
                    logger.warning(f"Failed to add new game: {game_name}")
                    continue

            # Rescan the game's directory to pick up any new/updated save files
            self._rescan_local_saves_and_states(matching_game)
            affected_games_dict[game_name] = matching_game

        return list(affected_games_dict.values())
    # endregion
