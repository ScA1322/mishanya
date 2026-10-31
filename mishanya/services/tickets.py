"""Тикеты: обращения участников в отдельных приватных каналах."""
from __future__ import annotations

from sqlalchemy import func, select, update

from ..db import Database, utcnow
from ..db.models import Ticket


class TicketService:
    def __init__(self, db: Database):
        self.db = db

    async def create(self, guild_id: int, owner_id: int, owner_name: str, topic: str = "") -> Ticket:
        """Создаёт запись тикета со следующим номером на сервере (канал привязывается позже)."""
        async with self.db.session() as s:
            last = (await s.execute(select(func.max(Ticket.number))
                                    .where(Ticket.guild_id == guild_id))).scalar() or 0
            t = Ticket(guild_id=guild_id, number=last + 1, owner_id=owner_id, owner_name=owner_name[:100],
                       topic=topic[:1000])
            s.add(t)
            await s.commit()
            return t

    async def set_channel(self, ticket_id: int, channel_id: int) -> None:
        async with self.db.session() as s:
            await s.execute(update(Ticket).where(Ticket.id == ticket_id).values(channel_id=channel_id))
            await s.commit()

    async def delete(self, ticket_id: int) -> None:
        """Удаляет запись, если канал для тикета создать не удалось."""
        async with self.db.session() as s:
            t = await s.get(Ticket, ticket_id)
            if t is not None:
                await s.delete(t)
                await s.commit()

    async def get(self, guild_id: int, ticket_id: int) -> Ticket | None:
        async with self.db.session() as s:
            t = await s.get(Ticket, ticket_id)
            return t if t is not None and t.guild_id == guild_id else None

    async def by_channel(self, channel_id: int) -> Ticket | None:
        async with self.db.session() as s:
            return (await s.execute(select(Ticket).where(Ticket.channel_id == channel_id,
                                                         Ticket.status == "open"))).scalar_one_or_none()

    async def open_count(self, guild_id: int, owner_id: int | None = None) -> int:
        async with self.db.session() as s:
            q = select(func.count()).select_from(Ticket).where(Ticket.guild_id == guild_id, Ticket.status == "open")
            if owner_id is not None:
                q = q.where(Ticket.owner_id == owner_id)
            return (await s.execute(q)).scalar_one()

    async def claim(self, ticket_id: int, user_id: int) -> None:
        async with self.db.session() as s:
            await s.execute(update(Ticket).where(Ticket.id == ticket_id).values(claimed_by=user_id))
            await s.commit()

    async def close(self, ticket_id: int, closed_by: int | None, reason: str = "") -> bool:
        async with self.db.session() as s:
            res = await s.execute(update(Ticket).where(Ticket.id == ticket_id, Ticket.status == "open").values(
                status="closed", closed_at=utcnow(), closed_by=closed_by, close_reason=reason[:512]))
            await s.commit()
            return res.rowcount > 0

    async def list(self, guild_id: int, status: str | None = None, limit: int = 50,
                   offset: int = 0) -> tuple[list[Ticket], int]:
        async with self.db.session() as s:
            q = select(Ticket).where(Ticket.guild_id == guild_id)
            c = select(func.count()).select_from(Ticket).where(Ticket.guild_id == guild_id)
            if status:
                q, c = q.where(Ticket.status == status), c.where(Ticket.status == status)
            rows = list((await s.execute(q.order_by(Ticket.number.desc()).limit(limit).offset(offset))).scalars())
            return rows, (await s.execute(c)).scalar_one()
