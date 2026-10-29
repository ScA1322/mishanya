"""Проверки состояния сервера: права бота, доступность каналов и ролей из настроек."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..bot.client import REQUIRED_PERMISSIONS
from ..bot.utils import PERMISSION_NAMES
from ..schemas import (
    AutomodConfig,
    AutorolesConfig,
    LevelingConfig,
    LoggingConfig,
    ModerationConfig,
    WelcomeConfig,
)
from .deps import discord_guild

if TYPE_CHECKING:
    from ..core import App

SEND_PERMS = ("view_channel", "send_messages", "embed_links")


async def guild_issues(app: "App", guild_id: int) -> list[dict]:
    guild = discord_guild(app, guild_id)
    if guild is None:
        return []
    issues: list[dict] = []

    def add(level: str, module: str, message: str) -> None:
        issues.append({"level": level, "module": module, "message": message})

    me = guild.me
    perms = me.guild_permissions
    if not perms.administrator:
        missing = [n for n, v in REQUIRED_PERMISSIONS if v and not getattr(perms, n, False)]
        for name in missing:
            add("warning", "general", f"У бота нет права «{PERMISSION_NAMES.get(name, name)}»")

    cfgs = await app.config.all_for_guild(guild_id)
    lev: LevelingConfig = cfgs["leveling"]  # type: ignore[assignment]
    mod: ModerationConfig = cfgs["moderation"]  # type: ignore[assignment]
    am: AutomodConfig = cfgs["automod"]  # type: ignore[assignment]
    wel: WelcomeConfig = cfgs["welcome"]  # type: ignore[assignment]
    ar: AutorolesConfig = cfgs["autoroles"]  # type: ignore[assignment]
    lg: LoggingConfig = cfgs["logging"]  # type: ignore[assignment]

    channels: list[tuple[str, str, str | None]] = [
        ("leveling", "Канал уведомлений об уровне", lev.announce_channel_id if lev.announce_mode == "channel" else None),
        ("moderation", "Канал журнала модерации", mod.log_channel_id),
        ("automod", "Канал журнала автомодерации", am.log_channel_id if am.enabled else None),
        ("welcome", "Канал приветствий", wel.join.channel_id if wel.join.enabled else None),
        ("welcome", "Канал прощаний", wel.leave.channel_id if wel.leave.enabled else None),
        ("logging", "Канал журнала по умолчанию", lg.default_channel_id if lg.enabled else None),
    ]
    if lg.enabled:
        for key, ev in lg.events.items():
            if ev.enabled and ev.channel_id:
                channels.append(("logging", f"Канал журнала «{key}»", ev.channel_id))
    for module, label, cid in channels:
        if not cid:
            continue
        ch = guild.get_channel(int(cid))
        if ch is None:
            add("error", module, f"{label}: канал удалён или недоступен")
            continue
        cperms = ch.permissions_for(me)
        missing = [PERMISSION_NAMES[p] for p in SEND_PERMS if not getattr(cperms, p)]
        if missing:
            add("error", module, f"{label} #{ch.name}: нет прав — {', '.join(missing)}")

    def check_role(module: str, label: str, rid: str | int) -> None:
        role = guild.get_role(int(rid))
        if role is None:
            add("error", module, f"{label}: роль {rid} удалена")
        elif role.managed:
            add("error", module, f"{label} «{role.name}»: роль управляется интеграцией")
        elif role.position >= me.top_role.position:
            add("error", module, f"{label} «{role.name}»: роль не ниже роли бота — переместите роль Mishanya выше")
        elif not perms.manage_roles:
            add("error", module, f"{label} «{role.name}»: у бота нет права «Управлять ролями»")

    if lev.enabled:
        for r in await app.leveling.list_rewards(guild_id):
            if r.enabled:
                check_role("leveling", f"Награда за уровень {r.level}", r.role_id)
    if ar.enabled:
        for rid in ar.roles:
            check_role("autoroles", "Автороль", rid)
        for rid in ar.bot_roles:
            check_role("autoroles", "Автороль для ботов", rid)
    if am.enabled and (am.escalation.enabled or any(
            getattr(am, k).enabled and getattr(am, k).delete_message
            for k in ("spam", "flood", "duplicates", "mentions", "words", "links", "invites"))):
        if not perms.manage_messages and not perms.administrator:
            add("error", "automod", "Автомодерация не сможет удалять сообщения: нет права «Управлять сообщениями»")
        if am.escalation.enabled and not perms.moderate_members and not perms.administrator:
            add("error", "automod", "Автомодерация не сможет выдавать тайм-аут: нет права «Отправлять подумать»")
    return issues
