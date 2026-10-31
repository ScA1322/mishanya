"""Учёт временных голосовых комнат, чтобы удалять их и после перезапуска бота."""
from __future__ import annotations

from sqlalchemy import delete, select, update

from ..db import Database
from ..db.models import TempVoiceChannel


class TempVoiceService:
    def __init__(self, db: Database):
        self.db = db

    async def add(self, guild_id: int, channel_id: int, owner_id: int, hub_id: int) -> None:
        async with self.db.session() as s:
            s.add(TempVoiceChannel(guild_id=guild_id, channel_id=channel_id, owner_id=owner_id, hub_id=hub_id))
            await s.commit()

    async def get(self, channel_id: int) -> TempVoiceChannel | None:
        async with self.db.session() as s:
            return await s.get(TempVoiceChannel, channel_id)

    async def owned_by(self, guild_id: int, owner_id: int) -> TempVoiceChannel | None:
        async with self.db.session() as s:
            return (await s.execute(select(TempVoiceChannel).where(
                TempVoiceChannel.guild_id == guild_id, TempVoiceChannel.owner_id == owner_id)
                .limit(1))).scalar_one_or_none()

    async def list(self, guild_id: int | None = None) -> list[TempVoiceChannel]:
        async with self.db.session() as s:
            q = select(TempVoiceChannel).order_by(TempVoiceChannel.created_at)
            if guild_id is not None:
                q = q.where(TempVoiceChannel.guild_id == guild_id)
            return list((await s.execute(q)).scalars())

    async def set_owner(self, channel_id: int, owner_id: int) -> None:
        async with self.db.session() as s:
            await s.execute(update(TempVoiceChannel).where(TempVoiceChannel.channel_id == channel_id)
                            .values(owner_id=owner_id))
            await s.commit()

    async def remove(self, channel_id: int) -> None:
        async with self.db.session() as s:
            await s.execute(delete(TempVoiceChannel).where(TempVoiceChannel.channel_id == channel_id))
            await s.commit()
