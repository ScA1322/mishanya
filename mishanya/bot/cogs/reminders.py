"""Напоминания: /remind, /reminders, /unremind и фоновая доставка."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ...db import utcnow
from ...schemas import UtilityConfig
from ...services.reminders import format_delta, parse_duration
from ..client import MishanyaBot
from ..utils import COLOR_INFO, UserFacingError, fmt_dt, ok_embed, respond

log = logging.getLogger(__name__)
LATE_THRESHOLD_SECONDS = 120


class Reminders(commands.Cog):
    def __init__(self, bot: MishanyaBot):
        self.bot = bot
        self.app = bot.app
        self.deliver_loop.start()

    async def cog_unload(self) -> None:
        self.deliver_loop.cancel()

    @app_commands.command(name="remind", description="Напомнить через заданное время")
    @app_commands.describe(when="Через сколько: 10m, 1h30m, 2д, 1w", text="Текст напоминания",
                           private="Прислать в личные сообщения вместо канала")
    async def remind(self, interaction: discord.Interaction, when: str,
                     text: app_commands.Range[str, 1, 1000], private: bool = False) -> None:
        if interaction.guild:
            cfg = await self.app.config.get_typed(interaction.guild.id, "utility", UtilityConfig)
            if not cfg.reminders_enabled:
                raise UserFacingError("Напоминания отключены на этом сервере.")
            await self.app.guilds.ensure(interaction.guild.id, interaction.guild.name)
        try:
            delta = parse_duration(when)
        except ValueError as exc:
            raise UserFacingError(str(exc)) from None
        due = utcnow() + delta
        try:
            r = await self.app.reminders.create(
                interaction.user.id, text, due, interaction.guild.id if interaction.guild else None,
                None if private or interaction.guild is None else interaction.channel_id)
        except ValueError as exc:
            raise UserFacingError(str(exc)) from None
        await respond(interaction, embed=ok_embed(
            f"Напомню {fmt_dt(due, 'R')} ({fmt_dt(due)}).\nНомер напоминания: `{r.id}`"), ephemeral=True)

    @app_commands.command(name="reminders", description="Ваши активные напоминания")
    async def reminders(self, interaction: discord.Interaction) -> None:
        items = await self.app.reminders.list_for_user(interaction.user.id)
        lines = [f"`{r.id}` {fmt_dt(r.due_at, 'R')}: {r.text[:80]}" for r in items]
        await respond(interaction, embed=discord.Embed(title="⏰ Ваши напоминания", color=COLOR_INFO,
                                                       description="\n".join(lines) or "Активных напоминаний нет."),
                      ephemeral=True)

    @app_commands.command(name="unremind", description="Отменить напоминание")
    @app_commands.describe(reminder_id="Номер из /reminders")
    async def unremind(self, interaction: discord.Interaction, reminder_id: int) -> None:
        if not await self.app.reminders.cancel(reminder_id, interaction.user.id):
            raise UserFacingError("Активное напоминание с таким номером не найдено.")
        await respond(interaction, embed=ok_embed(f"Напоминание `{reminder_id}` отменено."), ephemeral=True)

    @tasks.loop(seconds=20)
    async def deliver_loop(self) -> None:
        try:
            await self._deliver_due()
        except Exception:  # noqa: BLE001
            log.exception("Ошибка цикла напоминаний")

    async def _deliver_due(self) -> None:
        for r in await self.app.reminders.due():
            late = (utcnow() - r.due_at).total_seconds()
            embed = discord.Embed(title="⏰ Напоминание", description=r.text, color=COLOR_INFO)
            embed.add_field(name="Создано", value=fmt_dt(r.created_at, "R"))
            if late > LATE_THRESHOLD_SECONDS:
                embed.set_footer(text=f"Доставлено с опозданием на {format_delta(utcnow() - r.due_at)}: "
                                      "бот был выключен")
            error = await self._deliver(r.user_id, r.guild_id, r.channel_id, embed)
            await self.app.reminders.mark(r.id, "failed" if error else "sent", error)
            if error:
                log.warning("Напоминание %s не доставлено: %s", r.id, error)

    async def _deliver(self, user_id: int, guild_id: int | None, channel_id: int | None,
                       embed: discord.Embed) -> str | None:
        if channel_id and guild_id:
            guild = self.bot.get_guild(guild_id)
            channel = guild.get_channel_or_thread(channel_id) if guild else None
            if channel is not None and hasattr(channel, "send"):
                try:
                    await channel.send(content=f"<@{user_id}>", embed=embed,  # type: ignore[union-attr]
                                       allowed_mentions=discord.AllowedMentions(users=True))
                    return None
                except discord.HTTPException:
                    pass  # пробуем личные сообщения
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            await user.send(embed=embed)
            return None
        except discord.HTTPException as exc:
            return f"не удалось доставить ни в канал, ни в ЛС: {exc.status}"

    @deliver_loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: MishanyaBot) -> None:
    await bot.add_cog(Reminders(bot))
