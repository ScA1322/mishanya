"""SQLAlchemy-модели. Все даты хранятся в UTC без tzinfo."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


def guild_fk() -> ForeignKey:
    return ForeignKey("guilds.id", ondelete="CASCADE")


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | str | int | bool | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Guild(Base):
    __tablename__ = "guilds"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(100), default="")
    icon_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    member_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    present: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    left_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class GuildConfig(Base):
    """Настройки модуля сервера в виде JSON, проверяемого Pydantic-схемой."""

    __tablename__ = "guild_configs"
    guild_id: Mapped[int] = mapped_column(BigInteger, guild_fk(), primary_key=True)
    module: Mapped[str] = mapped_column(String(32), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class MemberLevel(Base):
    __tablename__ = "member_levels"
    guild_id: Mapped[int] = mapped_column(BigInteger, guild_fk(), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str] = mapped_column(String(100), default="")
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    xp: Mapped[int] = mapped_column(BigInteger, default=0)
    level: Mapped[int] = mapped_column(Integer, default=0)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    last_xp_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    left_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_member_levels_guild_xp", "guild_id", "xp"),)


class LevelReward(Base):
    __tablename__ = "level_rewards"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, guild_fk(), index=True)
    level: Mapped[int] = mapped_column(Integer)
    role_id: Mapped[int] = mapped_column(BigInteger)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("guild_id", "level", "role_id", name="uq_reward"),)


class Warning(Base):
    __tablename__ = "warnings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, guild_fk())
    user_id: Mapped[int] = mapped_column(BigInteger)
    moderator_id: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(512), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    removed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (Index("ix_warnings_guild_user", "guild_id", "user_id"),)


class ModCase(Base):
    __tablename__ = "mod_cases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, guild_fk())
    case_number: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    target_name: Mapped[str] = mapped_column(String(100), default="")
    moderator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    moderator_name: Mapped[str] = mapped_column(String(100), default="")
    reason: Mapped[str] = mapped_column(String(512), default="")
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("guild_id", "case_number", name="uq_case_number"),
        Index("ix_mod_cases_guild_created", "guild_id", "created_at"),
    )


class AutomodViolation(Base):
    __tablename__ = "automod_violations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, guild_fk())
    user_id: Mapped[int] = mapped_column(BigInteger)
    rule: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_automod_viol_guild_user", "guild_id", "user_id", "created_at"),)


class CustomCommand(Base):
    __tablename__ = "custom_commands"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, guild_fk())
    name: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(String(100), default="")
    response: Mapped[str] = mapped_column(Text, default="")
    embed: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    allowed_roles: Mapped[list] = mapped_column(JSON, default=list)
    allowed_channels: Mapped[list] = mapped_column(JSON, default=list)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=0)
    delete_trigger: Mapped[bool] = mapped_column(Boolean, default=False)
    send_dm: Mapped[bool] = mapped_column(Boolean, default=False)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("guild_id", "name", name="uq_custom_command_name"),)


class Reminder(Base):
    __tablename__ = "reminders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int | None] = mapped_column(BigInteger, guild_fk(), nullable=True)
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(String(1000))
    due_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|sent|failed|cancelled
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(String(256), nullable=True)

    __table_args__ = (Index("ix_reminders_status_due", "status", "due_at"),)


class EventLog(Base):
    __tablename__ = "event_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, guild_fk())
    event_type: Mapped[str] = mapped_column(String(32))
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    summary: Mapped[str] = mapped_column(String(512), default="")
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_event_logs_guild_created", "guild_id", "created_at"),
        Index("ix_event_logs_guild_type", "guild_id", "event_type"),
    )


class PanelSession(Base):
    __tablename__ = "panel_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
