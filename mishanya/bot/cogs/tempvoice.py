"""Голосовые комнаты: заходишь в канал-создатель — бот создаёт тебе комнату и удаляет её, когда все вышли."""
from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from ...db.models import TempVoiceChannel
from ...schemas import TempVoiceConfig, VoiceHub
from ...services.templates import member_values, render
from ..client import MishanyaBot
from ..utils import UserFacingError, ok_embed, require_guild, respond

log = logging.getLogger(__name__)
CREATE_COOLDOWN = 10  # секунд между созданием комнат одним участником
RENAME_TIMEOUT = 8  # Discord разрешает 2 переименования канала за 10 минут — дольше не ждём

OWNER_PERMS = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, manage_channels=True,
                                          move_members=True)
BOT_PERMS = discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True, move_members=True)


class TempVoice(commands.Cog):
    def __init__(self, bot: MishanyaBot):
        self.bot = bot
        self.app = bot.app
        self._cooldown: dict[tuple[int, int], float] = {}
        self._creating: set[tuple[int, int]] = set()

    async def _cfg(self, guild_id: int) -> TempVoiceConfig:
        return await self.app.config.get_typed(guild_id, "tempvoice", TempVoiceConfig)

    # ------------------------------------------------------------ события

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Убирает комнаты, опустевшие, пока бот был выключен."""
        for rec in await self.app.tempvoice.list():
            guild = self.bot.get_guild(rec.guild_id)
            if guild is None:
                continue
            channel = guild.get_channel(rec.channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                await self.app.tempvoice.remove(rec.channel_id)
            elif not channel.members:
                await self._delete(channel, "комната пуста после перезапуска бота")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState) -> None:
        if before.channel == after.channel:
            return
        try:
            if isinstance(before.channel, discord.VoiceChannel):
                await self._cleanup(before.channel)
            if isinstance(after.channel, discord.VoiceChannel) and not member.bot:
                await self._on_join(member, after.channel)
        except discord.HTTPException as exc:
            log.warning("Сервер %s: голосовые комнаты — ошибка Discord API: %s", member.guild.id, exc)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if await self.app.tempvoice.get(channel.id) is not None:
            await self.app.tempvoice.remove(channel.id)

    async def _on_join(self, member: discord.Member, channel: discord.VoiceChannel) -> None:
        cfg = await self._cfg(member.guild.id)
        if not cfg.enabled:
            return
        hub = next((h for h in cfg.hubs if h.channel_id == str(channel.id)), None)
        if hub is None:
            return
        key = (member.guild.id, member.id)
        if key in self._creating:
            return
        # Уже есть своя комната — просто переносим туда, а не плодим новые.
        existing = await self.app.tempvoice.owned_by(member.guild.id, member.id)
        if existing is not None:
            room = member.guild.get_channel(existing.channel_id)
            if isinstance(room, discord.VoiceChannel):
                await member.move_to(room, reason="Возврат в свою голосовую комнату")
                return
            await self.app.tempvoice.remove(existing.channel_id)
        now = time.monotonic()
        if now - self._cooldown.get(key, 0) < CREATE_COOLDOWN:
            return
        self._cooldown[key] = now
        self._creating.add(key)
        try:
            await self._create(member, channel, hub)
        finally:
            self._creating.discard(key)

    async def _create(self, member: discord.Member, hub_channel: discord.VoiceChannel, hub: VoiceHub) -> None:
        guild = member.guild
        me = guild.me
        if not (me.guild_permissions.manage_channels and me.guild_permissions.move_members):
            log.warning("Сервер %s: для голосовых комнат боту нужны права «Управлять каналами» и «Перемещать "
                        "участников»", guild.id)
            return
        category = guild.get_channel(int(hub.category_id)) if hub.category_id else hub_channel.category
        if not isinstance(category, discord.CategoryChannel):
            category = hub_channel.category
        overwrites: dict = dict(category.overwrites) if category else {}
        overwrites[me] = BOT_PERMS
        if hub.owner_manage:
            overwrites[member] = OWNER_PERMS
        name = render(hub.name_template, member_values(member, guild=guild)).strip()[:100] or f"Комната {member.name}"
        await self.app.guilds.ensure(guild.id, guild.name)
        room = await guild.create_voice_channel(
            name, category=category, overwrites=overwrites, user_limit=hub.user_limit,
            bitrate=min(hub_channel.bitrate, int(guild.bitrate_limit)),
            reason=f"Голосовая комната для {member}")
        await self.app.tempvoice.add(guild.id, room.id, member.id, hub_channel.id)
        try:
            await member.move_to(room, reason="Голосовая комната")
        except discord.HTTPException:
            # участник успел выйти из канала-создателя — комната не нужна
            await self._delete(room, "владелец не дождался переноса")
            return
        log.info("Сервер %s: создана голосовая комната «%s» для %s", guild.id, room.name, member)

    async def _cleanup(self, channel: discord.VoiceChannel) -> None:
        if channel.members:
            return
        if await self.app.tempvoice.get(channel.id) is not None:
            await self._delete(channel, "все вышли")

    async def _delete(self, channel: discord.VoiceChannel, why: str) -> None:
        await self.app.tempvoice.remove(channel.id)
        try:
            await channel.delete(reason=f"Голосовая комната удалена: {why}")
        except discord.NotFound:
            pass
        except discord.HTTPException as exc:
            log.warning("Не удалось удалить голосовую комнату %s: %s", channel.id, exc)

    # ------------------------------------------------------------ панель

    async def create_hub(self, guild_id: int, category_id: int | None, name: str) -> discord.VoiceChannel:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise UserFacingError("Бот не подключён к этому серверу.")
        if not guild.me.guild_permissions.manage_channels:
            raise UserFacingError("У бота нет права «Управлять каналами».")
        category = guild.get_channel(category_id) if category_id else None
        if category_id and not isinstance(category, discord.CategoryChannel):
            raise UserFacingError("Категория не найдена.")
        return await guild.create_voice_channel(name, category=category,  # type: ignore[arg-type]
                                                reason="Канал-создатель голосовых комнат (панель Mishanya)")

    # ------------------------------------------------------------ команды владельца

    async def _my_room(self, interaction: discord.Interaction,
                       allow_claim: bool = False) -> tuple[discord.VoiceChannel, TempVoiceChannel, discord.Member]:
        guild, member = require_guild(interaction)
        channel = member.voice.channel if member.voice else None
        rec = await self.app.tempvoice.get(channel.id) if isinstance(channel, discord.VoiceChannel) else None
        if rec is None or not isinstance(channel, discord.VoiceChannel):
            raise UserFacingError("Зайдите в свою голосовую комнату, созданную ботом.")
        if not allow_claim and rec.owner_id != member.id and not member.guild_permissions.manage_channels:
            raise UserFacingError(f"Управлять комнатой может только её владелец — <@{rec.owner_id}>.")
        return channel, rec, member

    voice = app_commands.Group(name="voice", description="Управление своей голосовой комнатой", guild_only=True)

    @voice.command(name="rename", description="Переименовать свою голосовую комнату")
    @app_commands.describe(name="Новое название")
    async def v_rename(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 100]) -> None:
        channel, _, _ = await self._my_room(interaction)
        await interaction.response.defer(ephemeral=True)
        try:
            await asyncio.wait_for(channel.edit(name=name, reason="Переименование владельцем"), RENAME_TIMEOUT)
        except asyncio.TimeoutError:
            raise UserFacingError("Discord разрешает переименовывать канал не чаще 2 раз за 10 минут. "
                                  "Попробуйте позже.") from None
        await respond(interaction, embed=ok_embed(f"Комната переименована: **{name}**"), ephemeral=True)

    @voice.command(name="limit", description="Ограничить число участников в комнате")
    @app_commands.describe(count="Максимум участников, 0 — без ограничения")
    async def v_limit(self, interaction: discord.Interaction, count: app_commands.Range[int, 0, 99]) -> None:
        channel, _, _ = await self._my_room(interaction)
        await channel.edit(user_limit=count, reason="Лимит от владельца комнаты")
        await respond(interaction, embed=ok_embed(f"Лимит: **{count or 'без ограничения'}**"), ephemeral=True)

    async def _set_lock(self, interaction: discord.Interaction, locked: bool) -> None:
        channel, _, member = await self._my_room(interaction)
        everyone = channel.guild.default_role
        ow = channel.overwrites_for(everyone)
        ow.connect = False if locked else None
        await channel.set_permissions(everyone, overwrite=ow, reason="Закрытие/открытие комнаты владельцем")
        if locked:
            mine = channel.overwrites_for(member)
            mine.connect = True
            await channel.set_permissions(member, overwrite=mine)
        await respond(interaction, embed=ok_embed("🔒 Комната закрыта — новые участники не смогут зайти."
                                                  if locked else "🔓 Комната открыта для всех."), ephemeral=True)

    @voice.command(name="lock", description="Закрыть комнату для новых участников")
    async def v_lock(self, interaction: discord.Interaction) -> None:
        await self._set_lock(interaction, True)

    @voice.command(name="unlock", description="Открыть комнату для всех")
    async def v_unlock(self, interaction: discord.Interaction) -> None:
        await self._set_lock(interaction, False)

    @voice.command(name="kick", description="Выгнать участника из своей комнаты")
    @app_commands.describe(member="Кого выгнать")
    async def v_kick(self, interaction: discord.Interaction, member: discord.Member) -> None:
        channel, _, me = await self._my_room(interaction)
        if member.id == me.id:
            raise UserFacingError("Нельзя выгнать самого себя.")
        if member.voice is None or member.voice.channel != channel:
            raise UserFacingError("Этого участника нет в вашей комнате.")
        await member.move_to(None, reason=f"Выгнан владельцем комнаты {me}")
        await respond(interaction, embed=ok_embed(f"{member.mention} выгнан(а) из комнаты."), ephemeral=True)

    @voice.command(name="claim", description="Стать владельцем комнаты, если прежний владелец ушёл")
    async def v_claim(self, interaction: discord.Interaction) -> None:
        channel, rec, member = await self._my_room(interaction, allow_claim=True)
        if rec.owner_id == member.id:
            raise UserFacingError("Вы уже владелец этой комнаты.")
        if any(m.id == rec.owner_id for m in channel.members):
            raise UserFacingError("Владелец ещё в комнате.")
        await self.app.tempvoice.set_owner(channel.id, member.id)
        old = channel.guild.get_member(rec.owner_id)
        if old is not None:
            await channel.set_permissions(old, overwrite=None)
        await channel.set_permissions(member, overwrite=OWNER_PERMS, reason="Новый владелец комнаты")
        await respond(interaction, embed=ok_embed(f"👑 {member.mention} теперь владелец комнаты."))


async def setup(bot: MishanyaBot) -> None:
    await bot.add_cog(TempVoice(bot))
