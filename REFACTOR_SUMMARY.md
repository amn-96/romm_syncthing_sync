# SyncOrchestrator Refactor Summary

## Overview

The glue functions (`match_romm_saves_and_states` and `push_saves_and_states_to_romm`) have been consolidated into a dedicated `SyncOrchestrator` class. This eliminates hidden choreography, improves testability, and provides a clearer API for sync operations.

## Changes Made

### New File: `src/sync_orchestrator.py`

Consolidated orchestration logic into a single module containing:

#### `SyncResult` Dataclass
- Captures output of sync operations
- Tracks successful syncs, failures, unmatched games
- Provides a `summary()` method for logging

#### `SyncOrchestrator` Class
- **Constructor**: Takes `LocalLibrary`, `RetroGameServer`, and `RommUser`
- **Internal Methods**:
  - `_fetch_romm_state()`: Fetches ROMM saves/states for games
  - `_push_local_to_romm()`: Pushes local changes to ROMM, returns (successful, failed) tuple
  - `_validate_games()`: Separates matched and unmatched games

- **Public Methods**:
  - `full_sync()`: Complete sync (all games)
  - `watchdog_sync()`: Incremental sync (specific file changes)

#### `SyncManager` Class
- **Constructor**: Takes `RetroGameServer`, `LocalLibrary`, `RommUser`, plus optional `watchdog_delay_seconds` and `cache_filepath`
- Manages pending filesystem events and schedules sync execution
- Delegates to `SyncOrchestrator` for actual sync operations
- Thread-safe event queue with debounce timer

#### `FileChangeHandler` Class
- Watchdog event handler that intercepts filesystem events
- Adds events to `SyncManager` queue and reschedules sync timer
- Skips directory events, only processes files

### Modified File: `src/romm_sync.py`

Simplified to focus only on initialization and full_sync flow:

1. **Imports**: Now imports `SyncOrchestrator`, `SyncManager`, `FileChangeHandler` from `sync_orchestrator`

2. **Removed Classes**:
   - `SyncManager` (moved to `sync_orchestrator.py`)
   - `FileChangeHandler` (moved to `sync_orchestrator.py`)

3. **Kept Functions**:
   - `cleanup()`: Handles graceful shutdown
   - `get_local_library()`: Builds and initializes local library
   - `full_sync()`: Orchestrates complete sync (uses `SyncOrchestrator.full_sync()`)
   - `initialize_romm_sync()`: Entry point for container initialization

## Benefits

### 1. **Explicit Choreography**
Before:
```python
games_to_sync = self.lcl.extract_games_from_watchdog_events(...)
push_saves_and_states_to_romm(games_to_sync)      # What does this do?
match_romm_saves_and_states(games_to_sync)        # When should this run?
self.lcl.to_json(filepath=CACHE_FILEPATH)         # Why now?
```

After:
```python
result = self.orchestrator.watchdog_sync(event_paths, CACHE_FILEPATH)
```

### 2. **Better Error Handling**
- Orchestrator catches exceptions per-game and returns detailed failure info
- Caller can inspect `result.games_failed` to see which games errored
- Full sync doesn't abort if a single game fails

### 3. **Testability**
- Can mock `SyncOrchestrator` in tests of `SyncManager`
- Can test sync choreography independently of watchdog logic
- Can verify correct sequencing (fetch → push → cache)

### 4. **Extensibility**
- Easy to add new sync modes (e.g., `conflict_resolution_sync()`)
- Can add retry logic, rate limiting, or batching without modifying callers
- `SyncResult` provides consistent interface for all operations

### 5. **Consistency**
- Both full sync and watchdog sync follow identical ordering
- Same error handling approach across all sync modes
- Logging is consistent and informative

## Module Organization

```
src/
├── romm_sync.py                  # Main entry point, full_sync flow
├── sync_orchestrator.py          # Orchestration logic (SyncOrchestrator, SyncManager, FileChangeHandler)
├── library_classes.py            # Library data structures (LocalLibrary, RetroGameServer)
├── games_class.py                # Individual game data (Game, SaveFile, SaveState)
├── config.py                     # Configuration management
└── romm_api_func.py              # ROMM API interaction (RommUser, RommSaves, RommStates)
```

## Removed Code

The following module-level functions in `library_classes.py` can now be removed (no longer called):
- `match_romm_saves_and_states()`
- `push_saves_and_states_to_romm()`

These were simple wrappers—their logic is now integrated into `SyncOrchestrator` with better error handling.

## Migration Checklist

- [ ] Test `full_sync()` mode
- [ ] Test watchdog sync via `SyncManager`
- [ ] Verify cache file is created correctly
- [ ] Check error logs for 1+ game failures
- [ ] Remove old glue functions from `library_classes.py`
- [ ] Update any other callers of old functions (if any)
