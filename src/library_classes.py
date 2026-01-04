"""Data classes for ROMM library and local game representations."""
from dataclasses import dataclass, field, asdict
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict
import pandas as pd
import logging
from collections import namedtuple

# program imports
from .romm_api_func import RommUser
from .config import Config

logger = logging.getLogger(__name__)
# logger.setLevel(getattr(logging, config.LOG_LEVEL))
# handler = logging.StreamHandler()
# handler.setFormatter(logging.Formatter(config.LOG_FORMAT))
# logger.addHandler(handler)

SaveState = namedtuple('SaveState', ['path', 'platform_id', 'emulator', 'modified_at', 'slot'])
SaveFile  = namedtuple('SaveFile', ['path', 'platform_id', 'modified_at'])

@dataclass
class Game:
    """Unified representation combining local sync folder data and RetroGameServer library data.

    Fields can be populated from either/both sources:
    - Local scan: name, platform, local_save_files, local_last_modified
    - ROMM library: romm_id, romm_name, romm_platform, romm_fs_size_bytes, romm_data
    """

    name: str  # Game name (from folder/filename)

    # Local folder game data (syncthing/backup directory)
    platform: Optional[str] = None  # platform. This is the save sync folder name (i.e. "slug")
    local_save_files: List[SaveFile] = field(default_factory=list)  # Paths to local save files
    local_state_files: List[SaveState] = field(default_factory=list)

    # Romm server-side game data
    romm_save_files: List[SaveFile] = field(default_factory=list)
    romm_state_files: List[SaveState] = field(default_factory=list)

    # RetroGameServer library data
    romm_id: Optional[int] = None  # ROMM database ID for this game -- used for api lookups
    romm_name: Optional[str] = None  # Game name from ROMM (may differ from local name) -- informational
    romm_platform: Optional[str] = None  # Platform from ROMM (may differ if matched) -- informational
    romm_platform_id: Optional[str] = None  # Platform ID (int) from ROMM -- used for api lookups.
    romm_fs_size: Optional[float] = None  # File size in MB from ROMM
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
        # modification time -- convert to UTC for consistency (ROMM API returns in UTC
        save_mtime = datetime.fromtimestamp(save_path.stat().st_mtime, tz=timezone.utc)
        self.local_save_files.append(SaveFile(path=save_path, platform_id=self.romm_platform_id, modified_at=save_mtime))

    def add_local_state(self, state_path: Path) -> None:
        """Add a local state file and update last_modified timestamp."""
        # modification time -- convert to UTC for consistency (ROMM API returns in UTC)
        state_mtime = datetime.fromtimestamp(state_path.stat().st_mtime, tz=timezone.utc)
        # save slot
        if "state" in state_path.name.split(".")[-1]:  # if "stateX" is last part, this is a save slot
            try:
                save_slot = int(state_path.name.split(".")[-1][-1])
            except ValueError:  # no number at end means slot 0
                save_slot = 0
        else:
            save_slot = -1   # -1 is an auto-save from Retroarch

        self.local_state_files.append(SaveState(path=state_path, platform_id=self.romm_platform_id, modified_at=state_mtime,
                                                slot=save_slot, emulator=None))

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
        self.romm_fs_size = float(romm_row.get('fs_size_bytes')) / 2**20  # in MB
        self.romm_data = romm_row  # Store full row for reference
        self.is_matched = True

    def add_romm_saves(self, romm_user: 'RommUser'):
        """Gets all saves associated with this game from ROMM and adds to the "Game" instance

        Args:
            creds: RommUser credentials object

        Returns the serialized response for debugging/scripting, but this will be ignored by main()
        """
        params = {
            "rom_id": self.romm_id,
            "platform_id": self.romm_platform_id
        }
        response: list[dict] = romm_user.saves.get(self.romm_id, self.romm_platform_id)
        # Expects one save (.srm) per ROMM user but it will work for multiple. Catalogueing will not be robust though.
        for s in response:
            # modification time -- convert to UTC for consistency (ROMM API returns in UTC)
            save_mtime = datetime.fromisoformat(s['updated_at'])
            self.romm_save_files.append(SaveFile(path=s['file_path'], platform_id=self.romm_platform_id, modified_at=save_mtime))
        
        return response

    def add_romm_states(self, romm_user: 'RommUser'):
        """Gets all states associated with this game from ROMM and adds to the "Game" instance.

        Args:
            creds: RommUser credentials object

        Returns the serialized response for debugging/scripting, but this will be ignored by main()
        """
        params = {
            "rom_id": self.romm_id,
            "platform_id": self.romm_platform_id
        }
        response: list[dict] = romm_user.states.get(self.romm_id, self.romm_platform_id)
        
        # organize API response into namedtuples
        for s in response: 
            # modification time -- convert to UTC for consistency (ROMM API returns in UTC)
            state_mtime = datetime.fromisoformat(s['updated_at'])
            if romm_user.romm_base_dir is not None:
                state_path = romm_user.romm_base_dir / "assets" / Path(s["file_path"])  # link it to the user's base dir. just in case....
            else:
                state_path = Path(s["file_path"])
            # save slot
            state_ext = s["file_extension"].split("state")
            if state_ext[1]:
                try:  # means this is a slot ("state1" etc)
                    save_slot = int(state_ext[-1])
                except ValueError:  # no number at end means this is an autosave ("state.auto")
                    save_slot = -1
            else:  # means this is the 0th state ("state")
                save_slot = 0   # -1 is an auto-save from Retroarch

            self.romm_state_files.append(SaveState(path=Path(s['file_path']),
                                                            platform_id=self.romm_platform_id,
                                                            modified_at=state_mtime,
                                                            slot=save_slot,
                                                            emulator=None))
        return response 


    def send_romm_saves(self, romm_user: 'RommUser'):
        """Send saves for this game to ROMM. Uses needs_sync class functions to determine whether to use PUT (update) or POST (add new)

        TODO: These methods are skeleton implementations and aren't integrated into main_loop.py yet
        They should be called as part of the sync workflow when save file syncing is enabled.
        """
        if len(self.get_romm_saves(romm_user)) == 0:  # no saves on ROMM for this game
            for local_sv in self.local_save_files:
                romm_user.saves.add(local_sv, self.romm_id)
        else:
            for local_sv in self.local_save_files:
                romm_user.saves.update(local_sv, self.romm_id)

    def send_romm_states(self, romm_user: 'RommUser'):
        """Send state files for this game to ROMM.

        TODO: This method is a skeleton implementation and isn't integrated into main_loop.py yet
        The copy_states_to_romm method is used instead for now.
        Eventually, this should use the API for uploads instead of direct file copy.
        """
        if len(self.get_romm_states(romm_user)) == 0:  # no saves on ROMM for this game
            for local_st in self.local_state_files:
                romm_user.states.add(local_st, self.romm_id)
        else:
            for local_sv in self.local_save_files:
                romm_user.saves.update(local_sv, self.romm_id)


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
        user_data = romm_user._get("/api/users/me")
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
    #endregion

    def needs_local_sync(self, last_sync_time: datetime) -> bool:
        """Check if any local save has been modified since last sync."""
        return self.local_last_modified is not None and self.local_last_modified > last_sync_time

    def needs_romm_sync(self, last_sync_time: datetime) -> bool:
        """Check if ROMM data indicates remote changes (would be set by a separate API call tracking ROMM changes).

        For now, this is a placeholder for future functionality to track ROMM-side changes.

        FUTURE: Implement remote change tracking
        - Would require tracking ROMM API response metadata (e.g., last_modified timestamps)
        - Or polling specific game entries for changes instead of full library refresh
        - For now, all games are re-synced regardless of remote changes
        """
        # TODO: Not implemented yet
        return False

    def summary(self) -> str:
        """Return a human-readable summary of the game's sync status."""
        status = []
        if self.local_save_files:
            status.append(f"Local: {len(self.local_save_files)} file(s), modified {self.local_last_modified}")
        else:
            status.append("Local: no saves")

        if self.is_matched:
            status.append(f"ROMM: ID {self.romm_id} ({self.romm_name}), {self.romm_fs_size} MB")
        else:
            status.append("ROMM: unmatched")

        return " | ".join(status)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization (handles Path and datetime objects)."""
        data = asdict(self)

        # Serialize SaveFile namedtuples (path, platform_id, modified_at)
        data["local_save_files"] = [
            {
                "path": str(sf.path),
                "platform_id": sf.platform_id,
                "modified_at": sf.modified_at.isoformat() if sf.modified_at else None
            }
            for sf in self.local_save_files
        ]

        # Serialize SaveState namedtuples (path, platform_id, emulator, modified_at, slot)
        data["local_state_files"] = [
            {
                "path": str(ss.path),
                "platform_id": ss.platform_id,
                "emulator": ss.emulator,
                "modified_at": ss.modified_at.isoformat() if ss.modified_at else None,
                "slot": ss.slot
            }
            for ss in self.local_state_files
        ]

        # Serialize ROMM SaveFile namedtuples
        data["romm_save_files"] = [
            {
                "path": str(sf.path),
                "platform_id": sf.platform_id,
                "modified_at": sf.modified_at.isoformat() if sf.modified_at else None
            }
            for sf in self.romm_save_files
        ]

        # Serialize ROMM SaveState namedtuples
        data["romm_state_files"] = [
            {
                "path": str(ss.path),
                "platform_id": ss.platform_id,
                "emulator": ss.emulator,
                "modified_at": ss.modified_at.isoformat() if ss.modified_at else None,
                "slot": ss.slot
            }
            for ss in self.romm_state_files
        ]

        data.pop("romm_data", None)  # Exclude romm_data to avoid over-serialization
        return data

    @staticmethod
    def from_dict(data: dict) -> 'Game':
        """Reconstruct a Game from a serialized dictionary."""
        data = data.copy()

        # Deserialize local SaveFile namedtuples
        data["local_save_files"] = [
            SaveFile(
                path=Path(sf["path"]),
                platform_id=sf["platform_id"],
                modified_at=datetime.fromisoformat(sf["modified_at"]) if sf["modified_at"] else None
            )
            for sf in data.get("local_save_files", [])
        ]

        # Deserialize local SaveState namedtuples
        data["local_state_files"] = [
            SaveState(
                path=Path(ss["path"]),
                platform_id=ss["platform_id"],
                emulator=ss["emulator"],
                modified_at=datetime.fromisoformat(ss["modified_at"]) if ss["modified_at"] else None,
                slot=ss["slot"]
            )
            for ss in data.get("local_state_files", [])
        ]

        # Deserialize ROMM SaveFile namedtuples
        data["romm_save_files"] = [
            SaveFile(
                path=Path(sf["path"]),
                platform_id=sf["platform_id"],
                modified_at=datetime.fromisoformat(sf["modified_at"]) if sf["modified_at"] else None
            )
            for sf in data.get("romm_save_files", [])
        ]

        # Deserialize ROMM SaveState namedtuples
        data["romm_state_files"] = [
            SaveState(
                path=Path(ss["path"]),
                platform_id=ss["platform_id"],
                emulator=ss["emulator"],
                modified_at=datetime.fromisoformat(ss["modified_at"]) if ss["modified_at"] else None,
                slot=ss["slot"]
            )
            for ss in data.get("romm_state_files", [])
        ]

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
