import random

import pytest
from pydantic import ValidationError

from mishanya.db.models import GuildConfig
from mishanya.schemas import (
    MODULES,
    AutorolesConfig,
    GreetingMessage,
    LevelingConfig,
    LoggingConfig,
    WelcomeConfig,
)
from mishanya.services.templates import member_values, render

from .conftest import GUILD_A, GUILD_B

pytestmark = pytest.mark.anyio


def test_all_module_defaults_valid():
    for name, cls in MODULES.items():
        cls.model_validate(cls().model_dump(mode="json"))


def test_validation_errors():
    with pytest.raises(ValidationError):
        LevelingConfig(xp_min=30, xp_max=10)
    with pytest.raises(ValidationError):
        LevelingConfig(announce_mode="channel")  # канал обязателен
    with pytest.raises(ValidationError):
        AutorolesConfig(roles=["not-an-id"])
    with pytest.raises(ValidationError):
        LevelingConfig.model_validate({"unknown_field": 1})
    with pytest.raises(ValidationError):
        GreetingMessage(enabled=True, channel_id=None, message="hi")
    with pytest.raises(ValidationError):
        LoggingConfig.model_validate({"events": {"hack": {"enabled": True}}})


def test_logging_config_fills_all_events():
    cfg = LoggingConfig()
    assert set(cfg.events) >= {"message_delete", "moderation", "automod"}


async def test_config_service_isolation_and_persistence(app):
    await app.config.set(GUILD_A, "leveling", {"xp_min": 1, "xp_max": 3})
    a = await app.config.get(GUILD_A, "leveling")
    b = await app.config.get(GUILD_B, "leveling")
    assert (a.xp_min, b.xp_min) == (1, LevelingConfig().xp_min)
    app.config.clear_cache()
    assert (await app.config.get(GUILD_A, "leveling")).xp_max == 3


async def test_corrupted_config_falls_back_to_defaults(app):
    async with app.db.session() as s:
        s.add(GuildConfig(guild_id=GUILD_A, module="welcome", data={"join": "broken"}))
        await s.commit()
    cfg = await app.config.get(GUILD_A, "welcome")
    assert isinstance(cfg, WelcomeConfig)


async def test_global_settings(app):
    s = await app.config.get_global()
    assert s.log_retention_days == 30
    await app.config.set_global({**s.model_dump(), "log_retention_days": 5})
    app.config.clear_cache()
    assert (await app.config.get_global()).log_retention_days == 5


def test_render_basic_and_unknown():
    out = render("Привет, {user}! {unknown} {server}", {"user": "@u", "server": "S"})
    assert out == "Привет, @u! {unknown} S"


def test_render_does_not_allow_attribute_access():
    class Secret:
        token = "SECRET"

    assert render("{user.token} {user.__class__}", {"user": Secret()}) == "{user.token} {user.__class__}"
    assert "SECRET" not in render("{user}", {"user": "x"})


def test_render_choose():
    rng = random.Random(0)
    outs = {render("{choose:a|b|c}", {}, rng) for _ in range(50)}
    assert outs <= {"a", "b", "c"} and len(outs) > 1


def test_member_values():
    class G:
        name, id, member_count = "Server", 1, 42

    class M:
        mention, name, display_name, id, guild = "<@5>", "nick", "Nick", 5, G()

    v = member_values(M())
    assert render("{user} {username} {server} {member_count} {user_id}", v) == "<@5> nick Server 42 5"
