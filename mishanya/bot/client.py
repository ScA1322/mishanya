"""Класс Discord-бота Mishanya: intents, загрузка модулей, синхронизация команд, фоновые задачи."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..db import utcnow
from ..db.backup import create_backup, prune_backups
from .utils import UserFacingError, err_embed, human_error, respond

if TYPE_CHECKING:
    from ..core import App

log = logging.getLogger(__name__)

COGS = [
    "mishanya.bot.cogs.event_logging",
    "mishanya.bot.cogs.automod",
    "mishanya.bot.cogs.leveling",
    "mishanya.bot.cogs.moderation",
    "mishanya.bot.cogs.welcome",
    "mishanya.bot.cogs.autoroles",
    "mishanya.bot.cogs.custom_commands",
    "mishanya.bot.cogs.utility",
    "mishanya.bot.cogs.reminders",
    "mishanya.bot.cogs.tickets",
    "mishanya.bot.cogs.menus",
    "mishanya.bot.cogs.music",
    "mishanya.bot.cogs.tempvoice",
    "mishanya.bot.cogs.posts",
    "mishanya.bot.cogs.mind",
    "mishanya.bot.cogs.chatter",
    "mishanya.bot.cogs.listener",
]

# Права, которые запрашиваются в ссылке приглашения и проверяются на странице диагностики.
REQUIRED_PERMISSIONS = discord.Permissions(
    view_channel=True, send_messages=True, embed_links=True, attach_files=True, read_message_history=True,
    add_reactions=True, manage_messages=True, manage_roles=True, manage_channels=True, kick_members=True,
    ban_members=True, moderate_members=True, manage_nicknames=True, view_audit_log=True,
    send_messages_in_threads=True, connect=True, speak=True, move_members=True, mute_members=True,
)


def build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.members = True  # привилегированный: приветствия, автороли, журнал участников
    intents.message_content = True  # привилегированный: автомодерация, пользовательские команды
    return intents


def interaction_lag(interaction: discord.Interaction) -> float:
    """Сколько секунд прошло с момента вызова команды в Discord (по часам Discord и этого компьютера)."""
    return (discord.utils.utcnow() - interaction.created_at).total_seconds()


class MishanyaTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        name = interaction.command.qualified_name if interaction.command else "?"
        lag = interaction_lag(interaction)
        if lag > 2.5:
            log.warning("Команда /%s от %s пришла с задержкой %.1f с — Discord ждёт ответ только 3 секунды. "
                        "Проверьте интернет/VPN и что часы Windows синхронизированы.", name, interaction.user, lag)
        else:
            log.info("Команда /%s от %s (задержка %.1f с)", name, interaction.user, lag)
        return True

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        original = getattr(error, "original", error)
        if isinstance(error, (app_commands.CommandNotFound, app_commands.CommandSignatureMismatch)):
            # В Discord осталась старая версия команды: синхронизируем заново и подсказываем пользователю.
            log.warning("Пришла устаревшая команда (%s) — синхронизирую slash-команды заново", error)
            text = "Список команд обновляется. Через минуту перезапустите Discord (Ctrl+R) и повторите команду."
            client = interaction.client
            if isinstance(client, MishanyaBot) and time.monotonic() - client.last_forced_sync > 120:
                client.last_forced_sync = time.monotonic()  # не чаще раза в 2 минуты — лимиты Discord
                client.background(client.sync_commands(force=True))
        elif isinstance(error, UserFacingError):
            text = str(error)
        elif isinstance(error, app_commands.MissingPermissions):
            text = "У вас недостаточно прав для этой команды."
        elif isinstance(error, app_commands.BotMissingPermissions):
            text = "У бота недостаточно прав для этой команды."
        elif isinstance(error, app_commands.CommandOnCooldown):
            text = f"Подождите {error.retry_after:.0f} с перед повторным вызовом."
        elif isinstance(error, app_commands.NoPrivateMessage):
            text = "Эта команда работает только на сервере."
        elif isinstance(error, app_commands.CheckFailure):
            text = "Команда недоступна."
        elif isinstance(original, UserFacingError):
            text = str(original)
        elif isinstance(original, discord.NotFound) and original.code == 10062:
            # Unknown interaction: Discord ждёт ответ не дольше 3 секунд — отвечать уже поздно.
            log.warning("Команда /%s: не успели ответить Discord за 3 секунды (задержка %.1f с). Обычно это "
                        "медленная сеть/VPN или перегруженный компьютер.",
                        interaction.command.qualified_name if interaction.command else "?", interaction_lag(interaction))
            return
        else:
            text = human_error(original)
            if not isinstance(original, discord.HTTPException):
                log.exception("Ошибка в команде /%s", interaction.command.qualified_name if interaction.command
                              else "?", exc_info=original)
            else:
                log.warning("Ошибка Discord API в команде: %s", original)
        try:
            await respond(interaction, embed=err_embed(text), ephemeral=True)
        except discord.HTTPException:
            pass


class MishanyaBot(commands.Bot):
    def __init__(self, app: "App"):
        super().__init__(command_prefix=commands.when_mentioned, intents=build_intents(),
                         tree_cls=MishanyaTree, help_command=None,
                         allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
                         max_messages=2000)
        self.app = app
        self.commands_synced: str | None = None
        self.last_sync_error: str | None = None
        self.last_forced_sync = 0.0
        self._tasks: set[asyncio.Task] = set()

    def background(self, coro) -> None:  # noqa: ANN001
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------ жизненный цикл

    async def setup_hook(self) -> None:
        for ext in COGS:
            await self.load_extension(ext)
        await self.sync_commands(force=False)
        self.maintenance.start()

    def commands_hash(self) -> str:
        data = []
        for cmd in sorted(self.tree.get_commands(), key=lambda c: c.name):
            try:
                data.append(cmd.to_dict(self.tree))
            except TypeError:
                data.append(cmd.to_dict())  # type: ignore[call-arg]
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()

    async def sync_commands(self, force: bool) -> int | None:
        """Синхронизирует slash-команды глобально, только если они изменились (лимиты Discord)."""
        # В хэш входит ID приложения: если сменили токен/бота (или перенесли папку data), команды
        # нового бота ещё не зарегистрированы, даже если сами команды не менялись.
        digest = f"{self.application_id}:{self.commands_hash()}"
        stored = await self.app.config.get_value("commands_hash")
        if not force and stored == digest:
            self.commands_synced = "актуальны"
            return None
        try:
            synced = await self.tree.sync()
        except discord.HTTPException as exc:
            self.last_sync_error = str(exc)
            log.error("Не удалось синхронизировать slash-команды: %s", exc)
            return None
        await self.app.config.set_value("commands_hash", digest)
        self.commands_synced = f"синхронизировано {len(synced)}"
        self.last_sync_error = None
        log.info("Синхронизировано slash-команд: %d", len(synced))
        return len(synced)

    async def command_health(self) -> list[dict]:
        """Почему бот может не отвечать на slash-команды: адрес Interactions Endpoint, скорость API, команды."""
        steps: list[dict] = []
        try:
            start = time.monotonic()
            info = await asyncio.wait_for(self.application_info(), 15)
            first = time.monotonic() - start
            start = time.monotonic()
            await asyncio.wait_for(self.application_info(), 15)
            rest = max(first, time.monotonic() - start)
        except discord.HTTPException as exc:
            return [{"name": "Связь с API Discord", "ok": False, "detail": f"запрос не прошёл: {exc}"}]
        except (OSError, asyncio.TimeoutError) as exc:
            return [{"name": "Связь с API Discord", "ok": False, "detail": (
                f"discord.com недоступен ({exc}). Бот получает события, но не может отвечать. "
                "Нужен VPN или обход блокировки, который работает для всех программ, а не только для браузера.")}]
        endpoint = getattr(info, "interactions_endpoint_url", None)
        steps.append({"name": "Interactions Endpoint URL", "ok": not endpoint, "detail": (
            "не задан — команды приходят боту" if not endpoint else
            f"задан адрес {endpoint}: Discord отправляет команды туда, а не боту, поэтому «приложение не отвечает». "
            "Откройте discord.com/developers/applications → ваше приложение → General Information, "
            "очистите поле Interactions Endpoint URL и сохраните.")})
        steps.append({"name": "Скорость ответа API Discord", "ok": rest < 1.5, "detail": (
            f"{rest:.2f} с" + ("" if rest < 1.5 else
                              " — слишком медленно: Discord ждёт ответ на команду не дольше 3 секунд. "
                              "Обычно виноват VPN/обход блокировки или плохой интернет на этом компьютере."))})
        try:
            remote = {c.name for c in await self.tree.fetch_commands()}
            local = {c.name for c in self.tree.get_commands()}
            missing = sorted(local - remote)
            steps.append({"name": "Slash-команды в Discord", "ok": not missing, "detail": (
                f"зарегистрировано {len(remote)}" if not missing else
                f"не зарегистрированы: {', '.join('/' + m for m in missing[:10])}. Откройте «Общие настройки» "
                "и нажмите «Пересинхронизировать slash-команды».")})
        except discord.HTTPException as exc:
            steps.append({"name": "Slash-команды в Discord", "ok": False, "detail": str(exc)})
        return steps

    async def on_ready(self) -> None:
        assert self.user is not None
        log.info("Mishanya подключён как %s (%s), серверов: %d", self.user, self.user.id, len(self.guilds))
        if not getattr(self, "_health_logged", False):
            self._health_logged = True
            try:
                for step in await self.command_health():
                    if step["ok"]:
                        continue
                    if step["name"] == "Slash-команды в Discord":
                        log.warning("В Discord зарегистрированы не все slash-команды — синхронизирую заново")
                        if await self.sync_commands(force=True) is not None:
                            continue
                    log.warning("Команды могут не работать — %s: %s", step["name"], step["detail"])
            except Exception:  # noqa: BLE001
                log.exception("Не удалось проверить работу slash-команд")
        self.app.bot_error = None
        for g in self.guilds:
            await self._store_guild(g)
        await self.app.guilds.mark_all_absent_except({g.id for g in self.guilds})
        settings = await self.app.config.get_global()
        await self.change_presence(activity=discord.CustomActivity(name=settings.bot_status_text))

    async def _store_guild(self, g: discord.Guild, present: bool = True) -> None:
        await self.app.guilds.upsert(g.id, g.name, g.icon.url if g.icon else None, g.member_count, present)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Бот добавлен на сервер %s (%s)", guild.name, guild.id)
        await self._store_guild(guild)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        log.info("Бот удалён с сервера %s (%s). Данные сохранены.", guild.name, guild.id)
        await self.app.guilds.mark_absent(guild.id)

    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        await self._store_guild(after)

    async def on_message(self, message: discord.Message) -> None:
        """Единая точка обработки сообщений: автомодерация → пользовательские команды → XP."""
        if message.author.bot:
            return
        if message.guild is None:  # личка: переписка с доверенными людьми
            mind = self.get_cog("Mind")
            if mind is not None:
                try:
                    await mind.handle_dm(message)  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    log.exception("Ошибка обработки личного сообщения")
            return
        if not isinstance(message.author, discord.Member):
            return
        automod = self.get_cog("Automod")
        if automod is not None:
            try:
                if await automod.handle_message(message):  # type: ignore[attr-defined]
                    return
            except Exception:  # noqa: BLE001
                log.exception("Ошибка автомодерации")
        for cog_name in ("CustomCommands", "Leveling", "Mind", "Chatter"):
            cog = self.get_cog(cog_name)
            if cog is None:
                continue
            try:
                await cog.handle_message(message)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                log.exception("Ошибка обработки сообщения в модуле %s", cog_name)

    async def on_error(self, event_method: str, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        log.exception("Необработанная ошибка в событии %s", event_method)

    # ------------------------------------------------------------ обслуживание

    @tasks.loop(minutes=30)
    async def maintenance(self) -> None:
        settings = await self.app.config.get_global()
        try:
            removed = await self.app.eventlog.cleanup(settings.log_retention_days, settings.max_event_logs_per_guild)
            removed += await self.app.automod.cleanup(max(1, settings.log_retention_days))
            if removed:
                log.info("Очистка журналов: удалено записей %d", removed)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка очистки журналов")
        if settings.auto_backup_enabled:
            await self.app_auto_backup(settings.auto_backup_interval_hours, settings.auto_backup_keep)

    async def app_auto_backup(self, interval_hours: int, keep: int) -> None:
        last = await self.app.config.get_value("last_auto_backup")
        now = utcnow()
        if last:
            try:
                if now - datetime.fromisoformat(last) < timedelta(hours=interval_hours):
                    return
            except ValueError:
                pass
        s = self.app.settings
        try:
            info = await asyncio.to_thread(create_backup, s.db_path, s.backup_dir, "auto")
            await asyncio.to_thread(prune_backups, s.backup_dir, keep)
            await self.app.config.set_value("last_auto_backup", now.isoformat())
            log.info("Автоматическая резервная копия создана: %s", info.name)
        except Exception:  # noqa: BLE001
            log.exception("Не удалось создать автоматическую резервную копию")

    @maintenance.before_loop
    async def _before_maintenance(self) -> None:
        await self.wait_until_ready()

    # ------------------------------------------------------------ состояние для панели

    def status(self) -> dict:
        ready = self.is_ready() and not self.is_closed()
        return {
            "connected": ready,
            "latency_ms": round(self.latency * 1000) if ready and self.latency == self.latency else None,
            "user": str(self.user) if self.user else None,
            "user_id": str(self.user.id) if self.user else None,
            "avatar_url": self.user.display_avatar.url if self.user else None,
            "guild_count": len(self.guilds),
            "commands": self.commands_synced,
            "sync_error": self.last_sync_error,
        }

    def invite_url(self) -> str | None:
        if self.application_id is None:
            return None
        return discord.utils.oauth_url(self.application_id, permissions=REQUIRED_PERMISSIONS,
                                       scopes=("bot", "applications.commands"))
