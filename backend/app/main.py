"""Точка входа: фабрика FastAPI-приложения."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api
from .auth import Auth
from .config import Settings, load_settings
from .db import Database
from .hub import Hub
from .logsetup import register_secret, setup_logging
from .node import NodeService

log = logging.getLogger("meshboard")


@dataclass
class Services:
    settings: Settings
    db: Database
    hub: Hub
    auth: Auth
    node: NodeService
    repeaters: object | None = None


async def _retention_loop(svc: Services) -> None:
    while True:
        try:
            res = await asyncio.to_thread(
                svc.db.cleanup, svc.settings.retention_messages_days, svc.settings.retention_metrics_days
            )
            if any(res.values()):
                log.info("retention cleanup: %s", res)
        except Exception as e:  # noqa: BLE001
            log.warning("retention cleanup failed: %r", e)
        await asyncio.sleep(3600)


def _register_secrets(s: Settings) -> None:
    register_secret(s.password)
    register_secret(s.secret_key)
    register_secret(s.repeater_default_password)
    for pwd in s.repeater_passwords.values():
        register_secret(pwd)


def create_app(settings: Settings | None = None, start_node: bool = True) -> FastAPI:
    s = settings or load_settings()
    _register_secrets(s)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(s.db_path)
        hub = Hub()
        node = NodeService(s, db, hub)
        svc = Services(settings=s, db=db, hub=hub, auth=Auth(s.password, s.secret_key, s.session_hours),
                       node=node)
        app.state.svc = svc
        try:
            from .repeaters import RepeaterPoller
        except ImportError:  # этап 3
            RepeaterPoller = None  # type: ignore[assignment]
        if RepeaterPoller is not None:
            svc.repeaters = RepeaterPoller(s, db, hub, node)
        tasks = [asyncio.create_task(_retention_loop(svc), name="retention")]
        if start_node:
            await node.start()
            if svc.repeaters is not None:
                svc.repeaters.start()
        log.info("MeshBoard started (mode=%s)", node.status["mode"])
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            if svc.repeaters is not None:
                await svc.repeaters.stop()
            await node.stop()
            db.close()

    app = FastAPI(title="MeshBoard", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)

    tu = urlparse(s.tile_url)
    tile_host = f"{tu.scheme}://{tu.netloc.replace('{s}', '*')}" if tu.netloc else ""
    csp = (
        "default-src 'self'; "
        f"img-src 'self' data: blob: {tile_host}; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; "
        "connect-src 'self' ws: wss:; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )

    @app.middleware("http")
    async def security(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and not api.same_origin(
            request.headers.get("origin"), request.headers.get("host")
        ):
            return JSONResponse({"detail": "bad_origin"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = csp
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(api.public)
    app.include_router(api.router)
    app.add_api_websocket_route("/api/ws", api.websocket_endpoint)

    static_dir = Path(s.static_dir) if s.static_dir else Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if static_dir.is_dir():
        if (static_dir / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")
        index = static_dir / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "not_found"}, status_code=404)
            candidate = (static_dir / path).resolve()
            if path and candidate.is_file() and static_dir.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(index)

    return app


def run() -> None:
    import uvicorn

    s = load_settings()
    setup_logging(s.log_level)
    uvicorn.run(create_app(s), host=s.host, port=s.port, log_config=None, access_log=False,
                proxy_headers=False)


if __name__ == "__main__":
    run()
