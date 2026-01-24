*(This document was initially AI-generated, then proofread and corrected by me)*
# romm_sync Architecture & Implementation Summary

## Overview

romm_sync is a Python daemon that synchronizes RetroArch save files and save states between local storage and a ROMM (Retro ROM Manager) server. It supports two sync modes: **periodic** (runs full sync at intervals) and **watch** (monitors filesystem for changes and syncs incrementally).

## Docker Environment

The application runs in a Docker container with the following configuration:

**Environment Variables** (`.env.example`):
- ROMM API credentials and configuration
- Sync mode selection (periodic vs watch)
- Directory configuration for saves/states
- Timing parameters (sync interval, watchdog delay)
- Platform verification settings
- Logging configuration

**Volume Mounts** (`docker-compose.yml`):
The container mount path is hard-coded and it is expected that the user define their host mounts via `.env.example`. See the [relevant section in the README](../README.md###sync-configuration)
- `/syncdata/saves` - Bind mount for save files (`.srm`, `.sav`, etc.)
- `/syncdata/states` - Bind mount for state files (`.state`, `.state0`, `.state.png`, etc.)
- `/appdata` - Application data directory for cache and logs

**Alternative Configuration**: Can use `/syncdata` as a single mount point when saves and states share the same directory structure (e.g., Knulli devices).

## Code Flow Architecture

### Entry Point: main.py

The application starts in `main.py` and follows this initialization sequence:

1. **Load environment variables** - Uses `dotenv` to load `.env` configuration
2. **Initialize sync system** - Calls `initialize_romm_sync()` from `romm_sync.py`
3. **Route to sync mode** - Dispatches to either `run_periodic_sync()` or `run_watch_sync()` based on `SYNC_MODE`

```
main()
  └─> initialize_romm_sync()  [romm_sync.py]
       ├─> get_config()  [Validate environment, create Config object]
       ├─> full_sync()   [Initial sync on startup]
       │    └─> Returns (RetroGameServer, LocalLibrary)
       └─> Return (app_cfg, srv, lcl)

  └─> run_periodic_sync() OR run_watch_sync()
```

### Configuration Layer: config.py

**Config class** manages all application settings:
- Parses environment variables with defaults as specified in [.env.example](../.env.example)
- Validates required configuration
- Sets up logging via `LoggingConfig` with per-library filtering
- Creates singleton instance via `get_config()` for global access

**Key validation**: Enforces that either (env variables) `(SAVE_SYNC_FOLDER + STATE_SYNC_FOLDER)` OR `ALL_SYNC_FOLDER` is specified, but not both.

### Initialization: romm_sync.py

**`initialize_romm_sync()`** performs the application bootstrap:

1. **Get configuration** - `get_config()` returns validated Config singleton
2. **Perform initial full_sync** - Establishes baseline state
3. **Return components** - Config, server library, and local library objects

**`full_sync(app_cfg)`** performs complete synchronization:

1. **Initialize ROMM library** - `RetroGameServer.build_romm_library()` fetches full game library from ROMM API
2. **Build local library** - `LocalLibrary(sync_dirs)` scans filesystem for saves/states
3. **Match games** - Links local files to ROMM entries via platform and filename matching
4. **Execute sync** - `SyncOrchestrator.full_sync()` performs bidirectional sync
5. **Return libraries** - Both server and local library objects for reuse

### Library Management: library_classes.py

**RetroGameServer** - Represents ROMM's game library:
- Fetches full game catalog via `RommUser.get_full_library()` API call
- Stores as Polars DataFrame with categorical platform slugs for memory efficiency
- Strips metadata fields (`metadatum`, `igdb_metadata`, etc.) to reduce footprint
- Keeps only essential columns: `id`, `name`, `platform_slug`, `fs_name`, etc.

**LocalLibrary** - Manages local save/state files:
- Accepts list of sync directories (supports multiple paths)
- `build_local_library()` - Scans platform directories for game files
  - Filters out metadata files (syncthing conflicts, .DS_Store, temp files)
  - Uses regex matching to categorize: saves (`.srm`), states (`.state*`), screenshots (`.state*.png`)
  - Aggregates files per game into `Game` objects
- `match_to_romm()` - Links games to ROMM library entries
  - Primary matching: filters by platform, then searches `fs_name` for game name.
  - Uses platform_mapping.yaml to allow multiple matches for romm's platform slug
- `extract_games_from_watchdog_events()` - Processes filesystem change events (watch mode)
  - Deduplicates events by game name
  - Adds new games if detected for the first time
  - Rescans affected games to pick up file changes

### Sync Orchestration: sync_orchestrator.py

**SyncOrchestrator** - Coordinates synchronization logic:
- `full_sync()` - Complete sync of entire library
  1. Validates matched vs unmatched games
  2. Fetches current ROMM state for all games (`_fetch_romm_data()`)
  3. Pushes local saves/states to ROMM (`_push_local_to_romm()`)
  4. Returns `SyncResult` with success/failure details

- `watchdog_sync()` - Incremental sync for file change events
  1. Extracts affected games from event paths
  2. Rescans those games for updated files
  3. Fetches ROMM state for affected games only
  4. Pushes local changes to ROMM

**SyncManager** - Manages watchdog event lifecycle:
- Buffers filesystem events in `pending_events` queue
- Implements debounce timer (default 60 seconds)
- Prevents concurrent syncs via `is_syncing` flag
- `schedule_sync()` - Resets timer on new events
- `execute_sync()` - Drains event queue and triggers `SyncOrchestrator.watchdog_sync()`
- Thread-safe via `event_lock` and `timer_lock`

**FileChangeHandler** - Watchdog event handler (inherits from `FileSystemEventHandler`):
- Listens for `on_modified`, `on_created`, `on_deleted`, `on_moved` events
- Filters metadata files before queueing
- Passes valid events to `SyncManager.add_event()`
- Triggers `SyncManager.schedule_sync()` to start/reset debounce timer

## Sync Mode Comparison

### Full Sync (Periodic Mode)

**Flow**:
```
run_periodic_sync()
  └─> Loop every SYNC_INTERVAL_SECONDS
       └─> full_sync()
            ├─> RetroGameServer.build_romm_library()  [Fresh API fetch]
            ├─> LocalLibrary.build_local_library()      [Full filesystem scan]
            ├─> LocalLibrary.match_to_romm()            [Match all games]
            └─> SyncOrchestrator.full_sync()
                 ├─> _fetch_romm_data() for all matched games
                 └─> _push_local_to_romm() for all games
```

**Characteristics**:
- Rebuilds ROMM and local libraries from scratch each cycle
- Fetches ROMM state for every matched game
- Syncs all games regardless of changes
- Higher API load and longer execution time

### Watchdog Sync (Watch Mode)

**Flow**:
```
run_watch_sync()
  ├─> Create SyncManager(srv, lcl, credentials)
  ├─> Create FileChangeHandler(sync_manager)
  └─> Start Observer monitoring SYNC_DIR
       │
       [Filesystem event occurs]
       │
       └─> FileChangeHandler._handle_event()
            ├─> Filter metadata files
            ├─> SyncManager.add_event()
            └─> SyncManager.schedule_sync()
                 │
                 [Wait WATCHDOG_DELAY_SECONDS]
                 │
                 └─> SyncManager.execute_sync()
                      └─> SyncOrchestrator.watchdog_sync()
                           ├─> LocalLibrary.extract_games_from_watchdog_events()
                           │    ├─> Detect affected games
                           │    ├─> Add new games if needed
                           │    └─> Rescan affected games
                           ├─> _fetch_romm_data() for affected games only
                           └─> _push_local_to_romm() for affected games only
```

**Characteristics**:
- Reuses initial ROMM and local libraries (no full rebuild)
- Only fetches ROMM state for games with detected changes
- Syncs only affected games
- Lower API load and faster execution
- Debounces rapid changes (waits for filesystem quiet period)
- Thread-safe event buffering with concurrent sync prevention

**Key Difference**: Watch mode is **incremental and event-driven**, while periodic mode is **exhaustive and time-driven**.

## Multiple Sync Folders Implementation

The application supports syncing from multiple directory locations, which is implemented as `SAVE` and `STATE` separate syncing to align with typical retro handheld custom firmware setups. However, the search logic behind-the-scenes doesn't actually differentiate and will capture both to make it easy to define arbitrary directories to sync if needed. (This would require refactoring a lot of the user-facing inputs though.)

**LocalLibrary Processing** ([library_classes.py:284-308](library_classes.py#L284-L308)):
- Constructor accepts `sync_folders: List[Path]` (plural, generic)
- `build_local_library()` iterates through each sync folder
- Platform directories are discovered within each folder
- Files are aggregated into the same `self.games` dictionary
- No distinction between save and state sources - unified game catalog

**Naming Convention**:
- Config uses `SAVE_SYNC_DIR` / `STATE_SYNC_DIR` (specific, user-facing)
- Internal code uses `sync_folders` / `sync_dirs` (generic, extensible)

## Key Components
- `library_classes.py` - Data models and filesystem operations
- `sync_orchestrator.py` - Sync coordination and event handling
- `romm_api_func.py` - ROMM API call functions (using python `requests` library)
- `games_class.py` - Individual game class, containing a game's local and romm data (name, platform, save files, save states...)

## Supporting Libraries

**External Dependencies**:
- `watchdog` - Filesystem event monitoring (watch mode)
- `polars` - High-performance DataFrame library
- `loguru` - Structured logging with per-module filtering
- `requests` - HTTP client for ROMM API (via `romm_api_func`)
- `dotenv` - Environment variable management

## Generative AI usage disclosure
I used AI assistance in the following ways:
- quickly get me going with sample patterns for libraries I was unfamiliar with (mainly: `pytest.mock`, `watchdog`, `loguru`, and `requests`) before I did the implementation myself. 
- documentation, which I proofread and sanitized/edited as needed
- autocomplete
- help generate the platform mapping.

The code is 100% mine (for better or worse).