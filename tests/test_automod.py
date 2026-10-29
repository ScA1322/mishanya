import pytest

from mishanya.schemas import AutomodConfig
from mishanya.services.automod import AutomodEngine, MessageInfo

from .conftest import GUILD_A, USER_1

pytestmark = pytest.mark.anyio
CHANNEL = 444444444444444444
ROLE_TRUSTED = 555555555555555555


def msg(content: str, t: float = 0.0, **kw) -> MessageInfo:
    return MessageInfo(guild_id=GUILD_A, user_id=USER_1, channel_id=CHANNEL, content=content, timestamp=t, **kw)


def cfg(**rules) -> AutomodConfig:
    data: dict = {"enabled": True}
    for name, opts in rules.items():
        data[name] = {"enabled": True, **opts}
    return AutomodConfig.model_validate(data)


def test_defaults_are_safe():
    c = AutomodConfig()
    assert not c.enabled
    assert not any(getattr(c, r).enabled for r in ("spam", "flood", "duplicates", "mentions", "words", "links", "invites"))
    assert AutomodEngine().check(c, msg("https://discord.gg/abc spam spam")) is None


def test_banned_words_word_mode_avoids_false_positives():
    c = cfg(words={"words": ["кот"], "match_mode": "word"})
    e = AutomodEngine()
    assert e.check(c, msg("какой милый КОТ!")).rule == "words"
    assert e.check(c, msg("котлета вкусная")) is None
    c2 = cfg(words={"words": ["кот"], "match_mode": "contains"})
    assert e.check(c2, msg("котлета")).rule == "words"


def test_zero_width_obfuscation():
    c = cfg(words={"words": ["плохо"]})
    assert AutomodEngine().check(c, msg("пл​охо")).rule == "words"


def test_links_and_allowed_domains():
    c = cfg(links={"allowed_domains": ["youtube.com"]})
    e = AutomodEngine()
    assert e.check(c, msg("смотри https://youtube.com/watch?v=1")) is None
    assert e.check(c, msg("https://m.youtube.com/x")) is None
    assert e.check(c, msg("https://evil-youtube.com/x")).rule == "links"
    assert e.check(c, msg("заходи www.example.org")).rule == "links"
    assert e.check(c, msg("просто текст без ссылок")) is None


def test_invites():
    c = cfg(invites={"allowed_codes": ["ourserver"]})
    e = AutomodEngine()
    assert e.check(c, msg("discord.gg/other")).rule == "invites"
    assert e.check(c, msg("https://discord.com/invite/other2")).rule == "invites"
    assert e.check(c, msg("discord.gg/ourserver")) is None


def test_mentions():
    c = cfg(mentions={"max_mentions": 3})
    e = AutomodEngine()
    assert e.check(c, msg("hi", mention_count=3)) is None
    assert e.check(c, msg("hi", mention_count=4)).rule == "mentions"
    assert e.check(c, msg("@everyone смотрите")).rule == "mentions"


def test_flood():
    c = cfg(flood={"max_lines": 5, "max_length": 100, "max_repeated_chars": 10})
    e = AutomodEngine()
    assert e.check(c, msg("\n".join(["a"] * 6))).rule == "flood"
    assert e.check(c, msg("x" * 101)).rule == "flood"
    assert e.check(c, msg("ааааааааааа")).rule == "flood"
    assert e.check(c, msg("нормальное сообщение")) is None


def test_spam_rate():
    c = cfg(spam={"max_messages": 3, "per_seconds": 5})
    e = AutomodEngine()
    results = [e.check(c, msg(f"m{i}", t=i * 0.5)) for i in range(4)]
    assert results[:3] == [None, None, None]
    assert results[3].rule == "spam"
    e2 = AutomodEngine()
    assert all(e2.check(c, msg(f"m{i}", t=i * 3.0)) is None for i in range(6))  # медленные сообщения не спам


def test_duplicates():
    c = cfg(duplicates={"max_duplicates": 3, "per_seconds": 30})
    e = AutomodEngine()
    assert e.check(c, msg("same", 0)) is None
    assert e.check(c, msg("SAME ", 1)) is None
    assert e.check(c, msg("same", 2)).rule == "duplicates"


def test_exemptions():
    e = AutomodEngine()
    c = cfg(words={"words": ["bad"]})
    assert e.check(c, msg("bad", is_moderator=True)) is None
    c.ignore_moderators = False
    assert e.check(c, msg("bad", is_moderator=True)) is not None
    c.exempt_roles = [str(ROLE_TRUSTED)]
    assert e.check(c, msg("bad", role_ids=[ROLE_TRUSTED])) is None
    c2 = cfg(words={"words": ["bad"], "exempt_channels": [str(CHANNEL)]})
    assert e.check(c2, msg("bad")) is None


async def test_escalation_counting(app):
    for _ in range(3):
        await app.automod.record_violation(GUILD_A, USER_1, "spam")
    assert await app.automod.recent_violations(GUILD_A, USER_1, 10) == 3
    assert await app.automod.recent_violations(GUILD_A, USER_1 + 1, 10) == 0
