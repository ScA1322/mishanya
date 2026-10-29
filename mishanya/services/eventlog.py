"""Локальный журнал событий серверов с ограничением объёма."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, select

from ..db import Database, utcnow
from ..db.models import EventLog


class EventLogService:
    def __init__(self, db: Database):
        self.db = db

    async def add(self, guild_id: int, event_type: str, summary: str, user_id: int | None = None,
                  channel_id: int | None = None, details: dict | None = None) -> None:
        async with self.db.session() as s:
            s.add(EventLog(guild_id=guild_id, event_type=event_type, summary=summary[:512], user_id=user_id,
                           channel_id=channel_id, details=details))
            await s.commit()

    async def query(self, guild_id: int | None = None, event_type: str | None = None, search: str = "",
                    limit: int = 50, offset: int = 0) -> tuple[list[EventLog], int]:
        async with self.db.session() as s:
            q = select(EventLog)
            if guild_id is not None:
                q = q.where(EventLog.guild_id == guild_id)
            if event_type:
                q = q.where(EventLog.event_type == event_type)
            if search:
                if search.isdigit():
                    q = q.where(EventLog.user_id == int(search))
                else:
                    q = q.where(EventLog.summary.ilike(f"%{search.replace('%', '')}%"))
            total = (await s.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
            rows = (await s.execute(q.order_by(EventLog.created_at.desc(), EventLog.id.desc())
                                    .limit(limit).offset(offset))).scalars()
            return list(rows), total

    async def counts_by_type(self, guild_id: int, days: int = 7) -> dict[str, int]:
        async with self.db.session() as s:
            rows = await s.execute(
                select(EventLog.event_type, func.count()).where(
                    EventLog.guild_id == guild_id, EventLog.created_at >= utcnow() - timedelta(days=days)
                ).group_by(EventLog.event_type))
            return {t: c for t, c in rows.all()}

    async def total(self) -> int:
        async with self.db.session() as s:
            return (await s.execute(select(func.count()).select_from(EventLog))).scalar_one()

    async def clear(self, guild_id: int, event_type: str | None = None) -> int:
        async with self.db.session() as s:
            q = delete(EventLog).where(EventLog.guild_id == guild_id)
            if event_type:
                q = q.where(EventLog.event_type == event_type)
            res = await s.execute(q)
            await s.commit()
            return res.rowcount

    async def cleanup(self, retention_days: int, max_per_guild: int) -> int:
        """Удаляет записи старше срока хранения и излишек сверх лимита на сервер."""
        removed = 0
        async with self.db.session() as s:
            res = await s.execute(delete(EventLog).where(
                EventLog.created_at < utcnow() - timedelta(days=retention_days)))
            removed += res.rowcount
            over = await s.execute(select(EventLog.guild_id, func.count()).group_by(EventLog.guild_id)
                                   .having(func.count() > max_per_guild))
            for guild_id, count in over.all():
                cutoff_id = (await s.execute(
                    select(EventLog.id).where(EventLog.guild_id == guild_id)
                    .order_by(EventLog.id.desc()).offset(max_per_guild - 1).limit(1))).scalar()
                if cutoff_id is not None:
                    res = await s.execute(delete(EventLog).where(
                        EventLog.guild_id == guild_id, EventLog.id < cutoff_id))
                    removed += res.rowcount
            await s.commit()
        return removed
