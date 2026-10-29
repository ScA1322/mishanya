"""Система уровней: начисление XP, уведомления, награды, команды /rank, /leaderboard, /xp."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ...schemas import LevelingConfig
from ...services.leveling import is_xp_eligible, level_from_xp, progress_bar
from ...services.templates import member_values, render
from ..client import MishanyaBot
from ..utils import (
    COLOR_INFO,
    UserFacingError,
    channel_from_id,
    fmt_dt,
    ok_embed,
    require_guild,
    require_perms,
    respond,
    safe_send,
)

log = logging.getLogger(__name__)
PAGE_SIZE = 10


class LeaderboardView(discord.ui.View):
    def __init__(self, cog: "Leveling", guild: discord.Guild, author_id: int, total: int):
        super().__init__(timeout=180)
        self.cog, self.guild, self.author_id = cog, guild, author_id
        self.page = 0
        self.pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.prev.disabled = self.page == 0
        self.next.disabled = self.page >= self.pages - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Листать может только тот, кто вызвал команду.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        self._update_buttons()
        embed, _ = await self.cog.leaderboard_embed(self.guild, self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.page = min(self.pages - 1, self.page + 1)
        self._update_buttons()
        embed, _ = await self.cog.leaderboard_embed(self.guild, self.page)
        await interaction.response.edit_message(embed=embed, view=self)


class Leveling(commands.Cog):
    xp_group = app_commands.Group(name="xp", description="Управление XP участников",
                                  guild_only=True, default_permissions=discord.Permissions(manage_guild=True))

    def __init__(self, bot: MishanyaBot):
        self.bot = bot
        self.app = bot.app

    async def cfg(self, guild_id: int) -> LevelingConfig:
        return await self.app.config.get_typed(guild_id, "leveling", LevelingConfig)

    # ------------------------------------------------------------ начисление XP

    async def handle_message(self, message: discord.Message) -> None:
        assert message.guild is not None and isinstance(message.author, discord.Member)
        cfg = await self.cfg(message.guild.id)
        if not cfg.enabled:
            return
        category_id = getattr(message.channel, "category_id", None)
        if isinstance(message.channel, discord.Thread) and message.channel.parent is not None:
            parent = message.channel.parent
            if str(parent.id) in cfg.no_xp_channels:
                return
            category_id = parent.category_id
        if not is_xp_eligible(cfg, message.channel.id, category_id, [r.id for r in message.author.roles]):
            return
        await self.app.guilds.ensure(message.guild.id, message.guild.name)
        result = await self.app.leveling.process_message(
            message.guild.id, message.author.id, str(message.author), message.author.display_avatar.url,
            message.content or ("[вложение]" if message.attachments else ""), cfg)
        if result.leveled_up:
            await self.on_level_up(message.author, result.old_level, result.new_level, result.total_xp, cfg,
                                   message.channel)

    async def on_level_up(self, member: discord.Member, old_level: int, new_level: int, total_xp: int,
                          cfg: LevelingConfig, channel: discord.abc.Messageable | None = None) -> None:
        await self.sync_rewards(member, new_level, cfg, from_level=old_level)
        if cfg.announce_mode == "off" or not cfg.levelup_message.strip():
            return
        rank = await self.app.leveling.rank_of(member.guild.id, member.id)
        text = render(cfg.levelup_message, member_values(member, channel=channel, level=new_level, xp=total_xp,
                                                         rank=rank or "?"))[:2000]
        if cfg.announce_mode == "dm":
            try:
                await member.send(text)
            except discord.HTTPException:
                pass
            return
        target = channel_from_id(member.guild, cfg.announce_channel_id) if cfg.announce_mode == "channel" else channel
        await safe_send(target, content=text)

    async def sync_rewards(self, member: discord.Member, level: int, cfg: LevelingConfig,
                           from_level: int | None = None) -> list[str]:
        """Выдаёт/снимает роли-награды. from_level=None — полная синхронизация (снимает и незаработанные).

        Возвращает список проблем (для панели), не бросает исключений Discord.
        """
        guild = member.guild
        me = guild.me
        problems: list[str] = []
        to_add_ids, to_remove_ids = await self.app.leveling.rewards_plan(
            guild.id, level, cfg, from_level=0 if from_level is None else from_level)
        if from_level is None:
            earned = set(to_add_ids)
            all_reward_roles = {r.role_id for r in await self.app.leveling.list_rewards(guild.id) if r.enabled}
            to_remove_ids = list(set(to_remove_ids) | (all_reward_roles - earned))
        if not (to_add_ids or to_remove_ids):
            return problems
        if not me.guild_permissions.manage_roles:
            problems.append("У бота нет права «Управлять ролями» — награды за уровни не выдаются.")
            log.warning("Сервер %s: %s", guild.id, problems[-1])
            return problems

        def usable(role_id: int) -> discord.Role | None:
            role = guild.get_role(role_id)
            if role is None:
                problems.append(f"Роль {role_id} удалена — обновите награды за уровни.")
                return None
            if role.position >= me.top_role.position or role.managed:
                problems.append(f"Роль «{role.name}» выше роли бота или управляется интеграцией.")
                return None
            return role

        add = [r for rid in to_add_ids if (r := usable(rid)) and r not in member.roles]
        remove = [r for rid in to_remove_ids if (r := usable(rid)) and r in member.roles]
        try:
            if add:
                await member.add_roles(*add, reason=f"Mishanya: награда за уровень {level}")
            if remove:
                await member.remove_roles(*remove, reason=f"Mishanya: смена награды за уровень {level}")
        except discord.HTTPException as exc:
            problems.append(f"Discord отклонил изменение ролей: {exc}")
        for p in problems:
            log.warning("Сервер %s, участник %s: %s", guild.id, member.id, p)
        return problems

    async def sync_member_by_id(self, guild_id: int, user_id: int) -> list[str]:
        """Синхронизация наград после изменения XP через панель."""
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return ["Бот не подключён к этому серверу — роли будут синхронизированы позже."]
        member = guild.get_member(user_id)
        if member is None:
            return []
        cfg = await self.cfg(guild_id)
        row = await self.app.leveling.get_member(guild_id, user_id)
        return await self.sync_rewards(member, row.level if row else 0, cfg)

    # ------------------------------------------------------------ вход/выход

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        cfg = await self.cfg(member.guild.id)
        await self.app.leveling.mark_left(member.guild.id, member.id, reset=cfg.reset_on_leave)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        row = await self.app.leveling.mark_rejoined(member.guild.id, member.id)
        if row is None:
            return
        cfg = await self.cfg(member.guild.id)
        if cfg.enabled and cfg.restore_rewards_on_rejoin and row.level > 0:
            await self.sync_rewards(member, row.level, cfg, from_level=0)

    # ------------------------------------------------------------ команды

    async def _card(self, guild: discord.Guild, member: discord.Member | discord.User, detailed: bool) -> discord.Embed:
        cfg = await self.cfg(guild.id)
        row = await self.app.leveling.get_member(guild.id, member.id)
        xp = row.xp if row else 0
        prog = level_from_xp(xp, cfg)
        rank = await self.app.leveling.rank_of(guild.id, member.id) if row else None
        embed = discord.Embed(title=f"{member.display_name}", color=COLOR_INFO)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Уровень", value=f"**{prog.level}**")
        embed.add_field(name="Место", value=f"#{rank}" if rank else "—")
        embed.add_field(name="Всего XP", value=f"{xp:,}".replace(",", " "))
        need = f"{prog.xp_into_level}/{prog.xp_needed} XP" if prog.xp_needed else "максимальный уровень"
        embed.add_field(name="Прогресс", value=f"{progress_bar(prog.percent)} {prog.percent:.0f}%\n{need}",
                        inline=False)
        if detailed:
            embed.add_field(name="Сообщений", value=str(row.message_count if row else 0))
            if isinstance(member, discord.Member):
                embed.add_field(name="На сервере с", value=fmt_dt(member.joined_at, "D"))
                warns = await self.app.moderation.active_count(guild.id, member.id)
                embed.add_field(name="Активных предупреждений", value=str(warns))
                rewards = [r for r in await self.app.leveling.list_rewards(guild.id) if r.enabled]
                nxt = next((r for r in rewards if r.level > prog.level), None)
                if nxt:
                    embed.add_field(name="Следующая награда", value=f"<@&{nxt.role_id}> на уровне {nxt.level}",
                                    inline=False)
            embed.add_field(name="Аккаунт создан", value=fmt_dt(member.created_at, "D"))
        if not cfg.enabled:
            embed.set_footer(text="Система уровней на этом сервере отключена")
        return embed

    @app_commands.command(name="rank", description="Карточка уровня участника")
    @app_commands.describe(member="Участник (по умолчанию — вы)")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        guild, user = require_guild(interaction)
        await respond(interaction, embed=await self._card(guild, member or user, detailed=False))

    @app_commands.command(name="level", description="Уровень и XP до следующего уровня")
    @app_commands.describe(member="Участник (по умолчанию — вы)")
    @app_commands.guild_only()
    async def level(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        guild, user = require_guild(interaction)
        target = member or user
        cfg = await self.cfg(guild.id)
        row = await self.app.leveling.get_member(guild.id, target.id)
        prog = level_from_xp(row.xp if row else 0, cfg)
        left = prog.xp_needed - prog.xp_into_level
        text = (f"{target.mention}: уровень **{prog.level}**, всего **{row.xp if row else 0}** XP.\n"
                + (f"До уровня {prog.level + 1}: **{left}** XP." if prog.xp_needed else "Достигнут максимальный уровень."))
        await respond(interaction, embed=discord.Embed(description=text, color=COLOR_INFO))

    @app_commands.command(name="profile", description="Профиль участника: уровень, статистика, награды")
    @app_commands.describe(member="Участник (по умолчанию — вы)")
    @app_commands.guild_only()
    async def profile(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        guild, user = require_guild(interaction)
        await respond(interaction, embed=await self._card(guild, member or user, detailed=True))

    async def leaderboard_embed(self, guild: discord.Guild, page: int) -> tuple[discord.Embed, int]:
        cfg = await self.cfg(guild.id)
        rows, total = await self.app.leveling.leaderboard(guild.id, PAGE_SIZE, page * PAGE_SIZE,
                                                          include_left=not cfg.hide_left_members)
        lines = []
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for i, r in enumerate(rows, start=page * PAGE_SIZE + 1):
            lines.append(f"{medals.get(i, f'`#{i}`')} <@{r.user_id}> — уровень **{r.level}**, {r.xp} XP")
        embed = discord.Embed(title=f"🏆 Таблица лидеров — {guild.name}", color=COLOR_INFO,
                              description="\n".join(lines) or "Пока никто не получил XP.")
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        embed.set_footer(text=f"Страница {page + 1}/{pages} · участников: {total}")
        return embed, total

    @app_commands.command(name="leaderboard", description="Таблица лидеров сервера по XP")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        guild, user = require_guild(interaction)
        embed, total = await self.leaderboard_embed(guild, 0)
        view = LeaderboardView(self, guild, user.id, total) if total > PAGE_SIZE else None
        await respond(interaction, embed=embed, view=view)

    # ------------------------------------------------------------ администрирование XP

    async def _admin_change(self, interaction: discord.Interaction, member: discord.Member, op: str,
                            value: int) -> None:
        guild, _ = require_guild(interaction)
        require_perms(interaction, "manage_guild", bot_too=False)
        if member.bot:
            raise UserFacingError("Боты не участвуют в системе уровней.")
        cfg = await self.cfg(guild.id)
        await self.app.guilds.ensure(guild.id, guild.name)
        svc = self.app.leveling
        if op == "give":
            res = await svc.add_xp(guild.id, member.id, value, cfg, str(member))
        elif op == "take":
            res = await svc.add_xp(guild.id, member.id, -value, cfg, str(member))
        elif op == "set":
            res = await svc.set_xp(guild.id, member.id, value, cfg, str(member))
        else:
            res = await svc.set_level(guild.id, member.id, value, cfg, str(member))
        problems = await self.sync_rewards(member, res.new_level, cfg)
        text = f"{member.mention}: теперь **{res.total_xp}** XP, уровень **{res.new_level}**."
        if problems:
            text += "\n⚠️ " + "\n⚠️ ".join(problems[:3])
        await respond(interaction, embed=ok_embed(text, "XP обновлён"), ephemeral=True)

    @xp_group.command(name="give", description="Добавить XP участнику")
    @app_commands.describe(member="Участник", amount="Сколько XP добавить")
    async def xp_give(self, interaction: discord.Interaction, member: discord.Member,
                      amount: app_commands.Range[int, 1, 10_000_000]) -> None:
        await self._admin_change(interaction, member, "give", amount)

    @xp_group.command(name="take", description="Отнять XP у участника")
    @app_commands.describe(member="Участник", amount="Сколько XP отнять")
    async def xp_take(self, interaction: discord.Interaction, member: discord.Member,
                      amount: app_commands.Range[int, 1, 10_000_000]) -> None:
        await self._admin_change(interaction, member, "take", amount)

    @xp_group.command(name="set", description="Установить точное количество XP")
    @app_commands.describe(member="Участник", amount="Новое количество XP")
    async def xp_set(self, interaction: discord.Interaction, member: discord.Member,
                     amount: app_commands.Range[int, 0, 1_000_000_000]) -> None:
        await self._admin_change(interaction, member, "set", amount)

    @xp_group.command(name="setlevel", description="Установить уровень участника")
    @app_commands.describe(member="Участник", level="Новый уровень")
    async def xp_setlevel(self, interaction: discord.Interaction, member: discord.Member,
                          level: app_commands.Range[int, 0, 5000]) -> None:
        await self._admin_change(interaction, member, "level", level)

    @xp_group.command(name="reset", description="Сбросить статистику участника")
    @app_commands.describe(member="Участник")
    async def xp_reset(self, interaction: discord.Interaction, member: discord.Member) -> None:
        guild, _ = require_guild(interaction)
        require_perms(interaction, "manage_guild", bot_too=False)
        removed = await self.app.leveling.reset_user(guild.id, member.id)
        await self.sync_rewards(member, 0, await self.cfg(guild.id))
        await respond(interaction, embed=ok_embed(
            f"Статистика {member.mention} сброшена." if removed else f"У {member.mention} не было статистики."),
            ephemeral=True)

    @xp_group.command(name="resetserver", description="Сбросить XP ВСЕХ участников сервера")
    @app_commands.describe(confirm="Подтвердите сброс: выберите True")
    async def xp_resetserver(self, interaction: discord.Interaction, confirm: bool) -> None:
        guild, _ = require_guild(interaction)
        require_perms(interaction, "manage_guild", bot_too=False)
        if not confirm:
            raise UserFacingError("Сброс не выполнен: для подтверждения укажите confirm: True.")
        count = await self.app.leveling.reset_guild(guild.id)
        await respond(interaction, embed=ok_embed(f"Сброшена статистика {count} участников. "
                                                  "Роли-награды не снимались автоматически."), ephemeral=True)

    @app_commands.command(name="rewards", description="Список ролей за уровни")
    @app_commands.guild_only()
    async def rewards(self, interaction: discord.Interaction) -> None:
        guild, _ = require_guild(interaction)
        rewards = [r for r in await self.app.leveling.list_rewards(guild.id) if r.enabled]
        text = "\n".join(f"Уровень **{r.level}** → <@&{r.role_id}>" for r in rewards) or "Награды не настроены."
        await respond(interaction, embed=discord.Embed(title="🎁 Роли за уровни", description=text,
                                                       color=COLOR_INFO), ephemeral=True)


async def setup(bot: MishanyaBot) -> None:
    await bot.add_cog(Leveling(bot))
