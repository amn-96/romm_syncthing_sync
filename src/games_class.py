"""Class that contains a single game's local and remote (romm) information."""
# Imports - builtins
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict
from loguru import logger
from collections import namedtuple

# Imports - app
from .romm_api_func import RommSaves, RommStates
from .config import get_config


LocalRemoteMatch = namedtuple('LocalRemoteMatch', ['local', 'romm', 'api_ops'])


@dataclass
class SaveState:
    """Representation of a single save state for a game. Applies to both local and romm data."""
    path: Path
    platform_id: int
    emulator: str | None
    modified_at: datetime
    slot: int
    in_sync: bool = False  # initialize with False so it has to be checked
    romm_api: RommStates = field(default=None)
    id: int | None = None  # will only be populated if the romm save exists

    def __post_init__(self):  # gets its own reference to the api function for nice syntax
        if self.romm_api is None:
            self.romm_api = get_config().ROMM_CREDENTIALS.states


@dataclass
class SaveFile:
    """Representation of a single save file for a game. Applies to both local and romm data."""
    path: Path
    platform_id: int
    modified_at: datetime
    in_sync: bool = False  # initialize with False so it has to be checked
    romm_api: RommSaves = field(default=None)
    id: int | None = None

    def __post_init__(self):  # gets its own reference to the api function for nice syntax
        if self.romm_api is None:
            self.romm_api = get_config().ROMM_CREDENTIALS.saves


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

    # # ROMM directory paths (set by determine_romm_dirs)
    # romm_saves_dir: Optional[Path] = None
    # romm_states_dir: Optional[Path] = None
    # romm_screenshots_dir: Optional[Path] = None

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

    # region ROMM Functions
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

    def fetch_romm_saves(self):
        """Fetches all saves associated with this game from ROMM API and populates the Game instance.
        Returns the serialized response for debugging/scripting, but this will be ignored by main()
        """
        self.romm_save_files.clear()
        api_ops = get_config().ROMM_CREDENTIALS.saves

        response: list[dict] = api_ops.get(self.romm_id, self.romm_platform_id)
        # Expects one save (.srm) per ROMM user but it will work for multiple. Catalogueing will not be robust though.
        for s in response:
            # modification time -- convert to UTC for consistency (ROMM API returns in UTC)
            save_mtime = datetime.fromisoformat(s['updated_at'])
            self.romm_save_files.append(
                SaveFile(path=Path(s['file_path']) / s['file_name'],
                         platform_id=self.romm_platform_id,
                         modified_at=save_mtime,
                         id=s['id']))
        logger.debug(f"Fetched ROMM saves for {self.name}.")
        return response

    def fetch_romm_states(self):
        """Fetches all states associated with this game from ROMM API and populates the Game instance.
        Returns the serialized response for debugging/scripting, but this will be ignored by main()
        """
        self.romm_state_files.clear()
        api_ops = get_config().ROMM_CREDENTIALS.states

        response: list[dict] = api_ops.get(self.romm_id, self.romm_platform_id)

        # organize API response into namedtuples
        for s in response:
            # modification time -- convert to UTC for consistency (ROMM API returns in UTC)
            state_mtime = datetime.fromisoformat(s['updated_at'])

            # save slot
            state_ext = s["file_extension"].split("state")
            if state_ext[1]:
                try:  # means this is a slot ("state1" etc)
                    save_slot = int(state_ext[-1])
                except ValueError:  # no number at end means this is an autosave ("state.auto")
                    save_slot = -1
            else:  # means this is the 0th state ("state")
                save_slot = 0   # -1 is an auto-save from Retroarch

            self.romm_state_files.append(SaveState(path=Path(s['file_path']) / s['file_name'],
                                                   platform_id=self.romm_platform_id,
                                                   modified_at=state_mtime,
                                                   id=s['id'],
                                                   slot=save_slot,
                                                   emulator=None))
        logger.debug(f"Fetched ROMM states for {self.name}.")
        return response
    # endregion

    def _romm_sync(self,
                   savedata: SaveState | SaveFile,
                   romm_items: List[SaveFile] | List[SaveState],
                   api_ops: RommSaves | RommStates):
        """Internal function: syncs from local to romm"""

        matched = [LocalRemoteMatch(local=savedata, romm=r, api_ops=api_ops)
                   for r in romm_items if (savedata.path.name == r.path.name)]
        # there should be exactly one match

        if len(matched):
            # now check modification time to see if sync is needed
            m = matched[0]
            logger.debug(f"Checking...{self.name} - {m.local.path.name} (local: {m.local.modified_at}, romm: {m.romm.modified_at})")
            if m.local.modified_at > m.romm.modified_at:
                logger.debug(f"Updating save data in ROMM: {m.local.path.name}")
                m.local.romm_api.update(local_filepath=m.local.path, rom_id=self.romm_id, id=m.romm.id)
            else:
                logger.debug("----ROMM is up to date.")
            if len(matched) > 1:
                logger.debug(f"Multiple files on ROMM? {matched}")

        else:  # local present but not in romm --> add
            logger.debug(f"ADDING SAVE to ROMM: {savedata.path.name}")
            api_ops.add(local_filepath=savedata.path, rom_id=self.romm_id)

    def sync_local_saves_to_remote(self):
        """Updates/Adds local save files for the game to its romm counterpart.
        Expects an EXACT match in .srm name here because it assumes the actual ROM
        is the same ROM as the one in the ROMM library"""
        # check if the local save file is already in ROMM
        api_ops = get_config().ROMM_CREDENTIALS.saves
        for lsv in self.local_save_files:
            self._romm_sync(savedata=lsv, romm_items=self.romm_save_files, api_ops=api_ops)

    def sync_local_states_to_remote(self):
        """Updates/Adds local state files for the game to its romm counterpart.
        Expects an EXACT match in .state<x> or .state.auto name here because it assumes the actual ROM is the same ROM as the one in the ROMM library"""
        # Check if the local state file is already in ROMM
        api_ops = get_config().ROMM_CREDENTIALS.states
        for lst in self.local_state_files:
            self._romm_sync(savedata=lst, romm_items=self.romm_state_files, api_ops=api_ops)

    def summary(self) -> str:
        """Return a human-readable summary of the game's sync status."""
        status = []
        if self.local_save_files:
            status.append(f"Local: {len(self.local_save_files)} file(s)")
        else:
            status.append("Local: no saves")

        if self.is_matched:
            status.append(f"ROMM: ID {self.romm_id} ({self.romm_name}), {self.romm_fs_size} MB")
        else:
            status.append("ROMM: unmatched")

        return " | ".join(status)

    def __repr__(self):
        return (f"Game(name={self.name}, "
                f"platform={self.platform}, "
                f"ROMM ID={self.romm_id}, ROMM Platform={self.romm_platform},\n"
                f"Sync Folder Saves={self.local_save_files}, Sync Folder States={self.local_state_files}")
