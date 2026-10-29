"""Движок автомодерации. Проверки — чистые функции над MessageInfo, без Discord API."""
from __future__ import annotations

import hashlib
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import delete, func, select

from ..db import Database, utcnow
from ..db.models import AutomodViolation
from ..schemas import AutomodConfig, RuleBase

RULE_NAMES = {
    "spam": "Антиспам",
    "flood": "Антифлуд",
    "duplicates": "Повтор сообщений",
    "mentions": "Массовые упоминания",
    "words": "Запрещённые слова",
    "links": "Ссылки",
    "invites": "Приглашения Discord",
}

INVITE_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com/invite|discord\.gg|dsc\.gg)/([A-Za-z0-9-]+)",
                       re.IGNORECASE)
URL_RE = re.compile(r"(?:https?://|www\.)([^\s/<>\"']+)", re.IGNORECASE)
EVERYONE_RE = re.compile(r"@(everyone|here)\b")
ZERO_WIDTH_RE = re.compile(r"[​-‏⁠﻿]")


@dataclass
class MessageInfo:
    guild_id: int
    user_id: int
    channel_id: int
    content: str
    role_ids: list[int] = field(default_factory=list)
    mention_count: int = 0
    is_moderator: bool = False
    timestamp: float = field(default_factory=time.monotonic)


@dataclass
class Violation:
    rule: str
    reason: str
    settings: RuleBase

    @property
    def rule_name(self) -> str:
        return RULE_NAMES.get(self.rule, self.rule)


def _exempt(rule: RuleBase, msg: MessageInfo) -> bool:
    if str(msg.channel_id) in rule.exempt_channels:
        return True
    roles = set(rule.exempt_roles)
    return any(str(r) in roles for r in msg.role_ids)


def _domain_allowed(domain: str, allowed: list[str]) -> bool:
    domain = domain.lower().split(":")[0].removeprefix("www.")
    return any(domain == a or domain.endswith("." + a) for a in allowed)


def _contains_word(text: str, word: str, mode: str) -> bool:
    if mode == "contains":
        return word in text
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) is not None


class AutomodEngine:
    def __init__(self, history_seconds: int = 3600):
        self._history: dict[tuple[int, int], deque[tuple[float, str]]] = {}
        self.history_seconds = history_seconds

    def _record(self, msg: MessageInfo) -> deque[tuple[float, str]]:
        key = (msg.guild_id, msg.user_id)
        hist = self._history.setdefault(key, deque(maxlen=60))
        digest = hashlib.sha1(msg.content.strip().lower().encode("utf-8")).hexdigest()
        hist.append((msg.timestamp, digest))
        while hist and msg.timestamp - hist[0][0] > self.history_seconds:
            hist.popleft()
        return hist

    def forget(self, guild_id: int, user_id: int) -> None:
        self._history.pop((guild_id, user_id), None)

    def check(self, cfg: AutomodConfig, msg: MessageInfo) -> Violation | None:
        if not cfg.enabled:
            return None
        if cfg.ignore_moderators and msg.is_moderator:
            return None
        if str(msg.channel_id) in cfg.exempt_channels:
            return None
        global_roles = set(cfg.exempt_roles)
        if any(str(r) in global_roles for r in msg.role_ids):
            return None

        hist = self._record(msg)
        text = ZERO_WIDTH_RE.sub("", msg.content)
        lower = text.lower()

        r = cfg.invites
        if r.enabled and not _exempt(r, msg):
            allowed = {c.lower() for c in r.allowed_codes}
            for code in INVITE_RE.findall(text):
                if code.lower() not in allowed:
                    return Violation("invites", "Приглашение на другой сервер", r)

        r = cfg.links
        if r.enabled and not _exempt(r, msg):
            for domain in URL_RE.findall(text):
                if not _domain_allowed(domain, r.allowed_domains):
                    return Violation("links", f"Ссылка на {domain.lower()[:60]}", r)

        r = cfg.words
        if r.enabled and r.words and not _exempt(r, msg):
            for w in r.words:
                if _contains_word(lower, w, r.match_mode):
                    return Violation("words", "Запрещённое слово", r)

        r = cfg.mentions
        if r.enabled and not _exempt(r, msg):
            if msg.mention_count > r.max_mentions:
                return Violation("mentions", f"Слишком много упоминаний ({msg.mention_count})", r)
            if r.block_everyone and EVERYONE_RE.search(text):
                return Violation("mentions", "Упоминание @everyone/@here", r)

        r = cfg.flood
        if r.enabled and not _exempt(r, msg):
            lines = text.count("\n") + 1
            if lines > r.max_lines:
                return Violation("flood", f"Слишком много строк ({lines})", r)
            if len(text) > r.max_length:
                return Violation("flood", f"Слишком длинное сообщение ({len(text)} символов)", r)
            if re.search(r"(.)\1{%d,}" % (r.max_repeated_chars - 1), text, re.DOTALL):
                return Violation("flood", "Повторяющиеся символы", r)

        r = cfg.duplicates
        if r.enabled and not _exempt(r, msg) and text.strip():
            now, digest = hist[-1]
            same = sum(1 for ts, d in hist if d == digest and now - ts <= r.per_seconds)
            if same >= r.max_duplicates:
                return Violation("duplicates", f"Одинаковое сообщение {same} раз за {r.per_seconds} с", r)

        r = cfg.spam
        if r.enabled and not _exempt(r, msg):
            now = hist[-1][0]
            recent = sum(1 for ts, _ in hist if now - ts <= r.per_seconds)
            if recent > r.max_messages:
                return Violation("spam", f"{recent} сообщений за {r.per_seconds} с", r)

        return None


class AutomodService:
    def __init__(self, db: Database):
        self.db = db
        self.engine = AutomodEngine()

    async def record_violation(self, guild_id: int, user_id: int, rule: str) -> None:
        async with self.db.session() as s:
            s.add(AutomodViolation(guild_id=guild_id, user_id=user_id, rule=rule))
            await s.commit()

    async def recent_violations(self, guild_id: int, user_id: int, window_minutes: int) -> int:
        since = utcnow() - timedelta(minutes=window_minutes)
        async with self.db.session() as s:
            return (await s.execute(select(func.count()).select_from(AutomodViolation).where(
                AutomodViolation.guild_id == guild_id, AutomodViolation.user_id == user_id,
                AutomodViolation.created_at >= since))).scalar_one()

    async def cleanup(self, older_than_days: int) -> int:
        async with self.db.session() as s:
            res = await s.execute(delete(AutomodViolation).where(
                AutomodViolation.created_at < utcnow() - timedelta(days=older_than_days)))
            await s.commit()
            return res.rowcount
