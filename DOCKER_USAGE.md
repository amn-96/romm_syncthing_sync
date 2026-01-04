# ROMM Sync Docker Usage Guide

This guide explains how to use the new Docker-friendly main event loop (`main_loop.py`) for continuous synchronization.

## Overview

The `main_loop.py` module provides:
- **Graceful shutdown handling** - Properly responds to SIGTERM/SIGINT signals (important for Docker containers)
- **Periodic sync mode** - Runs sync on a fixed schedule (default: every 5 minutes)
- **Watch mode** - Monitors local filesystem for changes (TODO: Not yet implemented)
- **Statistics tracking** - Records sync history and error metrics
- **Async/await architecture** - Non-blocking operations, ready for parallel tasks

## Quick Start

### 1. Configure Environment Variables

Copy the example configuration file:
```bash
cp .env.example .env
```

Edit `.env` with your ROMM server details:
```env
ROMM_URL=http://romm-app:8000
ROMM_USERNAME=your_username
ROMM_PASSWORD=your_password
SYNC_FOLDER=/data/sync
SYNC_INTERVAL_SECONDS=300
SYNC_MODE=periodic
```

See `.env.example` for detailed descriptions of all options.

### 2. Run in Docker

#### Option A: Docker Run (Standalone)

```bash
docker run -it \
  --env-file .env \
  -v /path/to/sync/folder:/data/sync \
  -v /path/to/romm/data:/romm_data \
  romm-sync:latest
```

#### Option B: Docker Compose

Add to your `docker-compose.yml`:

```yaml
services:
  romm-sync:
    image: romm-sync:latest
    container_name: romm-sync
    env_file: .env
    volumes:
      - ./sync_folder:/data/sync
      - romm_data:/romm_data
    restart: unless-stopped
    # Important: Don't set stdin_open or tty for prod
    stdin_open: true
    tty: true

volumes:
  romm_data:
    external: true  # or define locally
```

Then run:
```bash
docker-compose up -d romm-sync
```

### 3. Monitor Logs

```bash
# Real-time logs
docker logs -f romm-sync

# Last 100 lines
docker logs --tail 100 romm-sync

# With timestamps
docker logs -f --timestamps romm-sync
```

## Configuration Details

### Sync Modes

#### Periodic Mode (Default)
Runs sync every `SYNC_INTERVAL_SECONDS`:
1. Refresh ROMM library (if cache stale)
2. Scan local sync folder
3. Match games to ROMM library
4. Sync matched games

Best for: Regular, predictable sync schedules. Reduces API load with caching.

```env
SYNC_MODE=periodic
SYNC_INTERVAL_SECONDS=300  # 5 minutes
ROMM_LIBRARY_TTL_HOURS=1   # Cache library for 1 hour
```

#### Watch Mode (TODO)
Real-time monitoring of local filesystem changes:
- Triggers sync immediately when files are modified
- Parallel ROMM library refresh (separate from file-watching)
- Debouncing to coalesce rapid changes

Not yet implemented. Will require `watchdog` library.

### Key Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROMM_URL` | Required | ROMM server URL (e.g., `http://romm-app:8000`) |
| `ROMM_USERNAME` | Required | ROMM login username |
| `ROMM_PASSWORD` | Required | ROMM login password |
| `SYNC_FOLDER` | `/data/sync` | Local folder to sync (mounted in Docker) |
| `SYNC_INTERVAL_SECONDS` | `300` | Seconds between sync cycles |
| `SYNC_MODE` | `periodic` | `periodic` or `watch` |
| `ROMM_LIBRARY_TTL_HOURS` | `1` | How long to cache ROMM library |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `DRY_RUN` | `false` | Log-only mode (not fully implemented) |

## How It Works

### Startup Sequence
1. Parse environment variables into `Config` object
2. Create `RommSyncScheduler` with signal handlers
3. **Initialize**:
   - Validate configuration
   - Connect to ROMM
   - Load ROMM library
   - Scan local sync folder
4. Start main sync loop (periodic or watch mode)

### Periodic Sync Loop (Simplified)
```
while running:
  - Refresh ROMM library (cached, only if stale)
  - Scan local folder for games
  - Match local games to ROMM library
  - Sync matched games to ROMM
  - Sleep for SYNC_INTERVAL_SECONDS
```

### Graceful Shutdown
When Docker sends SIGTERM (normal shutdown):
1. Signal handler sets `_running = False`
2. Scheduler exits loop gracefully
3. Cleanup happens in `shutdown()` method
4. Exit code 0

This gives the app time to finish current operations (~5 seconds typically).

## Log Output Examples

### Successful Startup
```
2025-01-03 10:15:23 - root - INFO - Initializing ROMM Sync Scheduler...
2025-01-03 10:15:23 - root - DEBUG - Configuration validated
2025-01-03 10:15:23 - root - INFO - Connecting to ROMM at http://romm-app:8000
2025-01-03 10:15:25 - root - INFO - Library refreshed: RetroGameServer(games=245, size_gb=142.34, platforms=8)
2025-01-03 10:15:25 - root - INFO - Found 32 games in local folder
2025-01-03 10:15:25 - root - INFO - Matching complete: 28 matched, 4 unmatched
2025-01-03 10:15:25 - root - INFO - Scheduler initialized successfully
2025-01-03 10:15:25 - root - INFO - Starting ROMM Sync in PERIODIC mode
```

### Periodic Sync in Progress
```
2025-01-03 10:16:25 - root - INFO - Refreshing ROMM library from server...
2025-01-03 10:16:27 - root - INFO - Library refreshed: RetroGameServer(games=245, size_gb=142.34, platforms=8)
2025-01-03 10:16:28 - root - INFO - Starting sync operation...
2025-01-03 10:16:35 - root - INFO - Sync operation complete: Syncs: 12 | Games: 28 | Files: 45 | Last sync: 2025-01-03 10:16:35
2025-01-03 10:16:35 - root - DEBUG - Next sync in 300 seconds
```

### Graceful Shutdown
```
2025-01-03 10:20:00 - root - INFO - Received signal 15, initiating graceful shutdown...
2025-01-03 10:20:00 - root - INFO - Shutting down scheduler...
2025-01-03 10:20:00 - root - INFO - Final statistics: Syncs: 15 | Games: 28 | Files: 52 | Last sync: 2025-01-03 10:19:35
2025-01-03 10:20:00 - root - INFO - Scheduler shutdown complete
```

## Troubleshooting

### Container exits immediately
- Check logs: `docker logs romm-sync`
- Verify environment variables: `docker inspect romm-sync --format='{{json .Config.Env}}'`
- Common issues:
  - Missing `ROMM_URL`, `ROMM_USERNAME`, or `ROMM_PASSWORD`
  - Incorrect `SYNC_FOLDER` path (doesn't exist or no permissions)
  - Cannot connect to ROMM server

### Sync hangs or takes too long
- Increase `LOG_LEVEL=DEBUG` to see where it's stuck
- Check ROMM library size: if library is huge, first sync will take time
- Reduce `ROMM_API_LIMIT` if you hit rate limits
- Increase `SYNC_INTERVAL_SECONDS` if resources are constrained

### High memory usage
- Reduce `ROMM_API_LIMIT` (default 50, lower = less memory per request)
- Set `ROMM_LIBRARY_TTL_HOURS=6` to cache library longer (reduce refreshes)
- With large ROMM libraries, memory can be significant

### "Permission denied" errors
Ensure Docker volumes have correct permissions:
```bash
# Make sure host directories are readable/writable
chmod 755 /path/to/sync/folder
chmod 755 /path/to/romm/data
```

## Integration with Docker Compose

When running alongside ROMM and other services:

```yaml
services:
  romm:
    image: rommapp/romm:latest
    volumes:
      - romm_data:/romm_data
    ports:
      - "8000:8000"

  romm-sync:
    image: romm-sync:latest
    depends_on:
      - romm
    env_file: .env
    environment:
      ROMM_URL: http://romm:8000  # Use service name in Docker network
    volumes:
      - ./sync_folder:/data/sync
      - romm_data:/romm_data
    restart: unless-stopped
```

Key point: Use `http://romm:8000` (service name) instead of `http://localhost:8000` when running in Docker Compose.

## Future Enhancements

These features are marked with `FUTURE:` or `TODO:` comments in the code:

1. **LibraryCache** - Persistent caching of ROMM library to JSON
2. **LocalStateTracker** - Track file mtimes to skip unchanged files
3. **Watch mode** - Real-time file monitoring with watchdog library
4. **SyncStrategy pattern** - Only sync changed games, not all matched games
5. **Remote change detection** - Detect when ROMM side changes
6. **Metrics export** - Prometheus/StatsD integration for monitoring

See comments in `src/main_loop.py` and `src/sync_operations.py` for details.

## Running Tests

```bash
# Build test image
docker build -f Dockerfile.test -t romm-sync:test .

# Run tests
docker run --rm romm-sync:test pytest tests/

# With coverage
docker run --rm romm-sync:test pytest --cov=src tests/
```

## Next Steps

1. Set up `.env` file with your ROMM credentials
2. Test locally first: `python -m src.main_loop`
3. Build Docker image (when ready)
4. Deploy to Docker or Docker Compose
5. Monitor logs and adjust `SYNC_INTERVAL_SECONDS` as needed

---

For more details, see `.env.example` and comments in `src/main_loop.py`.
