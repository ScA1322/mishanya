"""Хранение настроек модулей серверов и глобальных настроек с кэшем в памяти."""
from __future__ import annotations

import logging
from typing import TypeVar

from pydantic import ValidationError
from sqlalchemy import select

from ..db import Database
from ..db.models import AppSetting, GuildConfig
from ..schemas import MODULES, GlobalSettings, Strict

log = logging.getLogger(__name__)
T = TypeVar("T", bound=Strict)


class ConfigService:
    def __init__(self, db: Database):
        self.db = db
        self._cache: dict[tuple[int, str], Strict] = {}
        self._global: GlobalSettings | None = None

    def clear_cache(self) -> None:
        self._cache.clear()
        self._global = None

    async def get(self, guild_id: int, module: str) -> Strict:
        key = (guild_id, module)
        if key in self._cache:
            return self._cache[key]
        model_cls = MODULES[module]
        async with self.db.session() as s:
            row = await s.get(GuildConfig, (guild_id, module))
        cfg: Strict
        if row is None:
            cfg = model_cls()
        else:
            try:
                cfg = model_cls.model_validate(row.data)
            except ValidationError as exc:
                # Повреждённые/устаревшие настройки не должны ронять бота: используем значения по умолчанию.
                log.error("Настройки %s сервера %s некорректны, использую значения по умолчанию: %s",
                          module, guild_id, exc.errors()[:3])
                cfg = model_cls()
        self._cache[key] = cfg
        return cfg

    async def get_typed(self, guild_id: int, module: str, cls: type[T]) -> T:
        cfg = await self.get(guild_id, module)
        assert isinstance(cfg, cls)
        return cfg

    async def set(self, guild_id: int, module: str, data: dict | Strict) -> Strict:
        """Проверяет и сохраняет настройки. Сервер должен существовать в таблице guilds."""
        model_cls = MODULES[module]
        cfg = data if isinstance(data, model_cls) else model_cls.model_validate(data)
        payload = cfg.model_dump(mode="json")
        async with self.db.session() as s:
            row = await s.get(GuildConfig, (guild_id, module))
            if row is None:
                s.add(GuildConfig(guild_id=guild_id, module=module, data=payload))
            else:
                row.data = payload
            await s.commit()
        self._cache[(guild_id, module)] = cfg
        return cfg

    async def all_for_guild(self, guild_id: int) -> dict[str, Strict]:
        return {m: await self.get(guild_id, m) for m in MODULES}

    def invalidate_guild(self, guild_id: int) -> None:
        for key in [k for k in self._cache if k[0] == guild_id]:
            del self._cache[key]

    # ------------------------------------------------------------ глобальные

    async def get_global(self) -> GlobalSettings:
        if self._global is not None:
            return self._global
        async with self.db.session() as s:
            row = await s.get(AppSetting, "global")
        try:
            self._global = GlobalSettings.model_validate(row.value) if row and row.value else GlobalSettings()
        except ValidationError:
            log.error("Глобальные настройки повреждены, использую значения по умолчанию")
            self._global = GlobalSettings()
        return self._global

    async def set_global(self, data: dict | GlobalSettings) -> GlobalSettings:
        cfg = data if isinstance(data, GlobalSettings) else GlobalSettings.model_validate(data)
        await self.set_value("global", cfg.model_dump(mode="json"))
        self._global = cfg
        return cfg

    async def get_value(self, key: str, default=None):  # noqa: ANN001, ANN201
        async with self.db.session() as s:
            row = await s.get(AppSetting, key)
        return row.value if row is not None else default

    async def set_value(self, key: str, value) -> None:  # noqa: ANN001
        async with self.db.session() as s:
            row = (await s.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
            if row is None:
                s.add(AppSetting(key=key, value=value))
            else:
                row.value = value
            await s.commit()
