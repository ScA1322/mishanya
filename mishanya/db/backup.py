"""Резервное копирование и восстановление SQLite через встроенный backup API."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .database import check_integrity

BACKUP_RE = re.compile(r"^mishanya-\d{8}-\d{6}(?:-[a-z0-9_]{1,24})?\.db$")


class BackupError(Exception):
    pass


@dataclass
class BackupInfo:
    name: str
    size: int
    created_at: datetime


def _copy(src_path: Path, dst_path: Path) -> None:
    src = sqlite3.connect(src_path, timeout=30)
    dst = sqlite3.connect(dst_path, timeout=30)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def create_backup(db_path: Path, backup_dir: Path, label: str = "") -> BackupInfo:
    """Создаёт согласованную копию базы (безопасно при работающем приложении)."""
    if not db_path.exists():
        raise BackupError("Файл базы данных не найден")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"-{label}" if label else ""
    target = backup_dir / f"mishanya-{stamp}{suffix}.db"
    n = 1
    while target.exists():
        n += 1
        target = backup_dir / f"mishanya-{stamp}-{label or 'copy'}{n}.db"
    _copy(db_path, target)
    return BackupInfo(target.name, target.stat().st_size, datetime.now())


def list_backups(backup_dir: Path) -> list[BackupInfo]:
    if not backup_dir.exists():
        return []
    items = [
        BackupInfo(p.name, p.stat().st_size, datetime.fromtimestamp(p.stat().st_mtime))
        for p in backup_dir.iterdir()
        if p.is_file() and BACKUP_RE.match(p.name)
    ]
    return sorted(items, key=lambda b: b.name, reverse=True)


def resolve_backup(backup_dir: Path, name: str) -> Path:
    """Проверяет имя файла (защита от обхода путей) и возвращает путь."""
    if not BACKUP_RE.match(name):
        raise BackupError("Недопустимое имя резервной копии")
    path = backup_dir / name
    if not path.is_file():
        raise BackupError("Резервная копия не найдена")
    return path


def restore_backup(db_path: Path, backup_dir: Path, name: str) -> BackupInfo:
    """Восстанавливает базу из копии. Перед этим сохраняет текущую базу как страховочную копию.

    Вызывающий код должен закрыть все соединения с базой до вызова.
    """
    source = resolve_backup(backup_dir, name)
    status = check_integrity(source)
    if status != "ok":
        raise BackupError(f"Резервная копия повреждена: {status}")
    safety: BackupInfo | None = None
    if db_path.exists():
        if check_integrity(db_path) == "ok":
            safety = create_backup(db_path, backup_dir, "before_restore")
        else:
            # Повреждённый файл не перезаписываем поверх, а откладываем в сторону для анализа.
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            db_path.replace(db_path.with_name(f"{db_path.name}.corrupted-{stamp}"))
            for suffix in ("-wal", "-shm"):
                Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _copy(source, db_path)
    return safety or BackupInfo("", 0, datetime.now())


def prune_backups(backup_dir: Path, keep: int) -> int:
    """Удаляет старые автоматические копии, оставляя `keep` последних."""
    autos = [b for b in list_backups(backup_dir) if b.name.endswith("-auto.db")]
    removed = 0
    for b in autos[keep:]:
        (backup_dir / b.name).unlink(missing_ok=True)
        removed += 1
    return removed
