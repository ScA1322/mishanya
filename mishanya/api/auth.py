"""Авторизация владельца панели: пароль из .env → сессионная cookie + CSRF-токен."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete

from ..db import utcnow
from ..db.models import PanelSession
from .deps import get_app

COOKIE = "mishanya_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class _Limiter:
    """Ограничение перебора пароля: после 5 ошибок — блокировка с удвоением времени."""

    def __init__(self) -> None:
        self.failures: dict[str, tuple[int, float]] = {}

    def check(self, key: str) -> float:
        count, until = self.failures.get(key, (0, 0.0))
        return max(0.0, until - time.monotonic())

    def fail(self, key: str) -> None:
        count, _ = self.failures.get(key, (0, 0.0))
        count += 1
        until = 0.0
        if count >= MAX_FAILURES:
            until = time.monotonic() + LOCKOUT_SECONDS * (2 ** min(count - MAX_FAILURES, 6))
        self.failures[key] = (count, until)

    def success(self, key: str) -> None:
        self.failures.pop(key, None)


limiter = _Limiter()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _get_session(request: Request) -> PanelSession | None:
    token = request.cookies.get(COOKIE)
    if not token or len(token) > 128:
        return None
    app = get_app(request)
    async with app.db.session() as s:
        sess = await s.get(PanelSession, _hash(token))
        if sess is None:
            return None
        if sess.expires_at < utcnow():
            await s.delete(sess)
            await s.commit()
            return None
        return sess


async def require_auth(request: Request) -> PanelSession:
    """Зависимость для всех защищённых маршрутов: проверка сессии и CSRF для изменяющих запросов."""
    sess = await _get_session(request)
    if sess is None:
        raise HTTPException(status_code=401, detail="Требуется вход в панель")
    if request.method not in SAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        if not header or not hmac.compare_digest(header, sess.csrf_token):
            raise HTTPException(status_code=403, detail="Неверный CSRF-токен. Обновите страницу.")
    return sess


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response) -> dict:
    app = get_app(request)
    key = request.client.host if request.client else "unknown"
    wait = limiter.check(key)
    if wait > 0:
        raise HTTPException(status_code=429, detail=f"Слишком много попыток. Подождите {int(wait) + 1} с.")
    expected = app.settings.panel_password
    if not expected or not hmac.compare_digest(body.password.encode(), expected.encode()):
        limiter.fail(key)
        raise HTTPException(status_code=401, detail="Неверный пароль")
    limiter.success(key)
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    hours = app.settings.session_hours
    async with app.db.session() as s:
        await s.execute(delete(PanelSession).where(PanelSession.expires_at < utcnow()))
        s.add(PanelSession(token_hash=_hash(token), csrf_token=csrf, expires_at=utcnow() + timedelta(hours=hours)))
        await s.commit()
    response.set_cookie(COOKIE, token, max_age=hours * 3600, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", path="/")
    return {"authenticated": True, "csrf_token": csrf}


@router.post("/logout")
async def logout(request: Request, response: Response, sess: PanelSession = Depends(require_auth)) -> dict:
    app = get_app(request)
    async with app.db.session() as s:
        await s.execute(delete(PanelSession).where(PanelSession.token_hash == sess.token_hash))
        await s.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"authenticated": False}


@router.get("/me")
async def me(request: Request) -> dict:
    sess = await _get_session(request)
    if sess is None:
        return {"authenticated": False, "csrf_token": None}
    return {"authenticated": True, "csrf_token": sess.csrf_token}
