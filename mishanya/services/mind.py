"""«Разум» бота: память о людях и сервере, обиды, голосовая активность и разбор ответов модели с действиями."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import webbrowser
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from ..db import Database
from ..db.models import BotGrudge, BotMemory, VoiceSeen, utcnow

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- разбор ответов модели

def parse_json(text: str) -> dict | None:
    """Достаёт JSON-объект из ответа модели (бывает обёрнут в ```json … ``` или окружён текстом)."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def parse_actions(raw: str) -> dict:
    """Ответ «мозга»: {"reply": str, "offense": 0..3, "move": [{"who", "to"}]}. Не-JSON — это просто реплика."""
    data = parse_json(raw)
    if data is None:
        return {"reply": raw.strip(), "offense": 0, "offense_reason": "", "mute_minutes": None, "move": []}
    try:
        offense = max(0, min(3, int(data.get("offense") or 0)))
    except (TypeError, ValueError):
        offense = 0
    try:
        mute = max(1, min(40320, int(data.get("mute_minutes")))) if data.get("mute_minutes") else None
    except (TypeError, ValueError):
        mute = None
    moves = data.get("move") or []
    if isinstance(moves, dict):
        moves = [moves]
    moves = [{"who": str(m.get("who", "")).strip()[:100], "to": str(m.get("to", "")).strip()[:100]}
             for m in moves if isinstance(m, dict) and m.get("who") and m.get("to")][:10]
    return {"reply": str(data.get("reply") or "").strip(), "offense": offense,
            "offense_reason": str(data.get("offense_reason") or "").strip()[:200], "mute_minutes": mute,
            "move": moves}


ACTIONS_FORMAT = (
    "Ответь СТРОГО одним JSON-объектом без текста вокруг:\n"
    '{"reply": "твой ответ (пустая строка, если последнее сообщение явно не тебе и встревать незачем)", '
    '"offense": 0, "offense_reason": "", "mute_minutes": 0, "move": []}\n'
    "offense — насколько последнее сообщение издевается над тобой или оскорбляет ТЕБЯ лично: 0 — нормально или "
    "обычный дружеский мат, 1 — подкол или стёб, 2 — настоящее оскорбление или унижение, 3 — жёсткая травля. "
    "Оскорбления других людей, мат в воздух и шутки не про тебя — это 0. offense_reason — если offense > 0, "
    "коротко, за что обиделся (например «назвал тупым ботом»). mute_minutes — если offense > 0, сам реши, на "
    "сколько минут замутить: за мелкий подкол пара минут, за оскорбление 10–30, за травлю часы.\n"
    'move — только если автор последнего сообщения прямо просит перекинуть кого-то в другой голосовой канал: '
    '[{"who": "имя участника или «я», если просит перекинуть себя", "to": "название голосового канала"}]. '
    "Иначе пустой список. Не выдумывай каналы — бери из списка голосовых каналов."
)


# ---------------------------------------------------------------- голосовые триггеры

def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def trigger_matches(phrase: str, text: str) -> bool:
    """Фраза распознана в тексте. Окончания и ошибки распознавания прощаем: «аллах акбар» ≈ «аллаху акбар»."""
    words = normalize(phrase).split()
    if not words:
        return False
    parts = [re.escape(w[:max(3, len(w) - 2)]) + r"\w*" for w in words]
    return re.search(r"\b" + r"\W*".join(parts) + r"\b", normalize(text)) is not None


def open_steam_game(app_id: int) -> None:
    """Запускает игру Steam на этом компьютере. Только steam://rungameid/<число> — ничего другого не открываем."""
    url = f"steam://rungameid/{int(app_id)}"
    if sys.platform == "win32":
        os.startfile(url)  # type: ignore[attr-defined]  # noqa: S606
    else:
        webbrowser.open(url)


# ---------------------------------------------------------------- поиск по именам

def _score(query: str, *names: str) -> int:
    q = normalize(query)
    best = 0
    for name in names:
        n = normalize(name)
        if not n or not q:
            continue
        if n == q:
            best = max(best, 3)
        elif n.startswith(q) or q.startswith(n):
            best = max(best, 2)
        elif q in n or n in q:
            best = max(best, 1)
    return best


def best_match(query: str, items: list, names) -> object | None:  # noqa: ANN001
    """Лучший объект по имени (точное > начало > вхождение); при равенстве — никого, чтобы не ошибиться."""
    scored = sorted(((_score(query, *names(i)), i) for i in items), key=lambda x: -x[0])
    if not scored or scored[0][0] == 0:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    return scored[0][1]


# ---------------------------------------------------------------- хранилище

class MindService:
    def __init__(self, db: Database):
        self.db = db

    # память
    async def remember(self, guild_id: int, items: list[tuple[int | None, str]], limit: int) -> int:
        """Сохраняет новые факты (без повторов) и обрезает память до limit, удаляя самые старые."""
        async with self.db.session() as s:
            known = {normalize(t) for t in (await s.execute(
                select(BotMemory.text).where(BotMemory.guild_id == guild_id))).scalars()}
            added = 0
            for user_id, text in items:
                text = " ".join(text.split())[:300]
                if len(text) < 5 or normalize(text) in known:
                    continue
                known.add(normalize(text))
                s.add(BotMemory(guild_id=guild_id, user_id=user_id, text=text))
                added += 1
            await s.flush()
            total = (await s.execute(select(func.count()).select_from(BotMemory)
                                     .where(BotMemory.guild_id == guild_id))).scalar() or 0
            if total > limit:
                old = (await s.execute(select(BotMemory.id).where(BotMemory.guild_id == guild_id)
                                       .order_by(BotMemory.id).limit(total - limit))).scalars().all()
                await s.execute(delete(BotMemory).where(BotMemory.id.in_(old)))
            await s.commit()
            return added

    async def memories(self, guild_id: int, user_ids: list[int] | None = None, limit: int = 500) -> list[BotMemory]:
        async with self.db.session() as s:
            q = select(BotMemory).where(BotMemory.guild_id == guild_id)
            if user_ids is not None:
                q = q.where(BotMemory.user_id.in_(user_ids))
            return list((await s.execute(q.order_by(BotMemory.id.desc()).limit(limit))).scalars())

    async def forget(self, guild_id: int, memory_id: int | None = None) -> int:
        async with self.db.session() as s:
            q = delete(BotMemory).where(BotMemory.guild_id == guild_id)
            if memory_id is not None:
                q = q.where(BotMemory.id == memory_id)
            n = (await s.execute(q)).rowcount or 0
            await s.commit()
            return n

    async def recall(self, guild_id: int, user_ids: list[int], per_user: int = 6, general: int = 10) -> list[str]:
        """Что бот помнит о собеседниках и о сервере — для промпта."""
        about = await self.memories(guild_id, user_ids, limit=per_user * max(1, len(user_ids))) if user_ids else []
        recent = await self.memories(guild_id, limit=general + len(about))
        seen = {m.id for m in about}
        rows = about + [m for m in recent if m.id not in seen][:general]
        return [m.text for m in rows]

    # обиды
    async def grudge(self, guild_id: int, user_id: int) -> BotGrudge | None:
        async with self.db.session() as s:
            return await s.get(BotGrudge, (guild_id, user_id))

    async def grudges(self, guild_id: int) -> list[BotGrudge]:
        async with self.db.session() as s:
            return list((await s.execute(select(BotGrudge).where(BotGrudge.guild_id == guild_id)
                                         .order_by(BotGrudge.created_at.desc()))).scalars())

    async def add_grudge(self, guild_id: int, user_id: int, role_ids: list[int], reason: str) -> None:
        async with self.db.session() as s:
            row = await s.get(BotGrudge, (guild_id, user_id))
            if row is None:
                s.add(BotGrudge(guild_id=guild_id, user_id=user_id, role_ids=[str(r) for r in role_ids],
                                reason=reason[:300]))
            else:  # повторная обида: роли, снятые в прошлый раз, не теряем
                row.role_ids = sorted(set(row.role_ids) | {str(r) for r in role_ids})
                row.reason = reason[:300]
            await s.commit()

    async def drop_grudge(self, guild_id: int, user_id: int) -> BotGrudge | None:
        async with self.db.session() as s:
            row = await s.get(BotGrudge, (guild_id, user_id))
            if row is not None:
                await s.delete(row)
                await s.commit()
            return row

    # голосовая активность
    async def seen_in_voice(self, guild_id: int, user_ids: list[int], when: datetime | None = None) -> None:
        when = when or utcnow()
        async with self.db.session() as s:
            for uid in user_ids:
                row = await s.get(VoiceSeen, (guild_id, uid))
                if row is None:
                    s.add(VoiceSeen(guild_id=guild_id, user_id=uid, last_seen=when))
                else:
                    row.last_seen, row.reported_at = when, None
            await s.commit()

    async def inactive(self, guild_id: int, days: int) -> list[VoiceSeen]:
        """Кто был в голосе раньше, но не заходит уже days дней и о ком ещё не докладывали."""
        border = utcnow() - timedelta(days=days)
        async with self.db.session() as s:
            return list((await s.execute(select(VoiceSeen).where(
                VoiceSeen.guild_id == guild_id, VoiceSeen.last_seen < border,
                VoiceSeen.reported_at.is_(None)).order_by(VoiceSeen.last_seen))).scalars())

    async def mark_reported(self, guild_id: int, user_ids: list[int]) -> None:
        async with self.db.session() as s:
            for uid in user_ids:
                row = await s.get(VoiceSeen, (guild_id, uid))
                if row is not None:
                    row.reported_at = utcnow()
            await s.commit()
