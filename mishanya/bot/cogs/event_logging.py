"""Журналирование событий сервера в каналы Discord и локальную базу."""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from ...schemas import LoggingConfig
from ..client import MishanyaBot
from ..utils import COLOR_ERR, COLOR_INFO, COLOR_OK, COLOR_WARN, channel_from_id, now_utc, safe_send

log = logging.getLogger(__name__)

ALWAYS_STORED = {"moderation", "automod"}


def _clip(text: str | None, limit: int = 1000) -> str:
    if not text:
        return "*(пусто)*"
    return text if len(text) <= limit else text[: limit - 1] + "…"


class EventLogging(commands.Cog):
    def __init__(self, bot: MishanyaBot):
        self.bot = bot
        self.app = bot.app

    async def log_event(self, guild: discord.Guild, event_type: str, summary: str, *,
                        embed: discord.Embed | None = None, user_id: int | None = None,
                        channel_id: int | None = None, details: dict | None = None,
                        skip_channel_ids: set[int] | None = None) -> None:
        cfg: LoggingConfig = await self.app.config.get(guild.id, "logging")  # type: ignore[assignment]
        ev = cfg.events.get(event_type)
        active = cfg.enabled and ev is not None and ev.enabled
        if channel_id is not None and str(channel_id) in cfg.ignored_channels and event_type not in ALWAYS_STORED:
            return
        if active or event_type in ALWAYS_STORED:
            try:
                await self.app.eventlog.add(guild.id, event_type, summary, user_id, channel_id, details)
            except Exception:  # noqa: BLE001
                log.exception("Не удалось записать событие в базу")
        if not active or embed is None:
            return
        target_id = (ev.channel_id if ev else None) or cfg.default_channel_id
        if not target_id or (skip_channel_ids and int(target_id) in skip_channel_ids):
            return
        if embed.timestamp is None:
            embed.timestamp = now_utc()
        await safe_send(channel_from_id(guild, target_id), embed=embed)

    async def _store_content(self) -> bool:
        return (await self.app.config.get_global()).store_message_content

    # ------------------------------------------------------------ сообщения

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        msg = payload.cached_message
        if msg is not None and msg.author.bot:
            return
        channel = guild.get_channel_or_thread(payload.channel_id)
        where = channel.mention if channel else f"<#{payload.channel_id}>"
        embed = discord.Embed(title="🗑️ Сообщение удалено", color=COLOR_ERR)
        if msg is not None:
            embed.description = _clip(msg.content)
            embed.set_author(name=str(msg.author), icon_url=msg.author.display_avatar.url)
            embed.add_field(name="Автор", value=msg.author.mention)
            if msg.attachments:
                embed.add_field(name="Вложения", value="\n".join(a.filename for a in msg.attachments)[:1000])
        else:
            embed.description = "*Сообщение не было в кэше бота — текст недоступен.*"
        embed.add_field(name="Канал", value=where)
        embed.set_footer(text=f"ID сообщения: {payload.message_id}")
        details = {"message_id": str(payload.message_id)}
        if msg is not None and await self._store_content():
            details["content"] = msg.content[:1000]
        await self.log_event(guild, "message_delete",
                             f"Удалено сообщение{' от ' + str(msg.author) if msg else ''} в #{getattr(channel, 'name', payload.channel_id)}",
                             embed=embed, user_id=msg.author.id if msg else None, channel_id=payload.channel_id,
                             details=details)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        if payload.guild_id is None or (guild := self.bot.get_guild(payload.guild_id)) is None:
            return
        embed = discord.Embed(title="🗑️ Массовое удаление", color=COLOR_ERR,
                              description=f"Удалено сообщений: **{len(payload.message_ids)}** в <#{payload.channel_id}>")
        await self.log_event(guild, "message_delete", f"Массовое удаление: {len(payload.message_ids)} сообщений",
                             embed=embed, channel_id=payload.channel_id)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.guild is None or before.author.bot or before.content == after.content:
            return
        embed = discord.Embed(title="✏️ Сообщение изменено", color=COLOR_WARN, url=after.jump_url)
        embed.set_author(name=str(after.author), icon_url=after.author.display_avatar.url)
        embed.add_field(name="Было", value=_clip(before.content), inline=False)
        embed.add_field(name="Стало", value=_clip(after.content), inline=False)
        embed.add_field(name="Канал", value=after.channel.mention)  # type: ignore[union-attr]
        details: dict = {"message_id": str(after.id)}
        if await self._store_content():
            details.update(before=before.content[:1000], after=after.content[:1000])
        await self.log_event(before.guild, "message_edit",
                             f"{after.author} изменил(а) сообщение в #{getattr(after.channel, 'name', '?')}",
                             embed=embed, user_id=after.author.id, channel_id=after.channel.id, details=details)

    # ------------------------------------------------------------ участники

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        age = (now_utc() - member.created_at).days
        embed = discord.Embed(title="📥 Участник вступил", color=COLOR_OK,
                              description=f"{member.mention} ({member})")
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Аккаунт создан", value=f"{discord.utils.format_dt(member.created_at, 'R')}"
                        + (" ⚠️ новый аккаунт" if age < 7 else ""))
        embed.add_field(name="Участников", value=str(member.guild.member_count))
        embed.set_footer(text=f"ID: {member.id}")
        await self.log_event(member.guild, "member_join", f"{member} вступил(а) на сервер", embed=embed,
                             user_id=member.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        embed = discord.Embed(title="📤 Участник вышел", color=COLOR_ERR, description=f"{member.mention} ({member})")
        embed.set_thumbnail(url=member.display_avatar.url)
        roles = [r.mention for r in member.roles if not r.is_default()]
        if roles:
            embed.add_field(name="Роли", value=" ".join(roles)[:1000], inline=False)
        if member.joined_at:
            embed.add_field(name="Был(а) на сервере с", value=discord.utils.format_dt(member.joined_at, "D"))
        embed.set_footer(text=f"ID: {member.id}")
        await self.log_event(member.guild, "member_leave", f"{member} покинул(а) сервер", embed=embed,
                             user_id=member.id)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.nick != after.nick:
            embed = discord.Embed(title="🏷️ Никнейм изменён", color=COLOR_INFO, description=after.mention)
            embed.add_field(name="Было", value=before.nick or "*(нет)*")
            embed.add_field(name="Стало", value=after.nick or "*(нет)*")
            await self.log_event(after.guild, "nickname_change",
                                 f"{after}: никнейм «{before.nick or '—'}» → «{after.nick or '—'}»",
                                 embed=embed, user_id=after.id)
        if before.roles != after.roles:
            added = [r for r in after.roles if r not in before.roles]
            removed = [r for r in before.roles if r not in after.roles]
            embed = discord.Embed(title="🎭 Роли участника изменены", color=COLOR_INFO, description=after.mention)
            if added:
                embed.add_field(name="Добавлены", value=" ".join(r.mention for r in added)[:1000])
            if removed:
                embed.add_field(name="Сняты", value=" ".join(r.mention for r in removed)[:1000])
            summary = f"{after}: " + ", ".join([f"+{r.name}" for r in added] + [f"-{r.name}" for r in removed])
            await self.log_event(after.guild, "member_roles", summary, embed=embed, user_id=after.id)

    # ------------------------------------------------------------ роли, каналы, сервер

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        embed = discord.Embed(title="➕ Роль создана", color=COLOR_OK, description=role.mention)
        await self.log_event(role.guild, "role_change", f"Создана роль {role.name}", embed=embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        embed = discord.Embed(title="➖ Роль удалена", color=COLOR_ERR, description=f"**{role.name}** (`{role.id}`)")
        await self.log_event(role.guild, "role_change", f"Удалена роль {role.name}", embed=embed)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        changes = []
        if before.name != after.name:
            changes.append(f"Название: {before.name} → {after.name}")
        if before.permissions != after.permissions:
            changes.append("Изменены права")
        if before.color != after.color:
            changes.append(f"Цвет: {before.color} → {after.color}")
        if before.hoist != after.hoist or before.mentionable != after.mentionable:
            changes.append("Изменены параметры отображения")
        if not changes:
            return
        embed = discord.Embed(title="🎨 Роль изменена", color=COLOR_INFO, description=after.mention)
        embed.add_field(name="Изменения", value="\n".join(changes)[:1000])
        await self.log_event(after.guild, "role_change", f"Изменена роль {after.name}: " + "; ".join(changes),
                             embed=embed)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        embed = discord.Embed(title="📁 Канал создан", color=COLOR_OK, description=f"{channel.mention} ({channel.type})")
        await self.log_event(channel.guild, "channel_change", f"Создан канал #{channel.name}", embed=embed,
                             channel_id=channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        embed = discord.Embed(title="🗑️ Канал удалён", color=COLOR_ERR, description=f"**#{channel.name}** (`{channel.id}`)")
        await self.log_event(channel.guild, "channel_change", f"Удалён канал #{channel.name}", embed=embed,
                             skip_channel_ids={channel.id})

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
        changes = []
        if before.name != after.name:
            changes.append(f"Название: #{before.name} → #{after.name}")
        if getattr(before, "topic", None) != getattr(after, "topic", None):
            changes.append("Изменена тема")
        if getattr(before, "slowmode_delay", None) != getattr(after, "slowmode_delay", None):
            changes.append(f"Медленный режим: {getattr(after, 'slowmode_delay', 0)} с")
        if before.overwrites != after.overwrites:
            changes.append("Изменены права доступа")
        if getattr(before, "nsfw", None) != getattr(after, "nsfw", None):
            changes.append("Изменён режим NSFW")
        if not changes:
            return
        embed = discord.Embed(title="🛠️ Канал изменён", color=COLOR_INFO, description=after.mention)
        embed.add_field(name="Изменения", value="\n".join(changes)[:1000])
        await self.log_event(after.guild, "channel_change", f"Изменён канал #{after.name}: " + "; ".join(changes),
                             embed=embed, channel_id=after.id)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        changes = []
        if before.name != after.name:
            changes.append(f"Название: {before.name} → {after.name}")
        if before.icon != after.icon:
            changes.append("Изменена иконка")
        if before.verification_level != after.verification_level:
            changes.append(f"Уровень проверки: {after.verification_level}")
        if before.owner_id != after.owner_id:
            changes.append(f"Новый владелец: <@{after.owner_id}>")
        if before.afk_channel != after.afk_channel or before.system_channel != after.system_channel:
            changes.append("Изменены системные каналы")
        if not changes:
            return
        embed = discord.Embed(title="⚙️ Настройки сервера изменены", color=COLOR_INFO,
                              description="\n".join(changes)[:2000])
        await self.log_event(after, "guild_update", "Изменены настройки сервера: " + "; ".join(changes), embed=embed)


async def setup(bot: MishanyaBot) -> None:
    await bot.add_cog(EventLogging(bot))
