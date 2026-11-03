"""«Разум» бота: память о людях и сервере, обиды, голосовая активность и разбор ответов модели с действиями."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import webbrowser
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from ..db import Database
from ..db.models import BotGrudge, BotMemory, BotRelation, DmContact, VoiceSeen, utcnow

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
        return {"reply": raw.strip(), "offense": 0, "offense_reason": "", "mute_minutes": None, "actions": [],
                "attitude": 0, "ignore_minutes": None, "unignore": False}
    try:
        offense = max(0, min(3, int(data.get("offense") or 0)))
    except (TypeError, ValueError):
        offense = 0
    try:
        mute = max(1, min(40320, int(data.get("mute_minutes")))) if data.get("mute_minutes") else None
    except (TypeError, ValueError):
        mute = None
    try:
        attitude = max(-10, min(10, int(data.get("attitude") or 0)))
    except (TypeError, ValueError):
        attitude = 0
    try:
        ignore = max(1, min(10080, int(data.get("ignore_minutes")))) if data.get("ignore_minutes") else None
    except (TypeError, ValueError):
        ignore = None
    actions = data.get("actions") or []
    if isinstance(actions, dict):
        actions = [actions]
    moves = data.get("move") or []  # старый формат
    actions = list(actions) + [{"type": "move", **m} for m in ([moves] if isinstance(moves, dict) else moves)
                               if isinstance(m, dict)]
    clean = []
    for a in actions:
        if not isinstance(a, dict) or not str(a.get("type") or "").strip():
            continue
        item = {}
        for k, v in list(a.items())[:8]:
            if isinstance(v, bool | int | float) or v is None:
                item[str(k)[:20]] = v
            else:
                item[str(k)[:20]] = str(v).strip()[:200]
        item["type"] = str(item["type"]).lower()
        clean.append(item)
    return {"reply": str(data.get("reply") or "").strip(), "offense": offense,
            "offense_reason": str(data.get("offense_reason") or "").strip()[:200], "mute_minutes": mute,
            "actions": clean[:10], "attitude": attitude, "ignore_minutes": ignore,
            "unignore": data.get("unignore") is True}


ACTIONS_FORMAT = (
    "Ответь СТРОГО одним JSON-объектом без текста вокруг:\n"
    '{"reply": "твой ответ (пустая строка, если последнее сообщение явно не тебе и встревать незачем)", '
    '"offense": 0, "offense_reason": "", "mute_minutes": 0, "actions": [], "attitude": 0, '
    '"ignore_minutes": 0, "unignore": false}\n'
    "У тебя свой характер: ты можешь любить, презирать и игнорить кого захочешь. attitude — как изменилось твоё "
    "отношение к автору после этого сообщения (от -10 до +10, обычно 0; копится по шкале от -100 до +100). ignore_minutes — если он тебя достал, спамит "
    "или просто бесит, начни его игнорить на сколько захочешь (можно бросить напоследок что-нибудь вроде «отъебись»); "
    "пусть потом добивается твоего внимания. unignore — true, если ты его игнорил, а он заслужил прощение.\n"
    "offense — насколько последнее сообщение издевается над тобой или оскорбляет ТЕБЯ лично: 0 — нормально или "
    "обычный дружеский мат, 1 — подкол или стёб, 2 — настоящее оскорбление или унижение, 3 — жёсткая травля. "
    "Оскорбления других людей, мат в воздух и шутки не про тебя — это 0. offense_reason — если offense > 0, "
    "коротко, за что обиделся (например «назвал тупым ботом»). mute_minutes — если offense > 0, сам реши срок "
    "приговора в минутах: за мелкий подкол пара минут, за оскорбление 10–60, за травлю часы и сутки. Тебе НРАВИТСЯ "
    "твоя власть: наказывая, ты наслаждаешься — в reply огласи приговор пафосно, с удовольствием, как император, "
    "который вершит суд (без предупреждений «не делай так» — сразу суд).\n"
    "actions — что ты делаешь на сервере. У тебя есть настоящая власть, ты не просто болтаешь: если просят что-то "
    "сделать, сам решай — выполнить, отказать или сделать по-своему (ты Император). Можешь и сам, без просьбы, "
    "например вырубить надоевшую музыку или замутить надоеду. Если делаешь — обязательно добавь действие, иначе "
    "ничего не произойдёт. Не пиши в reply, что сделал, если не добавил действие. Список (who — имя участника или "
    "«я» про автора сообщения):\n"
    '{"type": "music", "do": "play|pause|resume|skip|stop|leave|shuffle|loop|volume", "query": "для play — '
    'КОРОТКИЙ поисковый запрос: исполнитель и/или трек, как в поиске SoundCloud (например «MORGENSHTERN» или '
    '«MORGENSHTERN Cadillac»), без слов «плейлист», «в духе», «рандом»", "count": 1, "shuffle": false, '
    '"volume": 1-200 (для volume)}. count — сколько треков поставить: 1 — один трек; просят плейлист, подборку, '
    '«на несколько часов» — 30–50 и shuffle: true\n'
    '{"type": "move", "who": "…", "to": "голосовой канал"}\n'
    '{"type": "disconnect", "who": "…"} — выкинуть из голосового канала\n'
    '{"type": "voice_mute", "who": "…", "on": true/false} — выключить или вернуть микрофон в войсе\n'
    '{"type": "timeout", "who": "…", "minutes": 0-40320} — мут в чате, 0 — снять\n'
    '{"type": "nick", "who": "…", "name": "новый ник"} — пустое имя сбрасывает ник\n'
    '{"type": "role", "who": "…", "role": "название роли", "give": true/false}\n'
    '{"type": "purge", "count": 1-50} — удалить последние сообщения в этом канале\n'
    '{"type": "kick", "who": "…", "reason": "за что"} — выгнать с сервера\n'
    '{"type": "ban", "who": "…", "reason": "за что"} — забанить (по-крупному, не за ерунду)\n'
    '{"type": "unban", "who": "имя забаненного"}\n'
    '{"type": "dm", "who": "кому (участник сервера)", "text": "что передать"} — написать человеку в личку от '
    'своего имени, если тебя об этом просят (можешь и отказать)\n'
    '{"type": "join_voice"} — зайти в голосовой канал автора (свои — всегда; остальных пускаешь, только если '
    'отношение к ним не ниже порога аудиенции, иначе откажи и съязви); {"type": "leave_voice"} — выйти из голосового\n'
    '{"type": "create_group", "who": ["имя", "имя"]} — собрать группу в личке: автор, эти люди и ты (ты пересылаешь '
    'сообщения всем участникам); {"type": "close_group"} — распустить группу автора\n'
    '{"type": "timer", "minutes": 15, "text": "что напомнить"} — засечь время: по истечении ты тегнешь автора (может '
    'любой; seconds вместо minutes — для секунд)\n'
    '{"type": "tg_unread"} — кто пишет владельцу в Telegram (непрочитанные); {"type": "tg_read", "chat": "имя или '
    '@юзернейм", "count": 10} — прочитать его переписку; {"type": "tg_send", "chat": "кому", "text": "что"} — написать '
    'в Telegram от имени владельца. Только по приказу своих; результат уйдёт им в личку, вслух его не пересказывай\n'
    '{"type": "pc_open", "program": "название"} — открыть программу на компьютере владельца (слушаешься в этом '
    'только доверенных людей)\n'
    '{"type": "pc_close", "program": "название"} — закрыть программу на компьютере владельца\n'
    "Каналы и людей бери из списков выше, не выдумывай. Если ничего не делаешь — пустой список.\n"
    "СЛОВО = ДЕЛО: всё, что умеешь, — в этом списке. Никогда не говори «не могу» или «такой команды нет» про то, что "
    "в нём есть; хочешь отказать — откажи по своей воле. Если говоришь, что делаешь, — обязательно добавь действие. "
    "Если не добавил действие — не говори, что сделал. Результат действия допишется под твоим ответом сам."
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
    @staticmethod
    def _norm_hash(text: str) -> str:
        return hashlib.sha1(normalize(text).encode()).hexdigest()

    async def remember(self, guild_id: int, items: list[tuple[int | None, str]], limit: int) -> int:
        """Сохраняет новые факты (без повторов) и обрезает память до limit, удаляя самые старые."""
        async with self.db.session() as s:
            added = 0
            fresh: set[str] = set()
            for user_id, text in items:
                text = " ".join(text.split())[:300]
                h = self._norm_hash(text)
                if len(text) < 5 or h in fresh:
                    continue
                exists = (await s.execute(select(BotMemory.id).where(
                    BotMemory.guild_id == guild_id, BotMemory.norm == h).limit(1))).scalar()
                if exists:
                    continue
                fresh.add(h)
                s.add(BotMemory(guild_id=guild_id, user_id=user_id, text=text, norm=h))
                added += 1
            await s.flush()
            if added:
                total = (await s.execute(select(func.count()).select_from(BotMemory)
                                         .where(BotMemory.guild_id == guild_id))).scalar() or 0
                if total > limit:
                    border = (await s.execute(select(BotMemory.id).where(BotMemory.guild_id == guild_id)
                                              .order_by(BotMemory.id).offset(total - limit).limit(1))).scalar()
                    if border is not None:
                        await s.execute(delete(BotMemory).where(BotMemory.guild_id == guild_id, BotMemory.id < border))
            await s.commit()
            return added

    async def memories(self, guild_id: int, user_ids: list[int] | None = None, limit: int = 500) -> list[BotMemory]:
        async with self.db.session() as s:
            q = select(BotMemory).where(BotMemory.guild_id == guild_id)
            if user_ids is not None:
                q = q.where(BotMemory.user_id.in_(user_ids))
            return list((await s.execute(q.order_by(BotMemory.id.desc()).limit(limit))).scalars())

    async def search(self, guild_id: int, *, user_id: int | None = None, general: bool = False, query: str = "",
                     offset: int = 0, limit: int = 100) -> tuple[int, list[BotMemory]]:
        """Факты для панели: одного человека, только «о сервере» или все; с поиском по тексту."""
        async with self.db.session() as s:
            cond = [BotMemory.guild_id == guild_id]
            if user_id is not None:
                cond.append(BotMemory.user_id == user_id)
            elif general:
                cond.append(BotMemory.user_id.is_(None))
            for word in query.split()[:5]:
                cond.append(BotMemory.text.ilike(f"%{word}%"))
            total = (await s.execute(select(func.count()).select_from(BotMemory).where(*cond))).scalar() or 0
            rows = (await s.execute(select(BotMemory).where(*cond).order_by(BotMemory.id.desc())
                                    .offset(offset).limit(limit))).scalars()
            return total, list(rows)

    async def people(self, guild_id: int, limit: int = 500) -> tuple[int, int, list[tuple[int, int]]]:
        """(всего фактов, фактов о сервере, [(человек, сколько фактов о нём)] — самые изученные сверху)."""
        async with self.db.session() as s:
            total = (await s.execute(select(func.count()).select_from(BotMemory)
                                     .where(BotMemory.guild_id == guild_id))).scalar() or 0
            general = (await s.execute(select(func.count()).select_from(BotMemory).where(
                BotMemory.guild_id == guild_id, BotMemory.user_id.is_(None)))).scalar() or 0
            n = func.count().label("n")
            rows = (await s.execute(select(BotMemory.user_id, n).where(
                BotMemory.guild_id == guild_id, BotMemory.user_id.is_not(None))
                .group_by(BotMemory.user_id).order_by(n.desc()).limit(limit))).all()
            return total, general, [(int(u), int(c)) for u, c in rows]

    async def forget(self, guild_id: int, memory_id: int | None = None, user_id: int | None = None) -> int:
        async with self.db.session() as s:
            q = delete(BotMemory).where(BotMemory.guild_id == guild_id)
            if memory_id is not None:
                q = q.where(BotMemory.id == memory_id)
            if user_id is not None:
                q = q.where(BotMemory.user_id == user_id)
            n = (await s.execute(q)).rowcount or 0
            await s.commit()
            return n

    async def recall(self, guild_id: int, user_ids: list[int], per_user: int = 10, general: int = 10,
                     query: str = "") -> list[str]:
        """Что бот помнит для разговора: о каждом собеседнике — свежие факты и подходящие к теме,
        о сервере — так же. Из сотен тысяч фактов в промпт попадают несколько десятков нужных."""
        words = [w for w in dict.fromkeys(normalize(query).split()) if len(w) >= 4][:6]
        out: list[str] = []
        seen: set[int] = set()
        async with self.db.session() as s:
            async def take(cond, n: int) -> None:  # noqa: ANN001
                recent = (await s.execute(select(BotMemory).where(BotMemory.guild_id == guild_id, cond)
                                          .order_by(BotMemory.id.desc()).limit(max(1, n // 2) if words else n))).scalars().all()
                matched: list[BotMemory] = []
                for w in words:
                    if len(matched) >= n - len(recent):
                        break
                    matched += (await s.execute(select(BotMemory).where(
                        BotMemory.guild_id == guild_id, cond, BotMemory.text.ilike(f"%{w[:max(4, len(w) - 2)]}%"))
                        .order_by(BotMemory.id.desc()).limit(3))).scalars().all()
                for m in [*matched, *recent][:n]:
                    if m.id not in seen:
                        seen.add(m.id)
                        out.append(m.text)

            for uid in user_ids[:6]:
                await take(BotMemory.user_id == uid, per_user)
            await take(BotMemory.user_id.is_(None) if user_ids else BotMemory.id.is_not(None), general)
        return out

    # обиды
    async def grudge(self, guild_id: int, user_id: int) -> BotGrudge | None:
        async with self.db.session() as s:
            return await s.get(BotGrudge, (guild_id, user_id))

    async def grudges(self, guild_id: int) -> list[BotGrudge]:
        async with self.db.session() as s:
            return list((await s.execute(select(BotGrudge).where(BotGrudge.guild_id == guild_id)
                                         .order_by(BotGrudge.created_at.desc()))).scalars())

    async def add_grudge(self, guild_id: int, user_id: int, role_ids: list[int], reason: str,
                         until: datetime | None = None) -> None:
        async with self.db.session() as s:
            row = await s.get(BotGrudge, (guild_id, user_id))
            if row is None:
                s.add(BotGrudge(guild_id=guild_id, user_id=user_id, role_ids=[str(r) for r in role_ids],
                                reason=reason[:300], until=until))
            else:  # повторная обида: роли, снятые в прошлый раз, не теряем; срок продлевается
                row.role_ids = sorted(set(row.role_ids) | {str(r) for r in role_ids})
                row.reason = reason[:300]
                row.until = max(row.until, until) if row.until and until else until
            await s.commit()

    async def expired_grudges(self) -> list[BotGrudge]:
        async with self.db.session() as s:
            return list((await s.execute(select(BotGrudge).where(
                BotGrudge.until.is_not(None), BotGrudge.until <= utcnow()))).scalars())

    # кто писал в личку
    async def note_dm(self, user_id: int, name: str, display: str, status: str, text: str) -> None:
        async with self.db.session() as s:
            row = await s.get(DmContact, user_id)
            if row is None:
                row = DmContact(user_id=user_id, count=0)
                s.add(row)
            row.name, row.display, row.status = name[:100], display[:100], status[:40]
            row.count = (row.count or 0) + 1
            row.last_text, row.last_at = " ".join(text.split())[:300], utcnow()
            await s.commit()

    async def dm_contacts(self, limit: int = 15) -> list[DmContact]:
        async with self.db.session() as s:
            return list((await s.execute(select(DmContact).order_by(DmContact.last_at.desc()).limit(limit))).scalars())

    async def drop_grudge(self, guild_id: int, user_id: int) -> BotGrudge | None:
        async with self.db.session() as s:
            row = await s.get(BotGrudge, (guild_id, user_id))
            if row is not None:
                await s.delete(row)
                await s.commit()
            return row

    # отношение и игнор
    async def relation(self, guild_id: int, user_id: int) -> BotRelation | None:
        async with self.db.session() as s:
            return await s.get(BotRelation, (guild_id, user_id))

    async def relations(self, guild_id: int) -> list[BotRelation]:
        async with self.db.session() as s:
            return list((await s.execute(select(BotRelation).where(BotRelation.guild_id == guild_id)
                                         .order_by(BotRelation.attitude))).scalars())

    async def update_relation(self, guild_id: int, user_id: int, *, delta: int = 0,
                              ignore_minutes: int | None = None, ignore_reason: str = "",
                              unignore: bool = False) -> BotRelation:
        async with self.db.session() as s:
            row = await s.get(BotRelation, (guild_id, user_id))
            if row is None:
                row = BotRelation(guild_id=guild_id, user_id=user_id, attitude=0, ignore_reason="")
                s.add(row)
            row.attitude = max(-100, min(100, (row.attitude or 0) + delta))
            if unignore:
                row.ignore_until, row.ignore_reason = None, ""
            elif ignore_minutes:
                row.ignore_until = utcnow() + timedelta(minutes=ignore_minutes)
                row.ignore_reason = ignore_reason[:200]
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
