"""Поиск и получение треков SoundCloud через yt-dlp, очередь воспроизведения сервера."""
from __future__ import annotations

import asyncio
import logging
import random
import re
import shutil
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlparse

log = logging.getLogger(__name__)

SOUNDCLOUD_HOSTS = {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com", "on.soundcloud.com",
                    "api.soundcloud.com"}
URL_RE = re.compile(r"^https?://", re.IGNORECASE)
MAX_PLAYLIST = 100
YDL_BASE = {
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,
    "skip_download": True,
    # Прямой mp3-поток надёжнее для FFmpeg, HLS — запасной вариант.
    "format": "bestaudio[protocol^=http]/bestaudio/best",
    "socket_timeout": 15,
}
FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin"


class MusicError(Exception):
    """Ошибка, текст которой показывается пользователю."""


@dataclass
class Track:
    title: str
    page_url: str
    duration: int | None = None  # секунды
    artist: str = ""
    thumbnail: str | None = None
    requester_id: int = 0

    @property
    def duration_text(self) -> str:
        return format_duration(self.duration)


def format_duration(seconds: int | float | None) -> str:
    if not seconds:
        return "—"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def ffmpeg_executable() -> str:
    """FFmpeg из пакета imageio-ffmpeg (ставится вместе с зависимостями) или из PATH."""
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        pass
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise MusicError("Не найден FFmpeg. Запустите setup.bat ещё раз — он установит недостающие компоненты.")
    return exe


def classify_query(query: str) -> str:
    """Возвращает строку для yt-dlp: ссылку SoundCloud или поиск по SoundCloud."""
    query = query.strip()
    if not query:
        raise MusicError("Укажите название трека или ссылку на SoundCloud.")
    if URL_RE.match(query):
        host = (urlparse(query).hostname or "").lower()
        if host not in SOUNDCLOUD_HOSTS:
            raise MusicError("Поддерживаются только ссылки SoundCloud (soundcloud.com). "
                             "Или просто напишите название трека.")
        return query.split("?")[0] if host != "on.soundcloud.com" else query
    return f"scsearch1:{query[:200]}"


def _track_from_info(info: dict, requester_id: int) -> Track:
    url = info.get("webpage_url") or info.get("url") or ""
    return Track(title=(info.get("title") or "Без названия")[:200], page_url=url,
                 duration=int(info["duration"]) if info.get("duration") else None,
                 artist=(info.get("uploader") or info.get("artist") or "")[:100],
                 thumbnail=info.get("thumbnail"), requester_id=requester_id)


def _extract(target: str, flat: bool) -> dict:
    import yt_dlp

    opts = dict(YDL_BASE)
    if flat:
        opts["extract_flat"] = "in_playlist"
        opts["playlistend"] = MAX_PLAYLIST
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(target, download=False) or {}
    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc).removeprefix("ERROR: ")
        log.info("yt-dlp: %s", msg)
        if "404" in msg or "Unable to download JSON" in msg:
            raise MusicError("Трек не найден или недоступен (удалён, приватный или закрыт в вашей стране).") from None
        raise MusicError(f"SoundCloud не отдал трек: {msg[:200]}") from None


async def search(query: str, requester_id: int) -> list[Track]:
    """Находит трек или плейлист SoundCloud. Ссылки на поток не сохраняются — они быстро устаревают."""
    target = classify_query(query)
    info = await asyncio.to_thread(_extract, target, True)
    entries = info.get("entries")
    if entries is None:
        tracks = [_track_from_info(info, requester_id)]
    else:
        tracks = [_track_from_info(e, requester_id) for e in entries if e and (e.get("url") or e.get("webpage_url"))]
    tracks = [t for t in tracks if t.page_url]
    if not tracks:
        raise MusicError("Ничего не найдено на SoundCloud.")
    return tracks


async def resolve_stream(track: Track) -> tuple[str, dict]:
    """Получает свежую ссылку на аудиопоток и уточняет данные трека перед воспроизведением."""
    info = await asyncio.to_thread(_extract, track.page_url, False)
    if info.get("entries"):
        info = next((e for e in info["entries"] if e), {})
    url = info.get("url")
    if not url:
        raise MusicError(f"Не удалось получить аудиопоток «{track.title}».")
    fresh = _track_from_info(info, track.requester_id)
    track.title, track.artist = fresh.title, fresh.artist or track.artist
    track.duration = fresh.duration or track.duration
    track.thumbnail = fresh.thumbnail or track.thumbnail
    return url, info.get("http_headers") or {}


@dataclass
class MusicQueue:
    tracks: deque[Track] = field(default_factory=deque)
    current: Track | None = None
    loop: str = "off"  # off | track | queue
    volume: int = 50

    def add(self, items: list[Track], limit: int) -> int:
        free = max(0, limit - len(self.tracks))
        for t in items[:free]:
            self.tracks.append(t)
        return min(free, len(items))

    def next(self) -> Track | None:
        """Выбирает следующий трек с учётом режима повтора."""
        if self.current is not None:
            if self.loop == "track":
                return self.current
            if self.loop == "queue":
                self.tracks.append(self.current)
        self.current = self.tracks.popleft() if self.tracks else None
        return self.current

    def shuffle(self) -> None:
        items = list(self.tracks)
        random.shuffle(items)
        self.tracks = deque(items)

    def clear(self) -> None:
        self.tracks.clear()
        self.current = None

    @property
    def total_seconds(self) -> int:
        return sum(t.duration or 0 for t in self.tracks)
