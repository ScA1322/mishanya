"""Предупреждения, журнал модерации и проверки иерархии ролей."""
from __future__ import annotations

from datetime import timedelta
from typing import Protocol

from sqlalchemy import func, select, update

from ..db import Database, utcnow
from ..db.models import ModCase, Warning
from ..schemas import ModerationConfig, WarnAction

# ------------------------------------------------------------------ иерархия


class _Role(Protocol):
    position: int


class _Member(Protocol):
    id: int
    top_role: _Role


def check_hierarchy(actor: _Member, target: _Member, bot: _Member, owner_id: int) -> str | None:
    """Проверяет, может ли `actor` (и бот) применить действие к `target`.

    Возвращает текст ошибки или None. Правила повторяют поведение Discord:
    нельзя действовать на себя, владельца сервера и на участников с ролью не ниже своей.
    """
    if target.id == actor.id:
        return "Нельзя применить это действие к самому себе."
    if target.id == bot.id:
        return "Нельзя применить это действие к самому боту."
    if target.id == owner_id:
        return "Нельзя применить это действие к владельцу сервера."
    if actor.id != owner_id and target.top_role.position >= actor.top_role.position:
        return "Роль участника не ниже вашей — действие запрещено иерархией ролей."
    if target.top_role.position >= bot.top_role.position:
        return "Роль участника не ниже роли бота. Переместите роль Mishanya выше в настройках сервера."
    return None


def check_role_manageable(role: _Role, actor: _Member, bot: _Member, owner_id: int,
                          managed: bool = False, is_default: bool = False) -> str | None:
    if is_default:
        return "Роль @everyone нельзя выдать или снять."
    if managed:
        return "Эта роль управляется интеграцией и не может быть выдана вручную."
    if role.position >= bot.top_role.position:
        return "Роль не ниже роли бота — Discord не позволит её выдать. Переместите роль Mishanya выше."
    if actor.id != owner_id and role.position >= actor.top_role.position:
        return "Роль не ниже вашей высшей роли."
    return None


def action_for_count(cfg: ModerationConfig, count: int) -> WarnAction | None:
    for a in cfg.warn_actions:
        if a.count == count:
            return a
    return None


# ------------------------------------------------------------------ сервис


class ModerationService:
    def __init__(self, db: Database):
        self.db = db

    # предупреждения
    async def add_warning(self, guild_id: int, user_id: int, moderator_id: int, reason: str) -> Warning:
        async with self.db.session() as s:
            w = Warning(guild_id=guild_id, user_id=user_id, moderator_id=moderator_id, reason=reason[:512])
            s.add(w)
            await s.commit()
            return w

    def _active_filter(self, q, cfg: ModerationConfig | None):  # noqa: ANN001, ANN202
        q = q.where(Warning.active.is_(True))
        if cfg and cfg.warning_expiry_days:
            q = q.where(Warning.created_at >= utcnow() - timedelta(days=cfg.warning_expiry_days))
        return q

    async def active_count(self, guild_id: int, user_id: int, cfg: ModerationConfig | None = None) -> int:
        async with self.db.session() as s:
            q = select(func.count()).select_from(Warning).where(
                Warning.guild_id == guild_id, Warning.user_id == user_id)
            return (await s.execute(self._active_filter(q, cfg))).scalar_one()

    async def list_warnings(self, guild_id: int, user_id: int | None = None, include_inactive: bool = False,
                            cfg: ModerationConfig | None = None, limit: int = 100, offset: int = 0
                            ) -> tuple[list[Warning], int]:
        async with self.db.session() as s:
            q = select(Warning).where(Warning.guild_id == guild_id)
            if user_id is not None:
                q = q.where(Warning.user_id == user_id)
            if not include_inactive:
                q = self._active_filter(q, cfg)
            total = (await s.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
            rows = (await s.execute(q.order_by(Warning.created_at.desc()).limit(limit).offset(offset))).scalars()
            return list(rows), total

    async def remove_warning(self, guild_id: int, warning_id: int, removed_by: int) -> bool:
        async with self.db.session() as s:
            res = await s.execute(update(Warning).where(
                Warning.id == warning_id, Warning.guild_id == guild_id, Warning.active.is_(True)
            ).values(active=False, removed_at=utcnow(), removed_by=removed_by))
            await s.commit()
            return res.rowcount > 0

    async def clear_warnings(self, guild_id: int, user_id: int, removed_by: int) -> int:
        async with self.db.session() as s:
            res = await s.execute(update(Warning).where(
                Warning.guild_id == guild_id, Warning.user_id == user_id, Warning.active.is_(True)
            ).values(active=False, removed_at=utcnow(), removed_by=removed_by))
            await s.commit()
            return res.rowcount

    # журнал модерации
    async def add_case(self, guild_id: int, action: str, target_id: int | None, target_name: str,
                       moderator_id: int | None, moderator_name: str, reason: str = "",
                       duration_seconds: int | None = None) -> ModCase:
        async with self.db.session() as s:
            last = (await s.execute(select(func.max(ModCase.case_number))
                                    .where(ModCase.guild_id == guild_id))).scalar() or 0
            case = ModCase(guild_id=guild_id, case_number=last + 1, action=action, target_id=target_id,
                           target_name=target_name[:100], moderator_id=moderator_id,
                           moderator_name=moderator_name[:100], reason=(reason or "")[:512],
                           duration_seconds=duration_seconds)
            s.add(case)
            await s.commit()
            return case

    async def list_cases(self, guild_id: int, user_id: int | None = None, action: str | None = None,
                         limit: int = 50, offset: int = 0) -> tuple[list[ModCase], int]:
        async with self.db.session() as s:
            q = select(ModCase).where(ModCase.guild_id == guild_id)
            if user_id is not None:
                q = q.where(ModCase.target_id == user_id)
            if action:
                q = q.where(ModCase.action == action)
            total = (await s.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
            rows = (await s.execute(q.order_by(ModCase.created_at.desc(), ModCase.id.desc())
                                    .limit(limit).offset(offset))).scalars()
            return list(rows), total
