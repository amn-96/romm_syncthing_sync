"""Cached proxy for RomM's images, so the browser never needs to reach an internal ROMM_URL."""
from collections import OrderedDict

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .views import WebContext

ASSET_CACHE_SIZE = 256


def romm_proxy_router(ctx: WebContext) -> APIRouter:
    router = APIRouter(tags=["assets"])
    user = ctx.user
    asset_cache: OrderedDict[str, tuple[bytes, str, dict[str, str]]] = OrderedDict()

    @router.get("/romm/{asset_path:path}", response_class=Response,
                responses={200: {"content": {"image/*": {}}}, 304: {"description": "Unchanged"}, 404: {"description": "Not a RomM asset path"}})
    def romm_asset(asset_path: str, request: Request) -> Response:
        """Platform icons and covers from RomM's `assets/` path."""
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

    return router
