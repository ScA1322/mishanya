"""Меню с кнопками: хранение настроек и загруженных изображений."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select, update

from ..db import Database, utcnow
from ..db.models import ButtonMenu
from ..schemas import MenuConfig

# Discord принимает вложения до 10 МБ без Nitro-буста сервера; держим запас.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_MENUS_PER_GUILD = 100
IMAGE_TYPES = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpg",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
}


def detect_image(data: bytes) -> str | None:
    """Определяет формат по сигнатуре файла, а не по имени (PNG, JPEG, GIF, WebP)."""
    for magic, ext in IMAGE_TYPES.items():
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


class MenuLimit(ValueError):
    pass


class MenuService:
    def __init__(self, db: Database, image_dir: Path):
        self.db = db
        self.image_dir = image_dir

    async def list(self, guild_id: int) -> list[ButtonMenu]:
        async with self.db.session() as s:
            return list((await s.execute(select(ButtonMenu).where(ButtonMenu.guild_id == guild_id)
                                         .order_by(ButtonMenu.name, ButtonMenu.id))).scalars())

    async def get(self, guild_id: int, menu_id: int) -> ButtonMenu | None:
        async with self.db.session() as s:
            m = await s.get(ButtonMenu, menu_id)
            return m if m is not None and m.guild_id == guild_id else None

    async def get_any(self, menu_id: int) -> ButtonMenu | None:
        async with self.db.session() as s:
            return await s.get(ButtonMenu, menu_id)

    async def create(self, guild_id: int, data: MenuConfig) -> ButtonMenu:
        async with self.db.session() as s:
            count = (await s.execute(select(func.count()).select_from(ButtonMenu)
                                     .where(ButtonMenu.guild_id == guild_id))).scalar_one()
            if count >= MAX_MENUS_PER_GUILD:
                raise MenuLimit(f"На сервере уже {count} меню (максимум {MAX_MENUS_PER_GUILD})")
            m = ButtonMenu(guild_id=guild_id, **self._fields(data))
            s.add(m)
            await s.commit()
            return m

    async def update(self, guild_id: int, menu_id: int, data: MenuConfig) -> ButtonMenu | None:
        async with self.db.session() as s:
            m = await s.get(ButtonMenu, menu_id)
            if m is None or m.guild_id != guild_id:
                return None
            for k, v in self._fields(data).items():
                setattr(m, k, v)
            await s.commit()
            return m

    async def delete(self, guild_id: int, menu_id: int) -> ButtonMenu | None:
        async with self.db.session() as s:
            m = await s.get(ButtonMenu, menu_id)
            if m is None or m.guild_id != guild_id:
                return None
            await s.delete(m)
            await s.commit()
        self._remove_image_file(m.image_file)
        return m

    async def set_published(self, menu_id: int, channel_id: int | None, message_id: int | None) -> None:
        async with self.db.session() as s:
            await s.execute(update(ButtonMenu).where(ButtonMenu.id == menu_id).values(
                published_channel_id=channel_id if message_id else None, message_id=message_id,
                published_at=utcnow() if message_id else None))
            await s.commit()

    # ------------------------------------------------------------ изображение

    def image_path(self, menu: ButtonMenu) -> Path | None:
        if not menu.image_file:
            return None
        path = self.image_dir / menu.image_file
        return path if path.is_file() else None

    async def set_image(self, guild_id: int, menu_id: int, data: bytes) -> ButtonMenu | None:
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"Изображение больше {MAX_IMAGE_BYTES // 1024 // 1024} МБ")
        ext = detect_image(data)
        if ext is None:
            raise ValueError("Поддерживаются только изображения PNG, JPEG, GIF и WebP")
        async with self.db.session() as s:
            m = await s.get(ButtonMenu, menu_id)
            if m is None or m.guild_id != guild_id:
                return None
            old = m.image_file
            name = f"menu-{menu_id}-{utcnow():%Y%m%d%H%M%S}.{ext}"
            self.image_dir.mkdir(parents=True, exist_ok=True)
            (self.image_dir / name).write_bytes(data)
            m.image_file = name
            await s.commit()
        if old and old != name:
            self._remove_image_file(old)
        return m

    async def clear_image(self, guild_id: int, menu_id: int) -> ButtonMenu | None:
        async with self.db.session() as s:
            m = await s.get(ButtonMenu, menu_id)
            if m is None or m.guild_id != guild_id:
                return None
            old, m.image_file = m.image_file, None
            await s.commit()
        self._remove_image_file(old)
        return m

    def _remove_image_file(self, name: str | None) -> None:
        if name:
            (self.image_dir / name).unlink(missing_ok=True)

    @staticmethod
    def _fields(data: MenuConfig) -> dict:
        d = data.model_dump(mode="json")
        return {"name": d["name"], "channel_id": int(d["channel_id"]) if d["channel_id"] else None,
                "content": d["content"], "embed": d["embed"], "buttons": d["buttons"],
                "role_select": d["role_select"] if d["role_select"]["enabled"] else None}
