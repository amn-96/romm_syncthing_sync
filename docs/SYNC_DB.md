# History database

romm_sync keeps one SQLite file, `DATA_DIR/romm_sync.db` (`/data/romm_sync.db` in the container). `/data` must be a persistent volume: the history is also the baseline for change detection, and copies discarded when resolving a conflict go to `/data/backups` as `{UTC time}_{rom_id}_{md5[:8]}_{file name}` (identical content is stored once), outside the Syncthing folders so they are not propagated to every device. The DB works with read-only (`:ro`) sync mounts.

Code: `src/store.py` (`Store`). There are no migrations; the tables are created with `CREATE TABLE IF NOT EXISTS`.

An older `f/syncfromromm` database (tables `sync_log`, `file_state`) in the same file is ignored and can be dropped by deleting the file.

## Tables

### `sync_events` (history, pruned)

One row per push, pull, match, resolution, detected conflict and failed transfer. Nothing is written for files that compare as unchanged (a `match` row is written only when the stored baseline does not already show identical hashes).

| Column | Notes |
|---|---|
| `ts` | UTC ISO-8601. |
| `rom_id`, `kind`, `file_name`, `emulator` | The file key. `kind` is `save` or `state`; `emulator` is the core folder / RomM emulator, `''` for the platform folder. RomM keeps one save per `(file_name, emulator)`. |
| `action` | `push`, `pull` (also RomM-only downloads and Add to Local), `match` (both sides found identical), `resolve_local`, `resolve_romm`, `conflict` (written once when a file newly becomes a conflict; not a baseline). A `differs` file writes no event. |
| `local_hash` | md5 of the local file, directly comparable with RomM's `content_hash`. |
| `romm_hash`, `romm_updated_at` | RomM's copy. RomM hashes saves but not states, so a state's `romm_hash` is the md5 we recorded for the same `romm_updated_at` (baseline, then `library`), else one download. For saves it is RomM's `content_hash`. `romm_updated_at` is compared at whole seconds because upload responses carry microseconds but later GETs do not. |
| `device`, `backup_path` | Who changed it (Syncthing device name, `RomM`, `web UI`) and where the overwritten copy went. |
| `success`, `error` | `success = 0` rows are failed transfers. They show in the game history and are never a baseline. |

### Baseline

The baseline of a file is its newest row with `action` in `push, pull, match, resolve_local, resolve_romm` and `success = 1`. A file counts as changed locally when its md5 differs from the baseline's `local_hash`, and as changed in RomM when `romm_hash` differs (`romm_updated_at` for baselines recorded without a hash). Changed on both sides is a conflict and is held for the web UI. With no baseline, identical content records a `match` baseline; different content is `differs`: held like a conflict (never newer-wins) until the user keeps one side, which becomes the baseline.

### Pruning

Rows older than `LOG_DB_RETENTION_DAYS` (default 90) are deleted at startup and then every 24 hours, except the newest baseline row of every file, so pruning never resets change detection.

### Snapshot tables

- `library`: one row per tracked file (local and RomM side), rewritten after every sync pass. This is what the web UI lists. `status` is `synced`, `conflict`, `differs` (content differs and there is no baseline) or `blocked` (a RomM-only file that cannot be downloaded, with the reason in `note`).
- `sync_runs`: one row per sync pass (`trigger` is `full`, `watch` or `scheduled_pull`) with the checked and failed game counts.
- `kv`: orphaned history (ROMs no longer in RomM), local games left unsynced because their name matches several RomM games (`ambiguous`), local games with no matching RomM game (`unmatched`), and RomM library totals.

## Handy queries

```sql
SELECT ts, action, file_name, device, success, error FROM sync_events WHERE rom_id = 42 ORDER BY id DESC LIMIT 20;
SELECT ts, file_name, error FROM sync_events WHERE success = 0 ORDER BY id DESC;
SELECT * FROM library WHERE status != 'synced';
```
