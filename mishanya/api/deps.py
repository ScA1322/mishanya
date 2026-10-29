"""Общие зависимости и помощники API."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import HTTPException, Path, Request

if TYPE_CHECKING:
    import discord

    from ..core import App


def get_app(request: Request) -> "App":
    return request.app.state.mishanya


async def guild_id_dep(request: Request, guild_id: str = Path(pattern=r"^\d{15,22}$")) -> int:
    app = get_app(request)
    gid = int(guild_id)
    if await app.guilds.get(gid) is None:
        raise HTTPException(status_code=404, detail="Сервер не найден. Бот ещё не видел этот сервер.")
    return gid


def discord_guild(app: "App", guild_id: int) -> "discord.Guild | None":
    if app.bot is None or not app.bot.is_ready():
        return None
    return app.bot.get_guild(guild_id)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def user_label(app: "App", guild_id: int, user_id: int | None) -> str | None:
    """Имя пользователя из кэша бота, если доступно."""
    if user_id is None or app.bot is None:
        return None
    guild = app.bot.get_guild(guild_id)
    member = guild.get_member(user_id) if guild else None
    if member is not None:
        return member.display_name if member.display_name == member.name else f"{member.display_name} ({member.name})"
    user = app.bot.get_user(user_id)
    return str(user) if user else None
