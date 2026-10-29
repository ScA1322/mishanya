"""Первоначальная настройка Mishanya. Запускается из setup.bat внутри виртуального окружения."""
from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

FRONTEND = BASE / "frontend"
ENV = BASE / ".env"
ENV_EXAMPLE = BASE / ".env.example"


def step(text: str) -> None:
    print(f"\n=== {text}")


def build_frontend() -> bool:
    step("Веб-панель (frontend)")
    dist_ok = (FRONTEND / "dist" / "index.html").exists()
    npm = shutil.which("npm")
    if npm is None:
        if dist_ok:
            print("Node.js не найден — используется уже собранная панель из frontend\\dist. Это нормально.")
            return True
        print("ОШИБКА: нет собранной панели (frontend\\dist) и не найден Node.js.")
        print("Установите Node.js 22 LTS: https://nodejs.org/ и снова запустите setup.bat.")
        return False
    print("Найден Node.js — собираю панель (npm ci && npm run build)…")
    try:
        cmd = "ci" if (FRONTEND / "package-lock.json").exists() else "install"
        subprocess.run([npm, cmd, "--no-fund", "--no-audit"], cwd=FRONTEND, check=True)
        subprocess.run([npm, "run", "build"], cwd=FRONTEND, check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        if dist_ok:
            print(f"Предупреждение: сборка не удалась ({exc}). Используется ранее собранная панель.")
            return True
        print(f"ОШИБКА сборки панели: {exc}")
        return False
    print("Панель собрана.")
    return True


def create_env() -> None:
    step("Файл конфигурации .env")
    if ENV.exists():
        print(".env уже существует — не изменяю его.")
        return
    password = secrets.token_urlsafe(12)
    text = ENV_EXAMPLE.read_text(encoding="utf-8").replace("PANEL_PASSWORD=\n", f"PANEL_PASSWORD={password}\n", 1)
    ENV.write_text(text, encoding="utf-8")
    print("Создан файл .env.")
    print(f"Пароль для входа в панель: {password}")
    print("(Он сохранён в .env в строке PANEL_PASSWORD — его можно заменить своим.)")


def init_database() -> bool:
    step("База данных")
    from mishanya.config import ConfigError, load_settings
    from mishanya.db import Database, DatabaseError

    try:
        settings = load_settings(ENV)
    except ConfigError as exc:
        print(f"ОШИБКА в .env: {exc}")
        return False

    async def run() -> int:
        db = Database(settings.db_path)
        await db.init()
        version = await db.schema_version()
        await db.dispose()
        return version

    try:
        version = asyncio.run(run())
    except DatabaseError as exc:
        print(f"ОШИБКА: {exc}")
        return False
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    print(f"База готова: {settings.db_path} (схема v{version}). Существующие данные не изменяются.")
    return True


def run_tests() -> None:
    step("Самопроверка (тесты)")
    try:
        import pytest  # noqa: F401
    except ImportError:
        print("pytest не установлен — пропускаю.")
        return
    code = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=BASE).returncode
    print("Все тесты пройдены." if code == 0 else "Некоторые тесты не прошли — подробности выше. Приложение может работать некорректно.")


def token_status() -> None:
    step("Что дальше")
    token = ""
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("DISCORD_TOKEN="):
            token = line.split("=", 1)[1].strip()
    if not token:
        print("1. Откройте файл .env и вставьте токен бота в строку DISCORD_TOKEN=")
        print("   (Discord Developer Portal → ваше приложение → Bot → Reset Token).")
        print("2. Там же включите Server Members Intent и Message Content Intent.")
        print("3. Запустите start.bat.")
        if sys.stdin.isatty():
            try:
                answer = input("\nОткрыть .env в Блокноте сейчас? [Y/n]: ").strip().lower()
            except EOFError:
                return
            if answer in {"", "y", "д", "да", "yes"} and os.name == "nt":
                subprocess.Popen(["notepad.exe", str(ENV)])
    else:
        print("Токен задан. Запустите start.bat.")


def main() -> int:
    ok = build_frontend()
    create_env()
    ok = init_database() and ok
    if "--skip-tests" not in sys.argv:
        run_tests()
    token_status()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
