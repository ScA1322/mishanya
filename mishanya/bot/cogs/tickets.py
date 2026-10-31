"""Тикеты: приватный канал для обращения участника, кнопки «Закрыть» и «Взять», стенограмма при закрытии."""
from __future__ import annotations

import asyncio
import io
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from ...db.models import Ticket
from ...schemas import TicketsConfig
from ...services.templates import member_values, render
from ..client import MishanyaBot
from ..utils import (
    COLOR_INFO,
    COLOR_OK,
    COLOR_WARN,
    UserFacingError,
    channel_from_id,
    fmt_dt,
    guarded,
    human_error,
    ok_embed,
    require_guild,
    respond,
    safe_send,
)

log = logging.getLogger(__name__)
TRANSCRIPT_LIMIT = 2000  # сообщений в стенограмме

MEMBER_ACCESS = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                            attach_files=True, embed_links=True)


def is_support(member: discord.Member, cfg: TicketsConfig) -> bool:
    p = member.guild_permissions
    if p.administrator or p.manage_channels:
        return True
    return any(str(r.id) in cfg.support_roles for r in member.roles)


class TicketAction(discord.ui.DynamicItem[discord.ui.Button], template=r"mishanya:ticket:(?P<action>close|claim)"):
    """Постоянные кнопки в канале тикета: работают и после перезапуска бота."""

    def __init__(self, action: str, button: discord.ui.Button | None = None):
        if button is None:
            button = discord.ui.Button(
                style=discord.ButtonStyle.danger if action == "close" else discord.ButtonStyle.secondary,
                label="Закрыть тикет" if action == "close" else "Взять в работу",
                emoji="🔒" if action == "close" else "🙋", custom_id=f"mishanya:ticket:{action}")
        super().__init__(button)
        self.action = action

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item,  # noqa: ARG003
                             match: re.Match[str], /) -> "TicketAction":
        return cls(match["action"], item)  # type: ignore[arg-type]

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("Tickets")  # type: ignore[union-attr]
        if cog is not None:
            await guarded(interaction, cog.handle_button(interaction, self.action))


class TopicModal(discord.ui.Modal, title="Новый тикет"):
    topic = discord.ui.TextInput(label="Тема обращения", style=discord.TextStyle.paragraph, max_length=1000,
                                 required=False, placeholder="Коротко опишите вопрос")

    def __init__(self, cog: "Tickets"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await guarded(interaction, self.cog.open_ticket(interaction, str(self.topic.value or "").strip()))


class CloseConfirm(discord.ui.View):
    def __init__(self, cog: "Tickets", ticket_id: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.ticket_id = ticket_id

    @discord.ui.button(label="Да, закрыть", style=discord.ButtonStyle.danger, emoji="🔒")
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.stop()
        await guarded(interaction, self.cog.close_from_interaction(interaction, ""))


class Tickets(commands.Cog):
    def __init__(self, bot: MishanyaBot):
        self.bot = bot
        self.app = bot.app
        self._opening: set[tuple[int, int]] = set()  # защита от двойного нажатия

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(TicketAction)

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(TicketAction)

    async def _cfg(self, guild_id: int) -> TicketsConfig:
        return await self.app.config.get_typed(guild_id, "tickets", TicketsConfig)

    # ------------------------------------------------------------ открытие

    async def start_ticket(self, interaction: discord.Interaction) -> None:
        """Точка входа для кнопки меню и /ticket open без темы: спрашивает тему или сразу создаёт тикет."""
        guild, member = require_guild(interaction)
        cfg = await self._cfg(guild.id)
        await self._check_can_open(guild, member, cfg)
        if cfg.ask_topic:
            await interaction.response.send_modal(TopicModal(self))
        else:
            await self.open_ticket(interaction, "")

    async def _check_can_open(self, guild: discord.Guild, member: discord.Member, cfg: TicketsConfig) -> None:
        if not cfg.enabled:
            raise UserFacingError("Тикеты на этом сервере отключены.")
        if await self.app.tickets.open_count(guild.id, member.id) >= cfg.max_open_per_user:
            raise UserFacingError(f"У вас уже есть открытые тикеты (максимум {cfg.max_open_per_user}). "
                                  "Дождитесь ответа в существующем тикете.")

    async def open_ticket(self, interaction: discord.Interaction, topic: str) -> None:
        guild, member = require_guild(interaction)
        cfg = await self._cfg(guild.id)
        key = (guild.id, member.id)
        if key in self._opening:
            raise UserFacingError("Тикет уже создаётся, подождите пару секунд.")
        await self._check_can_open(guild, member, cfg)
        if not guild.me.guild_permissions.manage_channels:
            raise UserFacingError("У бота нет права «Управлять каналами» — тикет создать нельзя.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        self._opening.add(key)
        try:
            channel = await self._create(guild, member, cfg, topic)
        finally:
            self._opening.discard(key)
        await interaction.followup.send(embed=ok_embed(f"Тикет создан: {channel.mention}"), ephemeral=True)

    async def _create(self, guild: discord.Guild, member: discord.Member, cfg: TicketsConfig,
                      topic: str) -> discord.TextChannel:
        await self.app.guilds.ensure(guild.id, guild.name)
        ticket = await self.app.tickets.create(guild.id, member.id, str(member), topic)
        overwrites: dict = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: MEMBER_ACCESS,
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                                  manage_channels=True, embed_links=True, attach_files=True),
        }
        support = [r for rid in cfg.support_roles if (r := guild.get_role(int(rid))) is not None]
        for role in support:
            overwrites[role] = MEMBER_ACCESS
        category = guild.get_channel(int(cfg.category_id)) if cfg.category_id else None
        if not isinstance(category, discord.CategoryChannel):
            category = None
        values = member_values(member, guild=guild, number=f"{ticket.number:04d}", topic=topic or "—")
        name = render(cfg.channel_name, values)[:90] or f"тикет-{ticket.number:04d}"
        try:
            channel = await guild.create_text_channel(
                name, category=category, overwrites=overwrites,
                topic=f"Тикет #{ticket.number} · {member} · {topic}"[:1024],
                reason=f"Тикет #{ticket.number} от {member}")
        except discord.HTTPException as exc:
            await self.app.tickets.delete(ticket.id)
            raise UserFacingError(f"Не удалось создать канал тикета. {human_error(exc)}") from exc
        await self.app.tickets.set_channel(ticket.id, channel.id)

        embed = discord.Embed(title=f"🎫 Тикет #{ticket.number:04d}", color=COLOR_INFO,
                              description=render(cfg.opening_message, values)[:4000] or None)
        if topic:
            embed.add_field(name="Тема", value=topic[:1024], inline=False)
        embed.set_footer(text="Кнопка «Закрыть тикет» или команда /ticket close")
        view = discord.ui.View(timeout=None)
        view.add_item(TicketAction("close"))
        view.add_item(TicketAction("claim"))
        mentions = [member.mention] + ([r.mention for r in support] if cfg.ping_support else [])
        await safe_send(channel, content=" ".join(mentions), embed=embed, view=view,
                        allowed_mentions=discord.AllowedMentions(users=True, roles=support if cfg.ping_support else False))
        await self._log(guild, cfg, discord.Embed(
            title=f"🎫 Открыт тикет #{ticket.number:04d}", color=COLOR_OK,
            description=f"{member.mention} — {channel.mention}" + (f"\n**Тема:** {topic[:1000]}" if topic else "")))
        log.info("Сервер %s: открыт тикет #%s от %s", guild.id, ticket.number, member)
        return channel

    # ------------------------------------------------------------ кнопки в тикете

    async def _ticket_here(self, interaction: discord.Interaction) -> tuple[Ticket, TicketsConfig, discord.Member]:
        guild, member = require_guild(interaction)
        ticket = await self.app.tickets.by_channel(interaction.channel_id or 0)
        if ticket is None:
            raise UserFacingError("Это не канал открытого тикета.")
        return ticket, await self._cfg(guild.id), member

    async def handle_button(self, interaction: discord.Interaction, action: str) -> None:
        ticket, cfg, member = await self._ticket_here(interaction)
        if action == "close":
            if member.id != ticket.owner_id and not is_support(member, cfg):
                raise UserFacingError("Закрыть тикет может его автор или команда поддержки.")
            await respond(interaction, embed=discord.Embed(description="Закрыть этот тикет?", color=COLOR_WARN),
                          view=CloseConfirm(self, ticket.id), ephemeral=True)
        else:
            await self._claim(interaction, ticket, cfg, member)

    async def _claim(self, interaction: discord.Interaction, ticket: Ticket, cfg: TicketsConfig,
                     member: discord.Member) -> None:
        if not is_support(member, cfg):
            raise UserFacingError("Взять тикет в работу может только команда поддержки.")
        if ticket.claimed_by == member.id:
            raise UserFacingError("Вы уже ведёте этот тикет.")
        await self.app.tickets.claim(ticket.id, member.id)
        await respond(interaction, embed=discord.Embed(description=f"🙋 {member.mention} взял(а) тикет в работу.",
                                                       color=COLOR_OK))

    # ------------------------------------------------------------ закрытие

    async def close_from_interaction(self, interaction: discord.Interaction, reason: str) -> None:
        ticket, cfg, member = await self._ticket_here(interaction)
        if member.id != ticket.owner_id and not is_support(member, cfg):
            raise UserFacingError("Закрыть тикет может его автор или команда поддержки.")
        channel = interaction.channel
        assert isinstance(channel, discord.TextChannel)
        await respond(interaction, embed=ok_embed("Тикет закрывается…"), ephemeral=True)
        await self.close_ticket(channel, ticket, cfg, member, reason)

    async def close_ticket(self, channel: discord.TextChannel, ticket: Ticket, cfg: TicketsConfig,
                           closer: discord.abc.User | None, reason: str) -> bool:
        """Закрывает тикет: запись в базе, стенограмма в журнал, удаление канала с задержкой."""
        if not await self.app.tickets.close(ticket.id, closer.id if closer else None, reason):
            return False
        guild = channel.guild
        delay = cfg.delete_delay_seconds
        await safe_send(channel, embed=discord.Embed(
            color=COLOR_WARN, description=f"🔒 Тикет закрыт{f' пользователем {closer.mention}' if closer else ''}."
                                          + (f"\n**Причина:** {reason}" if reason else "")
                                          + (f"\nКанал будет удалён через {delay} с." if delay else "")))
        embed = discord.Embed(title=f"🔒 Закрыт тикет #{ticket.number:04d}", color=COLOR_WARN)
        embed.add_field(name="Автор", value=f"<@{ticket.owner_id}>")
        embed.add_field(name="Закрыл", value=closer.mention if closer else "—")
        embed.add_field(name="Открыт", value=fmt_dt(ticket.created_at, "f"))
        if ticket.claimed_by:
            embed.add_field(name="Вёл", value=f"<@{ticket.claimed_by}>")
        if ticket.topic:
            embed.add_field(name="Тема", value=ticket.topic[:1024], inline=False)
        if reason:
            embed.add_field(name="Причина закрытия", value=reason[:1024], inline=False)
        file = None
        if cfg.transcript:
            try:
                file = discord.File(io.BytesIO((await self._transcript(channel, ticket)).encode("utf-8")),
                                    filename=f"ticket-{ticket.number:04d}.txt")
            except discord.HTTPException as exc:
                log.warning("Не удалось собрать стенограмму тикета #%s: %s", ticket.number, exc)
        await self._log(guild, cfg, embed, file)
        if delay:
            await asyncio.sleep(delay)
        try:
            await channel.delete(reason=f"Тикет #{ticket.number} закрыт")
        except discord.HTTPException as exc:
            log.warning("Не удалось удалить канал тикета #%s: %s", ticket.number, exc)
        return True

    async def _transcript(self, channel: discord.TextChannel, ticket: Ticket) -> str:
        lines = [f"Тикет #{ticket.number:04d} · сервер {channel.guild.name} · канал #{channel.name}",
                 f"Автор: {ticket.owner_name} ({ticket.owner_id})", f"Тема: {ticket.topic or '—'}", ""]
        async for m in channel.history(limit=TRANSCRIPT_LIMIT, oldest_first=True):
            text = m.content
            for e in m.embeds:
                text += "\n[embed] " + " | ".join(x for x in (e.title, e.description) if x)
            for a in m.attachments:
                text += f"\n[вложение] {a.url}"
            lines.append(f"[{m.created_at:%Y-%m-%d %H:%M:%S} UTC] {m.author} ({m.author.id}): {text.strip()}")
        return "\n".join(lines) + "\n"

    async def _log(self, guild: discord.Guild, cfg: TicketsConfig, embed: discord.Embed,
                   file: discord.File | None = None) -> None:
        channel = channel_from_id(guild, cfg.log_channel_id)
        if channel is not None:
            kwargs: dict = {"embed": embed}
            if file is not None:
                kwargs["file"] = file
            await safe_send(channel, **kwargs)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        ticket = await self.app.tickets.by_channel(channel.id)
        if ticket is not None:
            await self.app.tickets.close(ticket.id, None, "Канал удалён вручную")

    # ------------------------------------------------------------ slash-команды

    ticket = app_commands.Group(name="ticket", description="Тикеты поддержки", guild_only=True)

    @ticket.command(name="open", description="Открыть тикет — приватный канал с командой поддержки")
    @app_commands.describe(topic="Тема обращения (необязательно)")
    async def t_open(self, interaction: discord.Interaction,
                     topic: app_commands.Range[str, 1, 1000] | None = None) -> None:
        if topic:
            await self.open_ticket(interaction, topic.strip())
        else:
            await self.start_ticket(interaction)

    @ticket.command(name="close", description="Закрыть текущий тикет")
    @app_commands.describe(reason="Причина закрытия (необязательно)")
    async def t_close(self, interaction: discord.Interaction,
                      reason: app_commands.Range[str, 1, 500] | None = None) -> None:
        await self.close_from_interaction(interaction, reason or "")

    @ticket.command(name="claim", description="Взять текущий тикет в работу")
    async def t_claim(self, interaction: discord.Interaction) -> None:
        ticket, cfg, member = await self._ticket_here(interaction)
        await self._claim(interaction, ticket, cfg, member)

    @ticket.command(name="add", description="Добавить участника в текущий тикет")
    @app_commands.describe(member="Кого добавить")
    async def t_add(self, interaction: discord.Interaction, member: discord.Member) -> None:
        ticket, cfg, me = await self._ticket_here(interaction)
        if not is_support(me, cfg):
            raise UserFacingError("Добавлять участников может только команда поддержки.")
        channel = interaction.channel
        assert isinstance(channel, discord.TextChannel)
        await channel.set_permissions(member, overwrite=MEMBER_ACCESS, reason=f"Тикет #{ticket.number}: добавлен")
        await respond(interaction, embed=ok_embed(f"{member.mention} добавлен(а) в тикет."))

    @ticket.command(name="remove", description="Убрать участника из текущего тикета")
    @app_commands.describe(member="Кого убрать")
    async def t_remove(self, interaction: discord.Interaction, member: discord.Member) -> None:
        ticket, cfg, me = await self._ticket_here(interaction)
        if not is_support(me, cfg):
            raise UserFacingError("Убирать участников может только команда поддержки.")
        if member.id == ticket.owner_id:
            raise UserFacingError("Нельзя убрать автора тикета — закройте тикет.")
        channel = interaction.channel
        assert isinstance(channel, discord.TextChannel)
        await channel.set_permissions(member, overwrite=None, reason=f"Тикет #{ticket.number}: убран")
        await respond(interaction, embed=ok_embed(f"{member.mention} убран(а) из тикета."))


async def setup(bot: MishanyaBot) -> None:
    await bot.add_cog(Tickets(bot))
