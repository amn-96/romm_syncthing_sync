"""FastAPI app for the sync dashboard. Reads the DB the sync loop fills; actions share the sync lock.

Routes live in read_routes (page state and status API), action_routes (anything that changes files) and romm_proxy (RomM images)."""
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import Response
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..conflict_resolution import ConflictResolution
from .action_routes import action_router
from .read_routes import read_router
from .romm_proxy import romm_proxy_router
from .schemas import Route
from .views import SYNC_VERSION, WebContext

STATIC_DIR = Path(__file__).parent.parent / "webui_static"
DISCOVERY = "Routes: GET /api. Guide: /llms.txt. Spec: /openapi.json"
# RFC 8631, so a client can find the spec from any response
LINK_HEADER = '</openapi.json>; rel="service-desc", </docs>; rel="service-doc", </llms.txt>; rel="describedby"'


class RevalidatingStaticFiles(StaticFiles):
    """Browsers must check the ETag on every load, or phones keep running an old app.js after a deploy."""

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(engine, conflicts: ConflictResolution) -> FastAPI:
    ctx = WebContext(engine, conflicts)
    app = FastAPI(title="romm_sync", version=SYNC_VERSION,
                  description="Sync dashboard API. Scripts and agents: read `/llms.txt`, then start with `GET /api/status`.")
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.middleware("http")
    async def drop_filesystem_cache_after_actions(request: Request, call_next) -> Response:
        response = await call_next(request)
        if request.method == "POST":
            ctx.filesystem_cache.clear()
        response.headers["Link"] = LINK_HEADER
        return response

    @app.exception_handler(StarletteHTTPException)
    async def point_unknown_paths_at_the_index(request: Request, error: StarletteHTTPException) -> Response:
        if error.status_code == 404 and error.detail == "Not Found":
            error = StarletteHTTPException(404, f"No route {request.url.path}. {DISCOVERY}")
        return await http_exception_handler(request, error)

    routers = (read_router(ctx), action_router(ctx), romm_proxy_router(ctx))
    for router in routers:
        app.include_router(router)
    # FastAPI 0.163+ nests included routers in app.routes, so list them from the routers themselves
    app.state.api_routes = [route for router in routers for route in router.routes if isinstance(route, APIRoute)]
    routes = [Route(method=method, path=route.path, summary=route.endpoint.__doc__.split("\n")[0])
              for route in app.state.api_routes if route.path.startswith("/api") for method in sorted(route.methods)]

    @app.get("/api", tags=["status"])
    def index() -> list[Route]:
        """Every API route with a one-line summary. Full schemas are at /openapi.json."""
        return routes

    app.mount("/", RevalidatingStaticFiles(directory=STATIC_DIR, html=True))
    return app


def serve_in_thread(engine, conflicts: ConflictResolution) -> None:
    server = uvicorn.Server(uvicorn.Config(create_app(engine, conflicts), host="0.0.0.0", port=engine.cfg.WEBUI_PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True, name="webui").start()
    logger.info(f"Web UI on port {engine.cfg.WEBUI_PORT}")
