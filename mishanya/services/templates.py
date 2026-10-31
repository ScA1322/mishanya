"""Безопасная подстановка переменных в шаблоны сообщений.

Поддерживается только `{имя}` из белого списка и `{choose:a|b|c}`.
Никакого str.format, eval и доступа к атрибутам объектов.
"""
from __future__ import annotations

import random
import re

VAR_RE = re.compile(r"\{([a-z_]{1,32})(?::([^{}]{1,500}))?\}")

TEMPLATE_VARIABLES: dict[str, str] = {
    "user": "Упоминание участника",
    "username": "Имя пользователя",
    "display_name": "Отображаемое имя на сервере",
    "user_id": "ID пользователя",
    "server": "Название сервера",
    "server_id": "ID сервера",
    "member_count": "Количество участников",
    "channel": "Упоминание канала",
    "level": "Уровень (уведомления об уровне, команды)",
    "xp": "Всего XP",
    "rank": "Место в таблице лидеров",
    "args": "Аргументы пользовательской команды",
    "number": "Номер тикета",
    "topic": "Тема тикета",
    "choose": "Случайный вариант: {choose:вариант1|вариант2}",
}


def render(template: str, values: dict[str, object], rng: random.Random | None = None) -> str:
    rng = rng or random

    def repl(m: re.Match[str]) -> str:
        name, arg = m.group(1), m.group(2)
        if name == "choose" and arg:
            options = [o for o in arg.split("|")]
            return rng.choice(options) if options else ""
        if name in values and arg is None:
            return str(values[name])
        return m.group(0)

    return VAR_RE.sub(repl, template)


def member_values(member, guild=None, channel=None, **extra) -> dict[str, object]:  # noqa: ANN001
    """Собирает значения переменных из объектов discord.py (или похожих)."""
    guild = guild or getattr(member, "guild", None)
    values: dict[str, object] = {
        "user": getattr(member, "mention", ""),
        "username": getattr(member, "name", ""),
        "display_name": getattr(member, "display_name", getattr(member, "name", "")),
        "user_id": getattr(member, "id", ""),
    }
    if guild is not None:
        values["server"] = guild.name
        values["server_id"] = guild.id
        values["member_count"] = guild.member_count or 0
    if channel is not None:
        values["channel"] = getattr(channel, "mention", f"#{getattr(channel, 'name', '')}")
    values.update(extra)
    return values


SAMPLE_VALUES: dict[str, object] = {
    "user": "@Участник",
    "username": "uchastnik",
    "display_name": "Участник",
    "user_id": "123456789012345678",
    "server": "Мой сервер",
    "server_id": "987654321098765432",
    "member_count": 128,
    "channel": "#общий",
    "level": 5,
    "xp": 1234,
    "rank": 3,
    "args": "аргументы",
    "number": "0042",
    "topic": "Не приходит роль после оплаты",
}
