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
                 "actions": [{"type": "move", "who": "я", "to": "Курилка"}],
                 "attitude": 0, "ignore_minutes": None, "unignore": False}
    a = parse_actions('{"reply": "отъебись", "attitude": -9, "ignore_minutes": 30}')
    assert (a["attitude"], a["ignore_minutes"], a["unignore"]) == (-3, 30, False)
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
    cog.app, cog.heard, cog.unlearned, cog.cooldowns, cog.quotas, cog.joins = app, {}, {}, {}, {}, {}
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

    async def no_messages(limit):  # noqa: ARG001
        return
        yield

    channel = SimpleNamespace(typing=Typing, send=send, history=no_messages)
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
    assert data["memory"] == {"total": 1, "general": 1, "people": []} and data["grudges"][0]["reason"] == "назвал тупым"
    page = (await authed.get(f"/api/guilds/{GID}/mind/memories?who=general&q=пельм")).json()
    assert page["total"] == 1 and page["items"][0]["text"] == "сервер любит пельмени"
    mid = page["items"][0]["id"]
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


def test_image_to_data_url():
    import base64
    import io

    from PIL import Image

    from mishanya.bot.cogs.mind import _to_data_url

    buf = io.BytesIO()
    Image.new("RGBA", (3000, 1500), (255, 0, 0, 255)).save(buf, "PNG")
    url = _to_data_url(buf.getvalue())
    assert url.startswith("data:image/jpeg;base64,")
    img = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert img.size == (1280, 640)  # уменьшено, пропорции сохранены
    assert _to_data_url(b"not an image") is None


async def test_ignore_and_relation(app, monkeypatch):
    """Бот может начать игнорить; пока игнорит — по просьбам ничего не делает; простить может сам."""
    from mishanya.bot.cogs import mind as M

    await app.guilds.ensure(GID, "g")
    cog, calls, sent = _cog(app, monkeypatch, [
        '{"reply": "отъебись", "attitude": -2, "ignore_minutes": 30}',
        '{"reply": "", "actions": [{"type": "music", "do": "stop"}]}',
        '{"reply": "ладно, говори", "unignore": true, "attitude": 1}',
    ])
    cog.bot = SimpleNamespace(get_cog=lambda n: None)
    author = SimpleNamespace(id=5, display_name="Вася", voice=None)
    guild = SimpleNamespace(id=GID, name="g", me=SimpleNamespace(display_name="Император"), voice_channels=[],
                            roles=[])
    cfg = ChatterConfig(enabled=True, memory_enabled=False)
    did = []

    async def fake_actions(*a):  # noqa: ANN002
        did.append(a[1])
        return ["сделал"]

    monkeypatch.setattr(cog, "do_actions", fake_actions)
    reply, _ = await cog.brain(guild, author, cfg, history="Вася: ты тупой", channel_name="общий", task="ответь")
    assert reply == "отъебись" and await cog.ignoring(GID, 5)
    reply, notes = await cog.brain(guild, author, cfg, history="Вася: выключи музыку", channel_name="общий", task="t")
    assert "ИГНОРИШЬ" in calls[1]["context"] and reply == "" and notes == [] and did == []
    reply, _ = await cog.brain(guild, author, cfg, history="Вася: прости, был неправ", channel_name="общий", task="t")
    assert reply == "ладно, говори" and not await cog.ignoring(GID, 5)
    assert (await app.mind.relation(GID, 5)).attitude == -1


async def test_kick_ban_limits(app, monkeypatch):
    cog, _, sent = _cog(app, monkeypatch, [])

    class Role(SimpleNamespace):
        def __ge__(self, other):  # noqa: ANN001
            return self.position >= other.position

    me = SimpleNamespace(id=1, top_role=Role(position=10), display_name="Император")
    guild = SimpleNamespace(id=GID, name="g", owner_id=2, me=me, members=[], get_member=lambda uid: None)
    kicked = []

    async def kick(reason=None):  # noqa: ANN001
        kicked.append(reason)

    victim = FakeMember(id=5, name="vasya", display_name="Вася", global_name=None, bot=False, guild=guild,
                        guild_permissions=SimpleNamespace(administrator=False), top_role=Role(position=1), kick=kick)
    guild.members = [victim]
    cfg = ChatterConfig(enabled=True, kick_ban_per_hour=2)
    for _ in range(3):
        notes = await cog.do_actions(victim, [{"type": "kick", "who": "Вася", "reason": "достал"}], cfg, None)
    assert len(kicked) == 2 and "лимит" in notes[0] and "достал" in kicked[0]
    cfg.control_kick_ban = False
    assert await cog.do_actions(victim, [{"type": "ban", "who": "Вася"}], cfg, None) == []


async def test_stranger_join_request_flow(app, monkeypatch):
    """Чужак долго пишет и просится на сервер: бот передаёт доверенным, «Пустить» — шлёт приглашение."""
    from mishanya.bot.cogs import mind as M

    app.settings.deepseek_api_key = "sk-test"
    boss = FakeMember(id=999999999999999999, bot=False, display_name="Босс")
    boss_dm = []

    async def boss_send(**kw):  # noqa: ANN003
        boss_dm.append(kw)
        return SimpleNamespace(channel=SimpleNamespace(id=1), id=len(boss_dm))

    boss.send = boss_send
    invites = []

    async def create_invite(**kw):  # noqa: ANN003
        invites.append(kw)
        return SimpleNamespace(url="https://discord.gg/abc")

    me = SimpleNamespace(display_name="Император")
    text_channel = SimpleNamespace(permissions_for=lambda m: SimpleNamespace(create_instant_invite=True),
                                   create_invite=create_invite)
    guild = SimpleNamespace(id=GID, name="Лечебница", me=me, system_channel=text_channel, text_channels=[],
                            get_member=lambda uid: boss if uid == boss.id else None)
    cfg = ChatterConfig(enabled=True, confidants=[str(boss.id)], join_min_messages=3)
    answers = ['{"reply": "сначала пообщайся, чужак", "wants_join": true, "forward_join": true}',
               '{"reply": "ладно, спрошу своих", "wants_join": true, "forward_join": true, "join_why": "упорный"}']
    cog, calls, _ = _cog(app, monkeypatch, answers)
    stranger_dm = []

    async def stranger_send(text, **kw):  # noqa: ANN001, ANN003
        stranger_dm.append(text)

    stranger = SimpleNamespace(id=5, name="spy_42", display_name="Шпион", send=stranger_send,
                               display_avatar=SimpleNamespace(url="https://cdn/x.png"))
    cog.bot = SimpleNamespace(guilds=[guild], user=SimpleNamespace(id=1), get_guild=lambda gid: guild,
                              get_user=lambda uid: stranger, get_channel=lambda cid: None)

    async def fake_cfg(gid):  # noqa: ANN001
        return cfg

    monkeypatch.setattr(cog, "cfg", fake_cfg)

    async def no_history(*a):  # noqa: ANN002
        return []

    monkeypatch.setattr(M, "read_history", no_history)
    written = {"n": 1}

    def history(limit):  # noqa: ARG001
        async def gen():
            for _ in range(written["n"]):
                yield SimpleNamespace(author=SimpleNamespace(id=5))
        return gen()

    class Typing:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):  # noqa: ANN002
            return False

    async def send(text, **kw):  # noqa: ANN001, ANN003
        pass

    channel = SimpleNamespace(typing=Typing, send=send, history=history)
    msg = lambda t: SimpleNamespace(author=stranger, channel=channel, content=t, attachments=[])  # noqa: E731
    await cog.handle_dm(msg("пусти на сервер"))
    assert boss_dm == [] and "всего 1" in calls[0]["task"]  # мало общался — рано
    written["n"] = 5
    cog.cooldowns.clear()
    await cog.handle_dm(msg("ну пусти, я хороший"))
    assert len(boss_dm) == 1 and "spy_42" in boss_dm[0]["embed"].description
    assert cog.joins[(GID, 5)]["status"] == "pending"

    responses = []

    class Resp:
        async def send_message(self, text, **kw):  # noqa: ANN001, ANN003
            responses.append(text)

        async def defer(self):
            pass

    async def followup_send(text, **kw):  # noqa: ANN001, ANN003
        responses.append(text)

    intruder = SimpleNamespace(user=SimpleNamespace(id=7, display_name="Левый"), response=Resp(), message=None)
    await cog.decide_join(intruder, True, GID, 5)
    assert responses == ["Решать могут только доверенные люди."] and invites == []
    click = SimpleNamespace(user=SimpleNamespace(id=boss.id, display_name="Босс"), response=Resp(), message=None,
                            followup=SimpleNamespace(send=followup_send))
    await cog.decide_join(click, True, GID, 5)
    assert invites and invites[0]["max_uses"] == 1 and "https://discord.gg/abc" in stranger_dm[-1]
    assert cog.joins[(GID, 5)]["status"] == "accepted"
    await cog.decide_join(click, False, GID, 5)
    assert responses[-1] == "Уже решено другим доверенным."


def test_pc_open_close(monkeypatch, tmp_path):
    """Открывает только из каталога, закрывает по названию, системные процессы и бота не трогает."""
    from mishanya.services import pc

    exe = tmp_path / "Steam.lnk"
    exe.write_text("x")
    opened, killed = [], []
    monkeypatch.setattr(pc.sys, "platform", "win32")
    monkeypatch.setattr(pc.os, "startfile", lambda t: opened.append(t), raising=False)
    items = {"Steam": str(exe), "The Isle": "steam://rungameid/376210"}
    assert pc.open_program("steam", items) == "Steam" and opened == [str(exe)]
    assert pc.open_program("the isle", items) == "The Isle" and opened[-1] == "steam://rungameid/376210"
    with pytest.raises(pc.PcError, match="Не нашёл"):
        pc.open_program("format c:", items)

    monkeypatch.setattr(pc, "running_programs", lambda: ["Discord.exe", "steam.exe", "steamwebhelper.exe"])
    monkeypatch.setattr(pc.subprocess, "run", lambda args, **kw: (killed.append(args[2]), SimpleNamespace(returncode=0))[1])
    assert pc.close_program("discord") == "Discord.exe" and killed == ["Discord.exe"]
    assert pc.close_program("steam") == "steam.exe"
    with pytest.raises(pc.PcError, match="Не нашёл"):
        pc.close_program("explorer")  # системные процессы даже не видны


async def test_pc_only_for_trusted(app, monkeypatch):
    cog, _, _ = _cog(app, monkeypatch, [])
    app.settings.owner_id = 42
    cfg = ChatterConfig(enabled=True, confidants=["999999999999999999"])
    stranger = SimpleNamespace(id=5)
    assert "только доверенные" in await cog._pc_action(stranger, "pc_open", {"program": "steam"}, cfg)
    monkeypatch.setattr("mishanya.services.pc.open_program", lambda name, items: "Steam")

    async def catalog(c):  # noqa: ANN001
        return {"Steam": "x"}

    monkeypatch.setattr(cog, "pc_catalog", catalog)
    assert "Открыл" in await cog._pc_action(SimpleNamespace(id=42), "pc_open", {"program": "steam"}, cfg)  # владелец
    cfg.pc_control = False
    assert await cog._pc_action(SimpleNamespace(id=42), "pc_open", {"program": "steam"}, cfg) is None


async def test_relay_dm(app, monkeypatch):
    """«Передай Васе в лс …» — бот пишет от себя с подписью, кто просил; есть лимит в час."""
    cog, _, _ = _cog(app, monkeypatch, [])
    got = []

    async def send(text, **kw):  # noqa: ANN001, ANN003
        got.append(text)

    guild = SimpleNamespace(id=GID, name="Лечебница", members=[])
    vasya = FakeMember(id=6, name="vasya", display_name="Вася", global_name=None, bot=False, send=send)
    asker = FakeMember(id=5, name="xnax", display_name="XNAX", global_name=None, bot=False, guild=guild)
    guild.members = [vasya, asker]
    cfg = ChatterConfig(enabled=True, relay_per_hour=1)
    notes = await cog.do_actions(asker, [{"type": "dm", "who": "Вася", "text": "1"}], cfg, None)
    assert notes == ["📨 Передал **Вася** в личку."] and "от **XNAX** (@xnax)" in got[0] and got[0].endswith("> 1")
    notes = await cog.do_actions(asker, [{"type": "dm", "who": "Вася", "text": "ещё"}], cfg, None)
    assert "не больше 1" in notes[0] and len(got) == 1


async def test_memory_scale_and_people(app):
    """200 000+ фактов: повторы ловятся по хешу, нужные факты о человеке находятся быстро, панель видит людей."""
    import time as _t

    from sqlalchemy import text

    from mishanya.services.mind import MindService

    await app.guilds.ensure(GID, "g")
    rows = [{"g": GID, "u": (i % 500) + 1, "t": f"участник {i % 500} любит штуку номер {i}",
             "n": MindService._norm_hash(f"участник {i % 500} любит штуку номер {i}")} for i in range(210_000)]
    async with app.db.session() as s:
        await s.execute(text("INSERT INTO bot_memory (guild_id, user_id, text, norm, created_at) "
                             "VALUES (:g, :u, :t, :n, CURRENT_TIMESTAMP)"), rows)
        await s.commit()
    t0 = _t.perf_counter()
    assert await app.mind.remember(GID, [(7, "участник 6 любит штуку номер 6"), (7, "Вася фанат пельменей")],
                                   limit=1_000_000) == 1  # первый — повтор
    facts = await app.mind.recall(GID, [7], query="а ты помнишь про пельмени?")
    assert "Вася фанат пельменей" in facts and _t.perf_counter() - t0 < 3
    total, general, people = await app.mind.people(GID)
    assert total == 210_001 and general == 0 and people[0][1] >= 420
    n, items = await app.mind.search(GID, user_id=7, query="пельмен")
    assert n == 1 and items[0].text == "Вася фанат пельменей"
    await app.mind.remember(GID, [(None, "сервер стал другим")], limit=200_000)  # обрезка до лимита
    assert (await app.mind.people(GID))[0] == 200_000


def test_memory_migration_adds_hash(tmp_path):
    from sqlalchemy import create_engine

    from mishanya.db.database import _m7_memory_index

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE bot_memory (id INTEGER PRIMARY KEY, guild_id BIGINT, user_id BIGINT, "
                             "text VARCHAR(300), created_at DATETIME)")
        conn.exec_driver_sql("INSERT INTO bot_memory (guild_id, user_id, text) VALUES (1, 2, 'Вася любит доту')")
        _m7_memory_index(conn)
        _m7_memory_index(conn)  # повторный запуск ничего не ломает
        norm = conn.exec_driver_sql("SELECT norm FROM bot_memory").scalar()
    assert len(norm) == 40
