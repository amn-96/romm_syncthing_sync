"""Shared state of the web app, and the read views it builds from the DB the sync loop fills."""
import time
from functools import cached_property
from pathlib import Path

from ..conflict_resolution import ConflictResolution
from ..library_classes import LibraryScanner
from ..store import KEY
from ..sync_orchestrator import SyncOrchestrator
from ..two_way_sync import iso

SYNC_VERSION = (Path(__file__).parent.parent.parent / "VERSION").read_text().strip()
FILESYSTEM_TTL_SECONDS = 5


def screenshot_of(state_path: str) -> Path:
    """RetroArch writes a state's screenshot beside it as `<state>.png`."""
    return Path(f"{state_path}.png")


def listed(file_name: str) -> bool:
    """A `.png` is a state's screenshot and travels with the state, so the UI never lists one on its own."""
    return not file_name.lower().endswith(".png")


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


class WebContext:
    def __init__(self, engine, conflicts: ConflictResolution):
        self.engine, self.conflicts = engine, conflicts
        self.cfg, self.store, self.user = engine.cfg, engine.store, engine.user
        self.orchestrator = SyncOrchestrator(local_library=None, romm_server=None, engine=engine)
        self.filesystem_cache: dict = {}

    @cached_property
    def romm_user(self) -> dict:
        me = self.user.get_me()
        return {key: me[key] for key in ("username", "role", "avatar_path")}

    def sync_configuration(self) -> dict:
        """Environment settings that shape the sync, as shown in the web UI's read-only server info."""
        cfg = self.cfg
        timing = ({"SYNC_INTERVAL_SECONDS": cfg.SYNC_INTERVAL_SECONDS} if cfg.SYNC_MODE == "periodic"
                  else {"WATCHDOG_DELAY_SECONDS": cfg.WATCHDOG_DELAY_SECONDS, "PULL_INTERVAL_SECONDS": cfg.PULL_INTERVAL_SECONDS})
        return {"SYNC_MODE": cfg.SYNC_MODE, **timing, "FULL_INITIAL_SYNC": cfg.FULL_INITIAL_SYNC, "FORCE_PUSH_SYNC": cfg.FORCE_PUSH_SYNC,
                "PUSH_ONLY": not self.engine.writable, "LOG_LEVEL": cfg.LOG_LEVEL, "LOG_DB_RETENTION_DAYS": cfg.LOG_DB_RETENTION_DAYS}

    def games(self) -> list[dict]:
        """Every tracked game with its files, in the `Game` schema's shape."""
        games: dict[int, dict] = {}
        synced_at = self.store.last_synced()
        rom_sha1 = self.store.kv_get("rom_sha1") or {}
        syncthing_conflicts = self.filesystem_state()["syncthing_conflicts"]
        for row in filter(lambda row: listed(row["file_name"]), self.store.library()):
            game = games.setdefault(row["rom_id"], {"rom_id": row["rom_id"], "name": row["game_name"], "platform_slug": row["platform_slug"],
                                                    "platform_name": row["platform_name"], "fs_folder": row["local_platform"],
                                                    "cover": row["cover_path"], "sha1": rom_sha1.get(str(row["rom_id"])), "files": [],
                                                    "syncthing_conflicts": [c for c in syncthing_conflicts if c["rom_id"] == row["rom_id"]]})
            game["files"].append({
                "kind": row["kind"], "file_name": row["file_name"], "emulator": row["emulator"], "core": row["emulator"] or None,
                "status": row["status"], "note": row["note"], "last_synced": synced_at.get(tuple(row[c] for c in KEY)),
                "screenshot": row["kind"] == "state" and bool(row["local_path"]) and screenshot_of(row["local_path"]).exists(),
                "add_local": row["status"] == "blocked" and {
                    layout: reason for layout, (_, reason) in
                    self.engine.add_local_options(row["kind"], row["local_platform"], row["file_name"], row["emulator"]).items()},
                "local": row["local_path"] and {"modified_at": row["local_mtime"], "device": row["local_device"] or "Syncthing",
                                                "size_bytes": row["local_size"], "hash": row["local_hash"]},
                "romm": row["romm_file_id"] and {"id": row["romm_file_id"], "modified_at": row["romm_updated_at"], "device": "RomM",
                                                 "size_bytes": row["romm_size"], "hash": row["romm_hash"]}})
        return list(games.values())

    def last_sync(self, games: list[dict]) -> dict | None:
        run = self.store.last_run()
        if run is None:
            return None
        game_names = {game["rom_id"]: game["name"] for game in games}
        events = self.store.events_since(run["started_at"])

        def files(action: str) -> list[dict]:
            return [{"file_name": e["file_name"], "game": game_names.get(e["rom_id"])} for e in events if e["action"] == action and listed(e["file_name"])]

        return {"started_at": run["started_at"], "finished_at": run["finished_at"], "trigger": run["trigger"], "checked": run["checked"],
                "errors": run["errors"], "pushed": files("push"), "pulled": files("pull")}

    def syncthing_conflicts(self) -> list[dict]:
        device_names = self.engine.resolver.device_names if self.engine.resolver else {}
        rom_ids = {row["local_path"]: row["rom_id"] for row in self.store.library() if row["local_path"]}
        return [{"path": str(conflict), "file_name": original.name, "rom_id": rom_ids.get(str(original)), "device": device_names.get(device, device),
                 "modified_at": iso(conflict.stat().st_mtime), "size_bytes": conflict.stat().st_size,
                 "original": {"path": str(original), "modified_at": iso(original.stat().st_mtime), "size_bytes": original.stat().st_size}}
                for conflict, original, device in LibraryScanner.find_syncthing_conflicts(self.cfg.sync_dirs) if listed(original.name)]

    def syncthing_originals(self) -> dict[str, Path]:
        """Conflict copy path -> the file it conflicts with."""
        return {str(conflict): original for conflict, original, _ in LibraryScanner.find_syncthing_conflicts(self.cfg.sync_dirs)}

    def filesystem_state(self) -> dict:
        """Syncthing conflicts, duplicates and backups need directory walks and stats; every POST action clears this."""
        cache = self.filesystem_cache
        if not cache or time.monotonic() - cache["at"] > FILESYSTEM_TTL_SECONDS:
            backups = self.conflicts.backups()
            cache.update(at=time.monotonic(), syncthing_conflicts=self.syncthing_conflicts(),
                         duplicates=[d for d in LibraryScanner.find_flat_core_duplicates(self.cfg.sync_dirs) if listed(d["file_name"])],
                         backups={"count": len(backups), "size_bytes": sum(path.stat().st_size for path in backups),
                                  "retention_days": self.cfg.LOG_DB_RETENTION_DAYS})
        return cache
