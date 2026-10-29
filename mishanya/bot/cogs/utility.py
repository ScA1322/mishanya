"""Общие команды: /help, /ping, /avatar, /userinfo, /serverinfo, /poll, /invite."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ...schemas import UtilityConfig
from ..client import MishanyaBot
from ..utils import COLOR_INFO, UserFacingError, fmt_dt, require_guild, respond

NUMBER_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

HELP_SECTIONS = {
    "📈 Уровни": "/rank, /level, /profile, /leaderboard, /rewards\nАдмин: /xp give|take|set|setlevel|reset|resetserver",
    "🛡️ Модерация": "/kick, /ban, /unban, /timeout, /untimeout, /warn, /warnings, /unwarn, /clearwarnings, "
                    "/cases, /clear, /purge, /slowmode, /lock, /unlock, /nickname, /role add|remove",
    "🧰 Полезное": "/help, /ping, /avatar, /userinfo, /serverinfo, /poll, /invite",
    "⏰ Напоминания": "/remind, /reminders, /unremind",
    "✨ Команды сервера": "/cmd название — или с префиксом, например `!правила`",
}


class Utility(commands.Cog):
    def __init__(self, bot: MishanyaBot):
        self.bot = bot
        self.app = bot.app

    @app_commands.command(name="help", description="Список команд Mishanya")
    async def help(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(title="Mishanya — команды", color=COLOR_INFO,
                              description="Настройки модулей меняются в веб-панели Mishanya.")
        for name, value in HELP_SECTIONS.items():
            embed.add_field(name=name, value=value, inline=False)
        if interaction.guild:
            cmds = [c for c in await self.app.commands.list(interaction.guild.id) if c.enabled]
            if cmds:
                embed.add_field(name="Команды этого сервера",
                                value=", ".join(c.name for c in cmds)[:1000], inline=False)
        await respond(interaction, embed=embed, ephemeral=True)

    @app_commands.command(name="ping", description="Задержка соединения с Discord")
    async def ping(self, interaction: discord.Interaction) -> None:
        await respond(interaction, embed=discord.Embed(
            description=f"🏓 Понг! Задержка шлюза: **{round(self.bot.latency * 1000)} мс**", color=COLOR_INFO))

    @app_commands.command(name="avatar", description="Показать аватар пользователя")
    @app_commands.describe(user="Пользователь (по умолчанию — вы)")
    async def avatar(self, interaction: discord.Interaction, user: discord.User | None = None) -> None:
        target = user or interaction.user
        if interaction.guild and (m := interaction.guild.get_member(target.id)):
            target = m
        embed = discord.Embed(title=f"Аватар {target.display_name}", color=COLOR_INFO)
        embed.set_image(url=target.display_avatar.with_size(1024).url)
        embed.description = f"[Открыть в полном размере]({target.display_avatar.url})"
        await respond(interaction, embed=embed)

    @app_commands.command(name="userinfo", description="Информация об участнике")
    @app_commands.describe(member="Участник (по умолчанию — вы)")
    @app_commands.guild_only()
    async def userinfo(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        guild, user = require_guild(interaction)
        m = member or user
        embed = discord.Embed(title=str(m), color=m.color if m.color.value else COLOR_INFO)
        embed.set_thumbnail(url=m.display_avatar.url)
        embed.add_field(name="ID", value=str(m.id))
        embed.add_field(name="Отображаемое имя", value=m.display_name)
        embed.add_field(name="Бот", value="да" if m.bot else "нет")
        embed.add_field(name="Аккаунт создан", value=fmt_dt(m.created_at, "D") + "\n" + fmt_dt(m.created_at, "R"))
        embed.add_field(name="На сервере с", value=fmt_dt(m.joined_at, "D") + "\n" + fmt_dt(m.joined_at, "R"))
        if m.is_timed_out():
            embed.add_field(name="Тайм-аут до", value=fmt_dt(m.timed_out_until))
        roles = [r.mention for r in reversed(m.roles) if not r.is_default()]
        embed.add_field(name=f"Роли ({len(roles)})", value=" ".join(roles)[:1000] or "нет", inline=False)
        row = await self.app.leveling.get_member(guild.id, m.id)
        if row:
            embed.add_field(name="Уровень", value=f"{row.level} ({row.xp} XP)")
        await respond(interaction, embed=embed)

    @app_commands.command(name="serverinfo", description="Информация о сервере")
    @app_commands.guild_only()
    async def serverinfo(self, interaction: discord.Interaction) -> None:
        guild, _ = require_guild(interaction)
        embed = discord.Embed(title=guild.name, color=COLOR_INFO, description=guild.description or None)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="ID", value=str(guild.id))
        embed.add_field(name="Владелец", value=f"<@{guild.owner_id}>")
        embed.add_field(name="Создан", value=fmt_dt(guild.created_at, "D"))
        embed.add_field(name="Участников", value=str(guild.member_count))
        embed.add_field(name="Каналов", value=f"{len(guild.text_channels)} текст. / {len(guild.voice_channels)} голос.")
        embed.add_field(name="Ролей", value=str(len(guild.roles)))
        embed.add_field(name="Бусты", value=f"уровень {guild.premium_tier}, бустов: {guild.premium_subscription_count}")
        embed.add_field(name="Проверка", value=str(guild.verification_level))
        embed.add_field(name="Эмодзи", value=str(len(guild.emojis)))
        await respond(interaction, embed=embed)

    @app_commands.command(name="poll", description="Создать опрос с реакциями")
    @app_commands.describe(question="Вопрос", options="Варианты через ; (до 10). Пусто — опрос да/нет")
    @app_commands.guild_only()
    async def poll(self, interaction: discord.Interaction, question: app_commands.Range[str, 1, 256],
                   options: str = "") -> None:
        guild, user = require_guild(interaction)
        cfg = await self.app.config.get_typed(guild.id, "utility", UtilityConfig)
        if not cfg.polls_enabled:
            raise UserFacingError("Опросы отключены на этом сервере.")
        choices = [o.strip() for o in options.split(";") if o.strip()]
        if len(choices) == 1 or len(choices) > 10:
            raise UserFacingError("Укажите от 2 до 10 вариантов через «;» или оставьте поле пустым для да/нет.")
        if choices:
            emojis = NUMBER_EMOJI[: len(choices)]
            desc = "\n".join(f"{e} {c[:200]}" for e, c in zip(emojis, choices))
        else:
            emojis, desc = ["👍", "👎"], "👍 Да\n👎 Нет"
        embed = discord.Embed(title=f"📊 {question}", description=desc, color=COLOR_INFO)
        embed.set_footer(text=f"Опрос от {user.display_name}")
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        try:
            for e in emojis:
                await msg.add_reaction(e)
        except discord.HTTPException:
            await interaction.followup.send("Не удалось добавить реакции: у бота нет права «Добавлять реакции».",
                                            ephemeral=True)

    @app_commands.command(name="invite", description="Ссылка для приглашения Mishanya на сервер")
    async def invite(self, interaction: discord.Interaction) -> None:
        url = self.bot.invite_url()
        if not url:
            raise UserFacingError("Ссылка недоступна: бот ещё не получил ID приложения.")
        await respond(interaction, embed=discord.Embed(
            description=f"[Пригласить Mishanya]({url})\nСсылка запрашивает права, нужные для всех модулей.",
            color=COLOR_INFO), ephemeral=True)


async def setup(bot: MishanyaBot) -> None:
    await bot.add_cog(Utility(bot))
