"""Dataclass that contains a single game's local and remote (romm) information."""
# Imports - builtins
import re
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, TYPE_CHECKING
from loguru import logger
from collections import namedtuple

from .config import get_config
from .cores import core_of

if TYPE_CHECKING:
    from src.romm_api_func import RommSaves, RommStates
    from src.two_way_sync import TwoWaySync


LocalRemoteMatch = namedtuple('LocalRemoteMatch', ['local', 'romm', 'api_ops'])


def state_slot(extension: str) -> int:
    """Slot number from a state file extension. Handles RetroArch "state"/"stateN" and melonDS "mlN".
    A bare "state"/"ml" is slot 0; anything else (e.g. "auto" from ".state.auto") is the RetroArch autosave, -1."""
    match = re.fullmatch(r"(?:state|ml)(\d*)", extension.split(".")[-1])
    if match is None:
        return -1
    return int(match.group(1) or 0)


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
    size: int | None = None  # RomM copies only
    content_hash: None = None  # RomM does not hash states; TwoWaySync.romm_hash fills the gap


@dataclass(slots=True)
class SaveFile:
    """Representation of a single save file for a game. Applies to both local and romm data."""
    path: Path
    platform_id: int
    modified_at: datetime
    in_sync: bool = False  # initialize with False so it has to be checked
    id: int | None = None
    emulator: str | None = None  # core folder the file sits in; None if directly in the platform folder
    content_hash: str | None = None  # RomM copies only (md5). Null for saves RomM could not hash
    slot: str | None = None  # RomM copies only. Only slotted saves can be paired by /api/sync/negotiate
    size: int | None = None  # RomM copies only


def paired_romm(local: SaveState | SaveFile, romms: list[SaveFile] | list[SaveState]) -> SaveFile | SaveState | None:
    """RomM keeps one state row per filename but one save row per (filename, emulator).

    A save in a core folder pairs only with the row of that emulator. A flat save (emulator None) pairs by name
    with a row of any emulator, preferring the row without one."""
    candidates = [romm for romm in romms if local.path.name == romm.path.name
                  and (isinstance(local, SaveState) or local.emulator in (None, romm.emulator))]
    return min(candidates, key=lambda romm: romm.emulator != local.emulator, default=None)


def local_for(romm: SaveFile | SaveState, locals_: list[SaveFile] | list[SaveState], romms: list[SaveFile] | list[SaveState]):
    """The local file that `romm` is paired with, if any."""
    return next((local for local in locals_ if paired_romm(local, romms) is romm), None)


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
    local_state_files: dict[str, SaveState] = field(default_factory=dict)  # objects containing local state file info, keyed by file name

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
    ambiguous_roms: list = field(default_factory=list)  # RomM rows that all match this name; the game is not synced while set
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
            existing = next((s for s in self.local_save_files if s.path.name == save_path.name), None)
            if existing is not None and existing.modified_at >= save_mtime:
                continue  # RomM keeps one save per filename, so only the newest copy across core folders is synced
            if existing is not None:
                self.local_save_files.remove(existing)
            self.local_save_files.append(SaveFile(path=save_path, platform_id=self.romm_platform_id, modified_at=save_mtime,
                                                  emulator=core_of(save_path, self.platform)))
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

            existing = self.local_state_files.get(state_path.name)
            if existing is not None and existing.modified_at >= state_mtime:
                continue  # RomM keeps one state per filename, so only the newest copy across core folders is synced
            self.local_state_files[state_path.name] = SaveState(path=state_path, platform_id=self.romm_platform_id, modified_at=state_mtime,
                                                                slot=state_slot(state_path.name), screenshot=screenshot_path, emulator=core_of(state_path, self.platform))
            logger.debug(f"Found state: {state_path.name} for {self.name}{screenshot_string}")

    # region RomM Functions
    def set_romm_data(self, romm_row: Dict) -> None:
        """Populate RomM data from a library row (from RetroGameServer.library DataFrame).
        """
        self.romm_id = romm_row['id']
        self.romm_name = romm_row['name']
        self.romm_platform = romm_row['platform_slug']
        self.romm_platform_id = romm_row['platform_id']
        self.romm_fs_size = float(romm_row['fs_size_bytes']) / 2**20  # in MB
        self.is_matched = True

    def fetch_romm_saves(self):
        """Fetches all saves associated with this game from RomM API and populates the Game instance.
        Returns the serialized response for debugging/scripting, but this will be ignored by main()
        """
        response: list[dict] = get_config().ROMM_CREDENTIALS.saves.get(self.romm_id, self.romm_platform_id)
        self.set_romm_saves(response)
        logger.debug(f"Fetched {len(self.romm_save_files)} RomM save(s) for {self.name}.")
        return response

    def set_romm_saves(self, rows: list[dict]) -> None:
        """Populate romm_save_files from API rows. Expects one save (.srm) per RomM user but it will work for multiple."""
        self.romm_save_files = [SaveFile(path=Path(s['file_path']) / s['file_name'],
                                         platform_id=self.romm_platform_id,
                                         modified_at=datetime.fromisoformat(s['updated_at']),  # RomM API returns UTC
                                         id=s['id'],
                                         emulator=s['emulator'],
                                         content_hash=s.get('content_hash'),
                                         slot=s.get('slot'),
                                         size=s.get('file_size_bytes')) for s in rows]

    def fetch_romm_states(self):
        """Fetches all states associated with this game from RomM API and populates the Game instance.
        Returns the serialized response for debugging/scripting, but this will be ignored by main()
        """
        response: list[dict] = get_config().ROMM_CREDENTIALS.states.get(self.romm_id, self.romm_platform_id)
        self.set_romm_states(response)
        logger.debug(f"Fetched {len(self.romm_state_files)} RomM state(s) for {self.name}.")
        return response

    def set_romm_states(self, rows: list[dict]) -> None:
        """Populate romm_state_files from API rows."""
        self.romm_state_files = [SaveState(path=Path(s['file_path']) / s['file_name'],
                                           platform_id=self.romm_platform_id,
                                           modified_at=datetime.fromisoformat(s['updated_at']),
                                           id=s['id'],
                                           slot=state_slot(s["file_extension"]),
                                           emulator=s['emulator'],
                                           size=s.get('file_size_bytes')) for s in rows]

    def files_by_kind(self) -> list[tuple[str, list[SaveFile] | list[SaveState], list[SaveFile] | list[SaveState]]]:
        return [("save", self.local_save_files, self.romm_save_files),
                ("state", list(self.local_state_files.values()), self.romm_state_files)]
    # endregion

    def _push(self, local: SaveState | SaveFile, romm: SaveState | SaveFile | None,
              api_ops: 'RommSaves | RommStates', kind: str, engine: 'TwoWaySync | None') -> None:
        def upload() -> dict:
            if romm is None or (local.emulator and local.emulator != romm.emulator):
                # PUT can't change the emulator; re-POSTing the same filename repoints the existing row.
                # A flat local copy has no emulator to repoint to, so it updates the row in place.
                return api_ops.add(obj=local, rom_id=self.romm_id, emulator=local.emulator)
            return api_ops.update(obj=local, rom_id=self.romm_id, id=romm.id)

        if engine:
            engine.push(self.romm_id, kind, local, upload)
        else:
            upload()

    def pull_from_romm(self, engine: 'TwoWaySync') -> None:
        """Bring this game's RomM files into the sync folders (romm_*_files must be populated). Never pushes."""
        for kind, locals_, romms in self.files_by_kind():
            for romm in romms:
                local = local_for(romm, locals_, romms)
                if local is None:
                    engine.pull_new(self, kind, romm)
                elif engine.verdict(self.romm_id, kind, local, romm) == "pull":
                    engine.pull(self.romm_id, kind, local, romm)

    def _romm_sync(self,
                   savedata: SaveState | SaveFile,
                   romm_items: List[SaveFile] | List[SaveState],
                   api_ops: 'RommSaves | RommStates',
                   force: bool,
                   engine: 'TwoWaySync | None' = None):
        """Lowest level internal sync function. Syncs a SINGLE file from local to romm (and, with a writable
        engine, pulls it when only RomM changed)"""
        if isinstance(savedata, SaveFile):
            sync_type = "save"
        elif isinstance(savedata, SaveState):
            sync_type = "state"
        else:
            raise TypeError("Provided save data is not expected type SaveState or SaveFile")

        matched_romm = paired_romm(savedata, romm_items)

        if matched_romm:
            m = LocalRemoteMatch(local=savedata, romm=matched_romm, api_ops=api_ops)
            logger.debug(f"Checking...{savedata.path.name} (local: {m.local.modified_at}, romm: {m.romm.modified_at})")

            if engine and engine.writable and not force:
                verdict = engine.verdict(self.romm_id, sync_type, m.local, m.romm)
                if verdict in ("conflict", "differs"):
                    logger.warning(f"{savedata.path.name} ({verdict}): " + ("changed locally and in RomM" if verdict == "conflict" else "differs from RomM's copy and has no sync history")
                                   + ". Leaving it for the web UI.")
                    return
                if verdict == "pull":
                    engine.pull(self.romm_id, sync_type, m.local, m.romm)
                    return

            if m.local.modified_at > m.romm.modified_at:
                sync_this_file = True
            else:
                sync_this_file = False
                logger.debug("RomM is already up to date.")
            
            if sync_this_file or force:
                debug_force_str = "(FORCE)" if force else ""
                logger.debug(f"Updating {sync_type} in RomM: {m.local.path.name} {debug_force_str}")
                self._push(savedata, m.romm, api_ops, sync_type, engine)

        else:  # local present but not in romm --> add
            logger.debug(f"Adding {sync_type} to RomM: {savedata.path.name}")
            self._push(savedata, None, api_ops, sync_type, engine)

    def sync_local_saves_to_remote(self, force: bool = False, engine: 'TwoWaySync | None' = None):
        """Updates/Adds local save files for the game to its romm counterpart.
        Expects an EXACT match in .srm name here because it assumes the actual ROM
        is the same ROM as the one in the RomM library"""
        # check if the local save file is already in RomM
        api_ops = get_config().ROMM_CREDENTIALS.saves
        for lsv in self.local_save_files:
            self._romm_sync(savedata=lsv, romm_items=self.romm_save_files, api_ops=api_ops, force=force, engine=engine)

    def sync_local_states_to_remote(self, force: bool = False, engine: 'TwoWaySync | None' = None):
        """Updates/Adds local state files for the game to its romm counterpart.
        Expects an EXACT match in .state<x> or .state.auto name here because it assumes the actual ROM is the same ROM as the one in the RomM library"""
        # Check if the local state file is already in RomM
        api_ops = get_config().ROMM_CREDENTIALS.states
        for lst in self.local_state_files.values():
            self._romm_sync(savedata=lst, romm_items=self.romm_state_files, api_ops=api_ops, force=force, engine=engine)

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
