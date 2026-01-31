"""Dataclass that contains a single game's local and remote (romm) information."""
# Imports - builtins
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, TYPE_CHECKING
from loguru import logger
from collections import namedtuple

from .config import get_config

if TYPE_CHECKING:
    from src.romm_api_func import RommSaves, RommStates


LocalRemoteMatch = namedtuple('LocalRemoteMatch', ['local', 'romm', 'api_ops'])


@dataclass(slots=True)
class SaveState:
    """Representation of a single save state for a game. Applies to both local and romm data."""
    path: Path
    platform_id: int
    emulator: str | None
    modified_at: datetime
    slot: int = 999  # dummy value in case a slot is not found for some reason
    in_sync: bool = False  # initialize with False so it has to be checked
    screenshot: Path | None = None  # only populated if the screenshot exists
    id: int | None = None  # will only be populated if the romm save exists


@dataclass(slots=True)
class SaveFile:
    """Representation of a single save file for a game. Applies to both local and romm data."""
    path: Path
    platform_id: int
    modified_at: datetime
    in_sync: bool = False  # initialize with False so it has to be checked
    id: int | None = None


@dataclass
class Game:
    """Dataclass combining local sync folder data and RetroGameServer library data for a single game.

    Fields can be populated from either/both sources:
    - Local scan: name, platform, local_save_files, local_last_modified
    - RomM library: romm_id, romm_name, romm_platform, romm_fs_size_bytes
    """

    name: str  # Game name (from folder/filename)
    path: Path  # local filepath
    platform: str  # Platform folder name (always set from parent directory)
    local_save_files: list[SaveFile] = field(default_factory=list)  # objects containing local save file info
    local_state_files: dict[int, SaveState] = field(default_factory=dict)  # objects containing local state file info, keyed by slot

    # Romm server-side game data
    romm_save_files: List[SaveFile] = field(default_factory=list)
    romm_state_files: List[SaveState] = field(default_factory=list)

    # RetroGameServer library data
    romm_id: Optional[int] = None  # RomM database ID for this game -- used for api lookups
    romm_name: Optional[str] = None  # Game name from RomM (may differ from local name) -- informational
    romm_platform: Optional[str] = None  # Platform from RomM (may differ if matched) -- informational
    romm_platform_id: Optional[int] = None  # Platform ID (int) from RomM -- used for api lookups.
    romm_fs_size: Optional[float] = None  # File size in MB from RomM

    # Match quality indicators
    is_matched: bool = False  # Whether this game was successfully matched to RomM
    match_confidence: float = 1.0  # 0.0-1.0 confidence score for fuzzy matching

    # # RomM directory paths (set by determine_romm_dirs)
    # romm_saves_dir: Optional[Path] = None
    # romm_states_dir: Optional[Path] = None
    # romm_screenshots_dir: Optional[Path] = None

    def add_local_saves(self, game_dict_entry: dict) -> None:
        """Add a local save file and update last_modified timestamp."""
        # modification time -- convert to UTC for consistency (RomM API returns in UTC
        for save_path in game_dict_entry['saves']:
            save_mtime = datetime.fromtimestamp(save_path.stat().st_mtime, tz=timezone.utc)
            self.local_save_files.append(SaveFile(path=save_path, platform_id=self.romm_platform_id, modified_at=save_mtime))
            logger.debug(f"Found save: {save_path.name} for {self.name}.")

    def add_local_states(self, game_dict_entry: dict) -> None:
        """Add a local state file and update last_modified timestamp."""

        for state_path in game_dict_entry['states']:
            # modification time -- convert to UTC for consistency (RomM API returns in UTC)
            state_mtime = datetime.fromtimestamp(state_path.stat().st_mtime, tz=timezone.utc)

            # check for a screenshot matching this state. Only works for Retroarch-style.
            png_path = Path(f"{state_path}.png")
            if png_path in game_dict_entry['screens']:
                screenshot_path = png_path
                screenshot_string = f" (Screenshot: {png_path.name})"
            else:
                screenshot_path = None
                screenshot_string = ""

            # save slot
            if "state" in state_path.name.split(".")[-1] or "ml" in state_path.name.split(".")[-1]:  # if "stateX" is last part, this is a save slot
                try:
                    save_slot = int(state_path.name.split(".")[-1][-1])
                except ValueError:  # no number at end means slot 0
                    save_slot = 0
            else:
                save_slot = -1   # -1 is an auto-save from Retroarch

            self.local_state_files[save_slot] = SaveState(path=state_path, platform_id=self.romm_platform_id, modified_at=state_mtime,
                                                          slot=save_slot, screenshot=screenshot_path, emulator=None)
            logger.debug(f"Found state: {state_path.name} for {self.name}{screenshot_string}")

    # region RomM Functions
    def set_romm_data(self, romm_row: Dict) -> None:
        """Populate RomM data from a library row (from RetroGameServer.library DataFrame).
        """
        self.romm_id = romm_row.get('id')
        self.romm_name = romm_row.get('name')
        self.romm_platform = romm_row.get('platform_slug')
        self.romm_platform_id = romm_row.get('platform_id')
        self.romm_fs_size = float(romm_row.get('fs_size_bytes')) / 2**20  # in MB
        self.is_matched = True

    def fetch_romm_saves(self):
        """Fetches all saves associated with this game from RomM API and populates the Game instance.
        Returns the serialized response for debugging/scripting, but this will be ignored by main()
        """
        self.romm_save_files.clear()
        api_ops = get_config().ROMM_CREDENTIALS.saves

        response: list[dict] = api_ops.get(self.romm_id, self.romm_platform_id)
        # Expects one save (.srm) per RomM user but it will work for multiple. Catalogueing will not be robust though.
        for s in response:
            # modification time -- convert to UTC for consistency (RomM API returns in UTC)
            save_mtime = datetime.fromisoformat(s['updated_at'])
            self.romm_save_files.append(
                SaveFile(path=Path(s['file_path']) / s['file_name'],
                         platform_id=self.romm_platform_id,
                         modified_at=save_mtime,
                         id=s['id']))
        logger.debug(f"Fetched {len(self.romm_save_files)} RomM save(s) for {self.name}.")
        return response

    def fetch_romm_states(self):
        """Fetches all states associated with this game from RomM API and populates the Game instance.
        Returns the serialized response for debugging/scripting, but this will be ignored by main()
        """
        self.romm_state_files.clear()
        api_ops = get_config().ROMM_CREDENTIALS.states

        response: list[dict] = api_ops.get(self.romm_id, self.romm_platform_id)

        # organize API response into namedtuples
        for s in response:
            # modification time -- convert to UTC for consistency (RomM API returns in UTC)
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
        logger.debug(f"Fetched {len(self.romm_state_files)} RomM state(s) for {self.name}.")
        return response
    # endregion

    def _romm_sync(self,
                   savedata: SaveState | SaveFile,
                   romm_items: List[SaveFile] | List[SaveState],
                   api_ops: 'RommSaves | RommStates',
                   force: bool):
        """Lowest level internal sync function. Syncs a SINGLE file from local to romm"""
        if isinstance(savedata, SaveFile):
            sync_type = "save"
        elif isinstance(savedata, SaveState):
            sync_type = "state"
        else:
            raise TypeError("Provided save data is not expected type SaveState or SaveFile")

        matched = [LocalRemoteMatch(local=savedata, romm=r, api_ops=api_ops)
                   for r in romm_items if (savedata.path.name == r.path.name)]

        if len(matched):
            if len(matched) > 1:
                logger.debug(f"Multiple files on RomM? {matched}")  # there should be exactly one match
            
            m = matched[0]
            logger.debug(f"Checking...{savedata.path.name} (local: {m.local.modified_at}, romm: {m.romm.modified_at})")

            if m.local.modified_at > m.romm.modified_at:
                sync_this_file = True
            else:
                sync_this_file = False
                logger.debug("RomM is already up to date.")
            
            if sync_this_file or force:
                debug_force_str = "(FORCE)" if force else ""
                logger.debug(f"Updating {sync_type} in RomM: {m.local.path.name} {debug_force_str}")
                api_ops.update(obj=savedata, rom_id=self.romm_id, id=m.romm.id)

        else:  # local present but not in romm --> add
            logger.debug(f"Adding {sync_type} to RomM: {savedata.path.name}")
            api_ops.add(obj=savedata, rom_id=self.romm_id)

    def sync_local_saves_to_remote(self, force: bool = False):
        """Updates/Adds local save files for the game to its romm counterpart.
        Expects an EXACT match in .srm name here because it assumes the actual ROM
        is the same ROM as the one in the RomM library"""
        # check if the local save file is already in RomM
        api_ops = get_config().ROMM_CREDENTIALS.saves
        for lsv in self.local_save_files:
            self._romm_sync(savedata=lsv, romm_items=self.romm_save_files, api_ops=api_ops, force=force)

    def sync_local_states_to_remote(self, force: bool = False):
        """Updates/Adds local state files for the game to its romm counterpart.
        Expects an EXACT match in .state<x> or .state.auto name here because it assumes the actual ROM is the same ROM as the one in the RomM library"""
        # Check if the local state file is already in RomM
        api_ops = get_config().ROMM_CREDENTIALS.states
        for lst in self.local_state_files.values():
            self._romm_sync(savedata=lst, romm_items=self.romm_state_files, api_ops=api_ops, force=force)

    def summary(self) -> str:
        """Return a human-readable summary of the game's sync status."""
        status = []
        if self.local_save_files:
            status.append(f"Local: {len(self.local_save_files)} file(s)")
        else:
            status.append("Local: no saves")

        if self.is_matched:
            status.append(f"RomM: ID {self.romm_id} ({self.romm_name}), {self.romm_fs_size} MB")
        else:
            status.append("RomM: unmatched")

        return " | ".join(status)

    def __repr__(self):
        return (f"Game(name={self.name}, "
                f"platform={self.platform}, "
                f"RomM ID={self.romm_id}, RomM Platform={self.romm_platform},\n"
                f"Sync Folder Saves={self.local_save_files}, Sync Folder States={self.local_state_files}")
