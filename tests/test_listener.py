"""Слушание: нарезка звука на фразы, WAV, распознавание (на фейковом сервере), расшифровка DAVE, API панели."""
from __future__ import annotations

import io
import math
import struct
import wave
from types import SimpleNamespace

import pytest
from aiohttp import web

from mishanya.services.stt import FRAME_BYTES, SttError, UtteranceCollector, pcm_to_wav, transcribe

pytestmark = pytest.mark.anyio
GID = "111111111111111111"


def frame(amplitude: int) -> bytes:
    """20 мс стерео 48 кГц: синус 440 Гц нужной громкости (0 — тишина)."""
    out = bytearray()
    for i in range(960):
        v = int(amplitude * math.sin(2 * math.pi * 440 * i / 48000))
        out += struct.pack("<hh", v, v)
    assert len(out) == FRAME_BYTES
    return bytes(out)


def test_collector_splits_phrases_by_silence():
    c = UtteranceCollector(end_silence=0.8, max_seconds=15)
    loud, quiet = frame(8000), frame(0)
    t = 0.0
    for _ in range(10):  # тишина до фразы не копится
        c.feed(1, quiet, t)
        t += 0.02
    for _ in range(50):  # 1 секунда речи
        c.feed(1, loud, t)
        t += 0.02
    for _ in range(10):  # короткая пауза внутри фразы
        c.feed(1, quiet, t)
        t += 0.02
    assert c.pop_finished(t) == []  # ещё не замолчал надолго
    done = c.pop_finished(t + 0.9)
    assert len(done) == 1 and done[0][0] == 1 and abs(done[0][2] - 1.0) < 0.01
    assert len(done[0][1]) == 60 * FRAME_BYTES
    assert c.pop_finished(t + 5) == []


def test_collector_cuts_long_speech():
    c = UtteranceCollector(end_silence=0.8, max_seconds=1)
    for i in range(60):
        c.feed(7, frame(8000), i * 0.02)
    assert len(c.pop_finished(1.2)) == 1  # длиннее max_seconds — отдаём, не дожидаясь паузы


def test_pcm_to_wav():
    wav = pcm_to_wav(frame(8000) * 50)  # 1 секунда
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        assert abs(w.getnframes() - 16000) < 50


async def test_transcribe_against_fake_api():
    seen = {}

    async def handler(request: web.Request) -> web.Response:
        form = await request.post()
        seen.update(model=form["model"], language=form["language"], auth=request.headers["Authorization"],
                    size=len(form["file"].file.read()))
        if form["model"] == "bad":
            return web.Response(status=404)
        return web.json_response({"text": " Император, ты где? "})

    app = web.Application()
    app.router.add_post("/v1/audio/transcriptions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1"  # noqa: SLF001
    try:
        wav = pcm_to_wav(frame(8000) * 25)
        assert await transcribe(wav, api_key="sk-x", base_url=url, model="gpt-4o-transcribe") == "Император, ты где?"
        assert seen == {"model": "gpt-4o-transcribe", "language": "ru", "auth": "Bearer sk-x", "size": len(wav)}
        with pytest.raises(SttError, match="не умеет распознавать"):
            await transcribe(wav, api_key="sk-x", base_url=url, model="bad")
        with pytest.raises(SttError, match="ключ"):
            await transcribe(wav, api_key="", base_url=url, model="m")
    finally:
        await runner.cleanup()


def test_dave_decrypt_wrapper():
    from mishanya.bot.cogs.listener import DAVE_MAGIC, OPUS_SILENCE, install_dave_decrypt

    calls = []

    class FakeSession:
        def decrypt(self, user_id, media_type, packet):  # noqa: ANN001
            calls.append(user_id)
            if packet.startswith(b"broken"):
                raise RuntimeError("bad frame")
            return b"opus:" + packet[:-len(DAVE_MAGIC)]

        def can_passthrough(self, user_id):  # noqa: ANN001
            return False

    decryptor = SimpleNamespace(decrypt_rtp=lambda p: p.data)
    vc = SimpleNamespace(_reader=SimpleNamespace(decryptor=decryptor), _ssrc_to_id={5: 42},
                         _connection=SimpleNamespace(dave_session=FakeSession(), dave_protocol_version=1))
    install_dave_decrypt(vc)
    pkt = lambda ssrc, data: SimpleNamespace(ssrc=ssrc, data=data)  # noqa: E731
    assert decryptor.decrypt_rtp(pkt(5, b"frame" + DAVE_MAGIC)) == b"opus:frame" and calls == [42]
    assert decryptor.decrypt_rtp(pkt(5, OPUS_SILENCE)) == OPUS_SILENCE  # тишину не трогаем
    assert decryptor.decrypt_rtp(pkt(9, b"x" + DAVE_MAGIC)) == OPUS_SILENCE  # чужой ssrc ещё неизвестен
    assert decryptor.decrypt_rtp(pkt(5, b"broken" + DAVE_MAGIC)) == OPUS_SILENCE
    vc._connection.dave_protocol_version = 0  # канал без E2EE — пакеты как есть
    assert decryptor.decrypt_rtp(pkt(5, b"plain")) == b"plain"


async def test_listen_api_without_bot(authed):
    st = (await authed.get(f"/api/guilds/{GID}/listen")).json()
    assert st["listening"] is False and st["stt_model"] == "gpt-4o-transcribe"
    assert (await authed.post(f"/api/guilds/{GID}/listen/start", json={"channel_id": GID})).status_code == 409
    cfg = (await authed.get(f"/api/guilds/{GID}/config/chatter")).json()
    assert cfg["listen_wake_words"] == ["император"] and cfg["listen_followup_seconds"] == 30


async def test_wake_word_and_followup(monkeypatch):
    from mishanya.bot.cogs import listener as L
    from mishanya.schemas import ChatterConfig

    heard = iter(["ну короче вчера было", "император, ты тут?", "а что скажешь про доту?", "погода норм"])
    monkeypatch.setattr(L, "pcm_to_wav", lambda pcm: b"wav")

    async def fake_transcribe(wav, **kw):  # noqa: ANN001
        return next(heard)

    replies = []

    async def fake_generate(**kw):  # noqa: ANN003
        replies.append(kw["history"].splitlines()[-1])
        return "холоп, Император слушает"

    async def fake_synth(*a):  # noqa: ANN002
        return b"mp3", "google"

    monkeypatch.setattr(L, "transcribe", fake_transcribe)
    monkeypatch.setattr(L, "generate", fake_generate)
    monkeypatch.setattr(L, "speak", fake_synth)
    monkeypatch.setattr(L.discord, "FFmpegPCMAudio", lambda *a, **kw: object())

    played = []
    member = SimpleNamespace(display_name="Вася", bot=False)
    vc = SimpleNamespace(is_connected=lambda: True, is_playing=lambda: False,
                         channel=SimpleNamespace(name="Основной", members=[member]),
                         play=lambda src, after: (played.append(src), after(None)))
    guild = SimpleNamespace(id=1, me=SimpleNamespace(display_name="Мишаня"), get_member=lambda uid: member)
    settings = SimpleNamespace(stt_key="k", stt_url="u", stt_model="m", deepseek_api_key="k",
                               deepseek_base_url="u", deepseek_model="m")
    cog = L.Listener.__new__(L.Listener)
    cog.bot = SimpleNamespace(get_guild=lambda gid: guild, get_cog=lambda name: None)
    cog.app = SimpleNamespace(settings=settings, chatter_error=None)
    cog.stt_sem = L.asyncio.Semaphore(2)
    session = L.Session(vc=vc, channel_id=10, started=0, collector=L.UtteranceCollector())
    cog.sessions = {1: session}
    cfg = ChatterConfig(enabled=True, listen_followup_seconds=30)

    await cog._handle(1, 5, b"pcm", cfg)  # без обращения — молчит
    assert replies == [] and played == []
    await cog._handle(1, 5, b"pcm", cfg)  # «император» — отвечает
    assert replies == ["Вася: император, ты тут?"] and len(played) == 1
    await cog._handle(1, 5, b"pcm", cfg)  # в течение 30 с после ответа — без обращения тоже
    assert len(played) == 2
    session.open_until = 0  # окно закрылось
    await cog._handle(1, 5, b"pcm", cfg)
    assert len(played) == 2
    assert [r[3] for r in session.recent if r[1] == "Вася"] == [False, True, True, False]
