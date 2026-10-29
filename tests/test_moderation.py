from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import update

from mishanya.db import utcnow
from mishanya.db.models import Warning
from mishanya.schemas import ModerationConfig, WarnAction
from mishanya.services.moderation import action_for_count, check_hierarchy, check_role_manageable

from .conftest import GUILD_A, GUILD_B, USER_1, USER_2

pytestmark = pytest.mark.anyio
OWNER = 900


def member(id_: int, pos: int):
    return NS(id=id_, top_role=NS(position=pos))


BOT = member(999, 50)


def test_hierarchy_rules():
    mod = member(1, 20)
    assert check_hierarchy(mod, member(2, 10), BOT, OWNER) is None
    assert "самому себе" in check_hierarchy(mod, mod, BOT, OWNER)
    assert "боту" in check_hierarchy(mod, BOT, BOT, OWNER) or "бот" in check_hierarchy(mod, BOT, BOT, OWNER)
    assert "владельцу" in check_hierarchy(mod, member(OWNER, 1), BOT, OWNER)
    assert "не ниже вашей" in check_hierarchy(mod, member(3, 20), BOT, OWNER)  # равная роль
    assert "не ниже вашей" in check_hierarchy(mod, member(3, 30), BOT, OWNER)
    # владелец может действовать на любую роль ниже бота
    assert check_hierarchy(member(OWNER, 1), member(3, 40), BOT, OWNER) is None
    # но не на роль выше бота
    assert "роли бота" in check_hierarchy(member(OWNER, 1), member(3, 60), BOT, OWNER)


def test_role_manageable():
    mod = member(1, 20)
    assert check_role_manageable(NS(position=10), mod, BOT, OWNER) is None
    assert check_role_manageable(NS(position=60), mod, BOT, OWNER) is not None
    assert check_role_manageable(NS(position=25), mod, BOT, OWNER) is not None
    assert check_role_manageable(NS(position=5), mod, BOT, OWNER, managed=True) is not None
    assert check_role_manageable(NS(position=0), mod, BOT, OWNER, is_default=True) is not None


def test_warn_action_thresholds_validation():
    cfg = ModerationConfig(warn_actions=[WarnAction(count=5, action="ban"), WarnAction(count=3, action="timeout")])
    assert [a.count for a in cfg.warn_actions] == [3, 5]
    assert action_for_count(cfg, 3).action == "timeout"
    assert action_for_count(cfg, 4) is None
    with pytest.raises(ValueError):
        ModerationConfig(warn_actions=[WarnAction(count=3, action="ban"), WarnAction(count=3, action="kick")])
    with pytest.raises(ValueError):
        WarnAction(count=1, action="timeout", duration_minutes=40321)  # больше 28 дней


async def test_warnings_lifecycle(app):
    svc = app.moderation
    w1 = await svc.add_warning(GUILD_A, USER_1, USER_2, "спам")
    await svc.add_warning(GUILD_A, USER_1, USER_2, "флуд")
    await svc.add_warning(GUILD_B, USER_1, USER_2, "другой сервер")
    assert await svc.active_count(GUILD_A, USER_1) == 2
    assert await svc.active_count(GUILD_B, USER_1) == 1

    assert not await svc.remove_warning(GUILD_B, w1.id, USER_2)  # нельзя снять с чужого сервера
    assert await svc.remove_warning(GUILD_A, w1.id, USER_2)
    assert not await svc.remove_warning(GUILD_A, w1.id, USER_2)  # повторно
    assert await svc.active_count(GUILD_A, USER_1) == 1
    _, total_all = await svc.list_warnings(GUILD_A, USER_1, include_inactive=True)
    assert total_all == 2

    assert await svc.clear_warnings(GUILD_A, USER_1, USER_2) == 1
    assert await svc.active_count(GUILD_A, USER_1) == 0
    assert await svc.active_count(GUILD_B, USER_1) == 1


async def test_warning_expiry(app):
    svc = app.moderation
    old = await svc.add_warning(GUILD_A, USER_1, USER_2, "старое")
    await svc.add_warning(GUILD_A, USER_1, USER_2, "новое")
    async with app.db.session() as s:
        await s.execute(update(Warning).where(Warning.id == old.id).values(created_at=utcnow() - timedelta(days=40)))
        await s.commit()
    assert await svc.active_count(GUILD_A, USER_1, ModerationConfig(warning_expiry_days=30)) == 1
    assert await svc.active_count(GUILD_A, USER_1, ModerationConfig(warning_expiry_days=0)) == 2


async def test_case_numbers_per_guild(app):
    svc = app.moderation
    a1 = await svc.add_case(GUILD_A, "warn", USER_1, "u1", USER_2, "mod", "r")
    a2 = await svc.add_case(GUILD_A, "ban", USER_1, "u1", USER_2, "mod", "r")
    b1 = await svc.add_case(GUILD_B, "kick", USER_1, "u1", USER_2, "mod", "r")
    assert (a1.case_number, a2.case_number, b1.case_number) == (1, 2, 1)
    rows, total = await svc.list_cases(GUILD_A, action="ban")
    assert total == 1 and rows[0].action == "ban"
