# romm_sync

A utility for syncing emulator save files across your gaming devices and backing them up to [ROMM.app](https://romm.app/).

## Overview

**Problem**: Gaming save files live on multiple devices (RetroArch on various emulators, different systems) and are at risk of loss.

**Solution**:
1. Gaming devices sync save files to a central location via Syncthing
2. `romm_sync` detects new/modified saves
3. Syncs saves back into ROMM's database structure (via ROMM API)
4. Backs up saves to a separate location for redundancy

## Architecture

```
Gaming Devices (RetroArch, etc.)
         ↓ (Syncthing)
    Central Sync Folder (TBD structure)
         ↓ (romm_sync watches)
    ROMM Database (via API)
    + Backup Location
```

## Usage

```python
from romm_sync import RetroGameServer

# Initialize connection to ROMM (takes a snapshot of database)
roms = RetroGameServer.initialize_romm_map(
    romm_url="https://emu.amnserv.xyz",
    username="your_username",
    passwd="your_password"
)

# Query the database
print(f"Total games: {roms.count()}")
print(f"Library size: {roms.size_gb()} GB")

# Access games by ID
game = roms.by_id[123]  # Get game name by ROM ID

# Access games by platform
platform_games = roms.by_platform["Nintendo 64"]  # List of game names

# Access full DataFrame for advanced queries
df = roms.library  # pandas DataFrame with all metadata
```

## RetroGameServer Class

**Purpose**: Snapshot of ROMM database with utility methods for querying and analysis.

### Structure

```python
RetroGameServer(
    raw_database: dict,           # Raw ROMM API response
    library: pd.DataFrame,        # Flattened view of all ROMs
    by_id: dict,                  # {rom_id: rom_name}
    by_platform: dict,            # {platform: [game_names]}
    games: list                   # Ordered list of all game names
)
```

### Attributes

- **`raw_database`**: Complete API response containing `items` (ROM list) and `rom_id_index`
- **`library`**: Pandas DataFrame from `pd.json_normalize()` with flattened nested keys
  - Examples: `id`, `name`, `platform_display_name`, `fs_size_bytes`
  - Metadata: `metadatum.genres`, `igdb_metadata.total_rating`, etc.
- **`by_id`**: Dict mapping ROM IDs → ROM names for quick lookup
- **`by_platform`**: Dict mapping platform names → lists of game names
- **`games`**: Simple ordered list of all game names

### Methods

- **`count()`**: Returns total number of games in library
- **`size_gb()`**: Returns total library size in GB (rounded to 2 decimals)

## ROMM Save File Structure

ROMM stores saves in `{romm_root}/saves/{platform}/{rom_id}/` with variable nesting depending on whether the emulator backend is stored:

```
saves/
├── gba/
│   ├── 3/
│   │   └── mgba/
│   │       └── Pokemon - Fire Red Version (U) (V1.1) [2025-08-07 22-38-35-854].srm
│   ├── 34/
│   │   └── Pokemon Lazarus.srm  (no backend folder)
│   └── 39/
│       └── mgba/
│           └── Star Wars - Episode III - Revenge of the Sith (USA) (En,Fr,Es) [2025-12-15 01-13-37-338].srm
└── nes/
    └── 10/
        └── fceumm/
            └── Super Mario Bros. 3 (USA) [2025-08-08 00-06-20-939].srm
```

**Key observations**:
- Path pattern: `saves/{platform}/{rom_id}/[{backend}/]{save_filename}.srm`
- Some ROMs have a backend subfolder (mgba, fceumm), others don't
- Backend presence depends on ROMM's emulator configuration for that ROM
- The `rom_id` can be queried from the ROMM API via `RetroGameServer.by_id` or `library` DataFrame
- Platform name comes from ROMM's `platform_display_name` field

**Challenge**: When syncing saves back, you must:
1. Determine the ROM ID from the game name (via `by_platform` or `by_id`)
2. Check the ROMM API to see if that ROM uses a backend folder
3. Place the save file in the correct nested location

## Next Steps

1. **Define save file structure**: Determine how synced saves are organized in the central folder (e.g., by platform, by device)
2. **Map RetroArch saves**: Document standard RetroArch save locations for each emulator core
3. **Implement sync logic**:
   - Watch central folder for changes
   - Query ROMM API for correct destination paths
   - Handle save file uploads via ROMM API
   - Copy to backup location
4. **Testing**: Validate with your RetroArch setup

## Configuration

Currently uses hardcoded credentials in `initialize_romm_map()`. Should migrate to environment variables:

```python
import os
romm_url = os.getenv("ROMM_URL", "https://emu.amnserv.xyz")
username = os.getenv("ROMM_USERNAME")
passwd = os.getenv("ROMM_PASSWORD")
```

## Utilities

### romrar.sh

Mass extraction utility for multi-part RAR archives downloaded via Pyload.

**Usage**: `./src/romrar.sh`

**Behavior**:
- Recursively finds all `.rar` files in subdirectories and extracts them in place
- Requires `unrar` utility to be installed

## API

### Overview

The `romm_sync` module communicates with ROMM via its REST API to fetch and manage game library data. All API interactions use HTTP Basic Authentication.

### API Endpoints

- **`GET /api/roms/`**: Fetches list of all ROMs with full metadata (pagination required)

### Pagination Quirk

**IMPORTANT**: The ROMM API uses `offset` and `limit` parameters for pagination, **not** `skip` and `limit`. The default limit is **50 records**.

**Quirk**: The `limit` parameter alone without `offset` will always return the first 50 records. Incrementing `skip` does not work—you must use `offset` to retrieve subsequent pages.

**Example**:
```
GET /api/roms/?limit=50&offset=0    # Records 1-50
GET /api/roms/?limit=50&offset=50   # Records 51-100
GET /api/roms/?limit=50&offset=100  # Records 101-150
```

The `get_romm_list()` function handles this automatically by making multiple requests with incrementing `offset` values until all records are retrieved.

### initialize_romm_map()

**Function**: `RetroGameServer.initialize_romm_map(romm_url, username, passwd)`

**Purpose**: Factory method that creates a `RetroGameServer` instance by:
1. Fetching the complete ROM library from ROMM API (with pagination)
2. Normalizing the JSON response into a pandas DataFrame
3. Building three index structures for fast lookups:
   - `by_id`: Map of ROM ID → ROM name
   - `by_platform`: Map of platform name → list of game names
   - `games`: Ordered list of all game names

**Returns**: A `RetroGameServer` snapshot containing the complete database state at call time. This is a read-only snapshot—changes to ROMM after initialization won't be reflected.

**Example**:
```python
roms = RetroGameServer.initialize_romm_map(
    romm_url="https://emu.amnserv.xyz",
    username="akshay",
    passwd="password"
)
print(f"Total games: {roms.count()}")  # Queries the snapshot
```
