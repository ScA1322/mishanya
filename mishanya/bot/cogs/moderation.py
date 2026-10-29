"""Модерация: kick/ban/timeout, предупреждения с авто-наказаниями, очистка, блокировка каналов, роли."""
from __future__ import annotations

import logging
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from ...schemas import ModerationConfig
from ...services.moderation import action_for_count, check_hierarchy, check_role_manageable
from ...services.reminders import format_delta, parse_duration
from ..client import MishanyaBot
from ..utils import (
    COLOR_ERR,
    COLOR_INFO,
    COLOR_OK,
    COLOR_WARN,
    UserFacingError,
    channel_from_id,
    fmt_dt,
    human_error,
    ok_embed,
    require_guild,
    require_perms,
    respond,
    safe_send,
)

log = logging.getLogger(__name__)

ACTION_LABELS = {
    "kick": "Кик", "ban": "Бан", "unban": "Разбан", "timeout": "Тайм-аут", "untimeout": "Снятие тайм-аута",
    "warn": "Предупреждение", "unwarn": "Снятие предупреждения", "clearwarnings": "Сброс предупреждений",
    "clear": "Очистка сообщений", "purge": "Очистка сообщений", "slowmode": "Медленный режим",
    "lock": "Блокировка канала", "unlock": "Разблокировка канала", "nickname": "Смена никнейма",
    "role_add": "Выдача роли", "role_remove": "Снятие роли", "automod_timeout": "Тайм-аут (автомодерация)",
}
ACTION_COLORS = {"ban": COLOR_ERR, "kick": COLOR_ERR, "timeout": COLOR_WARN, "warn": COLOR_WARN,
                 "automod_timeout": COLOR_WARN, "unban": COLOR_OK, "untimeout": COLOR_OK}
MAX_TIMEOUT = timedelta(days=28)
REASON_TEXT = "Причина (будет записана в журнал)"


class Moderation(commands.Cog):
    role_group = app_commands.Group(name="role", description="Выдача и снятие ролей", guild_only=True,
                                    default_permissions=discord.Permissions(manage_roles=True))

    def __init__(self, bot: MishanyaBot):
        self.bot = bot
        self.app = bot.app

    async def cfg(self, guild_id: int) -> ModerationConfig:
        return await self.app.config.get_typed(guild_id, "moderation", ModerationConfig)

    async def _enabled_cfg(self, guild: discord.Guild) -> ModerationConfig:
        cfg = await self.cfg(guild.id)
        if not cfg.enabled:
            raise UserFacingError("Модуль модерации отключён в панели Mishanya.")
        await self.app.guilds.ensure(guild.id, guild.name)
        return cfg

    # ------------------------------------------------------------ журнал

    async def record(self, guild: discord.Guild, action: str, target: discord.abc.User | None,
                     moderator: discord.abc.User | None, reason: str = "",
                     duration: timedelta | None = None, extra: str = "") -> int:
        """Сохраняет случай модерации, отправляет его в канал логов и журнал событий. Возвращает номер."""
        case = await self.app.moderation.add_case(
            guild.id, action, target.id if target else None, str(target) if target else "",
            moderator.id if moderator else None, str(moderator) if moderator else "", reason,
            int(duration.total_seconds()) if duration else None)
        embed = discord.Embed(title=f"Случай #{case.case_number} · {ACTION_LABELS.get(action, action)}",
                              color=ACTION_COLORS.get(action, COLOR_INFO))
        if target:
            embed.add_field(name="Участник", value=f"{target.mention} ({target})")
        if moderator:
            embed.add_field(name="Модератор", value=moderator.mention)
        if duration:
            embed.add_field(name="Длительность", value=format_delta(duration))
        embed.add_field(name="Причина", value=reason or "не указана", inline=False)
        if extra:
            embed.add_field(name="Подробности", value=extra[:1000], inline=False)
        cfg = await self.cfg(guild.id)
        skip: set[int] = set()
        if cfg.log_channel_id:
            embed.timestamp = discord.utils.utcnow()
            await safe_send(channel_from_id(guild, cfg.log_channel_id), embed=embed)
            skip.add(int(cfg.log_channel_id))
        logger = self.bot.get_cog("EventLogging")
        if logger is not None:
            summary = f"#{case.case_number} {ACTION_LABELS.get(action, action)}: {target or '-'} — {reason or 'без причины'}"
            await logger.log_event(guild, "moderation", summary, embed=embed,  # type: ignore[attr-defined]
                                   user_id=target.id if target else None,
                                   details={"case": case.case_number, "action": action,
                                            "moderator_id": str(moderator.id) if moderator else None},
                                   skip_channel_ids=skip)
        return case.case_number

    async def notify(self, user: discord.abc.User, guild: discord.Guild, action: str, reason: str,
                     duration: timedelta | None = None) -> bool:
        cfg = await self.cfg(guild.id)
        if not cfg.dm_on_action:
            return False
        embed = discord.Embed(title=f"{ACTION_LABELS.get(action, action)} на сервере {guild.name}",
                              color=ACTION_COLORS.get(action, COLOR_INFO))
        embed.add_field(name="Причина", value=reason or "не указана", inline=False)
        if duration:
            embed.add_field(name="Длительность", value=format_delta(duration))
        try:
            await user.send(embed=embed)
            return True
        except discord.HTTPException:
            return False

    def _check_target(self, interaction: discord.Interaction, target: discord.Member) -> None:
        guild, actor = require_guild(interaction)
        error = check_hierarchy(actor, target, guild.me, guild.owner_id)
        if error:
            raise UserFacingError(error)

    # ------------------------------------------------------------ наказания (используются и автоматикой)

    async def punish(self, guild: discord.Guild, member: discord.Member, action: str, minutes: int,
                     reason: str, moderator: discord.abc.User | None) -> str | None:
        """Применяет timeout/kick/ban. Возвращает текст ошибки или None."""
        me = guild.me
        if member.top_role.position >= me.top_role.position or member.id == guild.owner_id:
            return "роль участника не ниже роли бота"
        duration = timedelta(minutes=minutes) if action == "timeout" else None
        try:
            if action == "timeout":
                await member.timeout(min(duration, MAX_TIMEOUT), reason=reason)  # type: ignore[type-var]
            elif action == "kick":
                await self.notify(member, guild, "kick", reason)
                await member.kick(reason=reason)
            elif action == "ban":
                await self.notify(member, guild, "ban", reason)
                await member.ban(reason=reason, delete_message_seconds=0)
            else:
                return f"неизвестное действие {action}"
        except discord.HTTPException as exc:
            return human_error(exc)
        if action == "timeout":
            await self.notify(member, guild, "timeout", reason, duration)
        await self.record(guild, action, member, moderator or self.bot.user, reason, duration)
        return None

    # ------------------------------------------------------------ kick / ban

    @app_commands.command(name="kick", description="Выгнать участника с сервера")
    @app_commands.describe(member="Участник")
    @app_commands.describe(reason=REASON_TEXT)
    @app_commands.guild_only()
    @app_commands.default_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member,
                   reason: app_commands.Range[str, 0, 400] = "") -> None:
        guild, actor = require_guild(interaction)
        await self._enabled_cfg(guild)
        require_perms(interaction, "kick_members")
        self._check_target(interaction, member)
        dm = await self.notify(member, guild, "kick", reason)
        await member.kick(reason=f"{actor}: {reason}" if reason else str(actor))
        case = await self.record(guild, "kick", member, actor, reason)
        await respond(interaction, embed=ok_embed(f"{member} выгнан(а) с сервера. Случай #{case}."
                                                  + ("" if dm else "\nЛичное сообщение не доставлено.")))

    @app_commands.command(name="ban", description="Забанить пользователя")
    @app_commands.describe(user="Пользователь (можно указать ID того, кого нет на сервере)",
                           delete_days="Удалить его сообщения за последние N дней (0–7)")
    @app_commands.describe(reason=REASON_TEXT)
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, user: discord.User,
                  reason: app_commands.Range[str, 0, 400] = "",
                  delete_days: app_commands.Range[int, 0, 7] = 0) -> None:
        guild, actor = require_guild(interaction)
        await self._enabled_cfg(guild)
        require_perms(interaction, "ban_members")
        member = guild.get_member(user.id)
        if member is not None:
            self._check_target(interaction, member)
            dm = await self.notify(member, guild, "ban", reason)
        else:
            dm = False
        await guild.ban(user, reason=f"{actor}: {reason}" if reason else str(actor),
                        delete_message_seconds=delete_days * 86400)
        case = await self.record(guild, "ban", user, actor, reason)
        await respond(interaction, embed=ok_embed(f"{user} забанен(а). Случай #{case}."
                                                  + ("" if dm or member is None else "\nЛичное сообщение не доставлено.")))

    @app_commands.command(name="unban", description="Разбанить пользователя по ID")
    @app_commands.describe(user_id="ID пользователя")
    @app_commands.describe(reason=REASON_TEXT)
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str,
                    reason: app_commands.Range[str, 0, 400] = "") -> None:
        guild, actor = require_guild(interaction)
        await self._enabled_cfg(guild)
        require_perms(interaction, "ban_members")
        if not user_id.strip().isdigit():
            raise UserFacingError("Укажите числовой ID пользователя.")
        try:
            entry = await guild.fetch_ban(discord.Object(int(user_id)))
        except discord.NotFound:
            raise UserFacingError("Этот пользователь не забанен.") from None
        await guild.unban(entry.user, reason=f"{actor}: {reason}" if reason else str(actor))
        case = await self.record(guild, "unban", entry.user, actor, reason)
        await respond(interaction, embed=ok_embed(f"{entry.user} разбанен(а). Случай #{case}."))

    # ------------------------------------------------------------ тайм-аут

    @app_commands.command(name="timeout", description="Отправить участника в тайм-аут")
    @app_commands.describe(member="Участник", duration="Длительность: 10m, 1h, 2d (до 28 дней)")
    @app_commands.describe(reason=REASON_TEXT)
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def timeout(self, interaction: discord.Interaction, member: discord.Member, duration: str,
                      reason: app_commands.Range[str, 0, 400] = "") -> None:
        guild, actor = require_guild(interaction)
        await self._enabled_cfg(guild)
        require_perms(interaction, "moderate_members")
        self._check_target(interaction, member)
        try:
            delta = parse_duration(duration)
        except ValueError as exc:
            raise UserFacingError(str(exc)) from None
        if delta > MAX_TIMEOUT:
            raise UserFacingError("Discord ограничивает тайм-аут 28 днями.")
        if member.guild_permissions.administrator:
            raise UserFacingError("Discord не позволяет отправить в тайм-аут администратора.")
        await member.timeout(delta, reason=f"{actor}: {reason}" if reason else str(actor))
        await self.notify(member, guild, "timeout", reason, delta)
        case = await self.record(guild, "timeout", member, actor, reason, delta)
        await respond(interaction, embed=ok_embed(
            f"{member.mention} в тайм-ауте на {format_delta(delta)} (до {fmt_dt(discord.utils.utcnow() + delta)}). "
            f"Случай #{case}."))

    @app_commands.command(name="untimeout", description="Снять тайм-аут с участника")
    @app_commands.describe(member="Участник")
    @app_commands.describe(reason=REASON_TEXT)
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member,
                        reason: app_commands.Range[str, 0, 400] = "") -> None:
        guild, actor = require_guild(interaction)
        await self._enabled_cfg(guild)
        require_perms(interaction, "moderate_members")
        self._check_target(interaction, member)
        if not member.is_timed_out():
            raise UserFacingError("Участник не находится в тайм-ауте.")
        await member.timeout(None, reason=f"{actor}: {reason}" if reason else str(actor))
        case = await self.record(guild, "untimeout", member, actor, reason)
        await respond(interaction, embed=ok_embed(f"Тайм-аут {member.mention} снят. Случай #{case}."))

    # ------------------------------------------------------------ предупреждения

    async def warn_member(self, guild: discord.Guild, member: discord.Member, moderator: discord.abc.User,
                          reason: str) -> tuple[int, int, str | None]:
        """Выдаёт предупреждение и при необходимости авто-наказание. Возвращает (случай, счётчик, итог)."""
        cfg = await self.cfg(guild.id)
        await self.app.guilds.ensure(guild.id, guild.name)
        await self.app.moderation.add_warning(guild.id, member.id, moderator.id, reason)
        count = await self.app.moderation.active_count(guild.id, member.id, cfg)
        case = await self.record(guild, "warn", member, moderator, reason, extra=f"Активных предупреждений: {count}")
        await self.notify(member, guild, "warn", f"{reason or 'не указана'} (предупреждений: {count})")
        result = None
        auto = action_for_count(cfg, count)
        if auto is not None:
            err = await self.punish(guild, member, auto.action, auto.duration_minutes,
                                    f"Автоматически: {count} предупреждений", self.bot.user)
            label = ACTION_LABELS[auto.action].lower()
            result = f"Автоматическое действие ({label}) не выполнено: {err}" if err else \
                f"Автоматически применено: {label}"
        return case, count, result

    @app_commands.command(name="warn", description="Выдать предупреждение участнику")
    @app_commands.describe(member="Участник")
    @app_commands.describe(reason=REASON_TEXT)
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member,
                   reason: app_commands.Range[str, 0, 400] = "") -> None:
        guild, actor = require_guild(interaction)
        await self._enabled_cfg(guild)
        require_perms(interaction, "moderate_members", bot_too=False)
        if member.bot:
            raise UserFacingError("Ботам нельзя выдавать предупреждения.")
        self._check_target(interaction, member)
        await interaction.response.defer()
        case, count, result = await self.warn_member(guild, member, actor, reason)
        text = f"{member.mention} получил(а) предупреждение. Активных: **{count}**. Случай #{case}."
        if result:
            text += f"\n{result}."
        await respond(interaction, embed=discord.Embed(description=text, color=COLOR_WARN))

    @app_commands.command(name="warnings", description="История предупреждений участника")
    @app_commands.describe(member="Участник", show_removed="Показать также снятые предупреждения")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def warnings(self, interaction: discord.Interaction, member: discord.Member,
                       show_removed: bool = False) -> None:
        guild, _ = require_guild(interaction)
        require_perms(interaction, "moderate_members", bot_too=False)
        cfg = await self.cfg(guild.id)
        rows, total = await self.app.moderation.list_warnings(guild.id, member.id, include_inactive=show_removed,
                                                              cfg=cfg, limit=15)
        lines = []
        for w in rows:
            mark = "" if w.active else " ~~снято~~"
            lines.append(f"`#{w.id}` {fmt_dt(w.created_at, 'd')} <@{w.moderator_id}>: {w.reason or 'без причины'}{mark}")
        embed = discord.Embed(title=f"Предупреждения: {member}", color=COLOR_WARN,
                              description="\n".join(lines) or "Предупреждений нет.")
        embed.set_footer(text=f"Всего: {total}" + (f" · истекают через {cfg.warning_expiry_days} дн."
                                                     if cfg.warning_expiry_days else ""))
        await respond(interaction, embed=embed, ephemeral=True)

    @app_commands.command(name="unwarn", description="Снять предупреждение по номеру")
    @app_commands.describe(warning_id="Номер предупреждения из /warnings")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def unwarn(self, interaction: discord.Interaction, warning_id: int) -> None:
        guild, actor = require_guild(interaction)
        require_perms(interaction, "moderate_members", bot_too=False)
        if not await self.app.moderation.remove_warning(guild.id, warning_id, actor.id):
            raise UserFacingError("Активное предупреждение с таким номером не найдено.")
        await self.record(guild, "unwarn", None, actor, f"Снято предупреждение #{warning_id}")
        await respond(interaction, embed=ok_embed(f"Предупреждение #{warning_id} снято."), ephemeral=True)

    @app_commands.command(name="clearwarnings", description="Снять все предупреждения участника")
    @app_commands.describe(member="Участник")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def clearwarnings(self, interaction: discord.Interaction, member: discord.Member) -> None:
        guild, actor = require_guild(interaction)
        require_perms(interaction, "moderate_members", bot_too=False)
        n = await self.app.moderation.clear_warnings(guild.id, member.id, actor.id)
        if n:
            await self.record(guild, "clearwarnings", member, actor, f"Снято предупреждений: {n}")
        await respond(interaction, embed=ok_embed(f"Снято предупреждений у {member.mention}: {n}."), ephemeral=True)

    @app_commands.command(name="cases", description="Журнал модерации (последние случаи)")
    @app_commands.describe(member="Только случаи этого участника")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    async def cases(self, interaction: discord.Interaction, member: discord.User | None = None) -> None:
        guild, _ = require_guild(interaction)
        require_perms(interaction, "moderate_members", bot_too=False)
        rows, total = await self.app.moderation.list_cases(guild.id, member.id if member else None, limit=15)
        lines = [f"`#{c.case_number}` {fmt_dt(c.created_at, 'd')} **{ACTION_LABELS.get(c.action, c.action)}** "
                 f"{c.target_name or ''} — {c.reason or 'без причины'}" for c in rows]
        embed = discord.Embed(title="Журнал модерации", color=COLOR_INFO,
                              description="\n".join(lines)[:4000] or "Записей нет.")
        embed.set_footer(text=f"Всего: {total}")
        await respond(interaction, embed=embed, ephemeral=True)

    # ------------------------------------------------------------ сообщения и каналы

    @app_commands.command(name="clear", description="Удалить последние сообщения в канале")
    @app_commands.describe(amount="Количество (1–100)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    async def clear(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]) -> None:
        await self._purge(interaction, amount, None, None, False, "clear")

    @app_commands.command(name="purge", description="Расширенная очистка сообщений с фильтрами")
    @app_commands.describe(amount="Сколько последних сообщений проверить (1–500)", user="Только сообщения этого автора",
                           contains="Только сообщения, содержащие текст", bots_only="Только сообщения ботов")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 500],
                    user: discord.User | None = None, contains: str | None = None, bots_only: bool = False) -> None:
        await self._purge(interaction, amount, user, contains, bots_only, "purge")

    async def _purge(self, interaction: discord.Interaction, amount: int, user: discord.User | None,
                     contains: str | None, bots_only: bool, action: str) -> None:
        guild, actor = require_guild(interaction)
        await self._enabled_cfg(guild)
        require_perms(interaction, "manage_messages", "read_message_history")
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
            raise UserFacingError("Очистка работает только в текстовых каналах.")
        await interaction.response.defer(ephemeral=True)

        def check(m: discord.Message) -> bool:
            if m.pinned:
                return False
            if user and m.author.id != user.id:
                return False
            if bots_only and not m.author.bot:
                return False
            return not (contains and contains.lower() not in m.content.lower())

        deleted = await channel.purge(limit=amount, check=check, bulk=True, reason=f"{actor}: /{action}")
        filters = ", ".join(x for x in [f"автор {user}" if user else "", f"текст «{contains}»" if contains else "",
                                       "только боты" if bots_only else ""] if x)
        await self.record(guild, action, user, actor, f"Удалено {len(deleted)} сообщений в #{channel.name}"
                          + (f" ({filters})" if filters else ""))
        note = "\nСообщения старше 14 дней удаляются по одному и медленнее (ограничение Discord)." if len(deleted) < amount else ""
        await interaction.followup.send(embed=ok_embed(f"Удалено сообщений: {len(deleted)}.{note}"), ephemeral=True)

    @app_commands.command(name="slowmode", description="Установить медленный режим канала")
    @app_commands.describe(seconds="Задержка в секундах (0 — выключить, максимум 21600)", channel="Канал")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600],
                       channel: discord.TextChannel | None = None) -> None:
        guild, actor = require_guild(interaction)
        await self._enabled_cfg(guild)
        require_perms(interaction, "manage_channels")
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            raise UserFacingError("Укажите текстовый канал.")
        await target.edit(slowmode_delay=seconds, reason=f"{actor}: /slowmode")
        await self.record(guild, "slowmode", None, actor, f"#{target.name}: {seconds} с")
        await respond(interaction, embed=ok_embed(
            f"Медленный режим в {target.mention}: " + (f"{seconds} с." if seconds else "выключен.")))

    async def _set_lock(self, interaction: discord.Interaction, channel: discord.TextChannel | None,
                        reason: str, lock: bool) -> None:
        guild, actor = require_guild(interaction)
        await self._enabled_cfg(guild)
        require_perms(interaction, "manage_channels", "manage_roles")
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            raise UserFacingError("Укажите текстовый канал.")
        overwrite = target.overwrites_for(guild.default_role)
        overwrite.send_messages = False if lock else None
        overwrite.send_messages_in_threads = False if lock else None
        overwrite.create_public_threads = False if lock else None
        await target.set_permissions(guild.default_role, overwrite=overwrite,
                                     reason=f"{actor}: {reason}" if reason else str(actor))
        await self.record(guild, "lock" if lock else "unlock", None, actor, f"#{target.name}: {reason}".strip(": "))
        if target != interaction.channel:
            await safe_send(target, embed=discord.Embed(
                description=("🔒 Канал заблокирован." if lock else "🔓 Канал разблокирован.")
                + (f" Причина: {reason}" if reason else ""), color=COLOR_WARN if lock else COLOR_OK))
        await respond(interaction, embed=ok_embed(f"{target.mention} " + ("заблокирован 🔒" if lock else "разблокирован 🔓")
                                                  + (f". Причина: {reason}" if reason else "")))

    @app_commands.command(name="lock", description="Запретить @everyone писать в канал")
    @app_commands.describe(channel="Канал (по умолчанию текущий)")
    @app_commands.describe(reason=REASON_TEXT)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    async def lock(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None,
                   reason: app_commands.Range[str, 0, 400] = "") -> None:
        await self._set_lock(interaction, channel, reason, True)

    @app_commands.command(name="unlock", description="Снова разрешить @everyone писать в канал")
    @app_commands.describe(channel="Канал (по умолчанию текущий)")
    @app_commands.describe(reason=REASON_TEXT)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None,
                     reason: app_commands.Range[str, 0, 400] = "") -> None:
        await self._set_lock(interaction, channel, reason, False)

    @app_commands.command(name="nickname", description="Изменить или сбросить никнейм участника")
    @app_commands.describe(member="Участник", nickname="Новый никнейм (пусто — сбросить)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_nicknames=True)
    async def nickname(self, interaction: discord.Interaction, member: discord.Member,
                       nickname: app_commands.Range[str, 0, 32] = "") -> None:
        guild, actor = require_guild(interaction)
        await self._enabled_cfg(guild)
        require_perms(interaction, "manage_nicknames")
        if member.id != actor.id:
            self._check_target(interaction, member)
        elif member.id == guild.owner_id:
            raise UserFacingError("Discord не позволяет ботам менять никнейм владельца сервера.")
        old = member.nick
        await member.edit(nick=nickname or None, reason=f"{actor}: /nickname")
        await self.record(guild, "nickname", member, actor, f"«{old or '—'}» → «{nickname or '—'}»")
        await respond(interaction, embed=ok_embed(f"Никнейм {member.mention}: «{nickname or member.name}»."),
                      ephemeral=True)

    # ------------------------------------------------------------ роли

    async def _role_change(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role,
                           add: bool, reason: str) -> None:
        guild, actor = require_guild(interaction)
        await self._enabled_cfg(guild)
        require_perms(interaction, "manage_roles")
        error = check_role_manageable(role, actor, guild.me, guild.owner_id, role.managed, role.is_default())
        if error:
            raise UserFacingError(error)
        if add and role in member.roles:
            raise UserFacingError(f"У {member.mention} уже есть роль {role.mention}.")
        if not add and role not in member.roles:
            raise UserFacingError(f"У {member.mention} нет роли {role.mention}.")
        text_reason = f"{actor}: {reason}" if reason else str(actor)
        if add:
            await member.add_roles(role, reason=text_reason)
        else:
            await member.remove_roles(role, reason=text_reason)
        await self.record(guild, "role_add" if add else "role_remove", member, actor,
                          f"{role.name}" + (f": {reason}" if reason else ""))
        await respond(interaction, embed=ok_embed(
            f"Роль {role.mention} {'выдана' if add else 'снята у'} {member.mention}."), ephemeral=True)

    @role_group.command(name="add", description="Выдать роль участнику")
    @app_commands.describe(member="Участник", role="Роль")
    @app_commands.describe(reason=REASON_TEXT)
    async def role_add(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role,
                       reason: app_commands.Range[str, 0, 400] = "") -> None:
        await self._role_change(interaction, member, role, True, reason)

    @role_group.command(name="remove", description="Снять роль с участника")
    @app_commands.describe(member="Участник", role="Роль")
    @app_commands.describe(reason=REASON_TEXT)
    async def role_remove(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role,
                          reason: app_commands.Range[str, 0, 400] = "") -> None:
        await self._role_change(interaction, member, role, False, reason)


async def setup(bot: MishanyaBot) -> None:
    await bot.add_cog(Moderation(bot))
