"""Контейнер сервисов приложения и настройка журналирования."""
from __future__ import annotations

import logging
import logging.handlers
import re
from collections import deque
from dataclasses import dataclass, field
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .config import Settings
from .db import Database
from .services.automod import AutomodService
from .services.config_service import ConfigService
from .services.custom_commands import CustomCommandService
from .services.eventlog import EventLogService
from .services.guilds import GuildService
from .services.leveling import LevelingService
from .services.menus import MenuService
from .services.moderation import ModerationService
from .services.reminders import ReminderService
from .services.tickets import TicketService
from .services.updater import UpdateService

if TYPE_CHECKING:
    from .bot.client import MishanyaBot

TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{23,28}\.[A-Za-z0-9_\-]{6,7}\.[A-Za-z0-9_\-]{27,}")


class RedactingFilter(logging.Filter):
    """Маскирует строки, похожие на токен Discord, и заданные секреты."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 6]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        redacted = TOKEN_RE.sub("[скрыто]", msg)
        for s in self.secrets:
            redacted = redacted.replace(s, "[скрыто]")
        if redacted != msg:
            record.msg, record.args = redacted, None
        return True


@dataclass
class ErrorEntry:
    time: datetime
    level: str
    source: str
    message: str


class ErrorBuffer(logging.Handler):
    """Хранит последние предупреждения и ошибки для главной страницы панели."""

    def __init__(self, size: int = 100):
        super().__init__(level=logging.WARNING)
        self.items: deque[ErrorEntry] = deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                text += f" ({type(record.exc_info[1]).__name__}: {record.exc_info[1]})"
            self.items.append(ErrorEntry(datetime.fromtimestamp(record.created, timezone.utc),
                                         record.levelname, record.name, text[:500]))
        except Exception:  # noqa: BLE001
            self.handleError(record)


def setup_logging(settings: Settings) -> ErrorBuffer:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(settings.log_level if settings.log_level in logging._nameToLevel else "INFO")
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    redact = RedactingFilter([settings.discord_token, settings.panel_password])
    console = logging.StreamHandler()
    file = logging.handlers.RotatingFileHandler(settings.data_dir / "mishanya.log", maxBytes=2_000_000,
                                                backupCount=3, encoding="utf-8")
    errors = ErrorBuffer()
    for h in (console, file, errors):
        h.addFilter(redact)
        h.setFormatter(fmt)
        root.addHandler(h)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    return errors


@dataclass
class App:
    settings: Settings
    db: Database
    errors: ErrorBuffer = field(default_factory=ErrorBuffer)
    bot: "MishanyaBot | None" = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    bot_error: str | None = None
    # Устанавливается main.run: корректно завершает приложение с кодом перезапуска для start.bat.
    request_restart: Callable[[], None] | None = None

    def __post_init__(self) -> None:
        self.config = ConfigService(self.db)
        self.guilds = GuildService(self.db)
        self.leveling = LevelingService(self.db)
        self.moderation = ModerationService(self.db)
        self.automod = AutomodService(self.db)
        self.eventlog = EventLogService(self.db)
        self.reminders = ReminderService(self.db)
        self.commands = CustomCommandService(self.db)
        self.tickets = TicketService(self.db)
        self.menus = MenuService(self.db, self.settings.data_dir / "menus")
        self.updater = UpdateService(self.settings.base_dir, self.settings.db_path, self.settings.backup_dir)
