"""Напоминания, сохраняемые в базе и переживающие перезапуск."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from sqlalchemy import func, select, update

from ..db import Database, utcnow
from ..db.models import Reminder

DURATION_RE = re.compile(r"(\d+)\s*(нед|н|w|д|d|ч|h|мин|м|m|сек|с|s)", re.IGNORECASE)
UNIT_SECONDS = {
    "w": 604800, "н": 604800, "нед": 604800,
    "d": 86400, "д": 86400,
    "h": 3600, "ч": 3600,
    "m": 60, "м": 60, "мин": 60,
    "s": 1, "с": 1, "сек": 1,
}
MAX_REMINDER = timedelta(days=365)
MAX_PENDING_PER_USER = 25


def parse_duration(text: str) -> timedelta:
    """Разбирает строки вида `10m`, `1h30m`, `2д 3ч`, `45 мин`."""
    text = text.strip().lower()
    if not text:
        raise ValueError("Укажите время, например 10m, 1h30m или 2д")
    pos, total = 0, 0
    for m in DURATION_RE.finditer(text):
        if text[pos:m.start()].strip(" ,"):
            raise ValueError(f"Не удалось разобрать «{text}». Пример: 10m, 1h30m, 2д")
        total += int(m.group(1)) * UNIT_SECONDS[m.group(2).lower()]
        pos = m.end()
    if pos == 0 or text[pos:].strip(" ,"):
        raise ValueError(f"Не удалось разобрать «{text}». Пример: 10m, 1h30m, 2д")
    if total <= 0:
        raise ValueError("Время должно быть больше нуля")
    delta = timedelta(seconds=total)
    if delta > MAX_REMINDER:
        raise ValueError("Максимальный срок напоминания — 365 дней")
    return delta


def format_delta(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    parts = []
    for label, size in (("д", 86400), ("ч", 3600), ("мин", 60), ("с", 1)):
        if seconds >= size:
            parts.append(f"{seconds // size} {label}")
            seconds %= size
    return " ".join(parts[:2]) or "0 с"


class ReminderService:
    def __init__(self, db: Database):
        self.db = db

    async def create(self, user_id: int, text: str, due_at: datetime, guild_id: int | None,
                     channel_id: int | None) -> Reminder:
        async with self.db.session() as s:
            pending = (await s.execute(select(func.count()).select_from(Reminder).where(
                Reminder.user_id == user_id, Reminder.status == "pending"))).scalar_one()
            if pending >= MAX_PENDING_PER_USER:
                raise ValueError(f"У вас уже {pending} активных напоминаний (максимум {MAX_PENDING_PER_USER}).")
            r = Reminder(user_id=user_id, text=text[:1000], due_at=due_at, guild_id=guild_id,
                         channel_id=channel_id)
            s.add(r)
            await s.commit()
            return r

    async def due(self, now: datetime | None = None, limit: int = 50) -> list[Reminder]:
        async with self.db.session() as s:
            return list((await s.execute(select(Reminder).where(
                Reminder.status == "pending", Reminder.due_at <= (now or utcnow())
            ).order_by(Reminder.due_at).limit(limit))).scalars())

    async def list_for_user(self, user_id: int) -> list[Reminder]:
        async with self.db.session() as s:
            return list((await s.execute(select(Reminder).where(
                Reminder.user_id == user_id, Reminder.status == "pending").order_by(Reminder.due_at))).scalars())

    async def list_for_guild(self, guild_id: int, limit: int = 100) -> list[Reminder]:
        async with self.db.session() as s:
            return list((await s.execute(select(Reminder).where(
                Reminder.guild_id == guild_id).order_by(Reminder.due_at.desc()).limit(limit))).scalars())

    async def mark(self, reminder_id: int, status: str, error: str | None = None) -> None:
        async with self.db.session() as s:
            await s.execute(update(Reminder).where(Reminder.id == reminder_id).values(
                status=status, delivered_at=utcnow(), error=(error or None) and error[:256]))
            await s.commit()

    async def cancel(self, reminder_id: int, user_id: int | None = None) -> bool:
        async with self.db.session() as s:
            q = update(Reminder).where(Reminder.id == reminder_id, Reminder.status == "pending")
            if user_id is not None:
                q = q.where(Reminder.user_id == user_id)
            res = await s.execute(q.values(status="cancelled"))
            await s.commit()
            return res.rowcount > 0

    async def pending_count(self) -> int:
        async with self.db.session() as s:
            return (await s.execute(select(func.count()).select_from(Reminder)
                                    .where(Reminder.status == "pending"))).scalar_one()
