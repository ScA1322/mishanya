"""Статус, диагностика, глобальные настройки, резервные копии, предпросмотр шаблонов."""
from __future__ import annotations

import asyncio
import platform
import sys
from datetime import datetime, timezone

import discord
import fastapi
import sqlalchemy
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ...db.backup import BackupError, create_backup, list_backups, resolve_backup, restore_backup
from ...db.database import check_integrity
from ...schemas import LOG_EVENTS, MODULES, EmbedConfig, GlobalSettings
from ...services.automod import RULE_NAMES
from ...services.updater import UpdateError
from ...services.templates import SAMPLE_VALUES, TEMPLATE_VARIABLES, member_values, render
from ..auth import require_auth
from ..deps import discord_guild, get_app, iso
from ..health import guild_issues

router = APIRouter(prefix="/api", tags=["system"], dependencies=[Depends(require_auth)])


def _bot_status(app) -> dict:  # noqa: ANN001
    s = app.settings
    base = {
        "token_configured": s.token_configured,
        "error": app.bot_error,
        "connected": False,
        "latency_ms": None,
        "user": None,
        "user_id": None,
        "avatar_url": None,
        "guild_count": 0,
        "commands": None,
        "sync_error": None,
        "invite_url": None,
    }
    if app.bot is not None:
        base.update(app.bot.status())
        base["invite_url"] = app.bot.invite_url()
    return base


async def _db_status(app) -> dict:  # noqa: ANN001
    path = app.settings.db_path
    backups = list_backups(app.settings.backup_dir)
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "schema_version": await app.db.schema_version(),
        "ok": True,
        "event_logs": await app.eventlog.total(),
        "pending_reminders": await app.reminders.pending_count(),
        "backups": len(backups),
        "last_backup": backups[0].name if backups else None,
        "last_backup_at": iso(backups[0].created_at.astimezone(timezone.utc)) if backups else None,
    }


@router.get("/status")
async def status(request: Request) -> dict:
    app = get_app(request)
    guilds = await app.guilds.list()
    events, _ = await app.eventlog.query(limit=12)
    names = {g.id: g.name for g in guilds}
    try:
        db = await _db_status(app)
    except Exception as exc:  # noqa: BLE001
        db = {"ok": False, "error": str(exc)}
    return {
        "bot": _bot_status(app),
        "panel": {"url": app.settings.panel_url, "host": app.settings.panel_host,
                  "remote_allowed": app.settings.allow_remote_panel},
        "uptime_seconds": int((datetime.now(timezone.utc) - app.started_at).total_seconds()),
        "guilds": {"total": len(guilds), "present": sum(1 for g in guilds if g.present)},
        "db": db,
        "errors": [{"time": iso(e.time), "level": e.level, "source": e.source, "message": e.message}
                   for e in reversed(app.errors.items)][:20],
        "recent_events": [{"id": e.id, "guild_id": str(e.guild_id), "guild_name": names.get(e.guild_id, ""),
                           "type": e.event_type, "summary": e.summary, "created_at": iso(e.created_at)}
                          for e in events],
        "settings": (await app.config.get_global()).model_dump(),
    }


@router.get("/diagnostics")
async def diagnostics(request: Request) -> dict:
    app = get_app(request)
    integrity = await asyncio.to_thread(check_integrity, app.settings.db_path)
    guilds = []
    for g in await app.guilds.list():
        if not g.present:
            continue
        dg = discord_guild(app, g.id)
        guilds.append({
            "id": str(g.id), "name": g.name, "connected": dg is not None,
            "bot_top_role": dg.me.top_role.name if dg else None,
            "issues": await guild_issues(app, g.id),
        })
    bot = app.bot
    intents = bot.intents if bot else None
    return {
        "versions": {"python": sys.version.split()[0], "discord.py": discord.__version__,
                     "fastapi": fastapi.__version__, "sqlalchemy": sqlalchemy.__version__,
                     "platform": platform.platform()},
        "bot": _bot_status(app),
        "intents": {"members": bool(intents and intents.members),
                    "message_content": bool(intents and intents.message_content),
                    "guilds": bool(intents and intents.guilds)},
        "db": {**(await _db_status(app)), "integrity": integrity},
        "config": {"owner_id_set": app.settings.owner_id is not None, "panel_host": app.settings.panel_host,
                   "data_dir": str(app.settings.data_dir), "backup_dir": str(app.settings.backup_dir),
                   "frontend_built": (app.settings.frontend_dist / "index.html").exists()},
        "guilds": guilds,
        "errors": [{"time": iso(e.time), "level": e.level, "source": e.source, "message": e.message}
                   for e in reversed(app.errors.items)],
    }


@router.post("/errors/clear")
async def clear_errors(request: Request) -> dict:
    get_app(request).errors.items.clear()
    return {"ok": True}


@router.post("/commands/sync")
async def sync_commands(request: Request) -> dict:
    app = get_app(request)
    if app.bot is None or not app.bot.is_ready():
        raise HTTPException(409, "Бот не подключён к Discord")
    count = await app.bot.sync_commands(force=True)
    if count is None:
        raise HTTPException(502, f"Синхронизация не удалась: {app.bot.last_sync_error}")
    return {"ok": True, "count": count}


@router.get("/meta")
async def meta() -> dict:
    return {
        "modules": list(MODULES),
        "template_variables": TEMPLATE_VARIABLES,
        "log_events": LOG_EVENTS,
        "automod_rules": RULE_NAMES,
        "defaults": {m: cls().model_dump(mode="json") for m, cls in MODULES.items()},
    }


# ---------------------------------------------------------------- глобальные настройки

@router.get("/settings")
async def get_settings(request: Request) -> dict:
    return (await get_app(request).config.get_global()).model_dump()


@router.put("/settings")
async def put_settings(body: GlobalSettings, request: Request) -> dict:
    app = get_app(request)
    cfg = await app.config.set_global(body)
    if app.bot is not None and app.bot.is_ready():
        try:
            await app.bot.change_presence(activity=discord.CustomActivity(name=cfg.bot_status_text))
        except Exception:  # noqa: BLE001
            pass
    return cfg.model_dump()


@router.post("/maintenance/cleanup")
async def cleanup(request: Request) -> dict:
    app = get_app(request)
    s = await app.config.get_global()
    removed = await app.eventlog.cleanup(s.log_retention_days, s.max_event_logs_per_guild)
    removed += await app.automod.cleanup(s.log_retention_days)
    return {"removed": removed}


# ---------------------------------------------------------------- резервные копии

class RestoreIn(BaseModel):
    name: str = Field(max_length=100)


@router.get("/backups")
async def backups(request: Request) -> list[dict]:
    app = get_app(request)
    return [{"name": b.name, "size_bytes": b.size, "created_at": iso(b.created_at.astimezone(timezone.utc))}
            for b in list_backups(app.settings.backup_dir)]


@router.post("/backups")
async def make_backup(request: Request) -> dict:
    app = get_app(request)
    try:
        info = await asyncio.to_thread(create_backup, app.settings.db_path, app.settings.backup_dir, "manual")
    except (BackupError, OSError) as exc:
        raise HTTPException(500, f"Не удалось создать копию: {exc}") from exc
    return {"name": info.name, "size_bytes": info.size}


@router.post("/backups/restore")
async def restore(body: RestoreIn, request: Request) -> dict:
    app = get_app(request)
    try:
        resolve_backup(app.settings.backup_dir, body.name)
    except BackupError as exc:
        raise HTTPException(400, str(exc)) from exc
    await app.db.dispose()
    try:
        safety = await asyncio.to_thread(restore_backup, app.settings.db_path, app.settings.backup_dir, body.name)
    except (BackupError, OSError) as exc:
        await app.db.init()
        raise HTTPException(400, f"Восстановление не выполнено: {exc}") from exc
    await app.db.init()  # повторная проверка целостности и миграции до актуальной схемы
    app.config.clear_cache()
    return {"ok": True, "safety_backup": safety.name or None,
            "note": "Сессия панели могла быть сброшена — при необходимости войдите снова."}


@router.delete("/backups/{name}")
async def delete_backup(name: str, request: Request) -> dict:
    app = get_app(request)
    try:
        path = resolve_backup(app.settings.backup_dir, name)
    except BackupError as exc:
        raise HTTPException(404, str(exc)) from exc
    path.unlink()
    return {"ok": True}


# ---------------------------------------------------------------- предпросмотр

class PreviewIn(BaseModel):
    text: str = Field("", max_length=4000)
    embed: EmbedConfig | None = None
    guild_id: str | None = Field(None, pattern=r"^\d{15,22}$")
    extra: dict[str, str | int] = Field(default_factory=dict)


@router.post("/preview")
async def preview(body: PreviewIn, request: Request) -> dict:
    """Подставляет переменные так же, как бот, — для предпросмотра в панели."""
    app = get_app(request)
    values = dict(SAMPLE_VALUES)
    if body.guild_id:
        dg = discord_guild(app, int(body.guild_id))
        if dg is not None and app.bot and app.bot.user:
            me = dg.me
            values.update(member_values(me, guild=dg))
            values["user"] = f"@{me.display_name}"
    values.update({k: v for k, v in body.extra.items() if k in TEMPLATE_VARIABLES})
    out: dict = {"text": render(body.text, values)[:2000], "embed": None}
    if body.embed and body.embed.enabled:
        e = body.embed
        out["embed"] = {"title": render(e.title, values)[:256], "description": render(e.description, values)[:4000],
                        "footer": render(e.footer, values)[:2048], "color": e.color, "image_url": e.image_url,
                        "show_avatar": e.show_avatar}
    return out


# ---------------------------------------------------------------- обновления

_restart_tasks: set[asyncio.Task] = set()


def _update_out(app) -> dict:  # noqa: ANN001
    st = app.updater.state
    return {
        "supported": st.supported, "reason": st.reason, "branch": st.branch, "upstream": st.upstream,
        "current": st.current, "remote": st.remote, "behind": st.behind, "ahead": st.ahead, "commits": st.commits,
        "last_check": iso(st.last_check), "last_error": st.last_error, "last_update": iso(st.last_update),
        "busy": st.busy, "restart_required": st.restart_required,
        "launcher": app.updater.launched_by_script(),
    }


@router.get("/update")
async def update_status(request: Request) -> dict:
    app = get_app(request)
    if app.updater.state.last_check is None and not app.updater.state.busy:
        await app.updater.check()
    return _update_out(app)


@router.post("/update/check")
async def update_check(request: Request) -> dict:
    app = get_app(request)
    await app.updater.check()
    return _update_out(app)


@router.post("/update/apply")
async def update_apply(request: Request) -> dict:
    app = get_app(request)
    try:
        version = await app.updater.apply()
    except UpdateError as exc:
        raise HTTPException(409, str(exc)) from None
    restarting = app.updater.launched_by_script() and app.request_restart is not None
    if restarting:
        async def later() -> None:
            await asyncio.sleep(1.5)  # даём панели получить ответ
            app.request_restart()

        task = asyncio.create_task(later())
        _restart_tasks.add(task)
        task.add_done_callback(_restart_tasks.discard)
    return {"ok": True, "version": version, "restarting": restarting, "status": _update_out(app)}
