"""Classes for ROMM library and Local Library (saves/states).
In general, these are one step abstracted over Game, so they operate on groups of Game at once."""
from pathlib import Path
from typing import List, Dict, Optional
import polars as pl
from loguru import logger
import re
import yaml

# program imports
from .games_class import Game
from .romm_api_func import RommUser
from .config import METADATA_FILE_FILTERS, get_config


class RetroGameServer:
    def __init__(self, library, platform_map):
        self.library: pl.DataFrame = library
        self.PLATFORM_MAP: dict = platform_map

    @staticmethod
    def _load_platform_mapping() -> dict:
        """Load platform mapping from YAML file.

        Expected format:
            romm_platform_slug:
              - local_folder_name_1
              - local_folder_name_2

        Falls back to empty dictionary on errors, which means exact platform slug matching only.
        """
        try:
            platform_map_path = get_config().PLATFORM_MAP_PATH
            logger.debug(f"Attempting to load {str(platform_map_path)}")
            
            data = None

            if platform_map_path is not None:
                with open(platform_map_path, 'r') as f:
                    data = yaml.safe_load(f)

            if data is None:
                # safe fallback if the file is empty
                raise ValueError("Platform mapping file is empty.")

            if not isinstance(data, dict):
                # safe fallback if invalid yaml
                raise ValueError(f"Platform mapping must be a dictionary, got {type(data).__name__}.")

            # Validate structure: each value should be a list of strings (safe fallbacks again)
            for romm_slug, local_names in data.items():
                if not isinstance(local_names, list):
                    raise ValueError(f"Platform '{romm_slug}' must map to a list of strings, got {type(local_names).__name__}.")

                if not all(isinstance(name, str) for name in local_names):
                    raise ValueError(f"Platform '{romm_slug}' contains non-string values.")

            platform_map = data

        except yaml.YAMLError as e:
            logger.error(f"Failed to parse platform mapping YAML: {e}. Falling back to exact matching.")
            platform_map = {}
        except IOError as e:
            logger.error(f"Failed to read platform mapping file: {e}. Falling back to exact matching.")
            platform_map = {}
        except ValueError as e:
            logger.error(f"{e} Falling back to exact matching.")
            platform_map = {}
        except Exception as e:
            logger.error(f"Unexpected error while trying to read platform mapping file: {e}. Falling back to exact matching.")
            platform_map = {}
        
        finally:
            return platform_map

    def get_romm_platform(self, local_platform_name: str) -> str | None:
        """Get the ROMM platform slug for a local platform folder name.

        Args:
            local_platform_name: The local folder name (e.g., "gba", "snes")

        Returns:
            The ROMM platform slug if found, None otherwise.
            Match is case-insensitive.
        """
        local_platform_lower = local_platform_name.lower()
        for romm_slug, local_names in self.PLATFORM_MAP.items():
            if any(name.lower() == local_platform_lower for name in local_names):
                return romm_slug
        
        return None
    
    @classmethod
    def build_romm_library(cls,
                           romm_user: RommUser):
        """Factory method to instantiate a RetroGameServerClass with romm library info."""
        data = romm_user.get_full_library()

        # define the keys of the json response we want early
        library_columns = {
            'id': pl.Int64,
            'name': pl.String,
            'platform_slug': pl.String,
            'platform_id': pl.Int64,
            'fs_name': pl.String,
            'fs_size_bytes': pl.Int64,
            'platform_display_name': pl.String
        }

        # Strip all fields except essential ones before processing with polars
        # Make sure that the data being processed is ONLY the necessary data.
        filtered_items = []
        for item in data['items']:
            filtered_item = {k: v for k, v in item.items() if k in library_columns.keys()}
            filtered_items.append(filtered_item)

        library = pl.from_dicts(filtered_items, schema=library_columns)

        # Each game stores its non-unique platform slug, so use categorical dtype
        # avoids repeating the same string for every game on a given platform...might make a difference for big libraries
        library = library.with_columns(
            pl.col('platform_slug').cast(pl.Categorical)
        )

        platform_map = cls._load_platform_mapping()

        return cls(library=library, platform_map=platform_map)

    def count(self):
        return len(self.library)

    def size_gb(self):
        return round(self.library['fs_size_bytes'].sum() / 1e9, 2)

    def __repr__(self):
        platform_count = self.library['platform_slug'].n_unique()
        return (f"RetroGameServer(games={self.count()}, "
                f"size_gb={self.size_gb()}, "
                f"platforms={platform_count})")

    def summary(self, verbose=True):
        print(f"Games: {self.count()}, Library Size: {self.size_gb()} GB")
        print()
        platforms = self.library.group_by('platform_slug').agg(
            pl.col('name').alias('games')
        )
        for idx, row in enumerate(platforms.iter_rows(named=True)):
            platform = row['platform_slug']
            games = row['games']
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

    def __init__(self, sync_folders: List[Path]):
        self.sync_folders: List[Path] = sync_folders
        self.games: Dict[str, Game] = {}

    def _rescan_local_saves_and_states(self, game: Game) -> None:
        """Rescans and refreshes the existing list of save and state files for a particular game."""
        
        logger.debug(f"Refreshing saves and states for {game.name}")

        # Clear existing saves and states
        game.local_save_files = []
        game.local_state_files = {}

        for sync_dir in self.sync_folders:
            try:
                platform_dir = sync_dir / game.platform

                files = LibraryScanner._collect_games(platform_dir)
                games_dict = LibraryScanner._build_games_dict(files, game.platform)

                if game.name in games_dict:
                    file_info = games_dict[game.name]
                    LibraryScanner._load_local_saves_and_states(g=game, file_info=file_info)
                    logger.debug(f"Success! Found {len(game.local_save_files)} save(s), {len(game.local_state_files)} state(s).")
            
            except FileNotFoundError as fe:
                logger.debug(f"Not found in {sync_dir}: {fe}")
            except KeyError as ke:
                logger.debug(f"{ke} (this should never happen).")
            except Exception as e:
                logger.debug(f"Unexpected error: {e}")

    def build_local_library(self):
        """Scan local (syncthing or otherwise) folder and build local game catalog. Public class method.
        Aggregates all save files and state files for each game into a single Game object that is then matched to a romm server-side entry. 
        Game name is derived from base filename. Assumes that ROM's in ROMM are the same as that of the savefiles in the local library.
        """

        # Iterate through platform directories within each sync_folder
        for sync_dir in self.sync_folders:
            for platform_dir in sync_dir.iterdir():
                if not platform_dir.is_dir():
                    continue

                platform_name = platform_dir.name

                # Discover and filter all game files in this platform directory
                files = LibraryScanner._collect_games(platform_dir)

                # Build games dictionary from files
                games_dict = LibraryScanner._build_games_dict(files=files, platform_name=platform_name)

                # Create Game objects for all games in this platform
                logger.debug("Building 'Game' objects for local library...")
                for game_name, file_info in games_dict.items():
                    logger.debug(f"Processing game: {game_name} ({len(file_info['saves'])} saves, {len(file_info['states'])} states)")
                    if game_name in self.games:
                        existing_game = self.games[game_name]
                        LibraryScanner._load_local_saves_and_states(g=existing_game, file_info=file_info)
                    else:
                        new_game = Game(name=game_name, path=file_info['path'], platform=file_info['platform'])  
                        LibraryScanner._load_local_saves_and_states(g=new_game, file_info=file_info)
                        self.games[game_name] = new_game

                    logger.debug(f"  Added {game_name} to library")

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

        logger.debug(f"Total games in sync folder: {total}")
        if total > 0:
            logger.debug(f"Matched to ROMM: {matched} ({matched/total*100:.1f}%)")
            logger.debug(f"With local saves: {with_saves}")
            logger.debug(f"Unmatched: {total - matched}.")
        else:
            logger.warning(f"No saves or states were found in {", ".join([str(sf) for sf in self.sync_folders])}.")
    # endregion

    # region File Watcher / Sync Functions
    def _add_new_game_from_watchdog_event(self,
                                          event_file_path: Path,
                                          romm_library: RetroGameServer) -> Game | None:
        """Detect and add a new game based on a watchdog event file path.

        Expects the directory structure: sync_folder/platform_name/game_name.ext
        Follows the same initialization pattern as build_from_scratch for a single game.
        """
        try:
            # Extract platform and game name from path
            # Expected structure: sync_folder/platform_name/game_name.ext
            platform_dir = event_file_path.parent
            platform_name = platform_dir.name
            game_name = event_file_path.stem

            # Scan platform directory and collect files for this game
            files = LibraryScanner._collect_games(platform_dir)
            games_dict = LibraryScanner._build_games_dict(files, platform_name)

            # Create the Game object
            file_info = games_dict[game_name]
            new_game = Game(name=game_name, path=file_info['path'], platform=file_info['platform'])

            # Match to ROMM
            LibraryScanner.match_to_romm([new_game], romm_library)

            # Load local saves and states
            LibraryScanner._load_local_saves_and_states(g=new_game, file_info=file_info)

            # Add to games dict
            self.games[game_name] = new_game
            logger.info(f"[WATCHDOGSYNC] Added new game to library: {game_name}")

            return new_game

        except Exception as e:
            logger.error(f"[WATCHDOGSYNC] Error adding new game from file change event: {e}")
            return None

    def update_from_watchdog_events(self,
                                    event_paths: List[Path],
                                    romm_library: RetroGameServer) -> List[Game]:
        """
        Identifies which games have had file changes and ensures they're in the
        library (adding new games if needed). The affected games are then rescanned to pick up
        any new save/state files before being returned for sync operations.

        Args:
            event_paths: List of file paths from watchdog events (event.src_path)
            romm_library: RetroGameServer instance for matching new games to ROMM

        Returns:
            List of affected Game objects (deduplicated) ready for sync operations
        """
        game_changed_files = LibraryScanner._parse_watchdog_events(event_paths=event_paths)

        affected_games_dict = {}  # game_name -> Game object

        for game_name, changed_files in game_changed_files.items():
            # Find or create the game
            matching_Game = self.get_game_by_name(game_name)
            if not matching_Game:
                logger.info(f"[WATCHDOGSYNC] New game detected: {game_name}")
                # Use one of the changed files to add a game. It will be named the same as the ROM by save file/state convention
                matching_Game = self._add_new_game_from_watchdog_event(next(iter(changed_files)), romm_library)
                if matching_Game is None:
                    logger.warning(f"[WATCHDOGSYNC] Failed to add new game: {game_name}")
                    continue
            else:
                logger.info(f"[WATCHDOGSYNC] {game_name} has {len(changed_files)} files to be synced.")

            # Rescan the game's directory to pick up any new/updated save files.
            self._rescan_local_saves_and_states(matching_Game)
            affected_games_dict[game_name] = matching_Game

        return list(affected_games_dict.values())
    # endregion


class LibraryScanner:
    """Utility class for library scanning functions to interface between LocalLibrary, RetroGameServer, and SyncOrchestrator"""

    # region LocalLibrary helpers
    @staticmethod
    def _collect_games(platform_dir: Path) -> List[Path]:
        """Discover and filter all game files in a platform directory.

        Iterates through files in the platform directory and returns a list of valid game files,
        excluding metadata files and directories defined in METADATA_FILE_FILTERS.
        """
        files = []

        for file_path in platform_dir.iterdir():
            if not file_path.is_file():
                continue
            # Skip various metadata files and directories
            if any(filter_str in file_path.name for filter_str in METADATA_FILE_FILTERS):
                continue
            files.append(file_path)

        return files

    @staticmethod
    def _add_game_dict_entry(file_path: Path, platform_name: str, games_dict: dict) -> dict:
        """Process a file and return updated games_dict with its categorized content.

        Extracts game name and file type from filename, then adds the file to the
        appropriate category (save, state, or state_screen) in games_dict.

        The dict has one key per unique file "stem". Retroarch emulator cores should name files with similar patterns:
            - "game(stem)".srm
            - "game(stem).state"
            - "game(stem).
        Specials: 
            - melonDS uses .ml{n}.
            - regular DS is .dsv or .sav. (all "saves" are captured under the else block)

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
            # check for the actual states. RA-style and others *should* be mutually exclusive.
            state_match = re.match(r'^(.+?)\.state(?:\d+|\.auto)?$', filename)  # general retroarch state extension
            melonds_match = re.match(r'^(.+?)\.ml\d+$', filename)  # melonDS state extension ".ml{n}"
            if state_match:
                game_name = state_match.group(1)
                file_type = 'state'
            elif melonds_match:  # melonDS state extension
                game_name = melonds_match.group(1)
                file_type = 'state'
            else:
                # Regular save file (.srm, .sav, etc.)
                game_name = file_path.stem
                file_type = 'save'

        # Initialize game entry if not seen before
        if game_name not in games_dict:
            games_dict[game_name] = {
                'path': file_path,
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
    def _load_local_saves_and_states(g: Game, file_info: dict):
        """Loads and populates local save and state files from file_info dict into a Game object."""

        g.add_local_saves(game_dict_entry=file_info)

        g.add_local_states(game_dict_entry=file_info)

    @staticmethod
    def _build_games_dict(files: List[Path], platform_name: str) -> dict:
        """Build a fresh games_dict, a dictionary containing the required data for each game:
        {game_name:
            {'platform': str,
            'saves': [],
            'states': [],
            'screens': []
            }
        }
        This is a factory function that checks all the files given to it.
        """
        games_dict = {}
        for file_path in files:
            games_dict = LibraryScanner._add_game_dict_entry(file_path, platform_name, games_dict)

        return games_dict
    # endregion

    # RomM - LocalLibrary interface
    @staticmethod
    def match_to_romm(games: List[Game],
                      romm_library: RetroGameServer) -> None:
        """Match local games to RetroGameServer library entries. Allows passing an arbitrary list of games for incremental state updates.
        Uses platform information to narrow search space for efficiency.
        Currently performs exact name matching within platform. Future enhancement: fuzzy matching.
        """
        for game in games:
            # Narrow search to platform if available
            if game.platform:

                matched_romm_slug = romm_library.get_romm_platform(game.platform)
                logger.debug(f"{game.name} -- Matched platform {game.platform} to slug {matched_romm_slug}.")
                if matched_romm_slug is not None:
                    platform_games = romm_library.library.filter(
                        pl.col('platform_slug') == matched_romm_slug
                    )
                else:  # fallback to directly check romm slug vs detected game.platform
                    platform_games = romm_library.library.filter(
                        pl.col('platform_slug') == game.platform
                    )
                
                # Logic here checks for a column match to *fs_name*, not "name" from romm's api output.
                # This is because ROMM strips regions and rewrites the filename for a cleaned up name, while Retroarch save files and states use the filename directly.
                # For example: retroarch save srm: "Castlevania - Symphony of the Night (USA).srm", romm['name']: "Castlevania: Symphony of the Night"
                # romm_library.library columns: 'fs_name' (full romm filename including region and extension i.e. "Metal Slug X (USA).chd")
                #   I went with this way because it'd be reliable and broad enough, but there are other objects I could use.
                matching_rows = platform_games.filter(
                    pl.col('fs_name').str.contains(game.name, literal=True)
                )

                if matching_rows.height > 0:
                    # Convert DataFrame row to dictionary and populate ROMM data
                    romm_row = matching_rows.row(0, named=True)
                    game.set_romm_data(romm_row)
                else:
                    # Game not found in ROMM library
                    logger.warning(f"Could not match {game.name} with platform '{game.platform}' to ROMM server. Check your platform_mapping.yaml and verify that this game is on the ROMM server.")
                    game.is_matched = False

            else:
                logger.warning(f"Failed to match {game.name}. Games must be organized by platform/content directory. Check your local library structure.")

    # Helpers for watchdog sync
    @staticmethod
    def _parse_watchdog_events(event_paths: List[Path]) -> dict:
        """Parse a list of watchdog events and map them to existing games."""
        game_changed_files = {}  # game_name -> set of Path objects

        # Pre-filter captured events for the relevant file changes.
        # If we don't do this, every chunk that syncthing pushes will get processed. The sync is robust enough to reject these, but it clutters the logs.
        for event_path in event_paths:
            game_name = LibraryScanner._detect_game_from_watchdog_event(event_path)
            if game_name is not None:
                if game_name not in game_changed_files:
                    game_changed_files[game_name] = set()
                game_changed_files[game_name].add(event_path)

        return game_changed_files

    @staticmethod
    def _detect_game_from_watchdog_event(event_path: Path) -> str | None:
        """Extract game name from watchdog event path.

        Note: Metadata files are already filtered by FileChangeHandler, so this receives only valid game files.
        """
        event = event_path.name
        logger.debug(f"[WATCHDOGSYNC] Found modified savedata: {event}.")
        return str(event).split(".")[0]  # game name will be everything before the FIRST extension
