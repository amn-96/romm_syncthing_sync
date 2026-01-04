# ROMM Sync Usage Examples

## Running Locally (Development)

### 1. Using the Main Loop Directly

```bash
# Set environment variables
export ROMM_URL="http://localhost:8000"
export ROMM_USERNAME="admin"
export ROMM_PASSWORD="password"
export SYNC_FOLDER="$(pwd)/test_sync"
export SYNC_INTERVAL_SECONDS="30"
export LOG_LEVEL="DEBUG"

# Create test sync folder
mkdir -p test_sync/{NES,SNES,N64}
touch test_sync/NES/SuperMarioBros.srm
touch test_sync/SNES/Chrono\ Trigger.srm

# Run the main loop
python -c "
import asyncio
from src.config import Config
from src.main_loop import main

config = Config()
exit_code = asyncio.run(main(config, mode='periodic'))
"
```

### 2. Using the Original Main Function (Still Works)

```bash
# Your original test script continues to work
python -m src.romm_sync
```

### 3. Programmatic Usage in Your Own Code

```python
from src.main_loop import RommSyncScheduler
from src.config import Config
import asyncio

async def my_custom_sync():
    config = Config()
    scheduler = RommSyncScheduler(config)

    # Initialize once
    if not await scheduler.initialize():
        print("Failed to initialize")
        return 1

    # Do custom operations
    await scheduler._refresh_romm_library(force=True)
    await scheduler._scan_local_folder()
    await scheduler._match_local_to_romm()

    # Or run the periodic sync
    await scheduler.run_periodic_sync()

    return 0

if __name__ == "__main__":
    exit_code = asyncio.run(my_custom_sync())
```

## Docker Deployment Examples

### 1. Simple Docker Run

```bash
# Copy and configure environment
cp .env.example .env
# Edit .env with your ROMM server details

# Build image
docker build -f Dockerfile.example -t romm-sync:latest .

# Run
docker run -d \
  --name romm-sync \
  --env-file .env \
  -v /path/to/your/sync/folder:/data/sync \
  -v /path/to/romm/data:/romm_data \
  --restart unless-stopped \
  romm-sync:latest

# Watch logs
docker logs -f romm-sync

# Stop gracefully
docker stop romm-sync
```

### 2. Docker Compose Integration

**docker-compose.yml:**
```yaml
version: '3.8'

services:
  # Your existing ROMM service
  romm:
    image: rommapp/romm:latest
    container_name: rommapp
    environment:
      ROMM_DB_HOST: db
      ROMM_DB_USER: romm
      ROMM_DB_PASSWD: romm
    volumes:
      - romm_data:/romm_data
      - /mnt/games:/mnt/games
    ports:
      - "8000:8000"
    depends_on:
      - db
    restart: unless-stopped

  # New: ROMM Sync service
  romm-sync:
    build:
      context: ./romm_sync
      dockerfile: Dockerfile.example
    container_name: romm-sync
    env_file: ./romm_sync/.env
    environment:
      # Override for Docker Compose network
      ROMM_URL: http://romm:8000
    volumes:
      - ./sync_folder:/data/sync
      - romm_data:/romm_data
    depends_on:
      - romm
    restart: unless-stopped
    logging:
      driver: "json-file"
      options:
        max-size: "100m"
        max-file: "3"

  # Your other services...
  db:
    image: postgres:15
    # ... configuration

volumes:
  romm_data:
```

**Start everything:**
```bash
docker-compose up -d

# Check logs
docker-compose logs -f romm-sync

# Stop gracefully
docker-compose down
```

### 3. Multi-Machine Setup (Syncthing + ROMM Sync)

If you're using Syncthing to sync saves between machines:

```yaml
services:
  # Syncthing for keeping local saves in sync
  syncthing:
    image: syncthing/syncthing:latest
    container_name: syncthing
    environment:
      PUID: 1000
      PGID: 1000
    volumes:
      - syncthing_config:/var/syncthing
      - ./sync_folder:/mnt/sync
    ports:
      - "8384:8384"

  # ROMM Sync watches the synced folder
  romm-sync:
    build:
      context: ./romm_sync
      dockerfile: Dockerfile.example
    container_name: romm-sync
    env_file: ./romm_sync/.env
    environment:
      ROMM_URL: http://romm:8000
      SYNC_FOLDER: /mnt/sync
      SYNC_INTERVAL_SECONDS: 60  # Check every minute
    volumes:
      - ./sync_folder:/mnt/sync
      - romm_data:/romm_data
    depends_on:
      - syncthing
      - romm
    restart: unless-stopped

volumes:
  syncthing_config:
  romm_data:
```

## Configuration Examples

### 1. High-Performance Setup (Many Games)

**.env:**
```env
ROMM_URL=http://romm:8000
ROMM_USERNAME=admin
ROMM_PASSWORD=password

# Cache library for 24 hours to reduce API calls
ROMM_LIBRARY_TTL_HOURS=24

# Sync less frequently (every 30 minutes)
SYNC_INTERVAL_SECONDS=1800

# Use larger batch size for API
ROMM_API_LIMIT=100

# Keep verbose logging to monitor
LOG_LEVEL=INFO

SYNC_FOLDER=/data/sync
CACHE_FILE=/data/cache/local_games.json
```

### 2. Rapid Sync Setup (Game Development / Testing)

**.env:**
```env
ROMM_URL=http://localhost:8000
ROMM_USERNAME=dev
ROMM_PASSWORD=dev

# Fresh library every sync
ROMM_LIBRARY_TTL_HOURS=0

# Check very frequently
SYNC_INTERVAL_SECONDS=10

# Small API batches
ROMM_API_LIMIT=10

# Debug logging
LOG_LEVEL=DEBUG

SYNC_FOLDER=/data/sync
```

### 3. Minimal Resource Usage Setup (Arm/Low-Power Device)

**.env:**
```env
ROMM_URL=http://romm:8000
ROMM_USERNAME=admin
ROMM_PASSWORD=password

# Very long cache (1 day)
ROMM_LIBRARY_TTL_HOURS=24

# Sync once per hour
SYNC_INTERVAL_SECONDS=3600

# Small API batches to reduce memory
ROMM_API_LIMIT=25

# Only show errors
LOG_LEVEL=WARNING

SYNC_FOLDER=/data/sync
```

## Monitoring and Observability

### 1. Log Analysis Script

```bash
#!/bin/bash
# analyze_logs.sh - Quick sync analysis

echo "=== Last 10 Sync Operations ==="
docker logs romm-sync | grep "Sync operation complete" | tail -10

echo -e "\n=== Total Games Synced ==="
docker logs romm-sync | grep "Sync operation complete" | grep -oP 'Games: \K\d+' | paste -sd+ | bc

echo -e "\n=== Error Count ==="
docker logs romm-sync | grep -c "ERROR"

echo -e "\n=== Last Error ==="
docker logs romm-sync | grep "ERROR" | tail -1

echo -e "\n=== Container Status ==="
docker inspect romm-sync --format='{{.State.Status}}' ({{.State.ExitCode}})
```

### 2. Watch Sync in Real-Time

```bash
# Terminal 1: Watch logs with timestamps
watch -n 1 'docker logs --tail 20 --timestamps romm-sync'

# Terminal 2: Monitor container resource usage
docker stats romm-sync

# Terminal 3: Check sync folder
watch -n 5 'find /path/to/sync/folder -type f | wc -l'
```

### 3. Health Check Script

```bash
#!/bin/bash
# health_check.sh - Verify romm-sync is running properly

CONTAINER="romm-sync"

# Check if container is running
if ! docker ps --filter "name=$CONTAINER" --format="{{.Names}}" | grep -q $CONTAINER; then
    echo "ERROR: Container $CONTAINER is not running"
    exit 1
fi

# Check if syncing (recent log entries)
RECENT_LOGS=$(docker logs --since 5m $CONTAINER)

if echo "$RECENT_LOGS" | grep -q "Sync operation complete"; then
    echo "OK: Recent syncs detected"
else
    echo "WARNING: No recent syncs"
fi

# Check for errors
ERROR_COUNT=$(echo "$RECENT_LOGS" | grep -c "ERROR")
if [ $ERROR_COUNT -gt 0 ]; then
    echo "WARNING: $ERROR_COUNT errors detected"
    echo "$RECENT_LOGS" | grep "ERROR" | tail -1
fi

echo "LAST SYNC: $(docker logs $CONTAINER | grep 'Sync operation complete' | tail -1 | cut -d' ' -f1-2)"
```

## Troubleshooting Examples

### Issue: Container Exits Immediately

```bash
# Check last error
docker logs romm-sync 2>&1 | tail -20

# Test configuration manually
docker run --rm \
  --env-file .env \
  -v /data/sync:/data/sync \
  romm-sync:latest \
  python -c "from src.config import Config; Config().validate()"

# Verify environment variables
docker inspect romm-sync --format='{{json .Config.Env}}' | jq .
```

### Issue: Syncs Are Very Slow

```bash
# Check log level and enable debug
docker run -it \
  --env-file .env \
  -e LOG_LEVEL=DEBUG \
  -v /data/sync:/data/sync \
  romm-sync:latest

# Look for:
# - "Failed to stop ROMM container" - Docker issue
# - "Offset: X, Limit: Y" with many iterations - API pagination
# - Large file copy times - Disk I/O bottleneck
```

### Issue: High Memory Usage

```bash
# Monitor memory over time
watch -n 1 'docker stats --no-stream romm-sync | tail -1'

# Try reducing API batch size
docker run -d \
  --env-file .env \
  -e ROMM_API_LIMIT=25 \
  romm-sync:latest
```

### Issue: ROMM Container Won't Stop

```bash
# Check if stop is hanging
timeout 10 docker logs -f romm-sync | grep "stop"

# Manual stop
docker exec romm stop rommapp
docker wait rommapp

# Then restart romm-sync
docker restart romm-sync
```

## Integration with Existing Services

### With Home Assistant

If you have Home Assistant and want notifications on sync failures:

```yaml
# In your Home Assistant automation
automation:
  - alias: "ROMM Sync Failed"
    trigger:
      platform: state
      entity_id: sensor.romm_sync_status
      to: "failed"
    action:
      service: notify.mobile_app
      data:
        message: "ROMM sync failed - check logs"
```

Then in romm_sync, you could write status to a file that HA reads via REST.

### With Homelab Monitoring Stack

If you use Prometheus + Grafana:

```yaml
# Future enhancement: export metrics
# FROM: src/main_loop.py SyncStatistics

services:
  prometheus:
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml

  grafana:
    ports:
      - "3000:3000"
```

## Cleanup and Maintenance

### Remove Old Cache Files

```bash
# Clean cache older than 7 days
find /path/to/cache -name "*.json" -mtime +7 -delete
```

### Backup ROMM Before Sync

```bash
# Add to cron or systemd timer
#!/bin/bash
BACKUP_DIR="/backups/romm_$(date +%Y%m%d_%H%M%S)"
cp -r /romm_data "$BACKUP_DIR"
docker restart romm-sync
```

### Monitor Disk Usage

```bash
# Check sync folder growth
watch -n 60 'du -sh /path/to/sync/folder'

# Archive old saves
tar -czf "old_saves_$(date +%Y%m).tar.gz" /path/to/sync/folder
```

---

For more details, see:
- `DOCKER_USAGE.md` - Full Docker deployment guide
- `IMPLEMENTATION_SUMMARY.md` - Architecture and design
- `.env.example` - All configuration options
- `src/main_loop.py` - Source code with inline documentation
