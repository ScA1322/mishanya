"""Серверы: список, обзор, каналы/роли для форм, настройки модулей, удаление данных."""
from __future__ import annotations

import discord
from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request
from pydantic import ValidationError

from ...schemas import MODULES, LevelingConfig, module_enabled
from ..auth import require_auth
from ..deps import discord_guild, get_app, guild_id_dep, iso, user_label
from ..health import guild_issues

router = APIRouter(prefix="/api/guilds", tags=["guilds"], dependencies=[Depends(require_auth)])


def validation_detail(exc: ValidationError) -> list[dict]:
    out = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", ()))
        msg = str(e.get("msg", "")).removeprefix("Value error, ")
        out.append({"loc": loc, "msg": msg})
    return out


@router.get("")
async def list_guilds(request: Request) -> list[dict]:
    app = get_app(request)
    result = []
    for g in await app.guilds.list():
        cfgs = await app.config.all_for_guild(g.id)
        dg = discord_guild(app, g.id)
        result.append({
            "id": str(g.id),
            "name": dg.name if dg else g.name,
            "icon_url": (dg.icon.url if dg and dg.icon else g.icon_url),
            "member_count": dg.member_count if dg else g.member_count,
            "present": g.present,
            "connected": dg is not None,
            "left_at": iso(g.left_at),
            "modules": {m: module_enabled(m, c) for m, c in cfgs.items()},
        })
    return result


@router.get("/{guild_id}")
async def overview(request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    app = get_app(request)
    g = await app.guilds.get(guild_id)
    assert g is not None
    dg = discord_guild(app, guild_id)
    cfgs = await app.config.all_for_guild(guild_id)
    _, warn_total = await app.moderation.list_warnings(guild_id, limit=1)
    _, case_total = await app.moderation.list_cases(guild_id, limit=1)
    events, _ = await app.eventlog.query(guild_id, limit=10)
    return {
        "id": str(g.id),
        "name": dg.name if dg else g.name,
        "icon_url": dg.icon.url if dg and dg.icon else g.icon_url,
        "member_count": dg.member_count if dg else g.member_count,
        "present": g.present,
        "connected": dg is not None,
        "owner_id": str(dg.owner_id) if dg else None,
        "created_at": iso(dg.created_at) if dg else None,
        "channels": len(dg.channels) if dg else None,
        "roles": len(dg.roles) if dg else None,
        "modules": {m: module_enabled(m, c) for m, c in cfgs.items()},
        "stats": {
            "leveling": await app.leveling.stats(guild_id),
            "active_warnings": warn_total,
            "cases": case_total,
            "custom_commands": await app.commands.count(guild_id),
            "events_7d": await app.eventlog.counts_by_type(guild_id, 7),
        },
        "recent_events": [{"id": e.id, "type": e.event_type, "summary": e.summary, "created_at": iso(e.created_at)}
                          for e in events],
        "issues": await guild_issues(app, guild_id),
    }


@router.get("/{guild_id}/discord")
async def discord_resources(request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    """Каналы и роли сервера для выпадающих списков в настройках."""
    app = get_app(request)
    dg = discord_guild(app, guild_id)
    if dg is None:
        return {"connected": False, "channels": [], "categories": [], "roles": [], "bot_top_position": None}
    me = dg.me
    channels = []
    for ch in dg.channels:
        if isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel, discord.StageChannel)):
            perms = ch.permissions_for(me)
            channels.append({
                "id": str(ch.id), "name": ch.name, "type": str(ch.type), "position": ch.position,
                "category": ch.category.name if ch.category else None,
                "can_send": perms.view_channel and perms.send_messages,
                "text": isinstance(ch, (discord.TextChannel, discord.VoiceChannel)),
            })
    channels.sort(key=lambda c: ((c["category"] or ""), c["position"]))
    categories = [{"id": str(c.id), "name": c.name} for c in sorted(dg.categories, key=lambda c: c.position)]
    roles = [{
        "id": str(r.id), "name": r.name, "color": f"#{r.color.value:06x}" if r.color.value else None,
        "position": r.position, "managed": r.managed,
        "assignable": not r.managed and r.position < me.top_role.position,
    } for r in sorted(dg.roles, key=lambda r: -r.position) if not r.is_default()]
    return {"connected": True, "channels": channels, "categories": categories, "roles": roles,
            "bot_top_position": me.top_role.position}


@router.get("/{guild_id}/members/search")
async def search_members(request: Request, q: str = "", guild_id: int = Depends(guild_id_dep)) -> list[dict]:
    app = get_app(request)
    dg = discord_guild(app, guild_id)
    if dg is None or not q.strip():
        return []
    q = q.strip().lower()
    res = []
    for m in dg.members:
        if q in m.name.lower() or q in m.display_name.lower() or q == str(m.id):
            res.append({"id": str(m.id), "name": m.name, "display_name": m.display_name,
                        "avatar_url": m.display_avatar.url, "bot": m.bot})
            if len(res) >= 20:
                break
    return res


@router.get("/{guild_id}/config/{module}")
async def get_config(request: Request, module: str = Path(max_length=32),
                     guild_id: int = Depends(guild_id_dep)) -> dict:
    if module not in MODULES:
        raise HTTPException(404, "Неизвестный модуль")
    return (await get_app(request).config.get(guild_id, module)).model_dump(mode="json")


@router.put("/{guild_id}/config/{module}")
async def put_config(request: Request, module: str = Path(max_length=32), body: dict = Body(...),
                     guild_id: int = Depends(guild_id_dep)) -> dict:
    app = get_app(request)
    if module not in MODULES:
        raise HTTPException(404, "Неизвестный модуль")
    try:
        cfg = MODULES[module].model_validate(body)
    except ValidationError as exc:
        raise HTTPException(422, validation_detail(exc)) from None
    old = await app.config.get(guild_id, module)
    await app.config.set(guild_id, module, cfg)
    result: dict = {"config": cfg.model_dump(mode="json")}
    if isinstance(cfg, LevelingConfig) and isinstance(old, LevelingConfig):
        formula_keys = ("formula_a", "formula_b", "formula_c", "max_level")
        if any(getattr(cfg, k) != getattr(old, k) for k in formula_keys):
            result["recalculated"] = await app.leveling.recalculate_levels(guild_id, cfg)
    return result


@router.delete("/{guild_id}")
async def delete_guild_data(request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    app = get_app(request)
    if discord_guild(app, guild_id) is not None:
        raise HTTPException(409, "Бот всё ещё на этом сервере. Сначала удалите бота с сервера.")
    await app.guilds.delete_data(guild_id)
    app.config.invalidate_guild(guild_id)
    return {"ok": True}


@router.get("/{guild_id}/reminders")
async def reminders(request: Request, guild_id: int = Depends(guild_id_dep)) -> list[dict]:
    app = get_app(request)
    return [{"id": r.id, "user_id": str(r.user_id), "user_name": user_label(app, guild_id, r.user_id),
             "text": r.text, "due_at": iso(r.due_at), "status": r.status, "error": r.error,
             "channel_id": str(r.channel_id) if r.channel_id else None}
            for r in await app.reminders.list_for_guild(guild_id)]


@router.delete("/{guild_id}/reminders/{reminder_id}")
async def cancel_reminder(request: Request, reminder_id: int, guild_id: int = Depends(guild_id_dep)) -> dict:
    app = get_app(request)
    rows = await app.reminders.list_for_guild(guild_id, limit=10_000)
    if not any(r.id == reminder_id for r in rows):
        raise HTTPException(404, "Напоминание не найдено")
    if not await app.reminders.cancel(reminder_id):
        raise HTTPException(409, "Напоминание уже выполнено или отменено")
    return {"ok": True}
