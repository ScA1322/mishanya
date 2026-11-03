"""Telegram владельца: кто пишет, прочитать переписку, отправить сообщение — по приказу своих людей.

Работает от имени аккаунта владельца через официальный API Telegram (библиотека Telethon). Нужны api_id и
api_hash с my.telegram.org и одноразовый вход по коду из Telegram. Сессия хранится в data/telegram.session —
это полный доступ к аккаунту, файл никуда не отправляется и обновлением не перезаписывается.
"""
from __future__ import annotations

import logging
from datetime import timezone
from pathlib import Path

from .mind import best_match

log = logging.getLogger(__name__)

DIALOGS_LIMIT = 300


class TelegramError(Exception):
    """Понятная ошибка для панели и для ответа боту."""


class TelegramService:
    def __init__(self, data_dir: Path, settings):  # noqa: ANN001 — Settings
        self.session_path = data_dir / "telegram"
        self.settings = settings
        self.client = None
        self.phone: str | None = None
        self.phone_code_hash: str | None = None
        self.me: str | None = None
        self.error: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.settings.telegram_api_id and self.settings.telegram_api_hash)

    def _new_client(self):  # noqa: ANN202
        try:
            from telethon import TelegramClient
        except ImportError:
            raise TelegramError("Не установлен пакет telethon — перезапустите бота через start.bat.") from None
        if not self.configured:
            raise TelegramError("Не заданы api_id и api_hash Telegram (my.telegram.org → API development tools).")
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        return TelegramClient(str(self.session_path), int(self.settings.telegram_api_id), self.settings.telegram_api_hash,
                              device_model="Mishanya", app_version="1.0")

    async def client_ready(self):  # noqa: ANN201
        """Подключённый и авторизованный клиент или понятная ошибка."""
        if self.client is None:
            self.client = self._new_client()
        if not self.client.is_connected():
            await self.client.connect()
        if not await self.client.is_user_authorized():
            raise TelegramError("Telegram не подключён: войдите в аккаунт в панели («Болтовня» → «Telegram»).")
        if self.me is None:
            me = await self.client.get_me()
            self.me = f"{me.first_name or ''} {me.last_name or ''}".strip() + (f" (@{me.username})" if me.username else "")
        return self.client

    async def status(self) -> dict:
        out = {"configured": self.configured, "authorized": False, "me": None, "waiting_code": bool(self.phone_code_hash),
               "error": self.error}
        if not self.configured:
            return out
        try:
            await self.client_ready()
            out.update(authorized=True, me=self.me)
        except TelegramError as exc:
            out["error"] = None if "войдите" in str(exc) else str(exc)
        except Exception as exc:  # noqa: BLE001 — сеть, неверные api_id и т. п.
            out["error"] = f"Telegram: {exc}"
        return out

    # ------------------------------------------------------------ вход

    async def send_code(self, phone: str) -> None:
        if self.client is None:
            self.client = self._new_client()
        if not self.client.is_connected():
            await self.client.connect()
        try:
            sent = await self.client.send_code_request(phone)
        except Exception as exc:  # noqa: BLE001
            raise TelegramError(f"Telegram не отправил код: {exc}") from None
        self.phone, self.phone_code_hash = phone, sent.phone_code_hash

    async def sign_in(self, code: str, password: str = "") -> str:
        from telethon.errors import PasswordHashInvalidError, PhoneCodeInvalidError, SessionPasswordNeededError

        if self.client is None or not self.phone_code_hash:
            raise TelegramError("Сначала запросите код.")
        try:
            await self.client.sign_in(phone=self.phone, code=code, phone_code_hash=self.phone_code_hash)
        except SessionPasswordNeededError:
            if not password:
                raise TelegramError("На аккаунте включён облачный пароль (2FA) — введите его и нажмите «Войти» ещё раз.") from None
            try:
                await self.client.sign_in(password=password)
            except PasswordHashInvalidError:
                raise TelegramError("Неверный облачный пароль.") from None
        except PhoneCodeInvalidError:
            raise TelegramError("Неверный код.") from None
        self.phone_code_hash = None
        self.me = None
        await self.client_ready()
        log.info("Telegram: вошёл как %s", self.me)
        return self.me or ""

    async def logout(self) -> None:
        if self.client is not None:
            try:
                if self.client.is_connected() and await self.client.is_user_authorized():
                    await self.client.log_out()
            finally:
                await self.client.disconnect()
        self.client, self.me = None, None
        for p in self.session_path.parent.glob(self.session_path.name + ".session*"):
            p.unlink(missing_ok=True)

    # ------------------------------------------------------------ дела

    @staticmethod
    def _title(dialog) -> str:  # noqa: ANN001
        ent = dialog.entity
        uname = getattr(ent, "username", None)
        return dialog.name + (f" (@{uname})" if uname else "")

    async def _find_dialog(self, query: str):  # noqa: ANN202
        client = await self.client_ready()
        dialogs = [d async for d in client.iter_dialogs(limit=DIALOGS_LIMIT)]
        found = best_match(query.lstrip("@"), dialogs,
                           lambda d: (d.name or "", getattr(d.entity, "username", "") or "",
                                      getattr(d.entity, "first_name", "") or ""))
        if found is None:
            raise TelegramError(f"Не нашёл в Telegram чат «{query}».")
        return found

    async def unread(self, limit: int = 15) -> list[dict]:
        """Кто пишет: чаты с непрочитанными, свежие сверху."""
        client = await self.client_ready()
        out = []
        async for d in client.iter_dialogs(limit=DIALOGS_LIMIT):
            if d.unread_count and not getattr(d.entity, "bot", False):
                text = (d.message.message or "[вложение]") if d.message else ""
                out.append({"chat": self._title(d), "unread": d.unread_count, "last": text[:200],
                            "date": d.date.astimezone(timezone.utc).strftime("%d.%m %H:%M") if d.date else ""})
                if len(out) >= limit:
                    break
        return out

    async def read(self, query: str, count: int = 10) -> tuple[str, list[dict]]:
        """Последние сообщения чата (не отмечает их прочитанными)."""
        d = await self._find_dialog(query)
        client = await self.client_ready()
        msgs = []
        async for m in client.iter_messages(d.entity, limit=max(1, min(count, 50))):
            sender = "я" if m.out else (getattr(m.sender, "first_name", None) or getattr(m.sender, "title", None) or d.name)
            msgs.append({"from": sender, "text": (m.message or "[вложение]")[:500],
                         "date": m.date.astimezone(timezone.utc).strftime("%d.%m %H:%M") if m.date else ""})
        msgs.reverse()
        return self._title(d), msgs

    async def send(self, query: str, text: str) -> str:
        d = await self._find_dialog(query)
        client = await self.client_ready()
        await client.send_message(d.entity, text[:4000])
        log.info("Telegram: отправил сообщение в «%s»", d.name)
        return self._title(d)
