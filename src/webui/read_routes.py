"""Read-only routes: the web UI's page state, and the status API for scripts and agents."""
import hashlib
import json

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, Response

from ..config import LoggingConfig
from ..sync_orchestrator import SYNC_PROGRESS
from .schemas import FILE_STATUSES, FileStatus, Game, GameSummary, HistoryEvent, LastSync, Progress, RommStatus, StatusSummary, SyncthingConflict, errors
from .views import SYNC_VERSION, WebContext, listed, screenshot_of

RECENT_GAMES = 24  # the UI shows as many as fit, up to this


def read_router(ctx: WebContext) -> APIRouter:
    router = APIRouter(prefix="/api")
    cfg, store, engine = ctx.cfg, ctx.store, ctx.engine

    @router.get("/state", tags=["web UI"], response_class=Response,
                responses={200: {"content": {"application/json": {}}, "description": "Everything the page shows, in one payload"},
                           304: {"description": "Unchanged since the ETag sent in If-None-Match"}})
    def state(request: Request) -> Response:
        """The web UI's whole page in one response, with an ETag. For scripts, /api/status and /api/games are smaller."""
        games = ctx.games()
        filesystem = ctx.filesystem_state()
        unmatched = [game | {"files": files} for game in store.kv_get("unmatched") or [] if (files := [f for f in game["files"] if listed(f)])]
        body = json.dumps(jsonable_encoder({
            "push_only": not engine.writable, "sync_mode": cfg.SYNC_MODE, "romm_url": cfg.ROMM_PUBLIC_URL, "user": ctx.romm_user,
            "server_info": {"sync_version": SYNC_VERSION, "config": ctx.sync_configuration()},
            "last_sync": ctx.last_sync(games), "recent": store.recent_moves(RECENT_GAMES), "games": games,
            "syncthing_conflicts": filesystem["syncthing_conflicts"],
            "orphans": [o for o in store.kv_get("orphans") or [] if listed(o["file_name"])], "ambiguous": store.kv_get("ambiguous") or [],
            "unmatched": unmatched, "duplicates": filesystem["duplicates"], "backups": filesystem["backups"],
            "romm_library": store.kv_get("romm_library") or {"roms": 0, "size_bytes": 0}}), separators=(",", ":")).encode()
        etag = f'"{hashlib.sha1(body).hexdigest()}"'
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(body, media_type="application/json", headers=headers)

    @router.get("/status", tags=["status"])
    def status() -> StatusSummary:
        """Sync health at a glance: what ran last and how many files sit in each status. RomM's own health is /api/romm-status."""
        tracked = ctx.games()
        files = [f for game in tracked for f in game["files"]]
        run = store.last_run()
        counts = {s: sum(f["status"] == s for f in files) for s in FILE_STATUSES}
        syncthing_conflicts = len(ctx.filesystem_state()["syncthing_conflicts"])
        links = {f"files.{s}": f"/api/games?status={s}" for s, n in counts.items() if n and s != "synced"}
        if syncthing_conflicts:
            links["syncthing_conflicts"] = "/api/syncthing-conflicts"
        return StatusSummary(sync_version=SYNC_VERSION, sync_mode=cfg.SYNC_MODE, push_only=not engine.writable,
                             last_sync=run and LastSync(**{k: run[k] for k in LastSync.model_fields}), games=len(tracked),
                             files=counts, syncthing_conflicts=syncthing_conflicts,
                             unmatched_games=len(store.kv_get("unmatched") or []), ambiguous_games=len(store.kv_get("ambiguous") or []),
                             links=links)

    @router.get("/games", tags=["status"])
    def games(status: FileStatus | None = None, platform: str | None = None, q: str | None = None,
              syncthing_conflicts: bool = False) -> list[GameSummary]:
        """Tracked games, filtered by status (games with at least one file in it), RomM platform slug, name substring,
        or `syncthing_conflicts=true` for games with Syncthing conflict copies."""
        summaries = []
        for game in ctx.games():
            counts = {s: n for s in FILE_STATUSES if (n := sum(f["status"] == s for f in game["files"]))}
            if (status and status not in counts) or (platform and game["platform_slug"] != platform) \
                    or (q and q.lower() not in game["name"].lower()) or (syncthing_conflicts and not game["syncthing_conflicts"]):
                continue
            summaries.append(GameSummary(rom_id=game["rom_id"], name=game["name"], platform=game["platform_slug"],
                                         status=next(iter(counts)), files=counts,
                                         syncthing_conflicts=len(game["syncthing_conflicts"])))
        return summaries

    @router.get("/games/{rom_id}", tags=["status"], responses=errors(404))
    def game(rom_id: int) -> Game:
        """One game with every file: status, note, and the sync-folder (`local`) and RomM (`romm`) copies."""
        if found := next((g for g in ctx.games() if g["rom_id"] == rom_id), None):
            return found
        raise HTTPException(404, f"No tracked game with RomM ID {rom_id}")

    @router.get("/games/{rom_id}/history", tags=["status"])
    def history(rom_id: int) -> list[HistoryEvent]:
        """The game's sync events, newest first. Empty for an unknown game."""
        return store.history(rom_id)

    @router.get("/games/{rom_id}/screenshot", tags=["status"], response_class=FileResponse,
                responses={200: {"content": {"image/png": {}}}, **errors(404)})
    def screenshot(rom_id: int, file_name: str, emulator: str = "") -> FileResponse:
        """The screenshot RetroArch saved beside a state."""
        row = store.library_row(rom_id, "state", file_name, emulator)
        if row is None or not row["local_path"] or not screenshot_of(row["local_path"]).exists():
            raise HTTPException(404, "No screenshot for that state")
        return FileResponse(screenshot_of(row["local_path"]))

    @router.get("/syncthing-conflicts", tags=["status"])
    def syncthing_conflicts() -> list[SyncthingConflict]:
        """Every Syncthing conflict copy in the sync folders, including files of untracked games. Resolve with POST /api/syncthing-conflicts/resolve."""
        return ctx.filesystem_state()["syncthing_conflicts"]

    @router.get("/romm-status", tags=["status"], response_model_exclude_none=True)
    def romm_status() -> RommStatus:
        """A live RomM heartbeat, kept out of /api/state so an unreachable RomM cannot break the page."""
        try:
            return RommStatus(version=ctx.user.get_version())
        except (requests.RequestException, KeyError) as error:
            return RommStatus(error=str(error))

    @router.get("/logs", tags=["status"])
    def logs() -> list[str]:
        """Recent log lines, oldest first."""
        return list(LoggingConfig.LOG_BUFFER)

    @router.get("/actions/rebuild-library/progress", tags=["actions"])
    def rebuild_progress() -> Progress:
        """Games fetched so far in a running library rebuild."""
        return Progress(done=SYNC_PROGRESS.done, total=SYNC_PROGRESS.total)

    return router
