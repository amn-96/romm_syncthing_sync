"""Routes that change files: conflict resolution, held downloads, Syncthing conflict copies, and manual sync runs.

Every change runs under SYNC_LOCK so it never races a sync."""
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException
from loguru import logger

from ..conflict_resolution import LayoutUnavailable
from ..sync_orchestrator import SYNC_LOCK
from .schemas import AddLocalRequest, FileKey, Message, Ok, ResolveRequest, SyncthingKeepCurrentRequest, SyncthingResolveRequest, errors
from .views import WebContext, plural

PUSH_ONLY = "Push-only mode never writes to the sync folders"


def action_router(ctx: WebContext) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["actions"])
    store, engine, conflicts = ctx.store, ctx.engine, ctx.conflicts

    def held_row(body: FileKey) -> dict:
        row = store.library_row(body.rom_id, body.kind, body.file_name, body.emulator)
        if row is None or row["status"] != "blocked":
            raise HTTPException(404, "No held RomM download for that file")
        return row

    def syncthing_original(path: str, originals: dict[str, Path]) -> Path:
        if path not in originals:
            raise HTTPException(404, f"No such Syncthing conflict file: {path}")
        return originals[path]

    def require_writable(reason: str = PUSH_ONLY) -> None:
        if not engine.writable:
            raise HTTPException(409, reason)

    @router.post("/conflicts/resolve", responses=errors(404, 409, 500))
    def resolve(body: ResolveRequest) -> Ok:
        """Keep one side of a conflict or difference. The other side's copy is backed up first."""
        row = store.library_row(body.rom_id, body.kind, body.file_name, body.emulator)
        if row is None or row["status"] not in ("conflict", "differs"):
            raise HTTPException(404, "No open conflict or difference for that file")
        if body.keep == "romm":
            require_writable()
        with SYNC_LOCK:
            try:
                conflicts.resolve(row, body.keep)
            except (OSError, RuntimeError, requests.RequestException) as error:
                logger.error(f"Could not resolve {body.file_name}: {error}")
                raise HTTPException(500, f"Could not resolve: {error}")
        return Ok(ok=True)

    @router.post("/blocked/add-local", responses=errors(404, 409, 500))
    def add_local(body: AddLocalRequest) -> Ok:
        """Download a held RomM file into the platform or core folder. Never overwrites."""
        row = held_row(body)
        require_writable()
        with SYNC_LOCK:
            try:
                conflicts.add_to_local(row, body.layout)
            except LayoutUnavailable as error:
                raise HTTPException(409, f"Not added: {error}")
            except (OSError, RuntimeError, requests.RequestException) as error:
                logger.error(f"Could not add {body.file_name} to the sync folder: {error}")
                raise HTTPException(500, f"Could not add to local: {error}")
        return Ok(ok=True)

    @router.post("/blocked/delete-romm", responses=errors(404, 409, 500))
    def delete_romm(body: FileKey) -> Ok:
        """Delete a held RomM file the sync-folder copy supersedes. RomM's bytes are backed up first."""
        row = held_row(body)
        require_writable("Push-only mode never deletes from RomM")
        with SYNC_LOCK:
            try:
                conflicts.delete_from_romm(row)
            except (OSError, requests.RequestException) as error:
                logger.error(f"Could not delete {body.file_name} from RomM: {error}")
                raise HTTPException(500, f"Could not delete from RomM: {error}")
        return Ok(ok=True)

    @router.post("/syncthing-conflicts/resolve", responses=errors(404, 409, 500))
    def resolve_syncthing_conflict(body: SyncthingResolveRequest) -> Ok:
        """Keep the original or the conflict copy; the other moves to the backup folder."""
        original = syncthing_original(body.path, ctx.syncthing_originals())
        require_writable()
        with SYNC_LOCK:
            try:
                conflicts.resolve_syncthing_conflict(Path(body.path), original, body.keep)
            except OSError as error:
                logger.error(f"Could not resolve {body.path}: {error}")
                raise HTTPException(500, f"Could not resolve: {error}")
        return Ok(ok=True)

    @router.post("/syncthing-conflicts/keep-current", responses=errors(404, 409, 500))
    def keep_current_files(body: SyncthingKeepCurrentRequest) -> Message:
        """Keep the current file for each conflict copy listed; the copies move to the backup folder."""
        originals = ctx.syncthing_originals()
        pairs = [(path, syncthing_original(path, originals)) for path in body.paths]
        require_writable()
        with SYNC_LOCK:
            for path, original in pairs:
                try:
                    conflicts.resolve_syncthing_conflict(Path(path), original, "original")
                except OSError as error:
                    logger.error(f"Could not resolve {path}: {error}")
                    raise HTTPException(500, f"Could not resolve {Path(path).name}: {error}")
        return Message(message=f"Backed up and removed {plural(len(body.paths), 'sync-conflict file')}")

    @router.post("/actions/prune-backups")
    def prune_backups() -> Message:
        """Delete backups older than LOG_DB_RETENTION_DAYS."""
        with SYNC_LOCK:
            return Message(message=f"Pruned {plural(conflicts.prune_backups(), 'backup')}")

    @router.post("/actions/rebuild-library", responses=errors(500))
    def rebuild_library() -> Message:
        """Run a full sync now. Progress is at /api/actions/rebuild-library/progress."""
        sync_result = ctx.orchestrator.full_sync()
        if not sync_result.success:
            raise HTTPException(500, f"Rebuild failed: {sync_result.error_message or plural(len(sync_result.games_failed), 'game')}")
        return Message(message="Library rebuilt")

    @router.post("/actions/pull", responses=errors(409, 500))
    def pull() -> Message:
        """Pull RomM's changes into the sync folders now, without pushing."""
        require_writable()
        sync_result = ctx.orchestrator.pull_from_romm()
        if not sync_result.success:
            raise HTTPException(500, f"Pull failed: {sync_result.error_message or plural(len(sync_result.games_failed), 'game')}")
        return Message(message="Pulled changes from RomM")

    return router
