"""Пользовательские команды — безопасные шаблоны ответа без выполнения кода."""
from __future__ import annotations

import time

from pydantic import Field, field_validator, model_validator
from sqlalchemy import func, select, update

from ..db import Database
from ..db.models import CustomCommand
from ..schemas import EmbedConfig, Snowflake, Strict

# Имена встроенных slash-команд и групп — пользовательская команда не должна их перекрывать.
BUILTIN_NAMES = {
    "help", "ping", "avatar", "userinfo", "serverinfo", "poll", "remind", "reminders", "unremind",
    "rank", "leaderboard", "level", "profile", "invite", "kick", "ban", "unban", "timeout",
    "untimeout", "warn", "warnings", "unwarn", "clearwarnings", "clear", "purge", "slowmode", "lock",
    "unlock", "nickname", "role", "xp", "rewards", "cmd", "case", "cases", "ticket",
    "play", "skip", "stop", "pause", "resume", "queue", "nowplaying", "volume", "leave", "shuffle", "loop", "voice",
}


class CustomCommandIn(Strict):
    name: str = Field(pattern=r"^[a-z0-9_\-а-яё]{1,32}$")
    description: str = Field("", max_length=100)
    response: str = Field("", max_length=2000)
    embed: EmbedConfig | None = None
    enabled: bool = True
    allowed_roles: list[Snowflake] = Field(default_factory=list, max_length=50)
    allowed_channels: list[Snowflake] = Field(default_factory=list, max_length=100)
    cooldown_seconds: int = Field(0, ge=0, le=86400)
    delete_trigger: bool = False
    send_dm: bool = False

    @field_validator("name", mode="before")
    @classmethod
    def _lower(cls, v: object) -> object:
        return v.strip().lower() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _check(self) -> "CustomCommandIn":
        if self.name in BUILTIN_NAMES:
            raise ValueError(f"Имя «{self.name}» занято встроенной командой")
        if not self.response.strip() and not (self.embed and self.embed.enabled):
            raise ValueError("Укажите текст ответа или включите embed")
        return self


class CommandConflict(ValueError):
    pass


class CustomCommandService:
    def __init__(self, db: Database):
        self.db = db
        self._cooldowns: dict[tuple[int, int], float] = {}

    async def list(self, guild_id: int) -> list[CustomCommand]:
        async with self.db.session() as s:
            return list((await s.execute(select(CustomCommand).where(CustomCommand.guild_id == guild_id)
                                         .order_by(CustomCommand.name))).scalars())

    async def get_by_name(self, guild_id: int, name: str) -> CustomCommand | None:
        async with self.db.session() as s:
            return (await s.execute(select(CustomCommand).where(
                CustomCommand.guild_id == guild_id, CustomCommand.name == name.lower()))).scalar_one_or_none()

    async def _ensure_unique(self, s, guild_id: int, name: str, exclude_id: int | None = None) -> None:  # noqa: ANN001
        q = select(CustomCommand.id).where(CustomCommand.guild_id == guild_id, CustomCommand.name == name)
        if exclude_id is not None:
            q = q.where(CustomCommand.id != exclude_id)
        if (await s.execute(q)).first() is not None:
            raise CommandConflict(f"Команда «{name}» уже существует")

    async def create(self, guild_id: int, data: CustomCommandIn) -> CustomCommand:
        async with self.db.session() as s:
            await self._ensure_unique(s, guild_id, data.name)
            cmd = CustomCommand(guild_id=guild_id, **self._fields(data))
            s.add(cmd)
            await s.commit()
            return cmd

    async def update(self, guild_id: int, command_id: int, data: CustomCommandIn) -> CustomCommand | None:
        async with self.db.session() as s:
            cmd = await s.get(CustomCommand, command_id)
            if cmd is None or cmd.guild_id != guild_id:
                return None
            await self._ensure_unique(s, guild_id, data.name, exclude_id=command_id)
            for k, v in self._fields(data).items():
                setattr(cmd, k, v)
            await s.commit()
            return cmd

    async def delete(self, guild_id: int, command_id: int) -> bool:
        async with self.db.session() as s:
            cmd = await s.get(CustomCommand, command_id)
            if cmd is None or cmd.guild_id != guild_id:
                return False
            await s.delete(cmd)
            await s.commit()
            return True

    async def increment_uses(self, command_id: int) -> None:
        async with self.db.session() as s:
            await s.execute(update(CustomCommand).where(CustomCommand.id == command_id)
                            .values(uses=CustomCommand.uses + 1))
            await s.commit()

    async def count(self, guild_id: int) -> int:
        async with self.db.session() as s:
            return (await s.execute(select(func.count()).select_from(CustomCommand)
                                    .where(CustomCommand.guild_id == guild_id))).scalar_one()

    @staticmethod
    def _fields(data: CustomCommandIn) -> dict:
        d = data.model_dump(mode="json")
        return d

    # ------------------------------------------------------------ проверки вызова

    def check_access(self, cmd: CustomCommand, channel_id: int, role_ids: list[int]) -> str | None:
        if not cmd.enabled:
            return "Команда отключена."
        if cmd.allowed_channels and str(channel_id) not in cmd.allowed_channels:
            return "Эта команда недоступна в этом канале."
        if cmd.allowed_roles and not any(str(r) in cmd.allowed_roles for r in role_ids):
            return "У вас нет роли, необходимой для этой команды."
        return None

    def check_cooldown(self, cmd: CustomCommand, user_id: int, now: float | None = None) -> float:
        """Возвращает оставшееся время задержки (0 — можно вызывать) и фиксирует вызов."""
        if cmd.cooldown_seconds <= 0:
            return 0.0
        now = time.monotonic() if now is None else now
        key = (cmd.id, user_id)
        last = self._cooldowns.get(key)
        if last is not None and now - last < cmd.cooldown_seconds:
            return cmd.cooldown_seconds - (now - last)
        self._cooldowns[key] = now
        return 0.0
