"""Data classes for ROMM library and local game representations."""
from dataclasses import dataclass, field, asdict
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict
import pandas as pd
import logging
import shutil

# program imports
from .romm_api_func import RommUser
from .config import Config

logger = logging.getLogger(__name__)
# logger.setLevel(getattr(logging, config.LOG_LEVEL))
# handler = logging.StreamHandler()
# handler.setFormatter(logging.Formatter(config.LOG_FORMAT))
# logger.addHandler(handler)

@dataclass
class Game:
    """Unified representation combining local sync folder data and RetroGameServer library data.

    Fields can be populated from either/both sources:
    - Local scan: name, platform, local_save_files, local_last_modified
    - ROMM library: romm_id, romm_name, romm_platform, romm_fs_size_bytes, romm_data
    """
    # Local sync folder data
    name: str  # Game name (from folder/filename)
    platform: Optional[str] = None  # platform. This is the save sync folder name (i.e. "slug")
    local_save_files: List[Path] = field(default_factory=list)  # Paths to local save files
    local_state_files: List[Path] = field(default_factory=list)
    local_state_screens: List[Path] = field(default_factory=list)
    local_last_modified: Optional[datetime] = None  # Latest modification time of any save file

    # RetroGameServer library data
    romm_id: Optional[int] = None  # ROMM database ID for this game -- used for api lookups
    romm_name: Optional[str] = None  # Game name from ROMM (may differ from local name) -- informational
    romm_platform: Optional[str] = None  # Platform from ROMM (may differ if matched) -- informational
    romm_platform_id: Optional[str] = None  # Platform ID (int) from ROMM -- used for api lookups.
    romm_data: Optional[Dict] = None  # Full raw data from ROMM API for reference 

    # Match quality indicators
    is_matched: bool = False  # Whether this game was successfully matched to ROMM
    match_confidence: float = 1.0  # 0.0-1.0 confidence score for fuzzy matching

    # ROMM directory paths (set by determine_romm_dirs)
    romm_saves_dir: Optional[Path] = None
    romm_states_dir: Optional[Path] = None
    romm_screenshots_dir: Optional[Path] = None

    def add_local_save(self, save_path: Path) -> None:
        """Add a local save file and update last_modified timestamp."""
        self.local_save_files.append(save_path)
        save_mtime = datetime.fromtimestamp(save_path.stat().st_mtime)
        if self.local_last_modified is None or save_mtime > self.local_last_modified:
            self.local_last_modified = save_mtime

    def add_local_state(self, state_path: Path) -> None:
        """Add a local state file and update last_modified timestamp."""
        self.local_state_files.append(state_path)
        state_mtime = datetime.fromtimestamp(state_path.stat().st_mtime)
        if self.local_last_modified is None or state_mtime > self.local_last_modified:
            self.local_last_modified = state_mtime

    def add_local_state_screen(self, screen_path: Path) -> None:
        """Add a local state screenshot and update last_modified timestamp."""
        self.local_state_screens.append(screen_path)
        screen_mtime = datetime.fromtimestamp(screen_path.stat().st_mtime)
        if self.local_last_modified is None or screen_mtime > self.local_last_modified:
            self.local_last_modified = screen_mtime

    #region ROMM Functions
    def set_romm_data(self, romm_row: Dict) -> None:
        """Populate ROMM data from a library row (from RetroGameServer.library DataFrame).

        Args:
            romm_row: Dictionary with keys from the ROMM API response (id, name, platform_display_name, fs_size_bytes, etc.)
        """
        self.romm_id = romm_row.get('id')
        self.romm_name = romm_row.get('name')
        self.romm_platform = romm_row.get('platform_slug')
        self.romm_platform_id = romm_row.get('platform_id')
        self.romm_data = romm_row  # Store full row for reference
        self.is_matched = True

    def get_romm_save_states(self, romm_user: 'RommUser'):
        """Gets all save states associated with this game from ROMM.

        Args:
            creds: RommUser credentials object

        Returns:
            Response object from the API request
        """
        params = {
            "rom_id": self.romm_id,
            "platform_id": self.romm_platform_id
        }
        return romm_user.get("/api/states", params)

    def post_romm_save_states(self, romm_user: 'RommUser'):
        """Function to ADD a new save state."""
        params = {
            "rom_id": self.romm_id
        }
        return romm_user.post("/api/states", params)

    def determine_romm_dirs(self, romm_user: 'RommUser') -> bool:
        """Determine and set the ROMM directory paths for saves, states, and screenshots.

        Sets the following instance attributes:
        - romm_saves_dir: Path to saves directory
        - romm_states_dir: Path to states directory
        - romm_screenshots_dir: Path to screenshots directory

        Args:
            romm_user: RommUser object with credentials and base directory

        Returns:
            True if directories were successfully determined, False otherwise

        Raises:
            ValueError: If required ROMM data or user info is missing
        """
        # Validate required data
        if not self.romm_id or not self.romm_platform:
            logger.warning(f"Cannot determine ROMM dirs for {self.name}: missing romm_id or platform")
            return False

        if not romm_user.romm_base_dir:
            raise ValueError("romm_base_dir not set in RommUser")

        # Get current user's avatar path to extract user_id
        user_data = romm_user.get("/api/users/me")
        avatar_path = user_data.get('avatar_path')
        if not avatar_path:
            raise ValueError("Could not retrieve user avatar_path from API")

        # Extract user_id from avatar path using pathlib
        # avatar_path format: "users/557365723a31/profile/avatar.jpg"
        # Extract first two segments: "users/557365723a31"
        avatar_parts = Path(avatar_path).parts
        if len(avatar_parts) < 2:
            raise ValueError(f"Invalid avatar_path format: {avatar_path}")
        user_id = str(Path(avatar_parts[0]) / avatar_parts[1])

        # Build directory paths for each asset type
        # Structure: {romm_base}/assets/{user_id}/{asset_type}/{platform}/{rom_id}
        romm_base = Path(romm_user.romm_base_dir)
        self.romm_saves_dir = romm_base / "assets" / user_id / "saves" / self.romm_platform / str(self.romm_id)
        self.romm_states_dir = romm_base / "assets" / user_id / "states" / self.romm_platform / str(self.romm_id)
        self.romm_screenshots_dir = romm_base / "assets" / user_id / "screenshots" / self.romm_platform / str(self.romm_id)

        logger.debug(f"Set ROMM dirs for {self.name}:")
        logger.debug(f"  Saves: {self.romm_saves_dir}")
        logger.debug(f"  States: {self.romm_states_dir}")
        logger.debug(f"  Screenshots: {self.romm_screenshots_dir}")

        return True

    def copy_states_to_romm(self, romm_user: 'RommUser') -> tuple[bool, int]:
        """Sync this game's state files to ROMM.

        Orchestrates determining ROMM directories and copying state files.

        Args:
            romm_user: RommUser object with credentials and paths

        Returns:
            Tuple of (success: bool, files_copied: int)
        """
        if not self.local_state_files:
            logger.debug(f"Skipping {self.name}: no state files")
            return False, 0

        try:
            # Determine ROMM directories
            if not self.determine_romm_dirs(romm_user):
                logger.warning(f"Could not determine ROMM dirs for {self.name}")
                return False, 0

            if not self.romm_states_dir:
                logger.error(f"romm_states_dir not set for {self.name}")
                return False, 0

            # Create target directory
            try:
                self.romm_states_dir.mkdir(parents=True, exist_ok=True)
                logger.debug(f"Created/verified directory: {self.romm_states_dir}")
            except OSError as e:
                logger.error(f"Failed to create directory {self.romm_states_dir}: {e}")
                return False, 0

            # Copy all state files
            copied_count = 0
            for state_file in self.local_state_files:
                try:
                    dest_file = self.romm_states_dir / state_file.name
                    shutil.copy2(state_file, dest_file)
                    logger.debug(f"Copied {state_file.name} to {dest_file}")
                    copied_count += 1
                except (OSError, IOError) as e:
                    logger.error(f"Failed to copy {state_file} to {self.romm_states_dir}: {e}")
                    continue

            logger.info(f"[{self.name}] Copied {copied_count} state files to {self.romm_states_dir}")
            return True, copied_count

        except Exception as e:
            logger.error(f"Error syncing {self.name}: {e}")
            return False, 0

    #endregion
    def needs_local_sync(self, last_sync_time: datetime) -> bool:
        """Check if any local save has been modified since last sync."""
        return self.local_last_modified is not None and self.local_last_modified > last_sync_time

    def needs_remote_sync(self, last_sync_time: datetime) -> bool:
        """Check if ROMM data indicates remote changes (would be set by a separate API call tracking ROMM changes).

        For now, this is a placeholder for future functionality to track ROMM-side changes.
        """
        return False  # Placeholder for future ROMM change tracking

    def summary(self) -> str:
        """Return a human-readable summary of the game's sync status."""
        status = []
        if self.local_save_files:
            status.append(f"Local: {len(self.local_save_files)} file(s), modified {self.local_last_modified}")
        else:
            status.append("Local: no saves")

        if self.is_matched:
            status.append(f"ROMM: ID {self.romm_id} ({self.romm_name}), {self.romm_fs_size_bytes} bytes")
        else:
            status.append("ROMM: unmatched")

        return " | ".join(status)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization (handles Path and datetime objects)."""
        data = asdict(self)
        data["local_save_files"] = [str(p) for p in self.local_save_files]
        data["local_last_modified"] = self.local_last_modified.isoformat() if self.local_last_modified else None
        # Remove romm_data to avoid over-serialization; it's already captured in other fields
        data.pop("romm_data", None)
        return data

    @staticmethod
    def from_dict(data: dict) -> 'Game':
        """Reconstruct a Game from a serialized dictionary."""
        data = data.copy()
        data["local_save_files"] = [Path(p) for p in data.get("local_save_files", [])]
        if data.get("local_last_modified"):
            data["local_last_modified"] = datetime.fromisoformat(data["local_last_modified"])
        data.pop("romm_data", None)  # Exclude romm_data to avoid reconstruction issues
        return Game(**data)

    def __repr__(self):
        return (f"Game(name={self.name}, "
                f"platform={self.platform}, "
                f"ROMM ID={self.romm_id}, ROMM Platform={self.romm_platform},\n"
                f"Sync Folder Saves={self.local_save_files}, Sync Folder States={self.local_state_files}")


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
