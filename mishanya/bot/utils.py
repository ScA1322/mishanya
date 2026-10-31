"""Общие помощники для команд бота: embed-ы, проверки прав, безопасная отправка."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands

from ..schemas import EmbedConfig
from ..services.templates import render

log = logging.getLogger(__name__)

COLOR_OK = 0x3BA55D
COLOR_ERR = 0xED4245
COLOR_WARN = 0xFAA61A
COLOR_INFO = 0x5865F2

PERMISSION_NAMES = {
    "kick_members": "Выгонять участников",
    "ban_members": "Банить участников",
    "moderate_members": "Отправлять участников подумать (тайм-аут)",
    "manage_messages": "Управлять сообщениями",
    "manage_channels": "Управлять каналами",
    "manage_roles": "Управлять ролями",
    "manage_nicknames": "Управлять никнеймами",
    "manage_guild": "Управлять сервером",
    "send_messages": "Отправлять сообщения",
    "embed_links": "Встраивать ссылки",
    "read_message_history": "Читать историю сообщений",
    "add_reactions": "Добавлять реакции",
    "view_channel": "Просматривать каналы",
    "view_audit_log": "Просматривать журнал аудита",
    "connect": "Подключаться к голосовым каналам",
    "speak": "Говорить в голосовых каналах",
    "move_members": "Перемещать участников",
}


class UserFacingError(app_commands.AppCommandError):
    """Ошибка, текст которой можно показать пользователю как есть."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ok_embed(text: str, title: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=text, color=COLOR_OK)


def err_embed(text: str, title: str = "Не получилось") -> discord.Embed:
    return discord.Embed(title=f"⚠️ {title}", description=text, color=COLOR_ERR)


def info_embed(text: str = "", title: str | None = None, color: int = COLOR_INFO) -> discord.Embed:
    return discord.Embed(title=title, description=text or None, color=color, timestamp=now_utc())


async def respond(interaction: discord.Interaction, *, content: str | None = None,
                  embed: discord.Embed | None = None, ephemeral: bool = False,
                  view: discord.ui.View | None = None) -> None:
    kwargs: dict = {"content": content, "embed": embed, "ephemeral": ephemeral}
    if view is not None:
        kwargs["view"] = view
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


def require_guild(interaction: discord.Interaction) -> tuple[discord.Guild, discord.Member]:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        raise UserFacingError("Эта команда работает только на сервере.")
    return interaction.guild, interaction.user


def require_perms(interaction: discord.Interaction, *perms: str, bot_too: bool = True) -> None:
    """Проверяет права вызвавшего и бота в текущем канале. Скрытие команд в UI не заменяет эту проверку."""
    guild, member = require_guild(interaction)
    channel = interaction.channel
    user_perms = channel.permissions_for(member) if hasattr(channel, "permissions_for") else member.guild_permissions
    missing = [p for p in perms if not getattr(user_perms, p, False)]
    if missing and not user_perms.administrator:
        raise UserFacingError("Недостаточно прав: " + ", ".join(PERMISSION_NAMES.get(p, p) for p in missing))
    if bot_too:
        me = guild.me
        bot_perms = channel.permissions_for(me) if hasattr(channel, "permissions_for") else me.guild_permissions
        bot_missing = [p for p in perms if not getattr(bot_perms, p, False)]
        if bot_missing and not bot_perms.administrator:
            raise UserFacingError("У бота нет прав: " + ", ".join(PERMISSION_NAMES.get(p, p) for p in bot_missing))


def is_moderator(member: discord.Member) -> bool:
    p = member.guild_permissions
    return p.administrator or p.manage_guild or p.manage_messages


def channel_from_id(guild: discord.Guild, channel_id: str | int | None) -> discord.abc.Messageable | None:
    if not channel_id:
        return None
    ch = guild.get_channel_or_thread(int(channel_id))
    return ch if isinstance(ch, (discord.TextChannel, discord.Thread, discord.VoiceChannel)) else None


async def safe_send(channel: discord.abc.Messageable | None, **kwargs) -> discord.Message | None:  # noqa: ANN003
    """Отправляет сообщение, не роняя обработчик при отсутствии прав или удалённом канале."""
    if channel is None:
        return None
    kwargs.setdefault("allowed_mentions", discord.AllowedMentions(everyone=False, roles=False, users=True))
    try:
        return await channel.send(**kwargs)
    except discord.Forbidden:
        log.warning("Нет прав на отправку сообщения в канал %s", getattr(channel, "id", "?"))
    except discord.NotFound:
        log.warning("Канал %s не найден", getattr(channel, "id", "?"))
    except discord.HTTPException as exc:
        log.warning("Ошибка Discord API при отправке сообщения: %s", exc)
    return None


def build_embed(cfg: EmbedConfig, values: dict, avatar_url: str | None = None) -> discord.Embed:
    color = int(cfg.color.lstrip("#"), 16)
    embed = discord.Embed(
        title=render(cfg.title, values)[:256] or None,
        description=render(cfg.description, values)[:4000] or None,
        color=color,
    )
    if cfg.footer:
        embed.set_footer(text=render(cfg.footer, values)[:2048])
    if cfg.image_url:
        embed.set_image(url=cfg.image_url)
    if cfg.show_avatar and avatar_url:
        embed.set_thumbnail(url=avatar_url)
    return embed


def message_payload(text: str, embed_cfg: EmbedConfig | None, values: dict,
                    avatar_url: str | None = None) -> dict:
    payload: dict = {}
    content = render(text, values)[:2000] if text else ""
    if content:
        payload["content"] = content
    if embed_cfg is not None and embed_cfg.enabled:
        payload["embed"] = build_embed(embed_cfg, values, avatar_url)
    return payload


def fmt_dt(dt: datetime | None, style: str = "f") -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return discord.utils.format_dt(dt, style)


def human_error(exc: BaseException) -> str:
    if isinstance(exc, discord.Forbidden):
        return "Discord запретил действие: у бота недостаточно прав или роль бота ниже нужной."
    if isinstance(exc, discord.NotFound):
        return "Объект не найден — возможно, он уже удалён."
    if isinstance(exc, discord.HTTPException):
        return f"Ошибка Discord API ({exc.status}): {exc.text or 'без описания'}"
    return "Произошла внутренняя ошибка. Подробности записаны в журнал data/mishanya.log."


async def guarded(interaction: discord.Interaction, coro) -> None:  # noqa: ANN001
    """Выполняет обработчик кнопки или формы и показывает ошибку пользователю (у них нет on_error дерева команд)."""
    try:
        await coro
    except UserFacingError as exc:
        text = str(exc)
    except discord.HTTPException as exc:
        log.warning("Ошибка Discord API в обработчике кнопки: %s", exc)
        text = human_error(exc)
    except Exception as exc:  # noqa: BLE001
        log.exception("Ошибка в обработчике кнопки")
        text = human_error(exc)
    else:
        return
    try:
        await respond(interaction, embed=err_embed(text), ephemeral=True)
    except discord.HTTPException:
        pass
