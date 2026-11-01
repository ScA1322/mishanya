"""Посты: хранение оформленных сообщений и загруженных для них изображений."""
from __future__ import annotations

import re
import secrets
import time
from pathlib import Path

from sqlalchemy import func, select, update

from ..db import Database, utcnow
from ..db.models import Post
from ..schemas import UPLOAD_RE, PostConfig
from .menus import MAX_IMAGE_BYTES, detect_image

MAX_POSTS_PER_GUILD = 200
MAX_UPLOADS_PER_GUILD = 300
ORPHAN_TTL = 24 * 3600  # загруженный, но так и не сохранённый в пост файл живёт сутки


class PostLimit(ValueError):
    pass


class PostService:
    def __init__(self, db: Database, upload_dir: Path):
        self.db = db
        self.upload_dir = upload_dir

    # ------------------------------------------------------------ посты

    async def list(self, guild_id: int) -> list[Post]:
        async with self.db.session() as s:
            return list((await s.execute(select(Post).where(Post.guild_id == guild_id)
                                         .order_by(Post.updated_at.desc(), Post.id.desc()))).scalars())

    async def get(self, guild_id: int, post_id: int) -> Post | None:
        async with self.db.session() as s:
            p = await s.get(Post, post_id)
            return p if p is not None and p.guild_id == guild_id else None

    async def create(self, guild_id: int, data: PostConfig) -> Post:
        self._check_files(guild_id, data)
        async with self.db.session() as s:
            count = (await s.execute(select(func.count()).select_from(Post)
                                     .where(Post.guild_id == guild_id))).scalar_one()
            if count >= MAX_POSTS_PER_GUILD:
                raise PostLimit(f"На сервере уже {count} постов (максимум {MAX_POSTS_PER_GUILD})")
            p = Post(guild_id=guild_id, **self._fields(data))
            s.add(p)
            await s.commit()
        await self.cleanup(guild_id)
        return p

    async def update(self, guild_id: int, post_id: int, data: PostConfig) -> Post | None:
        self._check_files(guild_id, data)
        async with self.db.session() as s:
            p = await s.get(Post, post_id)
            if p is None or p.guild_id != guild_id:
                return None
            for k, v in self._fields(data).items():
                setattr(p, k, v)
            p.updated_at = utcnow()
            await s.commit()
        await self.cleanup(guild_id)
        return p

    async def delete(self, guild_id: int, post_id: int) -> Post | None:
        async with self.db.session() as s:
            p = await s.get(Post, post_id)
            if p is None or p.guild_id != guild_id:
                return None
            await s.delete(p)
            await s.commit()
        await self.cleanup(guild_id)
        return p

    async def set_published(self, post_id: int, channel_id: int | None, message_id: int | None) -> None:
        async with self.db.session() as s:
            await s.execute(update(Post).where(Post.id == post_id).values(
                published_channel_id=channel_id if message_id else None, message_id=message_id,
                published_at=utcnow() if message_id else None))
            await s.commit()

    @staticmethod
    def config(p: Post) -> PostConfig:
        return PostConfig.model_validate({**(p.data or {}), "name": p.name,
                                          "channel_id": str(p.channel_id) if p.channel_id else None})

    @staticmethod
    def _fields(data: PostConfig) -> dict:
        d = data.model_dump(mode="json", exclude={"name", "channel_id"})
        return {"name": data.name, "channel_id": int(data.channel_id) if data.channel_id else None, "data": d}

    # ------------------------------------------------------------ загруженные изображения

    def _dir(self, guild_id: int) -> Path:
        return self.upload_dir / str(guild_id)

    def file_path(self, guild_id: int, name: str) -> Path | None:
        if not re.match(UPLOAD_RE, name):
            return None
        path = self._dir(guild_id) / name
        return path if path.is_file() else None

    def save_upload(self, guild_id: int, data: bytes) -> str:
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"Изображение больше {MAX_IMAGE_BYTES // 1024 // 1024} МБ")
        ext = detect_image(data)
        if ext is None:
            raise ValueError("Поддерживаются только изображения PNG, JPEG, GIF и WebP")
        folder = self._dir(guild_id)
        folder.mkdir(parents=True, exist_ok=True)
        if sum(1 for _ in folder.iterdir()) >= MAX_UPLOADS_PER_GUILD:
            raise ValueError("Загружено слишком много изображений — удалите ненужные посты")
        name = f"img-{secrets.token_hex(6)}.{ext}"
        (folder / name).write_bytes(data)
        return name

    def _check_files(self, guild_id: int, data: PostConfig) -> None:
        missing = [n for n in data.uploads() if self.file_path(guild_id, n) is None]
        if missing:
            raise ValueError("Загруженное изображение не найдено — загрузите его заново")

    async def cleanup(self, guild_id: int) -> None:
        """Удаляет файлы, на которые не ссылается ни один пост (свежие загрузки не трогает)."""
        folder = self._dir(guild_id)
        if not folder.is_dir():
            return
        used: set[str] = set()
        for p in await self.list(guild_id):
            try:
                used.update(self.config(p).uploads())
            except ValueError:
                return  # повреждённые данные — лучше ничего не удалять
        now = time.time()
        for f in folder.iterdir():
            if f.is_file() and f.name not in used and now - f.stat().st_mtime > ORPHAN_TTL:
                f.unlink(missing_ok=True)
