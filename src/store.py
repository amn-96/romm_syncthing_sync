"""SQLite persistence: the tracked-file snapshot (`library`), the history (`sync_events`, pruned by age) and sync runs."""
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from .games_class import Game, SaveFile, SaveState, local_for, paired_romm
from .two_way_sync import file_md5, iso

if TYPE_CHECKING:
    from .two_way_sync import TwoWaySync

KEY = ("rom_id", "kind", "file_name", "emulator")
LIBRARY_COLUMNS = (*KEY, "platform_slug", "platform_name", "platform_id", "local_platform", "game_name", "cover_path",
                   "local_path", "note", "local_hash", "local_mtime", "local_size", "local_device",
                   "romm_file_id", "romm_hash", "romm_updated_at", "romm_size", "status")
EVENT_COLUMNS = (*KEY, "action", "local_hash", "local_mtime", "romm_id", "romm_hash", "romm_updated_at", "device", "backup_path", "success", "error")

INTEGER_COLUMNS = {"rom_id", "platform_id", "local_size", "romm_file_id", "romm_size"}
BASELINE_ACTIONS = "'push', 'pull', 'match', 'resolve_local', 'resolve_romm'"
UPSERT_LIBRARY_ROW = f"INSERT OR REPLACE INTO library ({', '.join(LIBRARY_COLUMNS)}) VALUES ({', '.join('?' * len(LIBRARY_COLUMNS))})"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS library (
    {", ".join(f"{c} {'INTEGER' if c in INTEGER_COLUMNS else 'TEXT'}" for c in LIBRARY_COLUMNS)},
    PRIMARY KEY (rom_id, kind, file_name, emulator)
);
CREATE TABLE IF NOT EXISTS sync_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
    rom_id INTEGER NOT NULL, kind TEXT NOT NULL, file_name TEXT NOT NULL, emulator TEXT NOT NULL,
    action TEXT NOT NULL, local_hash TEXT, local_mtime TEXT, romm_id INTEGER, romm_hash TEXT, romm_updated_at TEXT,
    device TEXT, backup_path TEXT, success INTEGER NOT NULL DEFAULT 1, error TEXT
);
CREATE INDEX IF NOT EXISTS sync_events_key ON sync_events (rom_id, kind, file_name, emulator, id);
CREATE TABLE IF NOT EXISTS sync_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT,
                                      trigger TEXT, checked INTEGER, errors INTEGER);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()  # one connection is shared by the sync thread and the web threads
        self._db.executescript(SCHEMA)

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._db.execute(sql, params)]

    def _write(self, sql: str, params: tuple = ()) -> None:
        with self._lock, self._db:
            self._db.execute(sql, params)

    def replace_library(self, rows: list[dict], rom_ids: set[int] | None = None) -> None:
        """Replace every row, or only the rows of `rom_ids` (partial rebuilds after a watchdog sync)."""
        with self._lock, self._db:
            if rom_ids is None:
                self._db.execute("DELETE FROM library")
            else:
                self._db.executemany("DELETE FROM library WHERE rom_id = ?", [(rom_id,) for rom_id in rom_ids])
            self._db.executemany(UPSERT_LIBRARY_ROW, [tuple(row[c] for c in LIBRARY_COLUMNS) for row in rows])

    def library(self) -> list[dict]:
        return self._query("SELECT * FROM library ORDER BY platform_name, game_name, file_name")

    def library_row(self, rom_id: int, kind: str, file_name: str, emulator: str) -> dict | None:
        rows = self._query("SELECT * FROM library WHERE rom_id=? AND kind=? AND file_name=? AND emulator=?", (rom_id, kind, file_name, emulator))
        return rows[0] if rows else None

    def update_library_row(self, row: dict) -> None:
        self._write(UPSERT_LIBRARY_ROW, tuple(row[c] for c in LIBRARY_COLUMNS))

    def add_event(self, action: str, **fields) -> None:
        columns = ["ts", "action", *(c for c in EVENT_COLUMNS if c in fields)]
        values = [now_iso(), action, *(fields[c] for c in columns[2:])]
        self._write(f"INSERT INTO sync_events ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})", tuple(values))

    def baseline(self, rom_id: int, kind: str, file_name: str, emulator: str) -> dict | None:
        rows = self._query("SELECT * FROM sync_events WHERE rom_id=? AND kind=? AND file_name=? AND emulator=? "
                           f"AND action IN ({BASELINE_ACTIONS}) AND success=1 ORDER BY id DESC LIMIT 1",
                           (rom_id, kind, file_name, emulator))
        return rows[0] if rows else None

    def last_event(self, rom_id: int, kind: str, file_name: str, emulator: str) -> dict | None:
        rows = self._query("SELECT * FROM sync_events WHERE rom_id=? AND kind=? AND file_name=? AND emulator=? ORDER BY id DESC LIMIT 1",
                           (rom_id, kind, file_name, emulator))
        return rows[0] if rows else None

    def history(self, rom_id: int) -> list[dict]:
        return self._query("SELECT * FROM sync_events WHERE rom_id=? ORDER BY id DESC", (rom_id,))

    def events_since(self, ts: str) -> list[dict]:
        return self._query("SELECT * FROM sync_events WHERE ts >= ? AND action IN ('push', 'pull') AND success=1 ORDER BY id", (ts,))

    def prune(self, retention_days: int) -> int:
        """Delete events older than `retention_days`, except each file's newest baseline row, which change detection needs."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self._lock, self._db:
            deleted = self._db.execute(
                "DELETE FROM sync_events WHERE ts < ? AND id NOT IN (SELECT MAX(id) FROM sync_events "
                f"WHERE action IN ({BASELINE_ACTIONS}) AND success=1 GROUP BY rom_id, kind, file_name, emulator)", (cutoff,)).rowcount
        if deleted:
            logger.info(f"Pruned {deleted} history row(s) older than {retention_days} days")
        return deleted

    def orphan_events(self, romm_rom_ids: set[int]) -> list[dict]:
        """Newest event per file for every ROM that no longer exists in RomM."""
        rows = self._query("SELECT rom_id, kind, file_name, emulator, MAX(ts) AS last_ts FROM sync_events GROUP BY rom_id, kind, file_name, emulator")
        return [row for row in rows if row["rom_id"] not in romm_rom_ids]

    def start_run(self, trigger: str) -> int:
        with self._lock, self._db:
            return self._db.execute("INSERT INTO sync_runs (started_at, trigger) VALUES (?, ?)", (now_iso(), trigger)).lastrowid

    def finish_run(self, run_id: int, checked: int, errors: int) -> None:
        self._write("UPDATE sync_runs SET finished_at=?, checked=?, errors=? WHERE id=?", (now_iso(), checked, errors, run_id))

    def last_run(self) -> dict | None:
        rows = self._query("SELECT * FROM sync_runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1")
        return rows[0] if rows else None

    def kv_get(self, key: str):
        rows = self._query("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else None

    def kv_set(self, key: str, value) -> None:
        self._write("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def snapshot(self, sync: "TwoWaySync", games: list[Game], romm_library: list[dict], partial: bool) -> None:
        """Rewrite the `library` table (only these games' rows when partial), log new conflicts and refresh the orphan list."""
        romm_games = {game["id"]: game for game in romm_library}
        matched_games = [game for game in games if game.is_matched]
        rows = [row for game in matched_games for row in game_rows(sync, game, romm_games[game.romm_id])]
        self.replace_library(rows, {game.romm_id for game in matched_games} if partial else None)
        for row in rows:
            key = {name: row[name] for name in ("rom_id", "kind", "file_name", "emulator")}
            last_event = self.last_event(**key)
            if row["status"] == "conflict" and (last_event is None or last_event["action"] != "conflict"):
                self.add_event("conflict", local_hash=row["local_hash"], local_mtime=row["local_mtime"], romm_id=row["romm_file_id"],
                           romm_hash=row["romm_hash"], romm_updated_at=row["romm_updated_at"], device=row["local_device"], **key)
        self.kv_set("orphans", self.orphan_events(set(romm_games)))
        for key, listed in (("ambiguous", ambiguous_games), ("unmatched", unmatched_games)):
            self.kv_set(key, listed(games, self.kv_get(key) if partial else None))
        self.kv_set("romm_library", {"roms": len(romm_library), "size_bytes": sum(game["fs_size_bytes"] for game in romm_library)})


def keep_unscanned(games: list[Game], previous: list[dict] | None, entries: list[dict]) -> list[dict]:
    """`entries` for the scanned games plus, for a partial snapshot (`previous` given), the other games' earlier entries."""
    scanned = {(game.platform, game.name) for game in games}
    return [entry for entry in previous or [] if (entry["local_platform"], entry["name"]) not in scanned] + entries


def ambiguous_games(games: list[Game], previous: list[dict] | None) -> list[dict]:
    """Local games left unsynced because their name matches several RomM games."""
    return keep_unscanned(games, previous, [
        {"local_platform": game.platform, "name": game.name, "platform_slug": game.ambiguous_roms[0]["platform_slug"],
         "platform_name": game.ambiguous_roms[0].get("platform_display_name"),
         "cover": next((rom.get("path_cover_small") for rom in game.ambiguous_roms if rom.get("path_cover_small")), None),
         "roms": [{"id": rom["id"], "fs_name": rom["fs_name"], "name": rom.get("name")} for rom in game.ambiguous_roms]}
        for game in games if game.ambiguous_roms])


def unmatched_games(games: list[Game], previous: list[dict] | None) -> list[dict]:
    """Local games with no RomM game of that file name (ambiguous ones are listed separately)."""
    return keep_unscanned(games, previous, [
        {"local_platform": game.platform, "name": game.name,
         "files": [save.path.name for save in game.local_save_files] + list(game.local_state_files)}
        for game in games if not game.is_matched and not game.ambiguous_roms])


def game_rows(sync: "TwoWaySync", game: Game, romm_game: dict) -> list[dict]:
    rows = []
    for kind, locals_, romms in game.files_by_kind():
        rows += [build_row(sync, game, romm_game, kind, local, paired_romm(local, romms)) for local in locals_]
        rows += [build_row(sync, game, romm_game, kind, None, romm) for romm in romms if not local_for(romm, locals_, romms)]
    return rows

def build_row(sync: "TwoWaySync", game: Game, romm_game: dict, kind: str, local: SaveFile | SaveState | None, romm: SaveFile | SaveState | None) -> dict:
    ref = local or romm
    row = {"rom_id": game.romm_id, "kind": kind, "file_name": ref.path.name, "emulator": ref.emulator or "",
           "platform_slug": game.romm_platform, "platform_name": romm_game["platform_display_name"], "platform_id": game.romm_platform_id,
           "local_platform": game.platform, "game_name": romm_game["name"], "cover_path": romm_game.get("path_cover_small"),
           "local_path": None, "note": None, "local_hash": None, "local_mtime": None, "local_size": None, "local_device": None,
           "romm_file_id": None, "romm_hash": None, "romm_updated_at": None, "romm_size": None, "status": "synced"}
    if local:
        stat = local.path.stat()
        row |= {"local_path": str(local.path), "local_hash": file_md5(local.path), "local_mtime": iso(stat.st_mtime),
                "local_size": stat.st_size, "local_device": sync.resolver.modified_by(local.path) if sync.resolver else None}
    if romm:
        row |= {"romm_file_id": romm.id, "romm_hash": sync.romm_hash(game.romm_id, kind, romm),
                "romm_updated_at": romm.modified_at.isoformat(), "romm_size": romm.size}
    if local and romm and (verdict := sync.verdict(game.romm_id, kind, local, romm)) in ("conflict", "differs"):
        row["status"] = verdict
    if romm and not local and sync.writable:
        target, row["note"] = sync.download_target(game, kind, romm)
        if target is None and row["note"]:
            row["status"] = "blocked"
            blocking_path = sync.cfg.sync_dir_for(kind) / game.platform / ref.path.name
            if blocking_path.exists():
                stat = blocking_path.stat()
                row |= {"local_path": str(blocking_path), "local_hash": file_md5(blocking_path), "local_mtime": iso(stat.st_mtime),
                        "local_size": stat.st_size}
    return row
