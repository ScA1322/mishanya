"""Озвучка текста для голосовых каналов.

Два бесплатных движка: голоса Microsoft Edge (edge-tts — естественнее, есть мужской голос, но сервис часто
отвечает пустым звуком) и Google Translate (gTTS — проще звучит, зато стабилен). В режиме «auto» сначала
пробуем Microsoft, при неудаче — Google.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re

log = logging.getLogger(__name__)

VOICES = {"dmitry": "ru-RU-DmitryNeural", "svetlana": "ru-RU-SvetlanaNeural"}
MAX_SPEECH = 400  # символов в одной фразе
EDGE_ATTEMPTS = 3

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
            async for chunk in com.stream():
                if chunk["type"] == "audio":
                    data += chunk["data"]
            if data:
                return bytes(data)
        except Exception as exc:  # noqa: BLE001 — NoAudioReceived, сеть и т. п.
            last = exc
        await asyncio.sleep(0.4 + 0.4 * attempt)
    raise TtsError(f"Microsoft не озвучил ({type(last).__name__ if last else 'пустой звук'})")


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


async def synthesize(text: str, voice: str = "dmitry", rate: int = 0, pitch: int = 0,
                     engine: str = "auto") -> tuple[bytes, str]:
    """Возвращает (MP3, движок). rate — скорость в % (-50…+100), pitch — высота в Гц (только Microsoft)."""
    text = speech_text(text)
    if not text:
        raise TtsError("Нечего озвучивать")
    errors = []
    if engine in ("auto", "edge"):
        try:
            return await _edge(text, voice, rate, pitch), "edge"
        except TtsError as exc:
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
