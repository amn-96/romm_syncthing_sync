"""Data classes for ROMM library and local game representations."""
from dataclasses import dataclass, field, asdict
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict
import json
import requests
from requests.auth import HTTPBasicAuth
import pandas as pd
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class Game:
    """Unified representation combining local sync folder data and RetroGameServer library data.

    Fields can be populated from either/both sources:
    - Local scan: name, platform, local_save_files, local_last_modified
    - ROMM library: romm_id, romm_name, romm_platform, romm_fs_size_bytes, romm_data
    """
    # Local sync folder data
    name: str  # Game name (from folder/filename)
    platform: Optional[str] = None  # Platform (GBA, NES, etc.)
    local_save_files: List[Path] = field(default_factory=list)  # Paths to local save files
    local_last_modified: Optional[datetime] = None  # Latest modification time of any save file

    # RetroGameServer library data
    romm_id: Optional[int] = None  # ROMM database ID for this game
    romm_name: Optional[str] = None  # Game name from ROMM (may differ from local name)
    romm_platform: Optional[str] = None  # Platform from ROMM (may differ if matched)
    romm_fs_size_bytes: Optional[int] = None  # File size in ROMM library
    romm_data: Optional[Dict] = None  # Full raw data from ROMM API for reference

    # Match quality indicators
    is_matched: bool = False  # Whether this game was successfully matched to ROMM
    match_confidence: float = 1.0  # 0.0-1.0 confidence score for fuzzy matching

    def add_local_save(self, save_path: Path) -> None:
        """Add a local save file and update last_modified timestamp."""
        self.local_save_files.append(save_path)
        save_mtime = datetime.fromtimestamp(save_path.stat().st_mtime)
        if self.local_last_modified is None or save_mtime > self.local_last_modified:
            self.local_last_modified = save_mtime

    def set_romm_data(self, romm_row: Dict) -> None:
        """Populate ROMM data from a library row (from RetroGameServer.library DataFrame).

        Args:
            romm_row: Dictionary with keys from the ROMM API response (id, name, platform_display_name, fs_size_bytes, etc.)
        """
        self.romm_id = romm_row.get('id')
        self.romm_name = romm_row.get('name')
        self.romm_platform = romm_row.get('platform_display_name')
        self.romm_fs_size_bytes = romm_row.get('fs_size_bytes')
        self.romm_data = romm_row  # Store full row for reference
        self.is_matched = True

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


def get_romm_list(romm_url: str, username: str, passwd: str) -> dict:
    """Gets the full list of ROM's in the ROMM.app database with pagination support"""

    url = f"{romm_url}/api/roms/"
    all_items = []
    limit = 50
    offset = 0

    while True:
        params = {"limit": limit, "offset": offset}
        response = requests.get(url, params=params, auth=HTTPBasicAuth(username, passwd))

        logger.debug(f"Status Code: {response.status_code}, Offset: {offset}, Limit: {limit}")
        data = response.json()

        if not data.get("items"):
            logger.debug("No more items to fetch")
            break

        items_count = len(data["items"])
        all_items.extend(data["items"])
        logger.debug(f"Fetched {items_count} items (total so far: {len(all_items)})")

        if items_count < limit:
            logger.debug("Reached end of records")
            break

        offset += limit

    logger.debug(f"Total items retrieved: {len(all_items)}")
    logger.debug("Response received successfully")

    return {"items": all_items}


class RetroGameServer:
    def __init__(self, raw_database, library, by_id, by_platform, games):
        self.raw_database: dict = raw_database
        self.library: pd.DataFrame = library
        self.by_id: dict = by_id
        self.by_platform: dict = by_platform
        self.games: list = games

    @classmethod
    def initialize_romm_map(cls,
                            romm_url: str = "https://emu.amnserv.xyz",
                            username: str = "akshay",
                            passwd: str = "inagalaxyfarfaraway"):
        """Use this factory method to initialize the sync with a snapshot of the ROMM database"""
        data = get_romm_list(romm_url, username, passwd)

        # represent the library as a dataframe for ease of use
        library = pd.json_normalize(data['items'])

        # build a map of ROM ID : ROM NAME
        id_map = dict(zip(library['id'], library['name']))
        platform_map = library.groupby('platform_display_name')['name'].apply(list).to_dict()
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
