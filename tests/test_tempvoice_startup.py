"""Голосовые комнаты (с имитацией Discord) и проверка обновлений при запуске."""
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from pydantic import ValidationError

from mishanya import main as main_mod
from mishanya.bot.client import COGS, MishanyaBot
from mishanya.schemas import TempVoiceConfig
from mishanya.services.updater import LAUNCHER_ENV

from .conftest import GUILD_A, USER_1, USER_2
from .test_tickets_menus_update import push_change, repos  # noqa: F401

pytestmark = pytest.mark.anyio

HUB = 777777777777777771
ROOM = 777777777777777772


@pytest.fixture
async def cog(app):
    bot = MishanyaBot(app)
    for ext in COGS:
        await bot.load_extension(ext)
    app.bot = bot
    yield bot.get_cog("TempVoice")
    await bot.close()


def _voice(cid, members=()):
    ch = MagicMock(spec=discord.VoiceChannel)
    ch.id, ch.members, ch.bitrate, ch.category = cid, list(members), 64000, None
    ch.delete = AsyncMock()
    return ch


def _setup(room_members=()):
    guild = MagicMock()
    guild.id, guild.name, guild.member_count, guild.bitrate_limit = GUILD_A, "G", 10, 96000
    guild.me.guild_permissions.manage_channels = True
    guild.me.guild_permissions.move_members = True
    hub, room = _voice(HUB), _voice(ROOM, room_members)
    guild.create_voice_channel = AsyncMock(return_value=room)
    guild.get_channel.side_effect = lambda cid: {HUB: hub, ROOM: room}.get(cid)
    member = MagicMock(spec=discord.Member)
    member.id, member.name, member.display_name, member.mention, member.bot = USER_1, "misha", "Миша", "@m", False
    member.guild = guild
    member.move_to = AsyncMock()
    return guild, hub, room, member


def _state(channel):
    s = MagicMock()
    s.channel = channel
    return s


async def test_hub_creates_room_and_deletes_when_empty(app, cog):
    await app.config.set(GUILD_A, "tempvoice", {"enabled": True, "hubs": [{"channel_id": str(HUB), "user_limit": 5}]})
    guild, hub, room, member = _setup()
    await cog.on_voice_state_update(member, _state(None), _state(hub))
    kwargs = guild.create_voice_channel.await_args
    assert kwargs.args[0] == "🔊 Комната Миша" and kwargs.kwargs["user_limit"] == 5
    member.move_to.assert_awaited_with(room, reason="Голосовая комната")
    rec = await app.tempvoice.get(ROOM)
    assert rec.owner_id == USER_1 and rec.hub_id == HUB

    # повторный вход в создатель — возврат в свою комнату, без новой
    cog._cooldown.clear()
    await cog.on_voice_state_update(member, _state(None), _state(hub))
    assert guild.create_voice_channel.await_count == 1

    # все вышли — комната удаляется
    await cog.on_voice_state_update(member, _state(room), _state(None))
    room.delete.assert_awaited()
    assert await app.tempvoice.get(ROOM) is None


async def test_room_kept_while_people_inside_and_disabled_module(app, cog):
    guild, hub, room, member = _setup(room_members=[object()])
    await app.tempvoice.add(GUILD_A, ROOM, USER_2, HUB)
    await cog.on_voice_state_update(member, _state(room), _state(None))
    room.delete.assert_not_awaited()
    # модуль выключен — канал-создатель ничего не делает
    await app.config.set(GUILD_A, "tempvoice", {"enabled": False, "hubs": [{"channel_id": str(HUB)}]})
    await cog.on_voice_state_update(member, _state(None), _state(hub))
    guild.create_voice_channel.assert_not_awaited()


def test_tempvoice_config_validation():
    with pytest.raises(ValidationError):
        TempVoiceConfig(hubs=[{"channel_id": str(HUB)}, {"channel_id": str(HUB)}])
    with pytest.raises(ValidationError):
        TempVoiceConfig(hubs=[{"channel_id": str(HUB), "user_limit": 100}])


async def test_tempvoice_api_without_bot(authed, app):
    await app.tempvoice.add(GUILD_A, ROOM, USER_1, HUB)
    rooms = (await authed.get(f"/api/guilds/{GUILD_A}/tempvoice/rooms")).json()
    assert rooms[0]["channel_id"] == str(ROOM) and rooms[0]["name"] is None
    assert (await authed.post(f"/api/guilds/{GUILD_A}/tempvoice/hub", json={})).status_code == 409


# ---------------------------------------------------------------- обновление при запуске

async def test_update_on_startup(repos, app, monkeypatch):  # noqa: F811
    dev, local = repos
    monkeypatch.setattr(app.updater, "repo_dir", local)
    monkeypatch.delenv(LAUNCHER_ENV, raising=False)
    push_change(dev, "app.txt", "v2\n", "v2")
    assert await main_mod.update_on_startup(app) is False  # без start.bat не обновляемся
    monkeypatch.setenv(LAUNCHER_ENV, "1")
    await app.config.set_global({"auto_update_enabled": False})
    assert await main_mod.update_on_startup(app) is False
    await app.config.set_global({"auto_update_enabled": True})
    assert await main_mod.update_on_startup(app) is True
    assert (local / "app.txt").read_text() == "v2\n"
    assert await main_mod.update_on_startup(app) is False  # уже последняя версия
