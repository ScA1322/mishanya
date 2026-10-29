"""Тесты логики бота с моками Discord API (без подключения к Discord)."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from mishanya.bot.client import COGS, MishanyaBot
from mishanya.bot.cogs.autoroles import role_problems
from mishanya.bot.utils import UserFacingError, require_perms
from mishanya.schemas import LevelingConfig

from .conftest import GUILD_A, USER_1

pytestmark = pytest.mark.anyio


@pytest.fixture
async def bot(app):
    b = MishanyaBot(app)
    for ext in COGS:
        await b.load_extension(ext)
    app.bot = b
    yield b
    await b.close()


async def test_all_commands_registered(bot):
    names = {c.name for c in bot.tree.get_commands()}
    expected = {"help", "ping", "avatar", "userinfo", "serverinfo", "poll", "remind", "rank", "leaderboard", "level",
                "profile", "invite", "kick", "ban", "unban", "timeout", "untimeout", "warn", "warnings", "clear",
                "slowmode", "lock", "unlock", "purge", "nickname", "role", "xp", "cmd"}
    assert expected <= names
    assert len(bot.commands_hash()) == 64


def test_intents():
    from mishanya.bot.client import build_intents

    i = build_intents()
    assert i.members and i.message_content and i.guilds and i.moderation


def _role(rid, pos, managed=False):
    r = MagicMock(spec=discord.Role)
    r.id, r.position, r.managed, r.name = rid, pos, managed, f"role{rid}"
    r.is_default.return_value = False
    return r


def _guild(roles, bot_pos=50, manage_roles=True):
    g = MagicMock()
    g.id = GUILD_A
    g.me.top_role.position = bot_pos
    g.me.guild_permissions.manage_roles = manage_roles
    g.get_role.side_effect = lambda rid: roles.get(rid)
    return g


async def test_sync_rewards_respects_hierarchy_and_no_duplicates(bot, app):
    cfg = LevelingConfig()
    await app.leveling.add_reward(GUILD_A, 1, 1001)
    await app.leveling.add_reward(GUILD_A, 2, 1002)  # выше роли бота
    await app.leveling.add_reward(GUILD_A, 3, 1003)  # роль удалена
    roles = {1001: _role(1001, 10), 1002: _role(1002, 60)}
    member = MagicMock()
    member.guild = _guild(roles)
    member.id = USER_1
    member.roles = []
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()

    cog = bot.get_cog("Leveling")
    problems = await cog.sync_rewards(member, 3, cfg, from_level=0)
    member.add_roles.assert_awaited_once()
    assert list(member.add_roles.call_args.args) == [roles[1001]]
    assert any("удалена" in p for p in problems) and any("выше роли бота" in p for p in problems)

    # роль уже есть — повторно не выдаётся
    member.add_roles.reset_mock()
    member.roles = [roles[1001]]
    await cog.sync_rewards(member, 3, cfg, from_level=0)
    member.add_roles.assert_not_awaited()


async def test_sync_rewards_without_manage_roles(bot, app):
    await app.leveling.add_reward(GUILD_A, 1, 1001)
    member = MagicMock()
    member.guild = _guild({1001: _role(1001, 10)}, manage_roles=False)
    member.add_roles = AsyncMock()
    problems = await bot.get_cog("Leveling").sync_rewards(member, 1, LevelingConfig(), from_level=0)
    member.add_roles.assert_not_awaited()
    assert "Управлять ролями" in problems[0]


def test_autorole_problems():
    roles = {1: _role(1, 10), 2: _role(2, 60), 3: _role(3, 5, managed=True)}
    g = _guild(roles)
    problems = role_problems(g, ["1", "2", "3", "4"])
    assert set(problems) == {"2", "3", "4"}


def test_require_perms_checks_user_and_bot():
    user_perms = discord.Permissions(kick_members=False)
    bot_perms = discord.Permissions(kick_members=True)
    member = MagicMock(spec=discord.Member)
    guild = MagicMock()
    channel = MagicMock()
    channel.permissions_for.side_effect = lambda m: bot_perms if m is guild.me else user_perms
    inter = NS(guild=guild, user=member, channel=channel)
    with pytest.raises(UserFacingError, match="Недостаточно прав"):
        require_perms(inter, "kick_members")
    user_perms = discord.Permissions(kick_members=True)
    bot_perms = discord.Permissions(kick_members=False)
    with pytest.raises(UserFacingError, match="У бота нет прав"):
        require_perms(inter, "kick_members")
    bot_perms = discord.Permissions(kick_members=True)
    require_perms(inter, "kick_members")


async def test_automod_cog_deletes_and_logs(bot, app):
    await app.config.set(GUILD_A, "automod", {"enabled": True, "words": {"enabled": True, "words": ["плохое"],
                                                                         "warn_user": False},
                                              "escalation": {"enabled": False}})
    guild = MagicMock()
    guild.id = GUILD_A
    guild.name = "A"
    member = MagicMock(spec=discord.Member)
    member.id, member.guild, member.roles = USER_1, guild, []
    member.guild_permissions = discord.Permissions.none()
    member.mention = "<@1>"
    message = MagicMock()
    message.guild, message.author, message.content = guild, member, "это плохое слово"
    message.channel.id = 777
    message.channel.mention = "#ch"
    message.mentions, message.role_mentions = [], []
    message.delete = AsyncMock()

    stopped = await bot.get_cog("Automod").handle_message(message)
    assert stopped is True
    message.delete.assert_awaited_once()
    rows, total = await app.eventlog.query(GUILD_A, "automod")
    assert total == 1 and "Запрещённые слова" in rows[0].summary
    assert await app.automod.recent_violations(GUILD_A, USER_1, 5) == 1
