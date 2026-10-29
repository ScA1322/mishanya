"""Автомодерация: применение правил к сообщениям, наказания, журнал."""
from __future__ import annotations

import logging
import time

import discord
from discord.ext import commands

from ...schemas import AutomodConfig
from ...services.automod import MessageInfo, Violation
from ...services.templates import member_values, render
from ..client import MishanyaBot
from ..utils import COLOR_WARN, channel_from_id, is_moderator, safe_send

log = logging.getLogger(__name__)
WARN_NOTICE_INTERVAL = 10.0


class Automod(commands.Cog):
    def __init__(self, bot: MishanyaBot):
        self.bot = bot
        self.app = bot.app
        self._last_notice: dict[tuple[int, int], float] = {}

    async def handle_message(self, message: discord.Message) -> bool:
        """Возвращает True, если сообщение удалено и дальнейшая обработка не нужна."""
        assert message.guild is not None and isinstance(message.author, discord.Member)
        cfg = await self.app.config.get_typed(message.guild.id, "automod", AutomodConfig)
        if not cfg.enabled:
            return False
        member = message.author
        mention_ids = {u.id for u in message.mentions if u.id != member.id} | {r.id for r in message.role_mentions}
        info = MessageInfo(
            guild_id=message.guild.id, user_id=member.id, channel_id=message.channel.id,
            content=message.content or "", role_ids=[r.id for r in member.roles],
            mention_count=len(mention_ids), is_moderator=is_moderator(member),
        )
        if isinstance(message.channel, discord.Thread) and message.channel.parent_id:
            # Исключение родительского канала распространяется на его ветки.
            if str(message.channel.parent_id) in cfg.exempt_channels:
                return False
        violation = self.app.automod.engine.check(cfg, info)
        if violation is None:
            return False
        await self.apply(message, member, cfg, violation)
        return violation.settings.delete_message

    async def apply(self, message: discord.Message, member: discord.Member, cfg: AutomodConfig,
                    v: Violation) -> None:
        guild = member.guild
        await self.app.guilds.ensure(guild.id, guild.name)
        actions: list[str] = []
        if v.settings.delete_message:
            try:
                await message.delete()
                actions.append("сообщение удалено")
            except discord.Forbidden:
                actions.append("не удалось удалить (нет права «Управлять сообщениями»)")
            except discord.NotFound:
                pass
            except discord.HTTPException as exc:
                actions.append(f"ошибка удаления: {exc.status}")

        key = (guild.id, member.id)
        now = time.monotonic()
        if v.settings.warn_user and now - self._last_notice.get(key, 0) > WARN_NOTICE_INTERVAL:
            self._last_notice[key] = now
            text = render(cfg.warn_message, member_values(member, channel=message.channel, rule=v.rule_name))
            await safe_send(message.channel, content=text[:2000], delete_after=10)

        await self.app.automod.record_violation(guild.id, member.id, v.rule)
        moderation = self.bot.get_cog("Moderation")
        if v.settings.add_warning and moderation is not None:
            _, count, result = await moderation.warn_member(  # type: ignore[attr-defined]
                guild, member, self.bot.user, f"Автомодерация: {v.rule_name} ({v.reason})")
            actions.append(f"предупреждение №{count}" + (f"; {result}" if result else ""))

        esc = cfg.escalation
        if esc.enabled and moderation is not None and not member.is_timed_out():
            recent = await self.app.automod.recent_violations(guild.id, member.id, esc.window_minutes)
            if recent >= esc.violations:
                err = await moderation.punish(  # type: ignore[attr-defined]
                    guild, member, "timeout", esc.timeout_minutes,
                    f"Автомодерация: {recent} нарушений за {esc.window_minutes} мин", self.bot.user)
                actions.append(f"тайм-аут не выдан: {err}" if err else f"тайм-аут на {esc.timeout_minutes} мин")
                self.app.automod.engine.forget(guild.id, member.id)

        embed = discord.Embed(title=f"🛡️ Автомодерация: {v.rule_name}", color=COLOR_WARN,
                              timestamp=discord.utils.utcnow())
        embed.add_field(name="Участник", value=f"{member.mention} ({member})")
        embed.add_field(name="Канал", value=message.channel.mention)  # type: ignore[union-attr]
        embed.add_field(name="Причина", value=v.reason, inline=False)
        if message.content:
            embed.add_field(name="Сообщение", value=message.content[:500], inline=False)
        embed.add_field(name="Действия", value=", ".join(actions) or "только запись в журнал", inline=False)

        skip: set[int] = set()
        if cfg.log_channel_id:
            await safe_send(channel_from_id(guild, cfg.log_channel_id), embed=embed)
            skip.add(int(cfg.log_channel_id))
        logger = self.bot.get_cog("EventLogging")
        if logger is not None:
            await logger.log_event(  # type: ignore[attr-defined]
                guild, "automod", f"{member}: {v.rule_name} — {v.reason}", embed=embed, user_id=member.id,
                channel_id=message.channel.id, details={"rule": v.rule, "actions": actions},
                skip_channel_ids=skip)


async def setup(bot: MishanyaBot) -> None:
    await bot.add_cog(Automod(bot))
