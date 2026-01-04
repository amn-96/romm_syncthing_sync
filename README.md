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

### A note on using the ROMM API to upload saves and states
I did not find any documentation within the ROMM API that detailed how exactly to send the file with the API. Luckily, I was able to find the skeleton API request thanks to the efforts of the Grout developers. Grout is a ROMM client for NextUI for my TrimUI Brick!

https://github.com/rommapp/grout

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

## Module Overview

The `romm_sync` codebase follows a 5-layer architecture from low-level API communication to high-level orchestration:

### Layer 1: Raw API Communication
**`romm_api_func.py` - ROMM API Client**

Encapsulates all HTTP communication with the ROMM REST API. The `RommUser` class handles credentials storage, authentication, and provides generic `get()`, `post()`, and `put()` methods that wrap `requests` library calls with HTTP Basic Auth. The `get_full_library()` method implements pagination logic to fetch the complete ROM database (required because ROMM uses `offset`/`limit` parameters, not `skip`/`limit`).

### Layer 2: Individual Data Organization
**`library_classes.py` - Data Models**

Defines core data structures for representing games and the ROMM library. The `Game` dataclass unifies local sync folder data (save/state files, modification times) with ROMM library metadata (ROM ID, name, platform). The `RetroGameServer` class is a read-only snapshot of the ROMM database, providing fast lookups by ID, platform, and maintains a flattened pandas DataFrame view for advanced queries. Both classes include serialization methods (`to_dict()`/`from_dict()`, `to_json()`/`from_json()`) for persistence between runs.

### Layer 3: Library-Level Operations
**`sync_operations.py` - Sync and Discovery Logic**

Orchestrates discovering local saves and syncing them to ROMM. The `SaveBackup` class scans the local sync folder structure, aggregates files by game name, and matches them to ROMM entries using platform + filename matching. Each `Game` instance is responsible for its own sync operations via the `copy_states_to_romm()` method, which determines target directories on the ROMM filesystem and copies state files. Helper functions `stop_romm_container()` and `start_romm_container()` manage Docker lifecycle around sync operations (stopping the container prevents file-lock issues during copying).

### Layer 4: Configuration
**`config.py` - Environment Configuration**

Loads and validates environment-based configuration from variables like `ROMM_URL`, `ROMM_USERNAME`, `ROMM_BASE_DIR`, `SYNC_FOLDER`, etc. Creates and provides the `RommUser` credentials object to higher layers. Centralizes all configuration logic to avoid hardcoded values scattered throughout the codebase.

### Layer 5: Orchestration
**`romm_sync.py` - Main Workflow Coordinator**

High-level script that ties all layers together. Workflow: initialize ROMM library snapshot → scan local sync folder → match local games to ROMM entries → sync matched games → report results. This layer is primarily responsible for calling methods on lower layers and managing the overall execution flow.

## API Implementation Notes

### Unused API Functions

The codebase includes several ROMM API methods on the `Game` class that remain **unused due to limitations in the ROMM API itself**:

- **`get_romm_save_states(romm_user)`**: Fetches existing save states for a game from the ROMM API
- **`post_romm_save_states(romm_user)`**: Uploads a new save state to ROMM via the API

**Why they're unused**:

1. **Documentation Gap**: The ROMM API's state upload endpoint (`POST /api/states`) lacks proper documentation in the OpenAPI spec. The request body schema is not defined, making it unclear how to properly format file uploads. Testing revealed the endpoint expects multipart form-data with a `file` field, but this is not documented.

2. **Known ROMM Issues**: ROMM has documented issues with save state handling, including save state corruption (#2319, #2562 in the ROMM GitHub repository). Users have reported data loss when relying on the API for state uploads.

**Fallback Implementation**: Instead of using the ROMM API for state uploads, `romm_sync` uses direct filesystem copying. It determines the target directory structure (`{romm_base}/assets/users/{user_id}/states/{platform}/{rom_id}/`) by extracting the user ID from the API response (`/api/users/me`) and copies state files directly using `shutil.copy2()`. This approach is more reliable and eliminates the risk of API-related data loss.

## Configuration

Set environment variables for runtime configuration:

```bash
ROMM_URL=https://emu.amnserv.xyz
ROMM_USERNAME=your_user
ROMM_PASSWORD=your_password
ROMM_API_LIMIT=50
SYNC_FOLDER=/data/sync
CACHE_FILE=/data/cache/local_games.json
LOG_LEVEL=INFO
SYNC_INTERVAL_SECONDS=300
DRY_RUN=false
ENABLE_METRICS=false
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
