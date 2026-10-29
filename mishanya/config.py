"""Загрузка конфигурации приложения из .env и переменных окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ConfigError(Exception):
    """Ошибка конфигурации, которую нужно показать пользователю."""


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}


def _int(name: str, value: str | None, default: int) -> int:
    if value is None or value.strip() == "":
        return default
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ConfigError(f"Переменная {name} должна быть целым числом, сейчас: {value!r}") from exc


@dataclass
class Settings:
    discord_token: str = ""
    owner_id: int | None = None
    panel_password: str = ""
    panel_host: str = "127.0.0.1"
    panel_port: int = 8777
    allow_remote_panel: bool = False
    open_browser: bool = True
    data_dir: Path = field(default_factory=lambda: BASE_DIR / "data")
    backup_dir: Path = field(default_factory=lambda: BASE_DIR / "backups")
    frontend_dist: Path = field(default_factory=lambda: BASE_DIR / "frontend" / "dist")
    log_level: str = "INFO"
    session_hours: int = 72

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mishanya.db"

    @property
    def token_configured(self) -> bool:
        return bool(self.discord_token) and "PASTE" not in self.discord_token.upper()

    @property
    def panel_url(self) -> str:
        host = "127.0.0.1" if self.panel_host in {"0.0.0.0", "::"} else self.panel_host
        return f"http://{host}:{self.panel_port}"

    def validate(self) -> list[str]:
        """Возвращает список критичных проблем конфигурации."""
        problems: list[str] = []
        if len(self.panel_password) < 8:
            problems.append(
                "PANEL_PASSWORD не задан или короче 8 символов. Откройте файл .env и задайте пароль панели."
            )
        if self.panel_host not in LOCAL_HOSTS and not self.allow_remote_panel:
            problems.append(
                f"PANEL_HOST={self.panel_host} открывает панель за пределы компьютера. "
                "Если это осознанное решение, установите ALLOW_REMOTE_PANEL=true."
            )
        if not (1 <= self.panel_port <= 65535):
            problems.append("PANEL_PORT должен быть в диапазоне 1–65535.")
        return problems


def load_settings(env_file: Path | None = None) -> Settings:
    env_path = env_file or BASE_DIR / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False, encoding="utf-8")

    owner_raw = os.getenv("OWNER_ID", "").strip()
    owner_id: int | None = None
    if owner_raw:
        if not owner_raw.isdigit():
            raise ConfigError("OWNER_ID должен содержать только цифры (ваш Discord ID).")
        owner_id = int(owner_raw)

    data_dir = Path(os.getenv("DATA_DIR", "") or BASE_DIR / "data")
    backup_dir = Path(os.getenv("BACKUP_DIR", "") or BASE_DIR / "backups")

    return Settings(
        discord_token=os.getenv("DISCORD_TOKEN", "").strip(),
        owner_id=owner_id,
        panel_password=os.getenv("PANEL_PASSWORD", "").strip(),
        panel_host=os.getenv("PANEL_HOST", "127.0.0.1").strip() or "127.0.0.1",
        panel_port=_int("PANEL_PORT", os.getenv("PANEL_PORT"), 8777),
        allow_remote_panel=_bool(os.getenv("ALLOW_REMOTE_PANEL"), False),
        open_browser=_bool(os.getenv("OPEN_BROWSER"), True),
        data_dir=data_dir,
        backup_dir=backup_dir,
        log_level=(os.getenv("LOG_LEVEL", "INFO").strip() or "INFO").upper(),
        session_hours=_int("SESSION_HOURS", os.getenv("SESSION_HOURS"), 72),
    )
