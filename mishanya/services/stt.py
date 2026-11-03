"""Распознавание речи для «слушания» в голосовых каналах.

Звук из Discord приходит кусками по 20 мс (PCM 48 кГц, стерео, 16 бит) отдельно для каждого участника.
UtteranceCollector склеивает их во фразы: фраза заканчивается, когда человек замолчал. Готовую фразу
переводим в WAV 16 кГц моно и отправляем в OpenAI-совместимый /audio/transcriptions (например, Inferhold).
"""
from __future__ import annotations

import io
import threading
import wave
from dataclasses import dataclass, field

import aiohttp

try:  # в Python 3.13+ модуль audioop ставится пакетом audioop-lts (зависимость discord.py)
    import audioop
except ImportError:  # pragma: no cover
    audioop = None  # type: ignore[assignment]

SAMPLE_RATE = 48000
FRAME_BYTES = 3840  # 20 мс стерео s16le
BYTES_PER_SECOND = SAMPLE_RATE * 2 * 2
VOICE_RMS = 350  # громкость, начиная с которой кадр считается речью
REQUEST_TIMEOUT = 40


class SttError(Exception):
    pass


def rms(pcm: bytes) -> int:
    if audioop is None or not pcm:
        return 0
    return audioop.rms(pcm, 2)


def pcm_to_wav(pcm: bytes) -> bytes:
    """PCM 48 кГц стерео → WAV 16 кГц моно (меньше трафика, распознавателю хватает)."""
    if audioop is None:
        raise SttError("нет модуля audioop — переустановите зависимости (setup.bat)")
    mono = audioop.tomono(pcm, 2, 0.5, 0.5)
    mono, _ = audioop.ratecv(mono, 2, 1, SAMPLE_RATE, 16000, None)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(mono)
    return buf.getvalue()


@dataclass
class _Speech:
    pcm: bytearray = field(default_factory=bytearray)
    voiced: int = 0  # кадров с речью
    last_voice: float = 0.0
    started: float = 0.0


class UtteranceCollector:
    """Собирает звук участников во фразы. feed() вызывается из потока приёма звука, pop_finished() — из asyncio."""

    def __init__(self, end_silence: float = 0.8, max_seconds: float = 15.0):
        self.end_silence = end_silence
        self.max_bytes = int(max_seconds * BYTES_PER_SECOND)
        self._lock = threading.Lock()
        self._users: dict[int, _Speech] = {}

    def feed(self, user_id: int, pcm: bytes, now: float) -> None:
        voiced = rms(pcm) >= VOICE_RMS
        with self._lock:
            sp = self._users.get(user_id)
            if sp is None:
                if not voiced:
                    return  # тишина до начала фразы не нужна
                sp = self._users[user_id] = _Speech(started=now)
            sp.pcm += pcm
            if voiced:
                sp.voiced += 1
                sp.last_voice = now

    def pop_finished(self, now: float) -> list[tuple[int, bytes, float]]:
        """Фразы, после которых человек молчит end_silence секунд (или слишком длинные). (id, pcm, сек речи)."""
        done = []
        with self._lock:
            for uid, sp in list(self._users.items()):
                if now - sp.last_voice >= self.end_silence or len(sp.pcm) >= self.max_bytes:
                    del self._users[uid]
                    done.append((uid, bytes(sp.pcm), sp.voiced * 0.02))
        return done

    def speaking(self, now: float, window: float = 0.35, min_voiced: int = 0) -> bool:
        """Говорит ли кто-то прямо сейчас (речь была не позже window секунд назад)."""
        with self._lock:
            return any(now - sp.last_voice < window and sp.voiced >= min_voiced for sp in self._users.values())

    def clear(self) -> None:
        with self._lock:
            self._users.clear()


async def transcribe(wav: bytes, *, api_key: str, base_url: str, model: str, language: str = "ru") -> str:
    if not api_key:
        raise SttError("не задан ключ для распознавания речи")
    form = aiohttp.FormData()
    form.add_field("model", model)
    form.add_field("language", language)
    form.add_field("file", wav, filename="speech.wav", content_type="audio/wav")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as s:
            async with s.post(f"{base_url}/audio/transcriptions", data=form,
                              headers={"Authorization": f"Bearer {api_key}"}) as r:
                if r.status == 404:
                    raise SttError(f"{base_url} не умеет распознавать речь (404). Укажите STT_BASE_URL в .env, "
                                   "например https://api.inferhold.com/v1")
                if r.status == 401:
                    raise SttError("сервис распознавания не принял ключ (401)")
                if r.status == 402:
                    raise SttError("на балансе сервиса распознавания закончились деньги (402)")
                if r.status >= 400:
                    raise SttError(f"сервис распознавания ответил ошибкой {r.status}: {(await r.text())[:200]}")
                data = await r.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise SttError(f"нет связи с сервисом распознавания: {exc or 'тайм-аут'}") from None
    text = data.get("text") if isinstance(data, dict) else None
    if not isinstance(text, str):
        raise SttError("сервис распознавания вернул ответ непонятного формата")
    return text.strip()
