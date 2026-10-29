import random
from datetime import timedelta

import pytest

from mishanya.db import utcnow
from mishanya.schemas import LevelingConfig
from mishanya.services.leveling import (
    ImportPayload,
    is_xp_eligible,
    level_from_xp,
    roll_xp,
    total_xp_for_level,
    xp_to_next,
)

from .conftest import GUILD_A, GUILD_B, USER_1, USER_2

pytestmark = pytest.mark.anyio
CFG = LevelingConfig()


def test_default_formula_matches_mee6_style():
    assert [xp_to_next(level, CFG) for level in range(4)] == [100, 155, 220, 295]
    assert total_xp_for_level(3, CFG) == 475


def test_level_from_xp_boundaries():
    assert level_from_xp(0, CFG).level == 0
    assert level_from_xp(99, CFG).level == 0
    p = level_from_xp(100, CFG)
    assert (p.level, p.xp_into_level, p.xp_needed) == (1, 0, 155)
    assert level_from_xp(total_xp_for_level(10, CFG), CFG).level == 10


def test_max_level_cap():
    cfg = LevelingConfig(max_level=3)
    p = level_from_xp(10**9, cfg)
    assert p.level == 3 and p.xp_needed == 0 and p.percent == 100.0


def test_roll_xp_within_range_and_multiplier():
    cfg = LevelingConfig(xp_min=10, xp_max=20, xp_multiplier=2)
    rng = random.Random(1)
    values = {roll_xp(cfg, rng) for _ in range(200)}
    assert min(values) >= 20 and max(values) <= 40


def test_exclusions():
    cfg = LevelingConfig(no_xp_channels=["100000000000000001"], no_xp_categories=["100000000000000002"],
                         no_xp_roles=["100000000000000003"])
    assert not is_xp_eligible(cfg, 100000000000000001, None, [])
    assert not is_xp_eligible(cfg, 5, 100000000000000002, [])
    assert not is_xp_eligible(cfg, 5, None, [100000000000000003])
    assert is_xp_eligible(cfg, 5, 6, [7])


async def test_xp_cooldown_prevents_farming(app):
    cfg = LevelingConfig(xp_min=10, xp_max=10, cooldown_seconds=60)
    now = utcnow()
    r1 = await app.leveling.process_message(GUILD_A, USER_1, "u", None, "hello world", cfg, now)
    r2 = await app.leveling.process_message(GUILD_A, USER_1, "u", None, "another message", cfg, now + timedelta(seconds=10))
    r3 = await app.leveling.process_message(GUILD_A, USER_1, "u", None, "third message", cfg, now + timedelta(seconds=61))
    assert (r1.awarded, r2.awarded, r3.awarded) == (10, 0, 10)
    m = await app.leveling.get_member(GUILD_A, USER_1)
    assert m.xp == 20 and m.message_count == 3


async def test_duplicate_and_short_messages_give_no_xp(app):
    cfg = LevelingConfig(xp_min=10, xp_max=10, cooldown_seconds=0, min_message_length=3)
    now = utcnow()
    assert (await app.leveling.process_message(GUILD_A, USER_1, "u", None, "same text", cfg, now)).awarded == 10
    assert (await app.leveling.process_message(GUILD_A, USER_1, "u", None, "same text", cfg, now)).awarded == 0
    assert (await app.leveling.process_message(GUILD_A, USER_1, "u", None, "ok", cfg, now)).awarded == 0


async def test_level_up_detected(app):
    cfg = LevelingConfig(xp_min=100, xp_max=100, cooldown_seconds=0)
    res = await app.leveling.process_message(GUILD_A, USER_1, "u", None, "level me up", cfg)
    assert res.leveled_up and (res.old_level, res.new_level) == (0, 1)


async def test_guild_isolation(app):
    await app.leveling.set_xp(GUILD_A, USER_1, 1000, CFG)
    await app.leveling.set_xp(GUILD_B, USER_1, 5, CFG)
    assert (await app.leveling.get_member(GUILD_A, USER_1)).xp == 1000
    assert (await app.leveling.get_member(GUILD_B, USER_1)).xp == 5
    await app.leveling.reset_guild(GUILD_B)
    assert await app.leveling.get_member(GUILD_B, USER_1) is None
    assert (await app.leveling.get_member(GUILD_A, USER_1)).xp == 1000
    rows, total = await app.leveling.leaderboard(GUILD_A)
    assert total == 1 and rows[0].user_id == USER_1


async def test_manual_changes_and_rank(app):
    await app.leveling.set_level(GUILD_A, USER_1, 5, CFG)
    m = await app.leveling.get_member(GUILD_A, USER_1)
    assert m.level == 5 and m.xp == total_xp_for_level(5, CFG)
    await app.leveling.add_xp(GUILD_A, USER_2, 50_000, CFG)
    assert await app.leveling.rank_of(GUILD_A, USER_2) == 1
    assert await app.leveling.rank_of(GUILD_A, USER_1) == 2
    res = await app.leveling.add_xp(GUILD_A, USER_1, -10**9, CFG)
    assert res.total_xp == 0 and res.new_level == 0  # XP не уходит в минус
    assert await app.leveling.reset_user(GUILD_A, USER_2)
    assert await app.leveling.get_member(GUILD_A, USER_2) is None


async def test_leave_and_rejoin_keeps_xp(app):
    await app.leveling.set_xp(GUILD_A, USER_1, 500, CFG)
    await app.leveling.mark_left(GUILD_A, USER_1, reset=False)
    _, total = await app.leveling.leaderboard(GUILD_A)
    assert total == 0  # вышедшие скрыты
    _, total = await app.leveling.leaderboard(GUILD_A, include_left=True)
    assert total == 1
    row = await app.leveling.mark_rejoined(GUILD_A, USER_1)
    assert row.xp == 500 and row.left_at is None
    await app.leveling.mark_left(GUILD_A, USER_1, reset=True)
    assert await app.leveling.get_member(GUILD_A, USER_1) is None


async def test_reward_plan_no_duplicates_and_ranges(app):
    await app.leveling.add_reward(GUILD_A, 5, 1001)
    await app.leveling.add_reward(GUILD_A, 10, 1002)
    disabled = await app.leveling.add_reward(GUILD_A, 7, 1003)
    await app.leveling.update_reward(GUILD_A, disabled.id, enabled=False)
    with pytest.raises(ValueError):
        await app.leveling.add_reward(GUILD_A, 5, 1001)

    assert await app.leveling.rewards_plan(GUILD_A, 4, CFG, from_level=3) == ([], [])
    assert await app.leveling.rewards_plan(GUILD_A, 5, CFG, from_level=4) == ([1001], [])
    # повторное сообщение на том же уровне не выдаёт награду снова
    assert await app.leveling.rewards_plan(GUILD_A, 5, CFG, from_level=5) == ([], [])
    # отключённая награда (уровень 7) не выдаётся
    assert await app.leveling.rewards_plan(GUILD_A, 8, CFG, from_level=6) == ([], [])
    # полная синхронизация с 0
    assert await app.leveling.rewards_plan(GUILD_A, 12, CFG, from_level=0) == ([1001, 1002], [])


async def test_reward_plan_without_stacking(app):
    cfg = LevelingConfig(stack_rewards=False)
    await app.leveling.add_reward(GUILD_A, 5, 1001)
    await app.leveling.add_reward(GUILD_A, 10, 1002)
    add, remove = await app.leveling.rewards_plan(GUILD_A, 10, cfg, from_level=9)
    assert add == [1002] and remove == [1001]


async def test_rewards_isolated_per_guild(app):
    await app.leveling.add_reward(GUILD_A, 5, 1001)
    assert await app.leveling.list_rewards(GUILD_B) == []
    r = (await app.leveling.list_rewards(GUILD_A))[0]
    assert not await app.leveling.delete_reward(GUILD_B, r.id)  # чужой сервер не может удалить
    assert await app.leveling.delete_reward(GUILD_A, r.id)


async def test_export_import_roundtrip(app):
    await app.leveling.set_xp(GUILD_A, USER_1, 1234, CFG, "one")
    await app.leveling.set_xp(GUILD_A, USER_2, 99, CFG, "two")
    data = await app.leveling.export(GUILD_A)
    assert data["format"] == "mishanya-levels-v1" and len(data["entries"]) == 2

    count = await app.leveling.import_(GUILD_B, ImportPayload.model_validate(data), CFG, "replace")
    assert count == 2
    m = await app.leveling.get_member(GUILD_B, USER_1)
    assert m.xp == 1234 and m.level == level_from_xp(1234, CFG).level


def test_import_payload_validation():
    with pytest.raises(Exception):
        ImportPayload.model_validate({"entries": [{"user_id": "abc", "xp": 5}]})
    with pytest.raises(Exception):
        ImportPayload.model_validate({"entries": [{"user_id": "123456789012345678", "xp": -1}]})


async def test_recalculate_after_formula_change(app):
    await app.leveling.set_xp(GUILD_A, USER_1, 1000, CFG)
    easy = LevelingConfig(formula_a=0, formula_b=0, formula_c=10)
    await app.leveling.recalculate_levels(GUILD_A, easy)
    assert (await app.leveling.get_member(GUILD_A, USER_1)).level == 100
