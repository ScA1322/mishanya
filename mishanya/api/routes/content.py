"""API модерации (предупреждения, случаи), пользовательских команд и журналов событий."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError

from ...schemas import LOG_EVENTS, ModerationConfig
from ...services.custom_commands import CommandConflict, CustomCommandIn
from ..auth import require_auth
from ..deps import get_app, guild_id_dep, iso, user_label
from .guilds import validation_detail

router = APIRouter(prefix="/api/guilds/{guild_id}", tags=["content"], dependencies=[Depends(require_auth)])
PANEL_MODERATOR_ID = 0  # действия из панели записываются с moderator_id=0


# ---------------------------------------------------------------- предупреждения и случаи

@router.get("/warnings")
async def warnings(request: Request, guild_id: int = Depends(guild_id_dep),
                   user_id: str | None = Query(None, pattern=r"^\d{15,22}$"), include_inactive: bool = False,
                   offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)) -> dict:
    app = get_app(request)
    cfg = await app.config.get_typed(guild_id, "moderation", ModerationConfig)
    rows, total = await app.moderation.list_warnings(guild_id, int(user_id) if user_id else None,
                                                     include_inactive, cfg, limit, offset)
    return {"total": total, "items": [{
        "id": w.id, "user_id": str(w.user_id), "user_name": user_label(app, guild_id, w.user_id),
        "moderator_id": str(w.moderator_id),
        "moderator_name": "Панель" if w.moderator_id == PANEL_MODERATOR_ID else user_label(app, guild_id, w.moderator_id),
        "reason": w.reason, "active": w.active, "created_at": iso(w.created_at), "removed_at": iso(w.removed_at),
    } for w in rows]}


@router.delete("/warnings/{warning_id}")
async def remove_warning(warning_id: int, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    app = get_app(request)
    if not await app.moderation.remove_warning(guild_id, warning_id, PANEL_MODERATOR_ID):
        raise HTTPException(404, "Активное предупреждение не найдено")
    await app.moderation.add_case(guild_id, "unwarn", None, "", None, "Панель Mishanya",
                                  f"Снято предупреждение #{warning_id}")
    return {"ok": True}


class ClearWarningsIn(BaseModel):
    user_id: str = Field(pattern=r"^\d{15,22}$")


@router.post("/warnings/clear")
async def clear_warnings(body: ClearWarningsIn, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    app = get_app(request)
    n = await app.moderation.clear_warnings(guild_id, int(body.user_id), PANEL_MODERATOR_ID)
    if n:
        await app.moderation.add_case(guild_id, "clearwarnings", int(body.user_id),
                                      user_label(app, guild_id, int(body.user_id)) or body.user_id,
                                      None, "Панель Mishanya", f"Снято предупреждений: {n}")
    return {"removed": n}


@router.get("/cases")
async def cases(request: Request, guild_id: int = Depends(guild_id_dep),
                user_id: str | None = Query(None, pattern=r"^\d{15,22}$"),
                action: str | None = Query(None, max_length=32),
                offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)) -> dict:
    app = get_app(request)
    rows, total = await app.moderation.list_cases(guild_id, int(user_id) if user_id else None, action or None,
                                                  limit, offset)
    return {"total": total, "items": [{
        "id": c.id, "case_number": c.case_number, "action": c.action,
        "target_id": str(c.target_id) if c.target_id else None, "target_name": c.target_name,
        "moderator_id": str(c.moderator_id) if c.moderator_id else None, "moderator_name": c.moderator_name,
        "reason": c.reason, "duration_seconds": c.duration_seconds, "created_at": iso(c.created_at),
    } for c in rows]}


# ---------------------------------------------------------------- пользовательские команды

def _cmd_out(c) -> dict:  # noqa: ANN001
    return {"id": c.id, "name": c.name, "description": c.description, "response": c.response, "embed": c.embed,
            "enabled": c.enabled, "allowed_roles": c.allowed_roles, "allowed_channels": c.allowed_channels,
            "cooldown_seconds": c.cooldown_seconds, "delete_trigger": c.delete_trigger, "send_dm": c.send_dm,
            "uses": c.uses, "created_at": iso(c.created_at), "updated_at": iso(c.updated_at)}


def _parse_cmd(body: dict) -> CustomCommandIn:
    try:
        return CustomCommandIn.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(422, validation_detail(exc)) from None


@router.get("/commands")
async def list_commands(request: Request, guild_id: int = Depends(guild_id_dep)) -> list[dict]:
    return [_cmd_out(c) for c in await get_app(request).commands.list(guild_id)]


@router.post("/commands")
async def create_command(body: dict, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    data = _parse_cmd(body)
    try:
        return _cmd_out(await get_app(request).commands.create(guild_id, data))
    except CommandConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/commands/{command_id}")
async def update_command(command_id: int, body: dict, request: Request,
                         guild_id: int = Depends(guild_id_dep)) -> dict:
    data = _parse_cmd(body)
    try:
        cmd = await get_app(request).commands.update(guild_id, command_id, data)
    except CommandConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if cmd is None:
        raise HTTPException(404, "Команда не найдена")
    return _cmd_out(cmd)


@router.delete("/commands/{command_id}")
async def delete_command(command_id: int, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    if not await get_app(request).commands.delete(guild_id, command_id):
        raise HTTPException(404, "Команда не найдена")
    return {"ok": True}


# ---------------------------------------------------------------- журналы событий

@router.get("/logs")
async def logs(request: Request, guild_id: int = Depends(guild_id_dep),
               type: str | None = Query(None, max_length=32), search: str = Query("", max_length=100),  # noqa: A002
               offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)) -> dict:
    if type and type not in LOG_EVENTS:
        raise HTTPException(422, "Неизвестный тип события")
    app = get_app(request)
    rows, total = await app.eventlog.query(guild_id, type or None, search.strip(), limit, offset)
    return {"total": total, "items": [{
        "id": e.id, "type": e.event_type, "summary": e.summary,
        "user_id": str(e.user_id) if e.user_id else None, "user_name": user_label(app, guild_id, e.user_id),
        "channel_id": str(e.channel_id) if e.channel_id else None, "details": e.details,
        "created_at": iso(e.created_at),
    } for e in rows]}


@router.delete("/logs")
async def clear_logs(request: Request, guild_id: int = Depends(guild_id_dep),
                     type: str | None = Query(None, max_length=32)) -> dict:  # noqa: A002
    if type and type not in LOG_EVENTS:
        raise HTTPException(422, "Неизвестный тип события")
    return {"removed": await get_app(request).eventlog.clear(guild_id, type or None)}
