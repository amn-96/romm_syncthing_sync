"""Map RetroArch core folder names to the core IDs RomM/EmulatorJS use for the `emulator` field."""
import re
from functools import cache
from pathlib import Path

from loguru import logger

# https://docs.romm.app/latest/using/in-browser-play/emulatorjs/ ("Supported systems")
EMULATORJS_CORES = {
    "opera", "puae", "mame2003_plus", "mame2003", "fbneo", "stella2014", "atari800", "prosystem",
    "virtualjaguar", "handy", "vice_x64sc", "gearcoleco", "prboom", "gambatte", "mgba", "dosbox-pure",
    "mednafen_ngp", "melonds", "desmume", "mupen64plus_next", "parallel_n64", "fceumm", "nestopia",
    "mednafen_pcfx", "mednafen_psx_hw", "pcsx_rearmed", "ppsspp", "picodrive", "genesis_plus_gx",
    "mednafen_saturn", "yabause", "snes9x", "bsnes", "mednafen_pce", "mednafen_vb", "mednafen_wswan",
}

# Real RetroArch cores that browser play can't load. Still synced, so their states aren't lost.
OTHER_RETROARCH_CORES = {"gpsp"}

# RetroArch folder names whose snake_case form isn't the EmulatorJS core ID.
CORE_ALIASES = {
    "finalburn_neo": "fbneo",
    "mame_2003_plus": "mame2003_plus",
    "mame_2003": "mame2003",
    "stella_2014": "stella2014",
    "dosbox_pure": "dosbox-pure",
}


@cache
def normalize_core(folder_name: str) -> str | None:
    """Core ID for a RetroArch core folder name (e.g. "mGBA" -> "mgba"), or None if it isn't a known core."""
    snake_name = re.sub(r"[^a-z0-9]+", "_", folder_name.lower()).strip("_")
    core_id = CORE_ALIASES.get(snake_name, snake_name)
    if core_id in EMULATORJS_CORES:
        return core_id
    if core_id in OTHER_RETROARCH_CORES:
        logger.warning(f"Core '{core_id}' (folder '{folder_name}') is not an EmulatorJS core, so RomM browser play can't load its states.")
        return core_id
    return None


def core_of(file_path: Path, platform: str) -> str | None:
    """Core a save/state file belongs to. None when the file sits directly in the platform folder."""
    return None if file_path.parent.name == platform else normalize_core(file_path.parent.name)
