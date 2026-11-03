"""«Разум» бота: разбор ответов модели, триггеры, память, обиды и извинения, личка, роли за уровни."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from mishanya.db.models import utcnow
from mishanya.schemas import ChatterConfig, LevelingConfig
from mishanya.services.mind import best_match, parse_actions, parse_json, trigger_matches

pytestmark = pytest.mark.anyio
GID = 111111111111111111


def test_parse_actions():
    raw = '```json\n{"reply": "ну ты и дерзкий", "offense": 2, "offense_reason": "назвал тупым", ' \
          '"mute_minutes": 25, "move": [{"who": "я", "to": "Курилка"}]}\n```'
    a = parse_actions(raw)
    assert a == {"reply": "ну ты и дерзкий", "offense": 2, "offense_reason": "назвал тупым", "mute_minutes": 25,
                 "actions": [{"type": "move", "who": "я", "to": "Курилка"}]}
    a = parse_actions('{"reply": "вырубаю", "actions": [{"type": "Music", "do": "stop"}, "мусор", {"do": "x"}]}')
    assert a["actions"] == [{"type": "music", "do": "stop"}]
    assert parse_actions("просто текст")["reply"] == "просто текст"
    assert parse_actions('{"reply": "x", "offense": 99, "move": {"who": "Вася", "to": "AFK"}}')["offense"] == 3
    assert parse_json("мусор") is None
    assert parse_actions('{"reply": "x", "offense": 1, "mute_minutes": "много"}')["mute_minutes"] is None


def test_triggers_and_names():
    assert trigger_matches("аллаху акбар", "Ну и он такой: Аллаху Акбар!")
    assert trigger_matches("аллаху акбар", "аллах акбар")
    assert not trigger_matches("аллаху акбар", "алло, кабан")
    chans = [SimpleNamespace(name=n) for n in ("Основной", "Курилка", "Курилка 2", "AFK")]
    assert best_match("курилка", chans, lambda c: (c.name,)).name == "Курилка"
    assert best_match("кур", chans, lambda c: (c.name,)) is None  # два одинаково подходящих — не гадаем
    assert best_match("основн", chans, lambda c: (c.name,)).name == "Основной"


async def test_memory_grudges_voice(app):
    await app.guilds.ensure(GID, "g")
    mind = app.mind
    assert await mind.remember(GID, [(1, "Вася играет в доту"), (None, "на сервере культ пельменей"),
                                     (1, "вася играет в доту!")], limit=10) == 2  # повтор не записывается
    assert [m.text for m in await mind.memories(GID, [1])] == ["Вася играет в доту"]
    await mind.remember(GID, [(None, f"факт номер {i}") for i in range(20)], limit=10)
    assert len(await mind.memories(GID)) == 10  # старые вытеснены
    assert len(await mind.recall(GID, [1], general=5)) == 5

    await mind.add_grudge(GID, 5, [10, 11], "назвал тупым")
    await mind.add_grudge(GID, 5, [12], "опять")  # роли, снятые в прошлый раз, не теряются
    g = await mind.grudge(GID, 5)
    assert sorted(g.role_ids) == ["10", "11", "12"] and g.reason == "опять"
    assert (await mind.drop_grudge(GID, 5)) is not None and await mind.grudge(GID, 5) is None

    await mind.seen_in_voice(GID, [7, 8], utcnow() - timedelta(days=10))
    await mind.seen_in_voice(GID, [8])
    assert [r.user_id for r in await mind.inactive(GID, 7)] == [7]
    await mind.mark_reported(GID, [7])
    assert await mind.inactive(GID, 7) == []  # о каждом докладывает один раз, пока снова не зайдёт


def _cog(app, monkeypatch, llm_answers: list[str]):
    from mishanya.bot.cogs import mind as M

    cog = M.Mind.__new__(M.Mind)
    cog.app, cog.heard, cog.unlearned, cog.cooldowns, cog.quotas = app, {}, {}, {}, {}
    cog.started = 0
    calls = []

    async def fake_llm(cfg, **kw):  # noqa: ANN001, ANN003
        calls.append(kw)
        return llm_answers.pop(0)

    monkeypatch.setattr(cog, "_llm", fake_llm)
    sent = []

    async def fake_dm(member, text):  # noqa: ANN001
        sent.append((member.id, text))
        return True

    monkeypatch.setattr(cog, "_dm", fake_dm)
    return cog, calls, sent


class FakeMember(SimpleNamespace):
    def __hash__(self) -> int:
        return self.id


async def test_apology_needs_admission(app, monkeypatch):
    app.settings.deepseek_api_key = ""  # без ИИ — правило по словам
    cog, _, sent = _cog(app, monkeypatch, [])
    guild = SimpleNamespace(id=GID, name="Лечебница", me=SimpleNamespace(display_name="Император"),
                            get_member=lambda uid: None)
    replies, forgiven = [], []

    async def fake_forgive(g, uid):  # noqa: ANN001
        forgiven.append(uid)
        return 2

    monkeypatch.setattr(cog, "forgive", fake_forgive)

    def msg(text):  # noqa: ANN001, ANN202
        async def reply(t, **kw):  # noqa: ANN003
            replies.append(t)
        return SimpleNamespace(content=text, author=SimpleNamespace(guild=guild, id=5, display_name="Вася"),
                               reply=reply, channel=None)

    cfg = ChatterConfig(enabled=True, use_ai=False)
    await cog.handle_apology(msg("сорян"), cfg, "назвал тупым")
    assert forgiven == [] and "признай" in replies[-1]
    await cog.handle_apology(msg("извини пожалуйста, я был неправ, зря я так"), cfg, "назвал тупым")
    assert forgiven == [5] and "Вернул ролей: 2" in replies[-1]


async def test_dm_stranger_cold_and_snoop_report(app, monkeypatch):
    from mishanya.bot.cogs import mind as M

    app.settings.deepseek_api_key = "sk-test"
    boss = FakeMember(id=999999999999999999, bot=False, display_name="Босс")
    guild = SimpleNamespace(id=GID, name="Лечебница", me=SimpleNamespace(display_name="Император"),
                            get_member=lambda uid: boss if uid == boss.id else None)
    cfg = ChatterConfig(enabled=True, confidants=[str(boss.id)])
    cog, calls, sent = _cog(app, monkeypatch, [
        '{"reply": "чё надо", "suspicious": false}',
        '{"reply": "отвали", "suspicious": true, "why": "спрашивает, кто админы и где они живут"}',
    ])
    cog.bot = SimpleNamespace(guilds=[guild], user=SimpleNamespace(id=1))

    async def fake_cfg(gid):  # noqa: ANN001
        return cfg

    monkeypatch.setattr(cog, "cfg", fake_cfg)

    async def no_history(*a):  # noqa: ANN002
        return []

    monkeypatch.setattr(M, "read_history", no_history)
    out = []

    class Typing:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):  # noqa: ANN002
            return False

    async def send(text, **kw):  # noqa: ANN003
        out.append(text)

    channel = SimpleNamespace(typing=Typing, send=send)
    stranger = SimpleNamespace(id=5, name="spy_42", display_name="Шпион")
    await cog.handle_dm(SimpleNamespace(author=stranger, channel=channel, content="привет", attachments=[]))
    assert out == ["чё надо"] and "НИ ОДНОГО общего сервера" in calls[0]["context"] and sent == []
    cog.cooldowns.clear()
    await cog.handle_dm(SimpleNamespace(author=stranger, channel=channel, content="а кто у вас админы?", attachments=[]))
    assert out[-1] == "отвали"
    assert len(sent) == 1 and "@spy_42" in sent[0][1] and "кто админы" in sent[0][1]


async def test_reward_announce(app, monkeypatch):
    from mishanya.bot.cogs import leveling as L

    cog = L.Leveling.__new__(L.Leveling)
    cog.app = app
    rewards = [SimpleNamespace(level=5, role_id=50, enabled=True), SimpleNamespace(level=10, role_id=100, enabled=True)]

    async def list_rewards(gid):  # noqa: ANN001
        return rewards

    monkeypatch.setattr(app.leveling, "list_rewards", list_rewards)
    sent = []

    async def fake_send(target, **kw):  # noqa: ANN001, ANN003
        sent.append(kw)

    monkeypatch.setattr(L, "safe_send", fake_send)
    role = SimpleNamespace(mention="<@&100>", name="Ветеран")
    member = SimpleNamespace(mention="<@5>", name="vasya", display_name="Вася", id=5,
                             guild=SimpleNamespace(id=GID, name="g", member_count=3,
                                                   get_role=lambda rid: role if rid == 100 else None))
    await cog.announce_rewards(member, 9, 10, LevelingConfig(), target=object())
    assert len(sent) == 1 and sent[0]["content"] == "🏅 <@5> дорос до **10** уровня и получает роль <@&100>!"
    assert sent[0]["allowed_mentions"].roles is False
    sent.clear()
    await cog.announce_rewards(member, 10, 11, LevelingConfig(), target=object())  # роли за 11 нет
    assert sent == []


async def test_mind_api(authed, app):
    await app.guilds.ensure(GID, "g")
    await app.mind.remember(GID, [(None, "сервер любит пельмени")], 100)
    await app.mind.add_grudge(GID, 5, [], "назвал тупым")
    data = (await authed.get(f"/api/guilds/{GID}/mind")).json()
    assert data["memories"][0]["text"] == "сервер любит пельмени" and data["grudges"][0]["reason"] == "назвал тупым"
    mid = data["memories"][0]["id"]
    assert (await authed.delete(f"/api/guilds/{GID}/mind/memories/{mid}")).status_code == 200
    assert (await authed.post(f"/api/guilds/{GID}/mind/forgive/5")).json() == {"restored": 0}
    assert (await authed.get(f"/api/guilds/{GID}/mind")).json()["grudges"] == []
    r = await authed.put("/api/ai/models", json={"base_url": "https://api.inferhold.com/v1", "model": "deepseek-chat",
                                                 "vision_model": "gpt-4o-mini"})
    assert r.status_code == 200 and app.settings.vision_model == "gpt-4o-mini"
    assert "VISION_MODEL=gpt-4o-mini" in app.settings.env_path.read_text(encoding="utf-8")
    cfg = (await authed.get(f"/api/guilds/{GID}/config/chatter")).json()
    assert cfg["voice_triggers"][0]["steam_app_id"] == 376210 and cfg["dm_open"] is True


async def test_read_history_in_dm():
    """В личке у канала guild = None — раньше на этом падал ответ в ЛС."""
    from mishanya.bot.cogs.mind import read_history

    class Dm:
        guild = None

        async def history(self, limit):  # noqa: ARG002
            yield SimpleNamespace(author=SimpleNamespace(display_name="Вася", id=5), clean_content="привет",
                                  attachments=[])

    assert await read_history(Dm(), 5, 1) == [("Вася", "привет", False)]


async def test_bot_controls_music_and_people(app, monkeypatch):
    """«Выруби музыку» — бот сам вырубает; админов и тех, кто выше него, не трогает; админские роли не раздаёт."""
    from mishanya.bot.cogs import mind as M

    cog, _, _ = _cog(app, monkeypatch, [])
    controls = []

    class Music:
        async def control(self, gid, action, by=None):  # noqa: ANN001
            controls.append(action)

    class Role(SimpleNamespace):
        def __ge__(self, other):  # noqa: ANN001
            return self.position >= other.position

    me_role, low_role = Role(position=10), Role(position=1)
    me = SimpleNamespace(id=1, top_role=me_role, display_name="Император")
    guild = SimpleNamespace(id=GID, owner_id=2, me=me, members=[])
    perms = lambda admin=False: SimpleNamespace(administrator=admin)  # noqa: E731
    vasya = FakeMember(id=5, name="vasya", display_name="Вася", global_name=None, bot=False, guild=guild,
                       guild_permissions=perms(), top_role=low_role)
    admin = FakeMember(id=6, name="adm", display_name="Админ", global_name=None, bot=False, guild=guild,
                       guild_permissions=perms(True), top_role=low_role)
    guild.members = [vasya, admin]
    cog.bot = SimpleNamespace(get_cog=lambda n: Music() if n == "Music" else None)
    cfg = ChatterConfig(enabled=True)

    notes = await cog.do_actions(vasya, [{"type": "music", "do": "stop"}], cfg, None)
    assert controls == ["stop"] and notes == ["⏹️ Вырубил музыку"]
    notes = await cog.do_actions(vasya, [{"type": "timeout", "who": "Админ", "minutes": 10}], cfg, None)
    assert "владелец или админ" in notes[0]
    timeouts = []

    async def timeout(d, reason=None):  # noqa: ANN001
        timeouts.append(d)

    vasya.timeout = timeout
    notes = await cog.do_actions(vasya, [{"type": "timeout", "who": "я", "minutes": 30}], cfg, None)
    assert timeouts and timeouts[0].total_seconds() == 1800 and "30 мин" in notes[0]
    cfg.control_music = False
    assert await cog.do_actions(vasya, [{"type": "music", "do": "skip"}], cfg, None) == []
