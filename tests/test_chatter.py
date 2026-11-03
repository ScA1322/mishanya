"""Болтовня: клиент DeepSeek (на локальном фейковом сервере), очистка ответов, API панели и ключ в .env."""
from __future__ import annotations

import pytest
from aiohttp import web

from mishanya.services.chatter import ChatterError, clean_reply, format_history, generate, set_env_value

pytestmark = pytest.mark.anyio
GID = "111111111111111111"


@pytest.fixture
async def fake_deepseek():
    """Локальный сервер с тем же API, что у DeepSeek. Поведение задаётся через state."""
    state = {"status": 200, "reply": "Мишаня: «ну это база»", "seen": []}

    async def handler(request: web.Request) -> web.Response:
        state["seen"].append({"auth": request.headers.get("Authorization"), "body": await request.json()})
        if state["status"] != 200:
            return web.Response(status=state["status"], text="nope")
        return web.json_response({"choices": [{"message": {"role": "assistant", "content": state["reply"]}}]})

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
    state["url"] = f"http://127.0.0.1:{port}"
    yield state
    await runner.cleanup()


async def test_generate_against_fake_api(fake_deepseek):
    kw = dict(api_key="sk-test", base_url=fake_deepseek["url"], model="deepseek-chat", system_prompt="Ты Мишаня",
              history="Вася: привет", channel="общий", bot_name="Мишаня", mentioned=False, temperature=1.3,
              max_tokens=100)
    assert await generate(**kw) == "ну это база"  # имя и кавычки убраны
    req = fake_deepseek["seen"][0]
    assert req["auth"] == "Bearer sk-test" and req["body"]["model"] == "deepseek-chat"
    assert req["body"]["messages"][0] == {"role": "system", "content": "Ты Мишаня"}
    assert "Вася: привет" in req["body"]["messages"][1]["content"]
    for status, text in ((401, "ключ"), (402, "баланс"), (429, "подождать"), (500, "500")):
        fake_deepseek["status"] = status
        with pytest.raises(ChatterError, match=text):
            await generate(**kw)
    with pytest.raises(ChatterError, match="не задан"):
        await generate(**{**kw, "api_key": ""})


def test_clean_and_history():
    assert clean_reply("  Mishanya: пинг @everyone  ", "Мишаня") == "пинг everyone"
    assert clean_reply('"ору"', "Бот") == "ору"
    hist = format_history([("Вася", "  много   пробелов ", False), ("x", "", False), ("Бот", "я тут", True)], "Мишаня")
    assert hist == "Вася: много пробелов\nМишаня: я тут"


def test_set_env_value(tmp_path):
    env = tmp_path / ".env"
    env.write_text("DISCORD_TOKEN=abc\n# DEEPSEEK_API_KEY=\nX=1\n", encoding="utf-8")
    set_env_value(env, "DEEPSEEK_API_KEY", "sk-123")
    assert env.read_text(encoding="utf-8") == "DISCORD_TOKEN=abc\nDEEPSEEK_API_KEY=sk-123\nX=1\n"
    set_env_value(env, "NEW", "v")
    assert env.read_text(encoding="utf-8").endswith("NEW=v\n")


async def test_chatter_api(authed, app, fake_deepseek):
    cfg = (await authed.get(f"/api/guilds/{GID}/config/chatter")).json()
    assert cfg["enabled"] is False and cfg["phrases"]
    status = (await authed.get(f"/api/guilds/{GID}/chatter/status")).json()
    assert status["ai_configured"] is False

    r = (await authed.post(f"/api/guilds/{GID}/chatter/test", json={"config": cfg})).json()
    assert r["source"] == "phrase" and r["text"] in cfg["phrases"]

    assert (await authed.put("/api/ai/key", json={"key": "sk-bad\nINJECT=1"})).status_code == 422
    r = await authed.put("/api/ai/key", json={"key": "sk-abcdef123456"})
    assert r.status_code == 200 and r.json()["key_hint"] == "…3456"
    assert "DEEPSEEK_API_KEY=sk-abcdef123456" in app.settings.env_path.read_text(encoding="utf-8")
    assert "sk-abcdef" not in (await authed.get(f"/api/guilds/{GID}/chatter/status")).text  # ключ не отдаётся

    app.settings.deepseek_base_url = fake_deepseek["url"]
    fake_deepseek["reply"] = "кто-нибудь видел мою дотку?"
    r = (await authed.post(f"/api/guilds/{GID}/chatter/test", json={"config": cfg})).json()
    assert r == {"text": "кто-нибудь видел мою дотку?", "source": "ai", "error": None, "context": r["context"]}
    assert "Вася: кто сегодня в доту?" in r["context"]
    fake_deepseek["status"] = 402
    r = (await authed.post(f"/api/guilds/{GID}/chatter/test", json={"config": cfg})).json()
    assert r["text"] is None and "баланс" in r["error"]
    assert "баланс" in (await authed.get(f"/api/guilds/{GID}/chatter/status")).json()["last_error"]

    assert (await authed.put("/api/ai/key", json={"key": ""})).json()["ai_configured"] is False


def test_hourly_limit():
    from mishanya.bot.cogs.chatter import Chatter

    c = Chatter.__new__(Chatter)
    c.hourly = {}
    assert all(c._take_hourly(1, 3, 100.0 + i) for i in range(3))
    assert not c._take_hourly(1, 3, 200.0)
    assert c._take_hourly(1, 3, 100.0 + 3601)  # старые вышли из окна часа


def test_speech_text_and_ffmpeg_options():
    from mishanya.services.tts import ffmpeg_options, speech_text

    assert speech_text("**Холопы** <@&123456789012345678> смотрите https://x.y 😂 <:kek:123456789012345678>") == "Холопы смотрите kek"
    assert ffmpeg_options("edge", 20) == "-vn"
    assert ffmpeg_options("google", 0) == "-vn"
    assert "atempo=1.20" in ffmpeg_options("google", 20)


async def test_voice_api(authed, monkeypatch):
    from mishanya.api.routes import features

    cfg = (await authed.get(f"/api/guilds/{GID}/config/chatter")).json()
    assert cfg["voice_enabled"] is False and cfg["voice_engine"] == "auto"

    async def fake_speak(text, c):  # noqa: ANN001
        assert (c.voice_name, c.voice_engine) == ("dmitry", "auto")
        return b"ID3fake-mp3", "google"

    monkeypatch.setattr(features, "speak", fake_speak)
    r = await authed.post(f"/api/guilds/{GID}/chatter/tts-preview", json={"text": "холопы", "config": cfg})
    assert r.status_code == 200 and r.content == b"ID3fake-mp3" and r.headers["x-tts-engine"] == "google"
    bad = await authed.post(f"/api/guilds/{GID}/chatter/tts-preview", json={"text": "x", "config": {**cfg, "voice_rate": 500}})
    assert bad.status_code == 422
    # без подключённого бота зайти в канал нельзя — понятная ошибка
    r = await authed.post(f"/api/guilds/{GID}/chatter/voice-say", json={"config": cfg, "channel_id": GID})
    assert r.status_code == 409


async def test_paid_tts_engines_and_fallback(monkeypatch):
    """ElevenLabs/OpenAI: правильный запрос; при сбое — Microsoft, а не женский Google."""
    from types import SimpleNamespace

    from mishanya.services import tts

    calls = []

    async def fake_post(url, headers, body, name):  # noqa: ANN001
        calls.append((url, headers, body))
        if "fail" in body.get("text", body.get("input", "")):
            raise tts.TtsError("сбой")
        return b"mp3"

    async def fake_edge(text, voice, rate, pitch):  # noqa: ANN001
        return b"edge-mp3"

    monkeypatch.setattr(tts, "_post_audio", fake_post)
    monkeypatch.setattr(tts, "_edge", fake_edge)
    monkeypatch.setattr(tts, "_edge_failed_at", 0.0)
    tts.configure(SimpleNamespace(elevenlabs_api_key="el-key", tts_key="sk", tts_url="https://x/v1", tts_model="tts-1"))
    assert await tts.synthesize("привет", engine="elevenlabs", voice_id="pNInz6obpgDQGcFmaJgB", rate=10) == (b"mp3", "elevenlabs")
    url, headers, body = calls[-1]
    assert url.endswith("/pNInz6obpgDQGcFmaJgB?output_format=mp3_44100_128") and headers == {"xi-api-key": "el-key"}
    assert body["model_id"] == "eleven_flash_v2_5" and body["voice_settings"]["speed"] == 1.1
    assert await tts.synthesize("привет", engine="openai", voice_id="onyx") == (b"mp3", "openai")
    assert calls[-1][0] == "https://x/v1/audio/speech" and calls[-1][2]["voice"] == "onyx"
    assert await tts.synthesize("fail", engine="elevenlabs") == (b"edge-mp3", "edge")
    tts.configure(None)
