"""API системы уровней: таблица лидеров, изменение XP, награды, импорт/экспорт."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ...schemas import LevelingConfig, Snowflake
from ...services.leveling import ImportPayload, level_from_xp
from ..auth import require_auth
from ..deps import discord_guild, get_app, guild_id_dep, iso

router = APIRouter(prefix="/api/guilds/{guild_id}", tags=["leveling"], dependencies=[Depends(require_auth)])
UserId = Path(pattern=r"^\d{15,22}$")


async def _cfg(app, guild_id: int) -> LevelingConfig:  # noqa: ANN001
    return await app.config.get_typed(guild_id, "leveling", LevelingConfig)


async def _sync(app, guild_id: int, user_id: int) -> list[str]:  # noqa: ANN001
    cog = app.bot.get_cog("Leveling") if app.bot else None
    if cog is None:
        return ["Бот не запущен — роли-награды будут выданы при следующем повышении уровня."]
    return await cog.sync_member_by_id(guild_id, user_id)


@router.get("/leaderboard")
async def leaderboard(request: Request, guild_id: int = Depends(guild_id_dep),
                      offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                      search: str = Query("", max_length=100), include_left: bool = False) -> dict:
    app = get_app(request)
    cfg = await _cfg(app, guild_id)
    rows, total = await app.leveling.leaderboard(guild_id, limit, offset, include_left, search.strip())
    dg = discord_guild(app, guild_id)
    items = []
    for r in rows:
        prog = level_from_xp(r.xp, cfg)
        rank = await app.leveling.rank_of(guild_id, r.user_id, include_left) if search else None
        member = dg.get_member(r.user_id) if dg else None
        items.append({
            "user_id": str(r.user_id),
            "username": member.name if member else r.username,
            "display_name": member.display_name if member else r.username,
            "avatar_url": member.display_avatar.url if member else r.avatar_url,
            "xp": r.xp, "level": prog.level, "xp_into_level": prog.xp_into_level, "xp_needed": prog.xp_needed,
            "percent": prog.percent, "message_count": r.message_count, "left": r.left_at is not None,
            "last_xp_at": iso(r.last_xp_at), "rank": rank,
        })
    if not search:
        for i, it in enumerate(items):
            it["rank"] = offset + i + 1
    return {"total": total, "items": items}


class XpChange(BaseModel):
    mode: Literal["set", "add", "level"]
    value: int = Field(ge=-10**9, le=10**12)


@router.put("/members/{user_id}/xp")
async def change_xp(body: XpChange, request: Request, user_id: str = UserId,
                    guild_id: int = Depends(guild_id_dep)) -> dict:
    app = get_app(request)
    cfg = await _cfg(app, guild_id)
    uid = int(user_id)
    dg = discord_guild(app, guild_id)
    member = dg.get_member(uid) if dg else None
    if member is not None and member.bot:
        raise HTTPException(400, "Боты не участвуют в системе уровней")
    name = str(member) if member else None
    if body.mode == "set":
        if body.value < 0:
            raise HTTPException(422, "XP не может быть отрицательным")
        res = await app.leveling.set_xp(guild_id, uid, body.value, cfg, name)
    elif body.mode == "add":
        res = await app.leveling.add_xp(guild_id, uid, body.value, cfg, name)
    else:
        if not 0 <= body.value <= cfg.max_level:
            raise HTTPException(422, f"Уровень должен быть от 0 до {cfg.max_level}")
        res = await app.leveling.set_level(guild_id, uid, body.value, cfg, name)
    problems = await _sync(app, guild_id, uid)
    return {"xp": res.total_xp, "level": res.new_level, "problems": problems}


@router.delete("/members/{user_id}/xp")
async def reset_member(request: Request, user_id: str = UserId, guild_id: int = Depends(guild_id_dep)) -> dict:
    app = get_app(request)
    removed = await app.leveling.reset_user(guild_id, int(user_id))
    problems = await _sync(app, guild_id, int(user_id)) if removed else []
    return {"removed": removed, "problems": problems}


class ResetIn(BaseModel):
    confirm: str


@router.post("/leveling/reset")
async def reset_guild(body: ResetIn, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    if body.confirm != "СБРОС":
        raise HTTPException(400, "Для подтверждения введите слово СБРОС")
    return {"removed": await get_app(request).leveling.reset_guild(guild_id)}


@router.get("/leveling/export")
async def export(request: Request, guild_id: int = Depends(guild_id_dep)) -> JSONResponse:
    data = await get_app(request).leveling.export(guild_id)
    return JSONResponse(data, headers={
        "Content-Disposition": f'attachment; filename="mishanya-levels-{guild_id}.json"'})


@router.post("/leveling/import")
async def import_(body: ImportPayload, request: Request, guild_id: int = Depends(guild_id_dep),
                  mode: Literal["merge", "replace"] = "merge") -> dict:
    app = get_app(request)
    try:
        count = await app.leveling.import_(guild_id, body, await _cfg(app, guild_id), mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"imported": count}


# ---------------------------------------------------------------- награды

class RewardIn(BaseModel):
    level: int = Field(ge=1, le=5000)
    role_id: Snowflake
    enabled: bool = True


class RewardPatch(BaseModel):
    level: int | None = Field(None, ge=1, le=5000)
    enabled: bool | None = None


def _reward_out(r, dg) -> dict:  # noqa: ANN001
    role = dg.get_role(r.role_id) if dg else None
    problem = None
    if dg is not None:
        if role is None:
            problem = "Роль удалена"
        elif role.position >= dg.me.top_role.position or role.managed:
            problem = "Бот не может выдать эту роль (роль выше роли бота или управляется интеграцией)"
    return {"id": r.id, "level": r.level, "role_id": str(r.role_id), "enabled": r.enabled,
            "role_name": role.name if role else None,
            "role_color": f"#{role.color.value:06x}" if role and role.color.value else None, "problem": problem}


@router.get("/rewards")
async def rewards(request: Request, guild_id: int = Depends(guild_id_dep)) -> list[dict]:
    app = get_app(request)
    dg = discord_guild(app, guild_id)
    return [_reward_out(r, dg) for r in await app.leveling.list_rewards(guild_id)]


@router.post("/rewards")
async def add_reward(body: RewardIn, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    app = get_app(request)
    dg = discord_guild(app, guild_id)
    if dg is not None and dg.get_role(int(body.role_id)) is None:
        raise HTTPException(422, "Такой роли нет на сервере")
    try:
        r = await app.leveling.add_reward(guild_id, body.level, int(body.role_id), body.enabled)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _reward_out(r, dg)


@router.patch("/rewards/{reward_id}")
async def patch_reward(reward_id: int, body: RewardPatch, request: Request,
                       guild_id: int = Depends(guild_id_dep)) -> dict:
    app = get_app(request)
    try:
        r = await app.leveling.update_reward(guild_id, reward_id, level=body.level, enabled=body.enabled)
    except Exception as exc:  # noqa: BLE001 — нарушение уникальности
        raise HTTPException(409, "Такая награда уже существует") from exc
    if r is None:
        raise HTTPException(404, "Награда не найдена")
    return _reward_out(r, discord_guild(app, guild_id))


@router.delete("/rewards/{reward_id}")
async def delete_reward(reward_id: int, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    if not await get_app(request).leveling.delete_reward(guild_id, reward_id):
        raise HTTPException(404, "Награда не найдена")
    return {"ok": True}
