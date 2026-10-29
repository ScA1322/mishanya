from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from pydantic import ValidationError
from sqlalchemy import update

from mishanya.db import utcnow
from mishanya.db.models import EventLog
from mishanya.services.custom_commands import CommandConflict, CustomCommandIn
from mishanya.services.reminders import MAX_PENDING_PER_USER, format_delta, parse_duration

from .conftest import GUILD_A, GUILD_B, USER_1

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------- пользовательские команды

def test_command_validation():
    assert CustomCommandIn(name="Rules", response="x").name == "rules"
    assert CustomCommandIn(name="правила", response="x").name == "правила"
    for bad in [{"name": "ban", "response": "x"}, {"name": "has space", "response": "x"},
                {"name": "ok", "response": "  "}, {"name": "x" * 33, "response": "x"},
                {"name": "ok", "response": "x", "exec": "import os"}]:
        with pytest.raises(ValidationError):
            CustomCommandIn.model_validate(bad)
    assert CustomCommandIn.model_validate({"name": "e", "embed": {"enabled": True, "title": "T"}}).embed.enabled


async def test_command_crud_and_conflicts(app):
    svc = app.commands
    c = await svc.create(GUILD_A, CustomCommandIn(name="rules", response="Правила: {server}"))
    with pytest.raises(CommandConflict):
        await svc.create(GUILD_A, CustomCommandIn(name="rules", response="dup"))
    await svc.create(GUILD_B, CustomCommandIn(name="rules", response="другой сервер"))  # другой сервер — можно
    other = await svc.create(GUILD_A, CustomCommandIn(name="faq", response="..."))
    with pytest.raises(CommandConflict):
        await svc.update(GUILD_A, other.id, CustomCommandIn(name="rules", response="x"))
    assert await svc.update(GUILD_B, c.id, CustomCommandIn(name="z", response="x")) is None  # чужой сервер
    assert (await svc.get_by_name(GUILD_A, "RULES")).id == c.id
    await svc.increment_uses(c.id)
    assert (await svc.get_by_name(GUILD_A, "rules")).uses == 1
    assert not await svc.delete(GUILD_B, c.id)
    assert await svc.delete(GUILD_A, c.id)
    assert await svc.count(GUILD_A) == 1


async def test_command_access_and_cooldown(app):
    svc = app.commands
    cmd = NS(id=1, enabled=True, allowed_channels=["100000000000000001"], allowed_roles=["100000000000000002"],
             cooldown_seconds=10)
    assert svc.check_access(cmd, 100000000000000009, [100000000000000002]) is not None
    assert svc.check_access(cmd, 100000000000000001, [5]) is not None
    assert svc.check_access(cmd, 100000000000000001, [100000000000000002]) is None
    cmd.enabled = False
    assert svc.check_access(cmd, 100000000000000001, [100000000000000002]) is not None
    assert svc.check_cooldown(cmd, USER_1, now=100.0) == 0
    assert svc.check_cooldown(cmd, USER_1, now=105.0) == pytest.approx(5.0)
    assert svc.check_cooldown(cmd, USER_1 + 1, now=105.0) == 0
    assert svc.check_cooldown(cmd, USER_1, now=111.0) == 0


# ---------------------------------------------------------------- напоминания

@pytest.mark.parametrize("text,seconds", [
    ("10m", 600), ("1h30m", 5400), ("2д", 172800), ("45 мин", 2700), ("1w", 604800), ("1ч 15м", 4500), ("30s", 30),
])
def test_parse_duration(text, seconds):
    assert parse_duration(text).total_seconds() == seconds


@pytest.mark.parametrize("text", ["", "abc", "10x", "0m", "400d", "10m abc"])
def test_parse_duration_invalid(text):
    with pytest.raises(ValueError):
        parse_duration(text)


def test_format_delta():
    assert format_delta(timedelta(hours=1, minutes=30)) == "1 ч 30 мин"


async def test_reminders_persist_and_due(app):
    svc = app.reminders
    past = await svc.create(USER_1, "прошлое", utcnow() - timedelta(minutes=5), GUILD_A, 1)
    await svc.create(USER_1, "будущее", utcnow() + timedelta(hours=1), None, None)
    due = await svc.due()
    assert [r.id for r in due] == [past.id]
    await svc.mark(past.id, "sent")
    assert await svc.due() == []
    assert await svc.pending_count() == 1
    future = (await svc.list_for_user(USER_1))[0]
    assert not await svc.cancel(future.id, user_id=USER_1 + 1)  # чужое напоминание
    assert await svc.cancel(future.id, user_id=USER_1)


async def test_reminder_limit(app):
    for i in range(MAX_PENDING_PER_USER):
        await app.reminders.create(USER_1, f"r{i}", utcnow() + timedelta(hours=1), None, None)
    with pytest.raises(ValueError):
        await app.reminders.create(USER_1, "лишнее", utcnow() + timedelta(hours=1), None, None)


# ---------------------------------------------------------------- журнал событий

async def test_eventlog_query_and_cleanup(app):
    svc = app.eventlog
    for i in range(10):
        await svc.add(GUILD_A, "member_join", f"join {i}", user_id=USER_1)
    await svc.add(GUILD_B, "member_leave", "leave")
    rows, total = await svc.query(GUILD_A)
    assert total == 10 and rows[0].summary == "join 9"
    _, total_b = await svc.query(GUILD_B, "member_join")
    assert total_b == 0
    _, by_user = await svc.query(GUILD_A, search=str(USER_1))
    assert by_user == 10

    async with app.db.session() as s:
        await s.execute(update(EventLog).where(EventLog.summary == "join 0").values(created_at=utcnow() - timedelta(days=60)))
        await s.commit()
    removed = await svc.cleanup(retention_days=30, max_per_guild=5)
    assert removed == 5  # 1 по сроку + 4 сверх лимита
    _, total = await svc.query(GUILD_A)
    assert total == 5
    assert await svc.clear(GUILD_B) == 1
