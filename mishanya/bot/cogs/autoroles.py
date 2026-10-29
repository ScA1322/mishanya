"""Автоматическая выдача ролей при вступлении."""
from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ...schemas import AutorolesConfig
from ..client import MishanyaBot

log = logging.getLogger(__name__)


def role_problems(guild: discord.Guild, role_ids: list[str]) -> dict[str, str]:
    """Проверяет роли из настроек: существуют ли и может ли бот их выдать."""
    me = guild.me
    problems: dict[str, str] = {}
    for rid in role_ids:
        role = guild.get_role(int(rid))
        if role is None:
            problems[rid] = "роль удалена"
        elif role.managed or role.is_default():
            problems[rid] = "роль управляется интеграцией и не может быть выдана"
        elif role.position >= me.top_role.position:
            problems[rid] = "роль не ниже роли бота"
        elif not me.guild_permissions.manage_roles:
            problems[rid] = "у бота нет права «Управлять ролями»"
    return problems


class Autoroles(commands.Cog):
    def __init__(self, bot: MishanyaBot):
        self.bot = bot
        self.app = bot.app

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        cfg = await self.app.config.get_typed(member.guild.id, "autoroles", AutorolesConfig)
        if not cfg.enabled or str(member.id) in cfg.excluded_users:
            return
        role_ids = cfg.bot_roles if member.bot else cfg.roles
        if not role_ids:
            return
        if cfg.delay_seconds:
            await asyncio.sleep(cfg.delay_seconds)
            member = member.guild.get_member(member.id)  # type: ignore[assignment]
            if member is None:
                return  # успел выйти
        problems = role_problems(member.guild, role_ids)
        for rid, p in problems.items():
            log.warning("Сервер %s: автороль %s не выдана — %s", member.guild.id, rid, p)
        roles = [r for rid in role_ids if rid not in problems and (r := member.guild.get_role(int(rid)))
                 and r not in member.roles]
        if not roles:
            return
        try:
            await member.add_roles(*roles, reason="Mishanya: автоматическая роль")
        except discord.HTTPException as exc:
            log.warning("Сервер %s: не удалось выдать автороли участнику %s: %s", member.guild.id, member.id, exc)


async def setup(bot: MishanyaBot) -> None:
    await bot.add_cog(Autoroles(bot))
