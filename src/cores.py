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
OTHER_RETROARCH_CORES = {
    "gpsp", "swanstation", "mednafen_pce_fast", "melonds_ds",
    "mednafen_psx", "mednafen_supafaust", "mednafen_snes", "mednafen_supergrafx", "mednafen_lynx",
    "duckstation", "mesen", "mesen_s", "bsnes_hd_beta", "snes9x2002", "snes9x2005", "snes9x2005_plus", "snes9x2010",
    "gearboy", "sameboy", "tgbdual", "vbam", "vba_next", "flycast", "dolphin", "citra", "kronos", "blastem", "gearsystem",
    "potator", "mame", "genesis_plus_gx_wide", "fmsx", "bluemsx", "fuse",
}

# MinUI/NextUI system tags. A RomM client on those devices (Grout) sends the save's folder name, which is the system pak's
# tag, as the emulator; MinArch reads saves from the platform folder with no timestamp in the name. Stock MinUI and NextUI
# paks plus Grout's cfw/*/data/save_directories.json (the second line: Grout lists them, no stock pak ships them).
MINUI_TAGS = {"FC", "GB", "GBC", "GBA", "MGBA", "SGB", "SFC", "SUPA", "MD", "GG", "SMS", "PS", "PCE", "VB", "NGP", "NGPC", "PKM", "P8",
              "32X", "A2600", "A5200", "A7800", "C128", "C64", "COLECO", "CPC", "FBN", "FDS", "LYNX", "MSX", "PET", "PLUS4",
              "PRBOOM", "PUAE", "SEGACD", "SG1000", "VIC",
              "N64", "NDS", "DC", "PS2", "PSP", "3DS", "WS", "WSC"}

# RetroArch folder names (the core's libretro `corename`) whose snake_case form isn't the core ID.
CORE_ALIASES = {
    "finalburn_neo": "fbneo",
    "mame_2003_plus": "mame2003_plus",
    "mame_2003_0_78": "mame2003",
    "stella_2014": "stella2014",
    "dosbox_pure": "dosbox-pure",
    "virtual_jaguar": "virtualjaguar",
    "beetle_pce_fast": "mednafen_pce_fast",
    "beetle_pce": "mednafen_pce",
    "beetle_psx": "mednafen_psx",
    "beetle_psx_hw": "mednafen_psx_hw",
    "beetle_vb": "mednafen_vb",
    "beetle_wonderswan": "mednafen_wswan",
    "beetle_neopop": "mednafen_ngp",
    "beetle_saturn": "mednafen_saturn",
    "beetle_pc_fx": "mednafen_pcfx",
    "beetle_supafaust": "mednafen_supafaust",
    "beetle_bsnes": "mednafen_snes",
    "beetle_supergrafx": "mednafen_supergrafx",
    "beetle_lynx": "mednafen_lynx",
    "snes9x_2002": "snes9x2002",
    "snes9x_2005": "snes9x2005",
    "snes9x_2005_plus": "snes9x2005_plus",
    "snes9x_2010": "snes9x2010",
    "tgb_dual": "tgbdual",
    "vba_m": "vbam",
}


def snake_case(folder_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", folder_name.lower()).strip("_")


def add_user_cores(folders: dict[str, str]) -> None:
    """The `cores:` section of the platform mapping file: core folder name -> core ID, on top of the built-in tables."""
    for folder_name, core_id in folders.items():
        CORE_ALIASES[snake_case(folder_name)] = core_id
        if core_id not in EMULATORJS_CORES:
            OTHER_RETROARCH_CORES.add(core_id)
    normalize_core.cache_clear()


@cache
def normalize_core(folder_name: str) -> str | None:
    """Core ID for a RetroArch core folder name (e.g. "mGBA" -> "mgba"), or None if it isn't a known core."""
    snake_name = snake_case(folder_name)
    core_id = CORE_ALIASES.get(snake_name, snake_name)
    if core_id in EMULATORJS_CORES:
        return core_id
    if core_id in OTHER_RETROARCH_CORES:
        logger.debug(f"Core '{core_id}' (folder '{folder_name}') is not an EmulatorJS core, so RomM browser play can't load its states.")
        return core_id
    return None


@cache
def warn_unknown_folder(folder: Path) -> None:
    """Once per folder: its files are not synced until it is renamed to a core or moved out of the sync folder."""
    logger.warning(f"Skipping '{folder}': not a known RetroArch core folder, so nothing in it is synced.")


def core_of(file_path: Path, platform: str) -> str | None:
    """Core a save/state file belongs to. None when the file sits directly in the platform folder."""
    return None if file_path.parent.name == platform else normalize_core(file_path.parent.name)
