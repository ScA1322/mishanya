"""Приветствия, прощания и приветственное личное сообщение."""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from ...schemas import GreetingMessage, WelcomeConfig
from ...services.templates import member_values, render
from ..client import MishanyaBot
from ..utils import channel_from_id, message_payload, safe_send

log = logging.getLogger(__name__)


class Welcome(commands.Cog):
    def __init__(self, bot: MishanyaBot):
        self.bot = bot
        self.app = bot.app

    async def _send(self, member: discord.Member, msg: GreetingMessage, kind: str) -> None:
        if not msg.enabled:
            return
        channel = channel_from_id(member.guild, msg.channel_id)
        if channel is None:
            log.warning("Сервер %s: канал для сообщения «%s» не найден или недоступен", member.guild.id, kind)
            return
        payload = message_payload(msg.message, msg.embed, member_values(member, channel=channel),
                                  member.display_avatar.url)
        if payload:
            await safe_send(channel, **payload)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        cfg = await self.app.config.get_typed(member.guild.id, "welcome", WelcomeConfig)
        await self._send(member, cfg.join, "приветствие")
        if cfg.dm_enabled and cfg.dm_message.strip() and not member.bot:
            try:
                await member.send(render(cfg.dm_message, member_values(member))[:2000])
            except discord.HTTPException:
                pass  # участник закрыл личные сообщения

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        cfg = await self.app.config.get_typed(member.guild.id, "welcome", WelcomeConfig)
        await self._send(member, cfg.leave, "прощание")


async def setup(bot: MishanyaBot) -> None:
    await bot.add_cog(Welcome(bot))
