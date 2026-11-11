# Upgrading to v2

v2 adds two things: **pulling from RomM** (web player saves, other devices, files that exist only in RomM) and a **web UI** for managing sync. v1 only pushed. This page covers what changes for an existing install and how to keep the old behavior.

RomM 5.x is required.

## What changes on upgrade

- **Two-way by default.** If the sync folders are writable, the app now also writes into them: it downloads RomM-side changes and RomM-only saves. If you mount them read-only (`:ro`) or set `PUSH_ONLY=true`, it keeps pushing only, as in v1. See [Sync strategy](SYNC_STRATEGY.md) for how decisions are made.
- **History database.** The app now keeps `romm_sync.db` in `/data`. It records what was pushed or pulled, which is how it tells which side changed. Mount `/data` on a persistent volume.
- **Web UI** on port 8080. It has no login, so keep it on your LAN. See the [walkthrough](WEBUI.md).
- **Nothing is overwritten without a baseline.** Files that differ between the two sides and have no history yet (every existing install starts that way) are not touched. They show up as "Differs" in the web UI until you keep one side. Identical files just get a baseline. Copies replaced when you resolve a conflict are backed up under `/data/backups`.

## Compose file

Use the new `docker-compose.yml`. The parts that matter on upgrade:

| Change | Why |
|---|---|
| `${DATA_DIR:-./romm_sync_data}:/data` | Persistent history and backups. Without a mount the database lives in the container and is lost when it is recreated, so every file starts again as "Differs". |
| `${WEBUI_PORT:-8080}` published | Web UI port. Set `WEBUI_PORT` if 8080 is taken on the host, or `docker compose up` fails on the port bind. |
| Sync folders mounted writable | The shipped file lets the app pull RomM changes into them. Add `:ro` to keep it push-only. |

An older compose file has writable sync mounts like the new one, but no `/data` mount. It upgrades into two-way mode with no persistent history, so add the `/data` mount first.

## New variables

All are optional. Defaults do not change how an existing install pushes.

| Variable | Default | Notes |
|---|---|---|
| `PUSH_ONLY` | `false` | `true` never writes to the sync folders. Also turns on by itself when a sync folder is read-only. |
| `PULL_INTERVAL_SECONDS` | `1800` | Watch mode only: how often RomM is checked for changes and RomM-only saves. Incremental syncs also pull, but only for the games whose files triggered them. In periodic mode the pull happens at the start of every full sync. |
| `LOG_DB_RETENTION_DAYS` | `90` | History kept. The newest record of every file is always kept. Must be positive. |
| `DATA_DIR` | `/data` | Where `romm_sync.db` and backups live. In the compose file this is the host folder behind the `/data` mount. |
| `WEBUI_PORT` | `8080` | Web UI port. |
| `ROMM_PUBLIC_URL` | `ROMM_URL` | The address your browser uses for the "Open RomM" link. |
| `SYNCTHING_URL`, `SYNCTHING_API_KEY` | empty in code | Shows which device changed a file (read-only Syncthing API access). `.env.example` ships with placeholder values; if Syncthing can't be reached the app logs a warning and shows "Syncthing" instead of device names. |

## Staying on push-only

Add `:ro` to the sync mounts, or set `PUSH_ONLY=true`. Nothing is written to the sync folders, and the web UI disables "Pull changes from RomM" and "Keep RomM copy".

## After upgrading

1. Open the web UI and check "Needs you" and the Differs list.
2. For each file, keep the local or the RomM copy. That becomes the baseline.
3. If you started push-only to look first, switch to two-way once you are happy: remove `:ro` (or `PUSH_ONLY=true`) and recreate the container.
