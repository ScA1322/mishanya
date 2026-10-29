import sqlite3

import pytest

from mishanya.db import Database, DatabaseError
from mishanya.db.backup import BackupError, create_backup, list_backups, prune_backups, resolve_backup, restore_backup
from mishanya.db.database import LATEST_VERSION, check_integrity
from mishanya.core import App
from mishanya.schemas import LevelingConfig

from .conftest import GUILD_A

pytestmark = pytest.mark.anyio


async def test_database_created_with_schema(settings):
    db = Database(settings.db_path)
    await db.init()
    assert settings.db_path.exists()
    assert await db.schema_version() == LATEST_VERSION
    tables = {r[0] for r in sqlite3.connect(settings.db_path).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"guilds", "guild_configs", "member_levels", "level_rewards", "warnings", "mod_cases", "custom_commands",
            "reminders", "event_logs", "automod_violations", "panel_sessions", "app_settings"} <= tables
    await db.dispose()


async def test_migrations_are_idempotent(settings):
    db = Database(settings.db_path)
    await db.init()
    await db.dispose()
    await db.init()  # повторный запуск не должен ничего ломать
    assert await db.schema_version() == LATEST_VERSION
    await db.dispose()


async def test_data_survives_restart(settings):
    db = Database(settings.db_path)
    await db.init()
    app = App(settings, db)
    await app.guilds.upsert(GUILD_A, "A")
    await app.config.set(GUILD_A, "leveling", LevelingConfig(xp_min=1, xp_max=2))
    await db.dispose()

    db2 = Database(settings.db_path)
    await db2.init()
    app2 = App(settings, db2)
    cfg = await app2.config.get(GUILD_A, "leveling")
    assert (cfg.xp_min, cfg.xp_max) == (1, 2)
    await db2.dispose()


async def test_corrupted_database_is_detected(settings):
    settings.db_path.parent.mkdir(parents=True)
    settings.db_path.write_bytes(b"this is not a sqlite database" * 100)
    with pytest.raises(DatabaseError, match="повреждён"):
        await Database(settings.db_path).init()


async def test_backup_and_restore(app, settings):
    await app.config.set(GUILD_A, "leveling", LevelingConfig(xp_min=7, xp_max=9))
    info = create_backup(settings.db_path, settings.backup_dir, "manual")
    assert check_integrity(settings.backup_dir / info.name) == "ok"

    await app.config.set(GUILD_A, "leveling", LevelingConfig(xp_min=100, xp_max=200))
    await app.db.dispose()
    safety = restore_backup(settings.db_path, settings.backup_dir, info.name)
    assert safety.name.endswith("before_restore.db")
    await app.db.init()
    app.config.clear_cache()
    cfg = await app.config.get(GUILD_A, "leveling")
    assert cfg.xp_min == 7
    assert {b.name for b in list_backups(settings.backup_dir)} >= {info.name, safety.name}


async def test_backup_name_validation_blocks_path_traversal(settings):
    settings.backup_dir.mkdir(parents=True)
    for bad in ["../mishanya.db", "..\\x.db", "mishanya-20200101-000000.db/../../x", "evil.db"]:
        with pytest.raises(BackupError):
            resolve_backup(settings.backup_dir, bad)


async def test_prune_keeps_latest_auto_backups(app, settings):
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (settings.backup_dir / f"mishanya-2026010{i}-000000-auto.db").write_bytes(b"x")
    (settings.backup_dir / "mishanya-20200101-000000-manual.db").write_bytes(b"x")
    assert prune_backups(settings.backup_dir, keep=2) == 3
    names = [b.name for b in list_backups(settings.backup_dir)]
    assert "mishanya-20200101-000000-manual.db" in names  # ручные копии не удаляются
    assert len([n for n in names if n.endswith("-auto.db")]) == 2


async def test_delete_guild_cascades(app):
    from mishanya.schemas import LevelingConfig as LC

    await app.leveling.set_xp(GUILD_A, 1, 500, LC())
    await app.moderation.add_warning(GUILD_A, 1, 2, "test")
    await app.eventlog.add(GUILD_A, "member_join", "x")
    assert await app.guilds.delete_data(GUILD_A)
    rows, total = await app.moderation.list_warnings(GUILD_A)
    assert total == 0
    assert await app.leveling.get_member(GUILD_A, 1) is None


async def test_restore_over_corrupted_database(settings):
    db = Database(settings.db_path)
    await db.init()
    app = App(settings, db)
    await app.guilds.upsert(GUILD_A, "A")
    info = create_backup(settings.db_path, settings.backup_dir, "manual")
    await db.dispose()
    settings.db_path.write_bytes(b"garbage" * 1000)
    restore_backup(settings.db_path, settings.backup_dir, info.name)
    assert check_integrity(settings.db_path) == "ok"
    assert list(settings.db_path.parent.glob("mishanya.db.corrupted-*"))
    db2 = Database(settings.db_path)
    await db2.init()
    assert await App(settings, db2).guilds.get(GUILD_A) is not None
    await db2.dispose()
