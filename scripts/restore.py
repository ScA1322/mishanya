"""Восстановление базы из резервной копии без запуска приложения (например, если база повреждена).

Использование: restore.bat  или  .venv\\Scripts\\python scripts\\restore.py [имя_копии]
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from mishanya.config import ConfigError, load_settings  # noqa: E402
from mishanya.db.backup import BackupError, list_backups, restore_backup  # noqa: E402


def app_running(pid_file: Path) -> bool:
    if not pid_file.exists():
        return False
    pid = pid_file.read_text(encoding="ascii").strip()
    if not pid.isdigit():
        return False
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return pid in out


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}")
        return 2
    if app_running(settings.data_dir / "mishanya.pid"):
        print("Mishanya сейчас запущена. Остановите её (stop.bat) или восстановите копию через веб-панель.")
        return 1
    backups = list_backups(settings.backup_dir)
    if not backups:
        print(f"Резервные копии не найдены в {settings.backup_dir}")
        return 1
    if len(sys.argv) > 1:
        name = sys.argv[1]
    else:
        print("Доступные резервные копии (новые сверху):")
        for i, b in enumerate(backups, 1):
            print(f"  {i:>2}. {b.name}  ({b.size // 1024} КБ, {b.created_at:%d.%m.%Y %H:%M})")
        choice = input("\nНомер копии для восстановления (Enter — отмена): ").strip()
        if not choice.isdigit() or not 1 <= int(choice) <= len(backups):
            print("Отменено.")
            return 0
        name = backups[int(choice) - 1].name
        if input(f"Заменить текущую базу копией {name}? Введите ДА: ").strip().upper() != "ДА":
            print("Отменено.")
            return 0
    try:
        safety = restore_backup(settings.db_path, settings.backup_dir, name)
    except BackupError as exc:
        print(f"Ошибка: {exc}")
        return 1
    print(f"Готово. База восстановлена из {name}.")
    if safety.name:
        print(f"Предыдущая база сохранена как {safety.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
