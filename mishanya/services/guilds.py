"""Учёт серверов, на которых находится (или находился) бот."""
from __future__ import annotations

from sqlalchemy import delete, select, update

from ..db import Database, utcnow
from ..db.models import Guild


class GuildService:
    def __init__(self, db: Database):
        self.db = db

    async def upsert(self, guild_id: int, name: str, icon_url: str | None = None,
                     member_count: int | None = None, present: bool = True) -> Guild:
        async with self.db.session() as s:
            row = await s.get(Guild, guild_id)
            if row is None:
                row = Guild(id=guild_id, name=name[:100], icon_url=icon_url, member_count=member_count,
                            present=present)
                s.add(row)
            else:
                row.name = name[:100]
                row.icon_url = icon_url
                if member_count is not None:
                    row.member_count = member_count
                if present and not row.present:
                    row.left_at = None
                row.present = present
            await s.commit()
            return row

    async def ensure(self, guild_id: int, name: str = "") -> None:
        async with self.db.session() as s:
            if await s.get(Guild, guild_id) is None:
                s.add(Guild(id=guild_id, name=name[:100]))
                await s.commit()

    async def mark_absent(self, guild_id: int) -> None:
        async with self.db.session() as s:
            await s.execute(update(Guild).where(Guild.id == guild_id).values(present=False, left_at=utcnow()))
            await s.commit()

    async def mark_all_absent_except(self, present_ids: set[int]) -> None:
        async with self.db.session() as s:
            q = update(Guild).where(Guild.present.is_(True))
            if present_ids:
                q = q.where(Guild.id.not_in(present_ids))
            await s.execute(q.values(present=False, left_at=utcnow()))
            await s.commit()

    async def get(self, guild_id: int) -> Guild | None:
        async with self.db.session() as s:
            return await s.get(Guild, guild_id)

    async def list(self) -> list[Guild]:
        async with self.db.session() as s:
            return list((await s.execute(select(Guild).order_by(Guild.present.desc(), Guild.name))).scalars())

    async def delete_data(self, guild_id: int) -> bool:
        """Удаляет сервер и все связанные данные (каскадно по внешним ключам)."""
        async with self.db.session() as s:
            res = await s.execute(delete(Guild).where(Guild.id == guild_id))
            await s.commit()
            return res.rowcount > 0
