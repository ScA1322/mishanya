"""Подключение к SQLite, миграции схемы и проверка целостности."""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .models import Base, BotGrudge, BotMemory, BotRelation, DmContact, ButtonMenu, Post, TempVoiceChannel, Ticket, VoiceSeen

log = logging.getLogger(__name__)


class DatabaseError(Exception):
    """База данных недоступна или повреждена."""


# Миграции: (версия, функция). Версия 1 создаёт всю схему.
# Новые изменения добавляются в конец списка новыми номерами — существующие данные не удаляются.
def _m1_initial(conn: Connection) -> None:
    Base.metadata.create_all(conn)


def _m2_tickets_menus(conn: Connection) -> None:
    Base.metadata.create_all(conn, tables=[Ticket.__table__, ButtonMenu.__table__])


def _m3_temp_voice(conn: Connection) -> None:
    Base.metadata.create_all(conn, tables=[TempVoiceChannel.__table__])


def _m4_posts(conn: Connection) -> None:
    Base.metadata.create_all(conn, tables=[Post.__table__])


def _m5_mind(conn: Connection) -> None:
    Base.metadata.create_all(conn, tables=[BotMemory.__table__, BotGrudge.__table__, VoiceSeen.__table__])
    # Поля новых настроек появляются сами (значения по умолчанию), менять таблицы конфигурации не нужно.


def _m6_relations(conn: Connection) -> None:
    Base.metadata.create_all(conn, tables=[BotRelation.__table__])


def _m7_memory_index(conn: Connection) -> None:
    """Память на сотни тысяч фактов: хеш текста для проверки повторов и индексы по людям."""
    import hashlib

    from ..services.mind import normalize

    cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(bot_memory)")}
    if "norm" not in cols:
        conn.exec_driver_sql("ALTER TABLE bot_memory ADD COLUMN norm VARCHAR(40) NOT NULL DEFAULT ''")
    rows = conn.exec_driver_sql("SELECT id, text FROM bot_memory WHERE norm = ''").fetchall()
    for mid, text in rows:
        conn.exec_driver_sql("UPDATE bot_memory SET norm = ? WHERE id = ?",
                             (hashlib.sha1(normalize(text).encode()).hexdigest(), mid))
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_bot_memory_guild_norm ON bot_memory (guild_id, norm)")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_bot_memory_guild_user_id ON bot_memory (guild_id, user_id, id)")


def _m8_sentences_dm(conn: Connection) -> None:
    cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(bot_grudges)")}
    if "until" not in cols:
        conn.exec_driver_sql("ALTER TABLE bot_grudges ADD COLUMN until DATETIME")
    Base.metadata.create_all(conn, tables=[DmContact.__table__])


def _m9_warnings(conn: Connection) -> None:
    cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(bot_relations)")}
    if "warnings" not in cols:
        conn.exec_driver_sql("ALTER TABLE bot_relations ADD COLUMN warnings INTEGER NOT NULL DEFAULT 0")
    if "warned_at" not in cols:
        conn.exec_driver_sql("ALTER TABLE bot_relations ADD COLUMN warned_at DATETIME")


MIGRATIONS: list[tuple[int, Callable[[Connection], None]]] = [
    (1, _m1_initial),
    (2, _m2_tickets_menus),
    (3, _m3_temp_voice),
    (4, _m4_posts),
    (5, _m5_mind),
    (6, _m6_relations),
    (7, _m7_memory_index),
    (8, _m8_sentences_dm),
    (9, _m9_warnings),
]
LATEST_VERSION = MIGRATIONS[-1][0]


def check_integrity(path: Path) -> str:
    """Возвращает 'ok' или текст ошибки целостности SQLite."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        return str(exc)
    return str(row[0]) if row else "unknown"


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.engine: AsyncEngine | None = None
        self.sessionmaker: async_sessionmaker[AsyncSession] | None = None

    def _create_engine(self) -> None:
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.path.as_posix()}",
            connect_args={"timeout": 30},
        )

        @event.listens_for(self.engine.sync_engine, "connect")
        def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size > 0:
            status = check_integrity(self.path)
            if status != "ok":
                raise DatabaseError(
                    f"Файл базы {self.path} повреждён ({status}). "
                    "Восстановите его из резервной копии: restore.bat (или scripts\\restore.py)."
                )
        self._create_engine()
        try:
            await self.migrate()
        except Exception as exc:  # noqa: BLE001
            await self.dispose()
            raise DatabaseError(f"Не удалось открыть или обновить базу {self.path}: {exc}") from exc

    async def migrate(self) -> int:
        assert self.engine is not None
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS schema_version ("
                    "id INTEGER PRIMARY KEY, version INTEGER NOT NULL, applied_at DATETIME)"
                )
            )
            current = (await conn.execute(text("SELECT MAX(version) FROM schema_version"))).scalar() or 0
            if current > LATEST_VERSION:
                raise DatabaseError(
                    f"Версия схемы базы ({current}) новее, чем поддерживает приложение ({LATEST_VERSION})."
                )
            for version, func in MIGRATIONS:
                if version > current:
                    log.info("Применяю миграцию схемы v%s", version)
                    await conn.run_sync(func)
                    await conn.execute(
                        text("INSERT INTO schema_version (version, applied_at) VALUES (:v, CURRENT_TIMESTAMP)"),
                        {"v": version},
                    )
            return max(current, LATEST_VERSION)

    async def schema_version(self) -> int:
        assert self.engine is not None
        async with self.engine.connect() as conn:
            return (await conn.execute(text("SELECT MAX(version) FROM schema_version"))).scalar() or 0

    def session(self) -> AsyncSession:
        if self.sessionmaker is None:
            raise DatabaseError("База данных не инициализирована")
        return self.sessionmaker()

    async def dispose(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()
            self.engine = None
            self.sessionmaker = None
