"""FastAPI-приложение панели: API, защита заголовками и раздача собранного frontend."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..config import LOCAL_HOSTS
from ..db import DatabaseError
from . import auth
from .routes import content, guilds, leveling, system

if TYPE_CHECKING:
    from ..core import App

log = logging.getLogger(__name__)

CSP = ("default-src 'self'; img-src 'self' https://cdn.discordapp.com https: data:; "
       "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

NO_FRONTEND_HTML = """<!doctype html><html lang="ru"><meta charset="utf-8"><title>Mishanya</title>
<body style="font-family:system-ui;max-width:640px;margin:60px auto;padding:0 16px;line-height:1.5">
<h1>Mishanya</h1><p>Backend работает, но веб-панель не собрана: нет папки <code>frontend/dist</code>.</p>
<p>Запустите <b>setup.bat</b> (нужен Node.js 20+) или восстановите папку <code>frontend/dist</code>
из архива проекта.</p></body></html>"""


def create_app(app: "App") -> FastAPI:
    api = FastAPI(title="Mishanya", docs_url=None, redoc_url=None, openapi_url=None)
    api.state.mishanya = app
    s = app.settings

    if not s.allow_remote_panel:
        # Защита от DNS rebinding: принимаем только запросы на локальные имена.
        api.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(LOCAL_HOSTS))
    origins = [f"http://127.0.0.1:{s.panel_port}", f"http://localhost:{s.panel_port}"]
    api.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Content-Type", auth.CSRF_HEADER])

    @api.middleware("http")
    async def security_headers(request: Request, call_next):  # noqa: ANN001, ANN202
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Content-Security-Policy", CSP)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @api.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = [{"loc": ".".join(str(x) for x in e.get("loc", ()) if x != "body"),
                   "msg": str(e.get("msg", "")).removeprefix("Value error, ")} for e in exc.errors()]
        return JSONResponse({"detail": detail}, status_code=422)

    @api.exception_handler(DatabaseError)
    async def db_handler(_request: Request, exc: DatabaseError) -> JSONResponse:
        return JSONResponse({"detail": f"База данных недоступна: {exc}"}, status_code=503)

    for r in (auth.router, system.router, guilds.router, leveling.router, content.router):
        api.include_router(r)

    @api.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def api_not_found(path: str) -> None:
        raise HTTPException(404, "Неизвестный адрес API")

    dist = s.frontend_dist
    if (dist / "assets").is_dir():
        api.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @api.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):  # noqa: ANN202
        index = dist / "index.html"
        if not index.exists():
            return HTMLResponse(NO_FRONTEND_HTML)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return api
