"""FastAPI app for the sync dashboard. Reads the DB the sync loop fills; resolve actions share the sync lock."""
import threading
from functools import cache
from pathlib import Path
from typing import Literal

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel

from .config import LoggingConfig
from .library_classes import LibraryScanner
from .two_way_sync import iso
from .conflict_resolution import ConflictResolution, LayoutUnavailable
from .sync_orchestrator import SYNC_LOCK, SYNC_PROGRESS, SyncOrchestrator

STATIC_DIR = Path(__file__).parent / "webui_static"


class RevalidatingStaticFiles(StaticFiles):
    """Browsers must check the ETag on every load, or phones keep running an old app.js after a deploy."""

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


class ResolveRequest(BaseModel):
    rom_id: int
    kind: Literal["save", "state"]
    file_name: str
    emulator: str
    keep: Literal["local", "romm"]


class AddLocalRequest(BaseModel):
    rom_id: int
    kind: str
    file_name: str
    emulator: str
    layout: Literal["content", "core"]


class SyncthingResolveRequest(BaseModel):
    path: str
    keep: Literal["original", "conflict"]


class SyncthingKeepCurrentRequest(BaseModel):
    paths: list[str]


def create_app(engine, conflicts: ConflictResolution) -> FastAPI:
    app = FastAPI(title="romm_sync")
    cfg, store, user = engine.cfg, engine.store, engine.user

    orchestrator = SyncOrchestrator(local_library=None, romm_server=None, engine=engine)

    @cache
    def romm_user() -> dict:
        me = user.get_me()
        return {key: me[key] for key in ("username", "role", "avatar_path")}

    def games_from_library() -> list[dict]:
        games: dict[int, dict] = {}
        for row in store.library():
            game = games.setdefault(row["rom_id"], {"rom_id": row["rom_id"], "name": row["game_name"], "platform_slug": row["platform_slug"],
                                                    "platform_name": row["platform_name"], "fs_folder": row["local_platform"],
                                                    "cover": row["cover_path"], "files": []})
            game["files"].append({
                "kind": row["kind"], "file_name": row["file_name"], "emulator": row["emulator"], "core": row["emulator"] or None,
                "status": row["status"], "note": row["note"],
                "add_local": row["status"] == "blocked" and {
                    layout: reason for layout, (_, reason) in engine.add_local_options(row["kind"], row["local_platform"], row["file_name"], row["emulator"]).items()},
                "local": row["local_path"] and {"modified_at": row["local_mtime"], "device": row["local_device"] or "Syncthing",
                                                "size_bytes": row["local_size"], "hash": row["local_hash"]},
                "romm": row["romm_file_id"] and {"modified_at": row["romm_updated_at"], "device": "RomM",
                                                 "size_bytes": row["romm_size"], "hash": row["romm_hash"]}})
        return list(games.values())

    def last_sync(games: list[dict]) -> dict | None:
        run = store.last_run()
        if run is None:
            return None
        game_names = {game["rom_id"]: game["name"] for game in games}
        events = store.events_since(run["started_at"])

        def files(action: str) -> list[dict]:
            return [{"file_name": e["file_name"], "game": game_names.get(e["rom_id"])} for e in events if e["action"] == action]

        return {"started_at": run["started_at"], "finished_at": run["finished_at"], "trigger": run["trigger"], "checked": run["checked"],
                "errors": run["errors"], "pushed": files("push"), "pulled": files("pull")}

    def syncthing_conflicts() -> list[dict]:
        device_names = engine.resolver.device_names if engine.resolver else {}
        return [{"path": str(conflict), "file_name": original.name, "device": device_names.get(device, device),
                 "modified_at": iso(conflict.stat().st_mtime), "size_bytes": conflict.stat().st_size,
                 "original": {"modified_at": iso(original.stat().st_mtime), "size_bytes": original.stat().st_size}}
                for conflict, original, device in LibraryScanner.find_syncthing_conflicts(cfg.sync_dirs)]

    @app.get("/api/state")
    def state() -> dict:
        games = games_from_library()
        return {"push_only": not engine.writable, "sync_mode": cfg.SYNC_MODE, "romm_url": cfg.ROMM_PUBLIC_URL, "user": romm_user(),
                "last_sync": last_sync(games), "games": games, "syncthing_conflicts": syncthing_conflicts(),
                "orphans": store.kv_get("orphans") or [], "ambiguous": store.kv_get("ambiguous") or [], "unmatched": store.kv_get("unmatched") or [],
                "duplicates": LibraryScanner.find_flat_core_duplicates(cfg.sync_dirs),
                "romm_library": store.kv_get("romm_library") or {"roms": 0, "size_bytes": 0},
                "backups": {"count": len(backups := conflicts.backups()), "size_bytes": sum(path.stat().st_size for path in backups),
                            "retention_days": cfg.LOG_DB_RETENTION_DAYS}}

    @app.post("/api/actions/prune-backups")
    def prune_backups() -> dict:
        with SYNC_LOCK:
            return {"message": f"Pruned {plural(conflicts.prune_backups(), 'backup')}"}

    @app.get("/api/actions/rebuild-library/progress")
    def rebuild_progress() -> dict:
        return {"done": SYNC_PROGRESS.done, "total": SYNC_PROGRESS.total}

    @app.post("/api/actions/rebuild-library")
    def rebuild_library() -> dict:
        sync_result = orchestrator.full_sync()
        if not sync_result.success:
            raise HTTPException(500, f"Rebuild failed: {sync_result.error_message or plural(len(sync_result.games_failed), 'game')}")
        return {"message": "Library rebuilt"}

    @app.post("/api/actions/pull")
    def pull() -> dict:
        if not engine.writable:
            raise HTTPException(409, "Push-only mode never writes to the sync folders")
        sync_result = orchestrator.pull_from_romm()
        if not sync_result.success:
            raise HTTPException(500, f"Pull failed: {sync_result.error_message or plural(len(sync_result.games_failed), 'game')}")
        return {"message": "Pulled changes from RomM"}

    @app.get("/api/logs")
    def logs() -> list[str]:
        return list(LoggingConfig.LOG_BUFFER)

    @app.get("/api/games/{rom_id}/history")
    def history(rom_id: int) -> list[dict]:
        return store.history(rom_id)

    @app.post("/api/conflicts/resolve")
    def resolve(body: ResolveRequest) -> dict:
        row = store.library_row(body.rom_id, body.kind, body.file_name, body.emulator)
        if row is None or row["status"] not in ("conflict", "differs"):
            raise HTTPException(404, "No open conflict or difference for that file")
        if body.keep == "romm" and not engine.writable:
            raise HTTPException(409, "Push-only mode never writes to the sync folders")
        with SYNC_LOCK:
            try:
                conflicts.resolve(row, body.keep)
            except (OSError, RuntimeError, requests.RequestException) as error:
                logger.error(f"Could not resolve {body.file_name}: {error}")
                raise HTTPException(500, f"Could not resolve: {error}")
        return {"ok": True}

    @app.post("/api/blocked/add-local")
    def add_local(body: AddLocalRequest) -> dict:
        row = store.library_row(body.rom_id, body.kind, body.file_name, body.emulator)
        if row is None or row["status"] != "blocked":
            raise HTTPException(404, "No held RomM download for that file")
        if not engine.writable:
            raise HTTPException(409, "Push-only mode never writes to the sync folders")
        with SYNC_LOCK:
            try:
                conflicts.add_to_local(row, body.layout)
            except LayoutUnavailable as error:
                raise HTTPException(409, f"Not added: {error}")
            except (OSError, RuntimeError, requests.RequestException) as error:
                logger.error(f"Could not add {body.file_name} to the sync folder: {error}")
                raise HTTPException(500, f"Could not add to local: {error}")
        return {"ok": True}

    @app.post("/api/syncthing-conflicts/resolve")
    def resolve_syncthing_conflict(body: SyncthingResolveRequest) -> dict:
        originals = {str(conflict): original for conflict, original, _ in LibraryScanner.find_syncthing_conflicts(cfg.sync_dirs)}
        if body.path not in originals:
            raise HTTPException(404, "No such Syncthing conflict file")
        if not engine.writable:
            raise HTTPException(409, "Push-only mode never writes to the sync folders")
        with SYNC_LOCK:
            try:
                conflicts.resolve_syncthing_conflict(Path(body.path), originals[body.path], body.keep)
            except OSError as error:
                logger.error(f"Could not resolve {body.path}: {error}")
                raise HTTPException(500, f"Could not resolve: {error}")
        return {"ok": True}

    @app.post("/api/syncthing-conflicts/keep-current")
    def keep_current_files(body: SyncthingKeepCurrentRequest) -> dict:
        """Keep the current file for each conflict copy listed; the copies move to the backup folder."""
        originals = {str(conflict): original for conflict, original, _ in LibraryScanner.find_syncthing_conflicts(cfg.sync_dirs)}
        if missing := [path for path in body.paths if path not in originals]:
            raise HTTPException(404, f"No such Syncthing conflict file: {missing[0]}")
        if not engine.writable:
            raise HTTPException(409, "Push-only mode never writes to the sync folders")
        with SYNC_LOCK:
            for path in body.paths:
                try:
                    conflicts.resolve_syncthing_conflict(Path(path), originals[path], "original")
                except OSError as error:
                    logger.error(f"Could not resolve {path}: {error}")
                    raise HTTPException(500, f"Could not resolve {Path(path).name}: {error}")
        return {"message": f"Backed up and removed {plural(len(body.paths), 'sync-conflict file')}"}

    @app.get("/romm/{asset_path:path}")
    def romm_asset(asset_path: str, request: Request) -> Response:
        """Platform icons and covers, so the browser never needs to reach an internal ROMM_URL."""
        if not asset_path.startswith("assets/") or ".." in asset_path:
            raise HTTPException(404)
        response = requests.get(f"{user.url}/{asset_path}", params=dict(request.query_params), timeout=15, **user._auth_kwargs())
        return Response(response.content, status_code=response.status_code, media_type=response.headers.get("content-type"),
                        headers={"Cache-Control": "max-age=3600"})

    app.mount("/", RevalidatingStaticFiles(directory=STATIC_DIR, html=True))
    return app


def serve_in_thread(engine, conflicts: ConflictResolution) -> None:
    server = uvicorn.Server(uvicorn.Config(create_app(engine, conflicts), host="0.0.0.0", port=engine.cfg.WEBUI_PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True, name="webui").start()
    logger.info(f"Web UI on port {engine.cfg.WEBUI_PORT}")
