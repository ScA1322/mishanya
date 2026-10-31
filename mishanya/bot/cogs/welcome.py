"""Приветствия, прощания и приветственное личное сообщение."""
from __future__ import annotations

import asyncio
import io
import logging

import discord
from discord.ext import commands

from ...schemas import GreetingMessage, WelcomeConfig
from ...services.cards import background_path, render_card
from ...services.templates import member_values, render
from ..client import MishanyaBot
from ..utils import channel_from_id, message_payload, safe_send

log = logging.getLogger(__name__)


def kind_file(kind: str) -> str:
    return "welcome" if kind == "приветствие" else "goodbye"


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
        values = member_values(member, channel=channel)
        payload = message_payload(msg.message, msg.embed, values, member.display_avatar.url)
        if msg.card.enabled:
            try:
                png = await self.render(member, msg, values)
                payload["file"] = discord.File(io.BytesIO(png), filename=f"{kind_file(kind)}.png")
                if "embed" in payload:
                    payload["embed"].set_image(url=f"attachment://{kind_file(kind)}.png")
            except Exception:  # noqa: BLE001
                log.exception("Сервер %s: не удалось нарисовать картинку «%s»", member.guild.id, kind)
        if payload:
            await safe_send(channel, **payload)

    async def render(self, member: discord.abc.User, msg: GreetingMessage, values: dict) -> bytes:
        try:
            avatar = await member.display_avatar.replace(size=256, static_format="png").read()
        except discord.HTTPException:
            avatar = None
        bg = background_path(self.app.settings.data_dir / "cards", msg.card.background_image)
        return await asyncio.to_thread(render_card, msg.card, values, avatar, bg)

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
