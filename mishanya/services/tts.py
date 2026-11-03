"""Озвучка текста для голосовых каналов.

Бесплатные движки: голоса Microsoft Edge (edge-tts — естественнее, есть мужской голос, но сервис часто
отвечает пустым звуком) и Google Translate (gTTS — стабилен, но голос только женский). Платные, быстрые и
стабильные: ElevenLabs (модель Flash, ~0,1–0,3 с) и OpenAI-совместимый /audio/speech (например голос onyx).
При сбое выбранного движка пробуем следующий: платный → Microsoft → Google.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import time

log = logging.getLogger(__name__)

VOICES = {"dmitry": "ru-RU-DmitryNeural", "svetlana": "ru-RU-SvetlanaNeural"}
ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVEN_MODEL = "eleven_flash_v2_5"  # самая быстрая модель ElevenLabs, говорит по-русски
PAID_TIMEOUT = 15
_settings = None  # Settings приложения: ключи платных движков (задаётся в App)


def configure(settings) -> None:  # noqa: ANN001
    global _settings
    _settings = settings
MAX_SPEECH = 400  # символов в одной фразе
EDGE_ATTEMPTS = 2
EDGE_TIMEOUT = 6  # секунд на попытку — дольше ждать нет смысла, лучше сразу Google
EDGE_COOLDOWN = 180  # после сбоя Microsoft столько секунд сразу озвучиваем через Google
_edge_failed_at = 0.0

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


class TtsError(Exception):
    pass


def speech_text(text: str) -> str:
    """Убирает из реплики то, что не надо зачитывать: упоминания, ссылки, разметку, эмодзи."""
    text = re.sub(r"<a?:(\w+):\d+>", r"\1", text)  # свои эмодзи сервера — по названию
    text = re.sub(r"<[@#][!&]?\d+>", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_~`|>#]", "", text)
    text = _EMOJI.sub("", text)
    return " ".join(text.split())[:MAX_SPEECH]


async def _edge(text: str, voice: str, rate: int, pitch: int) -> bytes:
    try:
        import edge_tts
    except ImportError:
        raise TtsError("не установлен пакет edge-tts") from None
    last: Exception | None = None
    for attempt in range(EDGE_ATTEMPTS):
        try:
            com = edge_tts.Communicate(text, VOICES.get(voice, VOICES["dmitry"]), rate=f"{rate:+d}%",
                                       pitch=f"{pitch:+d}Hz")
            data = bytearray()

            async def collect() -> None:
                async for chunk in com.stream():
                    if chunk["type"] == "audio":
                        data.extend(chunk["data"])

            await asyncio.wait_for(collect(), EDGE_TIMEOUT)
            if data:
                return bytes(data)
        except Exception as exc:  # noqa: BLE001 — NoAudioReceived, сеть и т. п.
            last = exc
        await asyncio.sleep(0.3)
    raise TtsError(f"Microsoft не озвучил ({type(last).__name__ if last else 'пустой звук'})")


async def _post_audio(url: str, headers: dict, body: dict, name: str) -> bytes:
    import aiohttp

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=PAID_TIMEOUT)) as s:
            async with s.post(url, json=body, headers=headers) as r:
                if r.status == 401:
                    raise TtsError(f"{name} не принял ключ (401)")
                if r.status == 402 or r.status == 429:
                    raise TtsError(f"{name}: закончился лимит или деньги ({r.status})")
                if r.status >= 400:
                    raise TtsError(f"{name} ответил ошибкой {r.status}: {(await r.text())[:200]}")
                data = await r.read()
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise TtsError(f"нет связи с {name}: {exc or 'тайм-аут'}") from None
    if not data:
        raise TtsError(f"{name} вернул пустой звук")
    return data


async def _eleven(text: str, voice_id: str, rate: int) -> bytes:
    key = getattr(_settings, "elevenlabs_api_key", "")
    if not key:
        raise TtsError("не задан ключ ElevenLabs")
    body = {"text": text, "model_id": ELEVEN_MODEL, "language_code": "ru",
            "voice_settings": {"stability": 0.45, "similarity_boost": 0.8,
                               "speed": max(0.7, min(1.2, 1 + rate / 100))}}
    return await _post_audio(f"{ELEVEN_URL}/{voice_id}?output_format=mp3_44100_128",
                             {"xi-api-key": key}, body, "ElevenLabs")


async def _openai(text: str, voice: str, rate: int) -> bytes:
    s = _settings
    key = getattr(s, "tts_key", "")
    if not key:
        raise TtsError("не задан ключ для озвучки")
    body = {"model": getattr(s, "tts_model", "tts-1"), "input": text, "voice": voice or "onyx",
            "response_format": "mp3", "speed": max(0.25, min(4.0, 1 + rate / 100))}
    return await _post_audio(f"{s.tts_url}/audio/speech", {"Authorization": f"Bearer {key}"}, body, "сервис озвучки")


def _google_sync(text: str) -> bytes:
    try:
        from gtts import gTTS
    except ImportError:
        raise TtsError("не установлен пакет gTTS") from None
    buf = io.BytesIO()
    try:
        gTTS(text, lang="ru").write_to_fp(buf)
    except Exception as exc:  # noqa: BLE001
        raise TtsError(f"Google не озвучил ({exc})") from None
    return buf.getvalue()


async def speak(text: str, cfg) -> tuple[bytes, str]:  # noqa: ANN001 — ChatterConfig
    """Озвучка по настройкам «Болтовни»."""
    voice_id = cfg.voice_eleven_id if cfg.voice_engine == "elevenlabs" else cfg.voice_openai
    return await synthesize(text, cfg.voice_name, cfg.voice_rate, cfg.voice_pitch, cfg.voice_engine, voice_id)


async def synthesize(text: str, voice: str = "dmitry", rate: int = 0, pitch: int = 0,
                     engine: str = "auto", voice_id: str = "") -> tuple[bytes, str]:
    """Возвращает (MP3, движок). rate — скорость в % (-50…+100), pitch — высота в Гц (только Microsoft).
    voice_id — голос платного движка (ID голоса ElevenLabs или имя голоса OpenAI)."""
    text = speech_text(text)
    if not text:
        raise TtsError("Нечего озвучивать")
    global _edge_failed_at
    errors = []
    if engine in ("elevenlabs", "openai"):
        try:
            if engine == "elevenlabs":
                return await _eleven(text, voice_id or "pNInz6obpgDQGcFmaJgB", rate), "elevenlabs"
            return await _openai(text, voice_id, rate), "openai"
        except TtsError as exc:
            log.warning("Озвучка %s не удалась, пробую Microsoft: %s", engine, exc)
            errors.append(str(exc))
            engine = "auto"
    edge_down = engine == "auto" and time.monotonic() - _edge_failed_at < EDGE_COOLDOWN
    if engine in ("auto", "edge") and not edge_down:
        try:
            return await _edge(text, voice, rate, pitch), "edge"
        except TtsError as exc:
            _edge_failed_at = time.monotonic()
            errors.append(str(exc))
    if engine in ("auto", "google"):
        try:
            return await asyncio.to_thread(_google_sync, text), "google"
        except TtsError as exc:
            errors.append(str(exc))
    log.warning("Озвучка не удалась: %s", "; ".join(errors))
    raise TtsError("Сервис озвучки не ответил: " + "; ".join(errors))


def ffmpeg_options(engine: str, rate: int) -> str:
    """Google не умеет менять скорость — ускоряем/замедляем при воспроизведении."""
    if engine == "google" and rate:
        tempo = max(0.5, min(2.0, 1 + rate / 100))
        return f'-vn -filter:a "atempo={tempo:.2f}"'
    return "-vn"
