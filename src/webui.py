"""FastAPI app for the sync dashboard. Reads the DB the sync loop fills; resolve actions share the sync lock."""
import hashlib
import json
import threading
import time
from collections import OrderedDict
from functools import cache
from pathlib import Path
from typing import Literal

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel

from .config import LoggingConfig
from .library_classes import LibraryScanner
from .two_way_sync import iso
from .conflict_resolution import ConflictResolution, LayoutUnavailable
from .store import KEY
from .sync_orchestrator import SYNC_LOCK, SYNC_PROGRESS, SyncOrchestrator

STATIC_DIR = Path(__file__).parent / "webui_static"
SYNC_VERSION = (Path(__file__).parent.parent / "VERSION").read_text().strip()
FILESYSTEM_TTL_SECONDS = 5
ASSET_CACHE_SIZE = 256


class RevalidatingStaticFiles(StaticFiles):
    """Browsers must check the ETag on every load, or phones keep running an old app.js after a deploy."""

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def screenshot_of(state_path: str) -> Path:
    """RetroArch writes a state's screenshot beside it as `<state>.png`."""
    return Path(f"{state_path}.png")


def listed(file_name: str) -> bool:
    """A `.png` is a state's screenshot and travels with the state, so the UI never lists one on its own."""
    return not file_name.lower().endswith(".png")


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


RECENT_GAMES = 24  # the UI shows as many as fit, up to this


def create_app(engine, conflicts: ConflictResolution) -> FastAPI:
    app = FastAPI(title="romm_sync")
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    cfg, store, user = engine.cfg, engine.store, engine.user

    orchestrator = SyncOrchestrator(local_library=None, romm_server=None, engine=engine)

    @cache
    def romm_user() -> dict:
        me = user.get_me()
        return {key: me[key] for key in ("username", "role", "avatar_path")}

    def sync_configuration() -> dict:
        """Environment settings that shape the sync, as shown in the web UI's read-only server info."""
        timing = ({"SYNC_INTERVAL_SECONDS": cfg.SYNC_INTERVAL_SECONDS} if cfg.SYNC_MODE == "periodic"
                  else {"WATCHDOG_DELAY_SECONDS": cfg.WATCHDOG_DELAY_SECONDS, "PULL_INTERVAL_SECONDS": cfg.PULL_INTERVAL_SECONDS})
        return {"SYNC_MODE": cfg.SYNC_MODE, **timing, "FULL_INITIAL_SYNC": cfg.FULL_INITIAL_SYNC, "FORCE_PUSH_SYNC": cfg.FORCE_PUSH_SYNC,
                "PUSH_ONLY": not engine.writable, "LOG_LEVEL": cfg.LOG_LEVEL, "LOG_DB_RETENTION_DAYS": cfg.LOG_DB_RETENTION_DAYS}

    def games_from_library() -> list[dict]:
        games: dict[int, dict] = {}
        synced_at = store.last_synced()
        for row in filter(lambda row: listed(row["file_name"]), store.library()):
            game = games.setdefault(row["rom_id"], {"rom_id": row["rom_id"], "name": row["game_name"], "platform_slug": row["platform_slug"],
                                                    "platform_name": row["platform_name"], "fs_folder": row["local_platform"],
                                                    "cover": row["cover_path"], "files": []})
            game["files"].append({
                "kind": row["kind"], "file_name": row["file_name"], "emulator": row["emulator"], "core": row["emulator"] or None,
                "status": row["status"], "note": row["note"], "last_synced": synced_at.get(tuple(row[c] for c in KEY)),
                "screenshot": row["kind"] == "state" and bool(row["local_path"]) and screenshot_of(row["local_path"]).exists(),
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
            return [{"file_name": e["file_name"], "game": game_names.get(e["rom_id"])} for e in events if e["action"] == action and listed(e["file_name"])]

        return {"started_at": run["started_at"], "finished_at": run["finished_at"], "trigger": run["trigger"], "checked": run["checked"],
                "errors": run["errors"], "pushed": files("push"), "pulled": files("pull")}

    def syncthing_conflicts() -> list[dict]:
        device_names = engine.resolver.device_names if engine.resolver else {}
        return [{"path": str(conflict), "file_name": original.name, "device": device_names.get(device, device),
                 "modified_at": iso(conflict.stat().st_mtime), "size_bytes": conflict.stat().st_size,
                 "original": {"modified_at": iso(original.stat().st_mtime), "size_bytes": original.stat().st_size}}
                for conflict, original, device in LibraryScanner.find_syncthing_conflicts(cfg.sync_dirs) if listed(original.name)]

    filesystem_cache: dict = {}

    @app.middleware("http")
    async def drop_filesystem_cache_after_actions(request: Request, call_next) -> Response:
        response = await call_next(request)
        if request.method == "POST":
            filesystem_cache.clear()
        return response

    def filesystem_state() -> dict:
        """Syncthing conflicts, duplicates and backups need directory walks and stats; every POST action clears this."""
        if not filesystem_cache or time.monotonic() - filesystem_cache["at"] > FILESYSTEM_TTL_SECONDS:
            backups = conflicts.backups()
            filesystem_cache.update(at=time.monotonic(), syncthing_conflicts=syncthing_conflicts(),
                                    duplicates=[d for d in LibraryScanner.find_flat_core_duplicates(cfg.sync_dirs) if listed(d["file_name"])],
                                    backups={"count": len(backups), "size_bytes": sum(path.stat().st_size for path in backups),
                                             "retention_days": cfg.LOG_DB_RETENTION_DAYS})
        return filesystem_cache

    @app.get("/api/state")
    def state(request: Request) -> Response:
        games = games_from_library()
        filesystem = filesystem_state()
        unmatched = [game | {"files": files} for game in store.kv_get("unmatched") or [] if (files := [f for f in game["files"] if listed(f)])]
        body = json.dumps(jsonable_encoder({"push_only": not engine.writable, "sync_mode": cfg.SYNC_MODE, "romm_url": cfg.ROMM_PUBLIC_URL, "user": romm_user(),
                "server_info": {"sync_version": SYNC_VERSION, "config": sync_configuration()},
                "last_sync": last_sync(games), "recent": store.recent_moves(RECENT_GAMES), "games": games, "syncthing_conflicts": filesystem["syncthing_conflicts"],
                "orphans": [o for o in store.kv_get("orphans") or [] if listed(o["file_name"])], "ambiguous": store.kv_get("ambiguous") or [], "unmatched": unmatched,
                "duplicates": filesystem["duplicates"], "backups": filesystem["backups"],
                "romm_library": store.kv_get("romm_library") or {"roms": 0, "size_bytes": 0}}), separators=(",", ":")).encode()
        etag = f'"{hashlib.sha1(body).hexdigest()}"'
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(body, media_type="application/json", headers=headers)

    @app.get("/api/romm-status")
    def romm_status() -> dict:
        """A live RomM heartbeat, kept out of /api/state so an unreachable RomM cannot break the page."""
        try:
            return {"version": user.get_version()}
        except (requests.RequestException, KeyError) as error:
            return {"error": str(error)}

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

    @app.get("/api/games/{rom_id}/screenshot")
    def screenshot(rom_id: int, file_name: str, emulator: str = "") -> FileResponse:
        row = store.library_row(rom_id, "state", file_name, emulator)
        if row is None or not row["local_path"] or not screenshot_of(row["local_path"]).exists():
            raise HTTPException(404)
        return FileResponse(screenshot_of(row["local_path"]))

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

    asset_cache: OrderedDict[str, tuple[bytes, str, dict[str, str]]] = OrderedDict()

    @app.get("/romm/{asset_path:path}")
    def romm_asset(asset_path: str, request: Request) -> Response:
        """Platform icons and covers, so the browser never needs to reach an internal ROMM_URL."""
        if not asset_path.startswith("assets/") or ".." in asset_path:
            raise HTTPException(404)
        key = f"{asset_path}?{request.url.query}"
        if key not in asset_cache:
            upstream = requests.get(f"{user.url}/{asset_path}", params=dict(request.query_params), timeout=15, **user._auth_kwargs())
            if upstream.status_code != 200:
                return Response(upstream.content, status_code=upstream.status_code, media_type=upstream.headers.get("content-type"))
            # Only assets/romm/ (covers) can change; platform icons and logos are fixed.
            headers = {"Cache-Control": "max-age=3600" if asset_path.startswith("assets/romm/") else "max-age=604800, immutable",
                       **{name: upstream.headers[name] for name in ("ETag", "Last-Modified") if name in upstream.headers}}
            asset_cache[key] = (upstream.content, upstream.headers.get("content-type"), headers)
            if len(asset_cache) > ASSET_CACHE_SIZE:
                asset_cache.popitem(last=False)
        asset_cache.move_to_end(key)
        body, content_type, headers = asset_cache[key]
        if request.headers.get("if-none-match", "") == headers.get("ETag") or request.headers.get("if-modified-since", "") == headers.get("Last-Modified"):
            return Response(status_code=304, headers=headers)
        return Response(body, media_type=content_type, headers=headers)

    app.mount("/", RevalidatingStaticFiles(directory=STATIC_DIR, html=True))
    return app


def serve_in_thread(engine, conflicts: ConflictResolution) -> None:
    server = uvicorn.Server(uvicorn.Config(create_app(engine, conflicts), host="0.0.0.0", port=engine.cfg.WEBUI_PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True, name="webui").start()
    logger.info(f"Web UI on port {engine.cfg.WEBUI_PORT}")
