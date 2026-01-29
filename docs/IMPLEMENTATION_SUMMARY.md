*(This document was initially AI-generated, then proofread and corrected by me)*
# romm_sync Architecture & Implementation Summary

## Overview

romm_sync is a Python daemon that synchronizes RetroArch save files and save states between local storage and a ROMM (Retro ROM Manager) server. It supports two sync modes: **periodic** (runs full sync at intervals) and **watch** (monitors filesystem for changes and syncs incrementally).

## Docker Environment

The application runs in a Docker container with the following configuration:

**Environment Variables** (`.env.example`):
- ROMM API credentials and configuration
- Sync mode selection (periodic vs watch)
- Sync directory(ies) configuration.
- Timing parameters (sync interval, watchdog delay)
- Platform verification settings
- Logging configuration

**Volume Mounts** (`docker-compose.yml`):
The container mount path is hard-coded and it is expected that the user define their host mounts via `.env.example`. See the [relevant section in the README](../README.md###sync-configuration)
- `/syncdata/saves` - Bind mount for save files (`.srm`, `.sav`, etc.)
- `/syncdata/states` - Bind mount for state files (`.state`, `.state0`, `.state.png`, etc.)
- `/config/platform_mapping.yaml` - Bind mount for the platform map file 

**Alternative Configuration**: Use `/syncdata` as a single mount point when saves and states are saved alongside each other in the same parent directory.

## Code Flow Architecture

### Entry Point: romm_sync.py

The application starts in [romm_sync.py](../romm_sync.py) and follows this initialization sequence:

1. **Load environment variables** - Uses `dotenv` to load `.env` configuration
2. **Initialize sync system** - Calls `initialize_romm_sync()`
3. **Route to sync mode** - Dispatches to either `run_periodic_sync()` or `run_watch_sync()` based on `SYNC_MODE`

```
main()  [romm_sync.py:96]
  └─> initialize_romm_sync()  [romm_sync.py:18]
       ├─> get_config()  [config.py:122 - Validate environment, create Config singleton]
       ├─> SyncOrchestrator.build_libraries()  [sync_orchestrator.py:56]
       │    ├─> RetroGameServer.build_romm_library()  [library_classes.py:92]
       │    ├─> LocalLibrary(sync_dirs)  [library_classes.py:167]
       │    ├─> LocalLibrary.build_local_library()  [library_classes.py:338]
       │    └─> LocalLibrary.match_to_romm()  [library_classes.py:268]
       └─> Return (app_cfg, srv, lcl)

  └─> run_periodic_sync(srv, lcl) OR run_watch_sync(srv, lcl)
```

### Configuration Layer: config.py

**Config class** manages all application settings:
- Parses env variables (see [.env.example](../.env.example)) and validates configuration
- Sets up logging with log interception with Loguru via `LoggingConfig` with per-library filtering
- Creates singleton instance for access without passthrough (`get_config()`)

**Important**: Enforces that either (env variables) `(SAVE_SYNC_FOLDER + STATE_SYNC_FOLDER)` OR `ALL_SYNC_FOLDER` is specified, but not both. The file matching is written such that it will pick up saves and states both in whatever folder it's searching within, so this separation is arbitrary. Could potentially refactor to allow an arbitrary list of directories, if needed.

### Initialization: romm_sync.py

**`initialize_romm_sync()`** 

Loads the configuration (docker env variables), builds the initial library state (`SyncOrchestrator.build_libraries()`), and passes on to the sync functionsl

- **`SyncOrchestrator.build_libraries(app_cfg)`** ([sync_orchestrator.py:56](../src/sync_orchestrator.py#L56)) performs initial library construction:

  1. **Initialize ROMM library** - `RetroGameServer.build_romm_library()` fetches full game library from ROMM API
  2. **Build local library** - `LocalLibrary(sync_dirs)` scans filesystem for saves/states
  3. **Match games** - Links local files to ROMM entries via platform and filename matching

### Library Management: library_classes.py

**RetroGameServer** - Represents ROMM's game library:
- `build_romm_library()` fetches full game catalog via `RommUser.get_full_library()` API call
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
  - Rescans all detected games to pick up any file changes

### Sync Orchestration: sync_orchestrator.py

**SyncOrchestrator** ([sync_orchestrator.py:45](../src/sync_orchestrator.py#L45)) - Coordinates synchronization logic:
- `full_sync()` ([sync_orchestrator.py:128](../src/sync_orchestrator.py#L128)) - Complete sync of entire library
  1. Rebuilds fresh ROMM and local libraries
  2. Validates matched vs unmatched games
  3. Fetches current ROMM state for all games (`_fetch_romm_data()`)
  4. Pushes local saves/states to ROMM (`_push_local_to_romm()`)
  5. Returns `SyncResult` with success/failure details

- `watchdog_sync()` ([sync_orchestrator.py:190](../src/sync_orchestrator.py#L190)) - Incremental sync for file change events
  1. Refreshes ROMM library to catch any newly added server-side games.
  2. Extracts affected games from event paths via `LocalLibrary.extract_games_from_watchdog_events()`
  3. Rescans those games for updated files
  4. Fetches ROMM state for the detected games only
  5. Pushes local changes to ROMM

**WatchdogSyncManager** ([sync_orchestrator.py:257](../src/sync_orchestrator.py#L257)) - Manages watchdog event lifecycle:
- Buffers filesystem events in `pending_events` queue
- Implements debounce timer (configurable via `WATCHDOG_DELAY_SECONDS`, default 60s)
- Prevents concurrent syncs via `is_syncing` flag
- `schedule_sync()` - Resets timer on new events
- `execute_sync()` - Drains event queue and triggers `SyncOrchestrator.watchdog_sync()`
- Thread-safe via `event_lock` and `timer_lock`

**PeriodicSyncManager** ([sync_orchestrator.py:335](../src/sync_orchestrator.py#L335)) - Manages periodic sync:
- Runs full sync at intervals (configurable via `SYNC_INTERVAL_SECONDS`, default 1800s)
- Uses `threading.Event` for graceful shutdown
- Blocks using `stop_event.wait()` for each sync cycle

**FileChangeHandler** ([sync_orchestrator.py:365](../src/sync_orchestrator.py#L365)) - inherits from Watchdog's `FileSystemEventHandler` according to their docs:
- Listens for `on_modified`, `on_created`, `on_deleted`, `on_moved` events
- Filters metadata files before queueing (uses `METADATA_FILE_FILTERS`)
- Passes valid events to `WatchdogSyncManager.add_event()`
- Triggers `WatchdogSyncManager.schedule_sync()` to start/reset debounce timer

**AppExit** ([romm_sync.py:28](../romm_sync.py#L28)) - Signal handler for graceful shutdown:
- Factory methods for periodic and watchdog modes -- tries to gracefully shutdown on any interrupt.

## Sync Mode Comparison

### Full Sync (Periodic Mode)

**Flow**:
```
run_periodic_sync()  [romm_sync.py:52]
  └─> PeriodicSyncManager.run()  [sync_orchestrator.py:343]
       └─> Loop every SYNC_INTERVAL_SECONDS
            └─> SyncOrchestrator.full_sync()  [sync_orchestrator.py:128]
                 ├─> SyncOrchestrator.build_libraries()  [Fresh ROMM & local rebuild]
                 ├─> _validate_games()  [Separate matched/unmatched]
                 ├─> _fetch_romm_data() for all matched games
                 └─> _push_local_to_romm() for all games
```

**Characteristics**:
- Rebuilds ROMM and local libraries from scratch each cycle
- Fetches ROMM state for every matched game via API
- Syncs all games regardless of changes
- Higher API load and longer execution time
- Ideal for environments with infrequent changes or unreliable filesystem monitoring

### Watchdog Sync (Watch Mode)

**Flow**:
```
run_watch_sync()  [romm_sync.py:61]
  ├─> Create WatchdogSyncManager(srv, lcl)  [sync_orchestrator.py:257]
  ├─> Create FileChangeHandler(sync_manager)  [sync_orchestrator.py:365]
  ├─> Create Observer()  [watchdog library]
  └─> observer.schedule() for each sync_folder
       │
       [Filesystem event occurs]
       │
       └─> FileChangeHandler._handle_event()  [sync_orchestrator.py:371]
            ├─> Filter metadata files (METADATA_FILE_FILTERS)
            ├─> WatchdogSyncManager.add_event()  [sync_orchestrator.py:291]
            └─> WatchdogSyncManager.schedule_sync()  [sync_orchestrator.py:271]
                 │
                 [Wait WATCHDOG_DELAY_SECONDS - debounce timer]
                 │
                 └─> WatchdogSyncManager.execute_sync()  [sync_orchestrator.py:296]
                      └─> SyncOrchestrator.watchdog_sync()  [sync_orchestrator.py:190]
                           ├─> RetroGameServer.build_romm_library()  [Refresh ROMM state]
                           ├─> LocalLibrary.extract_games_from_watchdog_events()  [library_classes.py:450]
                           │    ├─> _detect_game_from_watchdog_event()  [Extract game names]
                           │    ├─> _add_new_game_from_watchdog_event()  [Add new games if needed]
                           │    └─> _rescan_local_saves_and_states()  [Rescan affected games]
                           ├─> _fetch_romm_data() for affected games only
                           └─> _push_local_to_romm() for affected games only
```

**Characteristics**:
- Reuses initial local library (only rescans affected games)
- Refreshes ROMM library on each sync (catches server-side additions)
- Only fetches ROMM state for games with detected changes
- Syncs only affected games
- Lower API load and faster execution than periodic mode
- Debounces rapid changes (waits for filesystem quiet period)
- Thread-safe event buffering with concurrent sync prevention
- Automatically detects and adds new games to library

**Key Difference**: Watch mode is **incremental**, while periodic mode is **a full refresh each time**. I'd probably use watchdog mode unless there's something specific. I developed periodic mode first with a very simple while() loop implementation and ported it over to the more sophisticated version here while working on the incremental sync function.

## Multiple Sync Folders Implementation

The application supports syncing from multiple directory locations, implemented as `SAVE` and `STATE` separate directories to align with typical retro handheld custom firmware setups.

**Configuration** ([config.py:34-58](../src/config.py#L34-L58)):
- Supports two patterns: `(SAVE_SYNC_DIR + STATE_SYNC_DIR)` XOR `ALL_SYNC_DIR`
- Config validation enforces mutual exclusivity of save+state or combined folder sync.
- Paths converted to `List[Path]` for internal use

**LocalLibrary Processing** ([library_classes.py:167](../src/library_classes.py#L167)):
- Constructor accepts `sync_folders: List[Path]` (plural, generic)
- `build_local_library()` ([library_classes.py:338](../src/library_classes.py#L338)) iterates through each sync folder
- Platform directories are discovered within each folder
- Files are aggregated into the same `self.games` dictionary
- No distinction between save and state sources - unified game catalog

**Naming Convention**:
- Config uses `SAVE_SYNC_DIR` / `STATE_SYNC_DIR` (specific, user-facing)
- Internal code uses `sync_folders` / `sync_dirs` (generic, extensible)

## Key Components
- [romm_sync.py](../romm_sync.py) - Application entrypoint, signal handling, and mode routing
- [config.py](../src/config.py) - Environment configuration, validation, and logging setup
- [library_classes.py](../src/library_classes.py) - RetroGameServer and LocalLibrary data models, filesystem scanning, platform mapping
- [sync_orchestrator.py](../src/sync_orchestrator.py) - Sync coordination, manager classes (Periodic/Watchdog), event handling
- [romm_api_func.py](../src/romm_api_func.py) - ROMM API wrapper using `requests` library (RommUser class)
- [games_class.py](../src/games_class.py) - Game class containing local and ROMM data (saves, states, metadata)

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

The architecture is mine (for better or worse).