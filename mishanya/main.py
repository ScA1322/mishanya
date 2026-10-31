"""Точка входа: запускает веб-панель и Discord-бота в одном процессе и одном event loop."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import webbrowser

import discord
import uvicorn

from .api.app import create_app
from .bot.client import MishanyaBot
from .config import ConfigError, Settings, load_settings
from .core import App, setup_logging
from .db import Database, DatabaseError
from .services.updater import RESTART_EXIT_CODE, UpdateError

log = logging.getLogger("mishanya")

INTENTS_HELP = (
    "Discord отклонил подключение: не включены привилегированные intents. Откройте "
    "https://discord.com/developers/applications → ваше приложение → Bot → Privileged Gateway Intents и "
    "включите SERVER MEMBERS INTENT и MESSAGE CONTENT INTENT, затем перезапустите Mishanya."
)
TOKEN_HELP = (
    "Токен бота не задан. Откройте файл .env в папке Mishanya и заполните DISCORD_TOKEN "
    "(Developer Portal → Bot → Reset Token). Панель работает, но бот не подключён."
)


def _port_free(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


async def run_bot(app: App) -> None:
    s = app.settings
    if not s.token_configured:
        app.bot_error = TOKEN_HELP
        log.warning(TOKEN_HELP)
        return
    bot = MishanyaBot(app)
    app.bot = bot
    try:
        await bot.start(s.discord_token)
    except discord.LoginFailure:
        app.bot_error = "Discord не принял токен. Проверьте DISCORD_TOKEN в .env (возможно, токен был сброшен)."
        log.error(app.bot_error)
    except discord.PrivilegedIntentsRequired:
        app.bot_error = INTENTS_HELP
        log.error(INTENTS_HELP)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        app.bot_error = f"Бот остановился с ошибкой: {exc}"
        log.exception("Бот остановился с ошибкой")
    finally:
        if not bot.is_closed():
            await bot.close()


async def watch_stop_flag(settings: Settings, server: uvicorn.Server) -> None:
    """stop.bat создаёт файл data/stop.flag — завершаемся корректно."""
    flag = settings.data_dir / "stop.flag"
    flag.unlink(missing_ok=True)
    while not server.should_exit:
        if flag.exists():
            flag.unlink(missing_ok=True)
            log.info("Получен запрос на остановку (stop.bat)")
            server.should_exit = True
            return
        await asyncio.sleep(1)


async def open_browser_when_ready(settings: Settings, server: uvicorn.Server) -> None:
    for _ in range(100):
        if server.started:
            log.info("Панель Mishanya: %s", settings.panel_url)
            # После перезапуска для обновления вкладка панели уже открыта.
            if settings.open_browser and os.environ.get("MISHANYA_RESTARTED") != "1":
                await asyncio.to_thread(webbrowser.open, settings.panel_url)
            return
        await asyncio.sleep(0.1)


UPDATE_FIRST_CHECK_DELAY = 90  # секунд после запуска
UPDATE_LOOP_INTERVAL = 600


async def auto_update_loop(app: App, server: uvicorn.Server) -> None:
    """Периодически проверяет GitHub; при запуске через start.bat сам ставит обновление и перезапускается."""
    await asyncio.sleep(UPDATE_FIRST_CHECK_DELAY)
    while not server.should_exit:
        try:
            cfg = await app.config.get_global()
            updater = app.updater
            if (cfg.auto_update_enabled and not updater.state.restart_required
                    and updater.due(cfg.update_check_interval_hours)):
                state = await updater.check()
                if state.supported and state.behind:
                    if updater.launched_by_script():
                        log.info("Найдено обновление (%d новых коммитов) — устанавливаю", state.behind)
                        await updater.apply()
                        if app.request_restart is not None:
                            app.request_restart()
                            return
                    else:
                        log.info("Доступно обновление Mishanya (%d новых коммитов). Автоустановка работает при "
                                 "запуске через start.bat.", state.behind)
        except UpdateError as exc:
            log.warning("Автообновление не выполнено: %s", exc)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка автообновления")
        await asyncio.sleep(UPDATE_LOOP_INTERVAL)


async def run(settings: Settings, app: App) -> int:
    try:
        await app.db.init()
    except DatabaseError as exc:
        log.error("%s", exc)
        return 3
    api = create_app(app)
    server = uvicorn.Server(uvicorn.Config(api, host=settings.panel_host, port=settings.panel_port,
                                           log_config=None, access_log=False, lifespan="off"))
    pid_file = settings.data_dir / "mishanya.pid"
    pid_file.write_text(str(os.getpid()), encoding="ascii")
    exit_code = 0

    def request_restart() -> None:
        nonlocal exit_code
        log.info("Перезапуск Mishanya для применения обновления…")
        exit_code = RESTART_EXIT_CODE
        server.should_exit = True

    app.request_restart = request_restart
    bot_task = asyncio.create_task(run_bot(app), name="bot")
    helpers = [asyncio.create_task(watch_stop_flag(settings, server)),
               asyncio.create_task(open_browser_when_ready(settings, server)),
               asyncio.create_task(auto_update_loop(app, server))]
    try:
        await server.serve()
    finally:
        log.info("Остановка Mishanya…")
        if app.bot is not None and not app.bot.is_closed():
            await app.bot.close()
        for t in [bot_task, *helpers]:
            t.cancel()
        await asyncio.gather(bot_task, *helpers, return_exceptions=True)
        await app.db.dispose()
        pid_file.unlink(missing_ok=True)
        log.info("Mishanya остановлена")
    return exit_code


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"[Mishanya] Ошибка конфигурации: {exc}")
        return 2
    problems = settings.validate()
    if problems:
        print("[Mishanya] Не удалось запустить — исправьте файл .env:")
        for p in problems:
            print("  •", p)
        return 2
    errors = setup_logging(settings)
    if not _port_free(settings.panel_host, settings.panel_port):
        log.error("Порт %s:%s занят. Возможно, Mishanya уже запущена — откройте %s или выполните stop.bat. "
                  "Иначе смените PANEL_PORT в .env.", settings.panel_host, settings.panel_port, settings.panel_url)
        return 4
    app = App(settings, Database(settings.db_path), errors)
    try:
        return asyncio.run(run(settings, app))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
