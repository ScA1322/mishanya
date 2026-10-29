"""Система XP и уровней: формулы, начисление, таблица лидеров, награды."""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from ..db import Database, utcnow
from ..db.models import LevelReward, MemberLevel
from ..schemas import LevelingConfig

# ------------------------------------------------------------------ формулы


def xp_to_next(level: int, cfg: LevelingConfig) -> int:
    """XP, необходимый для перехода с уровня `level` на следующий."""
    return cfg.formula_a * level * level + cfg.formula_b * level + cfg.formula_c


def total_xp_for_level(level: int, cfg: LevelingConfig) -> int:
    level = max(0, min(level, cfg.max_level))
    return sum(xp_to_next(i, cfg) for i in range(level))


@dataclass
class LevelProgress:
    level: int
    xp_into_level: int
    xp_needed: int  # 0, если достигнут максимальный уровень

    @property
    def percent(self) -> float:
        return 100.0 if self.xp_needed == 0 else round(100 * self.xp_into_level / self.xp_needed, 1)


def level_from_xp(xp: int, cfg: LevelingConfig) -> LevelProgress:
    level, remaining = 0, max(0, xp)
    while level < cfg.max_level:
        need = xp_to_next(level, cfg)
        if remaining < need:
            return LevelProgress(level, remaining, need)
        remaining -= need
        level += 1
    return LevelProgress(level, remaining, 0)


def progress_bar(percent: float, width: int = 12) -> str:
    filled = int(round(width * percent / 100))
    return "▰" * filled + "▱" * (width - filled)


def roll_xp(cfg: LevelingConfig, rng: random.Random | None = None) -> int:
    rng = rng or random
    return int(round(rng.randint(cfg.xp_min, cfg.xp_max) * cfg.xp_multiplier))


def is_xp_eligible(cfg: LevelingConfig, channel_id: int, category_id: int | None,
                   role_ids: list[int]) -> bool:
    if str(channel_id) in cfg.no_xp_channels:
        return False
    if category_id is not None and str(category_id) in cfg.no_xp_categories:
        return False
    no_roles = set(cfg.no_xp_roles)
    return not any(str(r) in no_roles for r in role_ids)


# ------------------------------------------------------------------ сервис


@dataclass
class AwardResult:
    awarded: int
    total_xp: int
    old_level: int
    new_level: int

    @property
    def leveled_up(self) -> bool:
        return self.new_level > self.old_level


class ImportEntry(BaseModel):
    user_id: str = Field(pattern=r"^\d{15,22}$")
    xp: int = Field(ge=0, le=10**12)
    message_count: int = Field(0, ge=0, le=10**9)
    username: str = Field("", max_length=100)


class ImportPayload(BaseModel):
    format: str = Field("mishanya-levels-v1")
    entries: list[ImportEntry] = Field(max_length=200_000)


class LevelingService:
    def __init__(self, db: Database):
        self.db = db
        self._last_content: dict[tuple[int, int], str] = {}

    async def process_message(
        self,
        guild_id: int,
        user_id: int,
        username: str,
        avatar_url: str | None,
        content: str,
        cfg: LevelingConfig,
        now: datetime | None = None,
        rng: random.Random | None = None,
    ) -> AwardResult:
        """Учитывает сообщение и начисляет XP, если прошла задержка и сообщение не спам."""
        now = now or utcnow()
        key = (guild_id, user_id)
        digest = hashlib.sha1(content.strip().lower().encode("utf-8")).hexdigest()
        duplicate = cfg.ignore_duplicate_messages and self._last_content.get(key) == digest
        self._last_content[key] = digest
        too_short = len(content.strip()) < cfg.min_message_length

        async with self.db.session() as s:
            row = await s.get(MemberLevel, (guild_id, user_id))
            if row is None:
                row = MemberLevel(guild_id=guild_id, user_id=user_id, xp=0, level=0, message_count=0)
                s.add(row)
            row.username = username[:100]
            row.avatar_url = avatar_url
            row.message_count = (row.message_count or 0) + 1
            row.left_at = None
            old_level = row.level or 0
            awarded = 0
            cooldown_ok = row.last_xp_at is None or now - row.last_xp_at >= timedelta(seconds=cfg.cooldown_seconds)
            if cooldown_ok and not duplicate and not too_short:
                awarded = roll_xp(cfg, rng)
                row.xp = (row.xp or 0) + awarded
                row.last_xp_at = now
            row.level = level_from_xp(row.xp or 0, cfg).level
            await s.commit()
            return AwardResult(awarded, row.xp, old_level, row.level)

    async def get_member(self, guild_id: int, user_id: int) -> MemberLevel | None:
        async with self.db.session() as s:
            return await s.get(MemberLevel, (guild_id, user_id))

    async def rank_of(self, guild_id: int, user_id: int, include_left: bool = False) -> int | None:
        async with self.db.session() as s:
            row = await s.get(MemberLevel, (guild_id, user_id))
            if row is None:
                return None
            q = select(func.count()).select_from(MemberLevel).where(
                MemberLevel.guild_id == guild_id, MemberLevel.xp > row.xp
            )
            if not include_left:
                q = q.where(MemberLevel.left_at.is_(None))
            return (await s.execute(q)).scalar_one() + 1

    async def leaderboard(self, guild_id: int, limit: int = 10, offset: int = 0,
                          include_left: bool = False, search: str = "") -> tuple[list[MemberLevel], int]:
        async with self.db.session() as s:
            base = select(MemberLevel).where(MemberLevel.guild_id == guild_id)
            if not include_left:
                base = base.where(MemberLevel.left_at.is_(None))
            if search:
                if search.isdigit():
                    base = base.where(MemberLevel.user_id == int(search))
                else:
                    like = f"%{search.replace('%', '').replace('_', '')}%"
                    base = base.where(MemberLevel.username.ilike(like))
            total = (await s.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
            rows = (await s.execute(
                base.order_by(MemberLevel.xp.desc(), MemberLevel.user_id).limit(limit).offset(offset)
            )).scalars().all()
            return list(rows), total

    async def set_xp(self, guild_id: int, user_id: int, xp: int, cfg: LevelingConfig,
                     username: str | None = None) -> AwardResult:
        xp = max(0, min(int(xp), 10**12))
        async with self.db.session() as s:
            row = await s.get(MemberLevel, (guild_id, user_id))
            if row is None:
                row = MemberLevel(guild_id=guild_id, user_id=user_id, xp=0, level=0, message_count=0,
                                  username=(username or "")[:100])
                s.add(row)
            elif username:
                row.username = username[:100]
            old_level = row.level or 0
            old_xp = row.xp or 0
            row.xp = xp
            row.level = level_from_xp(xp, cfg).level
            await s.commit()
            return AwardResult(xp - old_xp, xp, old_level, row.level)

    async def add_xp(self, guild_id: int, user_id: int, delta: int, cfg: LevelingConfig,
                     username: str | None = None) -> AwardResult:
        current = await self.get_member(guild_id, user_id)
        return await self.set_xp(guild_id, user_id, (current.xp if current else 0) + delta, cfg, username)

    async def set_level(self, guild_id: int, user_id: int, level: int, cfg: LevelingConfig,
                        username: str | None = None) -> AwardResult:
        return await self.set_xp(guild_id, user_id, total_xp_for_level(level, cfg), cfg, username)

    async def recalculate_levels(self, guild_id: int, cfg: LevelingConfig) -> int:
        """Пересчитывает уровни после изменения формулы."""
        async with self.db.session() as s:
            rows = (await s.execute(select(MemberLevel).where(MemberLevel.guild_id == guild_id))).scalars().all()
            for r in rows:
                r.level = level_from_xp(r.xp, cfg).level
            await s.commit()
            return len(rows)

    async def reset_user(self, guild_id: int, user_id: int) -> bool:
        async with self.db.session() as s:
            res = await s.execute(delete(MemberLevel).where(
                MemberLevel.guild_id == guild_id, MemberLevel.user_id == user_id))
            await s.commit()
        self._last_content.pop((guild_id, user_id), None)
        return res.rowcount > 0

    async def reset_guild(self, guild_id: int) -> int:
        async with self.db.session() as s:
            res = await s.execute(delete(MemberLevel).where(MemberLevel.guild_id == guild_id))
            await s.commit()
        for key in [k for k in self._last_content if k[0] == guild_id]:
            del self._last_content[key]
        return res.rowcount

    async def mark_left(self, guild_id: int, user_id: int, reset: bool) -> None:
        if reset:
            await self.reset_user(guild_id, user_id)
            return
        async with self.db.session() as s:
            row = await s.get(MemberLevel, (guild_id, user_id))
            if row is not None:
                row.left_at = utcnow()
                await s.commit()

    async def mark_rejoined(self, guild_id: int, user_id: int) -> MemberLevel | None:
        async with self.db.session() as s:
            row = await s.get(MemberLevel, (guild_id, user_id))
            if row is not None:
                row.left_at = None
                await s.commit()
            return row

    async def stats(self, guild_id: int) -> dict:
        async with self.db.session() as s:
            q = select(func.count(), func.coalesce(func.sum(MemberLevel.xp), 0),
                       func.coalesce(func.sum(MemberLevel.message_count), 0),
                       func.coalesce(func.max(MemberLevel.level), 0)).where(MemberLevel.guild_id == guild_id)
            count, xp, messages, max_level = (await s.execute(q)).one()
            return {"members": count, "total_xp": int(xp), "messages": int(messages), "max_level": int(max_level)}

    # -------------------------------------------------------------- награды

    async def list_rewards(self, guild_id: int) -> list[LevelReward]:
        async with self.db.session() as s:
            return list((await s.execute(
                select(LevelReward).where(LevelReward.guild_id == guild_id)
                .order_by(LevelReward.level, LevelReward.id))).scalars())

    async def add_reward(self, guild_id: int, level: int, role_id: int, enabled: bool = True) -> LevelReward:
        async with self.db.session() as s:
            exists = (await s.execute(select(LevelReward).where(
                LevelReward.guild_id == guild_id, LevelReward.level == level,
                LevelReward.role_id == role_id))).scalar_one_or_none()
            if exists is not None:
                raise ValueError("Такая награда уже существует")
            row = LevelReward(guild_id=guild_id, level=level, role_id=role_id, enabled=enabled)
            s.add(row)
            await s.commit()
            return row

    async def update_reward(self, guild_id: int, reward_id: int, **fields) -> LevelReward | None:  # noqa: ANN003
        async with self.db.session() as s:
            row = await s.get(LevelReward, reward_id)
            if row is None or row.guild_id != guild_id:
                return None
            for k, v in fields.items():
                if v is not None:
                    setattr(row, k, v)
            await s.commit()
            return row

    async def delete_reward(self, guild_id: int, reward_id: int) -> bool:
        async with self.db.session() as s:
            res = await s.execute(delete(LevelReward).where(
                LevelReward.id == reward_id, LevelReward.guild_id == guild_id))
            await s.commit()
            return res.rowcount > 0

    async def rewards_plan(self, guild_id: int, level: int, cfg: LevelingConfig,
                           from_level: int = 0) -> tuple[list[int], list[int]]:
        """Какие роли выдать и какие снять при достижении `level`.

        Выдаются только награды уровней в диапазоне (from_level, level] — так одна и та же
        награда не выдаётся повторно при каждом сообщении. При stack_rewards=False снимаются
        награды более низких уровней.
        """
        rewards = [r for r in await self.list_rewards(guild_id) if r.enabled]
        earned = [r for r in rewards if r.level <= level]
        to_add = [r.role_id for r in earned if r.level > from_level]
        to_remove: list[int] = []
        if not cfg.stack_rewards and earned:
            top = max(r.level for r in earned)
            keep = {r.role_id for r in earned if r.level == top}
            to_add = [rid for rid in to_add if rid in keep]
            to_remove = [r.role_id for r in earned if r.level < top and r.role_id not in keep]
        return list(dict.fromkeys(to_add)), list(dict.fromkeys(to_remove))

    # -------------------------------------------------------------- импорт/экспорт

    async def export(self, guild_id: int) -> dict:
        async with self.db.session() as s:
            rows = (await s.execute(select(MemberLevel).where(MemberLevel.guild_id == guild_id)
                                    .order_by(MemberLevel.xp.desc()))).scalars().all()
        return {
            "format": "mishanya-levels-v1",
            "guild_id": str(guild_id),
            "exported_at": utcnow().isoformat(),
            "entries": [
                {"user_id": str(r.user_id), "xp": r.xp, "message_count": r.message_count, "username": r.username}
                for r in rows
            ],
        }

    async def import_(self, guild_id: int, payload: ImportPayload, cfg: LevelingConfig,
                      mode: str = "merge") -> int:
        if payload.format != "mishanya-levels-v1":
            raise ValueError("Неподдерживаемый формат файла")
        if mode not in {"merge", "replace"}:
            raise ValueError("mode должен быть merge или replace")
        async with self.db.session() as s:
            if mode == "replace":
                await s.execute(delete(MemberLevel).where(MemberLevel.guild_id == guild_id))
            for e in payload.entries:
                uid = int(e.user_id)
                row = await s.get(MemberLevel, (guild_id, uid))
                if row is None:
                    row = MemberLevel(guild_id=guild_id, user_id=uid, xp=0, level=0, message_count=0)
                    s.add(row)
                row.xp = e.xp
                row.message_count = max(row.message_count or 0, e.message_count)
                if e.username:
                    row.username = e.username
                row.level = level_from_xp(e.xp, cfg).level
            await s.commit()
        return len(payload.entries)
