"""Пользовательские команды: вызов через префикс (например, !правила) или /cmd."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ...db.models import CustomCommand
from ...schemas import CustomCommandsConfig, EmbedConfig
from ...services.templates import member_values
from ..client import MishanyaBot
from ..utils import UserFacingError, message_payload, require_guild, safe_send

log = logging.getLogger(__name__)


class CustomCommands(commands.Cog):
    def __init__(self, bot: MishanyaBot):
        self.bot = bot
        self.app = bot.app

    def _payload(self, cmd: CustomCommand, member: discord.Member, channel, args: str) -> dict:  # noqa: ANN001
        embed = EmbedConfig.model_validate(cmd.embed) if cmd.embed else None
        return message_payload(cmd.response, embed, member_values(member, channel=channel, args=args),
                               member.display_avatar.url)

    async def handle_message(self, message: discord.Message) -> None:
        assert message.guild is not None and isinstance(message.author, discord.Member)
        cfg = await self.app.config.get_typed(message.guild.id, "custom_commands", CustomCommandsConfig)
        content = message.content or ""
        if not cfg.enabled or not content.startswith(cfg.prefix):
            return
        body = content[len(cfg.prefix):]
        name, _, args = body.partition(" ")
        if not name:
            return
        cmd = await self.app.commands.get_by_name(message.guild.id, name)
        if cmd is None or not cmd.enabled:
            return
        member = message.author
        if self.app.commands.check_access(cmd, message.channel.id, [r.id for r in member.roles]):
            return  # молча игнорируем, чтобы не засорять чат
        if self.app.commands.check_cooldown(cmd, member.id) > 0:
            return
        payload = self._payload(cmd, member, message.channel, args.strip()[:500])
        if not payload:
            return
        if cmd.send_dm:
            try:
                await member.send(**payload)
            except discord.HTTPException:
                await safe_send(message.channel, content=f"{member.mention}, не удалось отправить личное сообщение.",
                                delete_after=10)
        else:
            await safe_send(message.channel, **payload)
        if cmd.delete_trigger:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        await self.app.commands.increment_uses(cmd.id)

    async def _autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        cmds = await self.app.commands.list(interaction.guild.id)
        current = current.lower()
        return [app_commands.Choice(name=f"{c.name} — {c.description}"[:100] if c.description else c.name,
                                    value=c.name)
                for c in cmds if c.enabled and current in c.name][:25]

    @app_commands.command(name="cmd", description="Выполнить пользовательскую команду сервера")
    @app_commands.describe(name="Название команды", args="Аргументы (подставляются в {args})")
    @app_commands.autocomplete(name=_autocomplete)
    @app_commands.guild_only()
    async def cmd(self, interaction: discord.Interaction, name: str, args: str = "") -> None:
        guild, member = require_guild(interaction)
        cfg = await self.app.config.get_typed(guild.id, "custom_commands", CustomCommandsConfig)
        if not cfg.enabled:
            raise UserFacingError("Пользовательские команды отключены на этом сервере.")
        cmd = await self.app.commands.get_by_name(guild.id, name)
        if cmd is None:
            raise UserFacingError(f"Команда «{name}» не найдена.")
        denied = self.app.commands.check_access(cmd, interaction.channel_id or 0, [r.id for r in member.roles])
        if denied:
            raise UserFacingError(denied)
        wait = self.app.commands.check_cooldown(cmd, member.id)
        if wait > 0:
            raise UserFacingError(f"Подождите {wait:.0f} с перед повторным вызовом.")
        payload = self._payload(cmd, member, interaction.channel, args[:500])
        if not payload:
            raise UserFacingError("Ответ команды пуст.")
        if cmd.send_dm:
            try:
                await member.send(**payload)
            except discord.HTTPException:
                raise UserFacingError("Не удалось отправить личное сообщение — откройте ЛС от участников сервера.") from None
            await interaction.response.send_message("Ответ отправлен в личные сообщения.", ephemeral=True)
        else:
            await interaction.response.send_message(
                **payload, allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True))
        await self.app.commands.increment_uses(cmd.id)


async def setup(bot: MishanyaBot) -> None:
    await bot.add_cog(CustomCommands(bot))
