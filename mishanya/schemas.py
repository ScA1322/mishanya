"""Pydantic-схемы настроек модулей сервера.

Discord ID везде передаются строками: JavaScript теряет точность чисел больше 2^53.
"""
from __future__ import annotations

import re

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Snowflake = Annotated[str, Field(pattern=r"^\d{15,22}$")]
OptSnowflake = Annotated[str | None, Field(default=None, pattern=r"^\d{15,22}$")]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


class EmbedConfig(Strict):
    enabled: bool = False
    title: str = Field("", max_length=256)
    description: str = Field("", max_length=4000)
    color: Color = "#5865F2"
    footer: str = Field("", max_length=2048)
    image_url: str = Field("", max_length=512)
    show_avatar: bool = True

    @field_validator("image_url")
    @classmethod
    def _url(cls, v: str) -> str:
        if v and not v.startswith(("https://", "http://")):
            raise ValueError("Ссылка на изображение должна начинаться с http:// или https://")
        return v


# ---------------------------------------------------------------- Уровни

class LevelingConfig(Strict):
    enabled: bool = True
    xp_min: int = Field(15, ge=0, le=1000)
    xp_max: int = Field(25, ge=0, le=1000)
    cooldown_seconds: int = Field(60, ge=0, le=86400)
    min_message_length: int = Field(3, ge=0, le=500)
    ignore_duplicate_messages: bool = True
    xp_multiplier: float = Field(1.0, ge=0, le=10)
    # Формула: XP для перехода с уровня L на L+1 = a*L^2 + b*L + c
    formula_a: int = Field(5, ge=0, le=10000)
    formula_b: int = Field(50, ge=0, le=100000)
    formula_c: int = Field(100, ge=1, le=1000000)
    max_level: int = Field(500, ge=1, le=5000)
    announce_mode: Literal["current", "channel", "dm", "off"] = "current"
    announce_channel_id: OptSnowflake = None
    levelup_message: str = Field("🎉 {user}, поздравляем! Новый уровень: **{level}**.", max_length=2000)
    # Отдельное сообщение с упоминанием, когда за новый уровень выдаётся роль-награда
    reward_announce: bool = True
    reward_message: str = Field("🏅 {user} дорос до **{level}** уровня и получает роль {role}!", max_length=2000)
    no_xp_channels: list[Snowflake] = Field(default_factory=list, max_length=200)
    no_xp_categories: list[Snowflake] = Field(default_factory=list, max_length=100)
    no_xp_roles: list[Snowflake] = Field(default_factory=list, max_length=100)
    stack_rewards: bool = True
    restore_rewards_on_rejoin: bool = True
    reset_on_leave: bool = False
    hide_left_members: bool = True

    @field_validator("no_xp_channels", "no_xp_categories", "no_xp_roles")
    @classmethod
    def _dedupe_lists(cls, v: list[str]) -> list[str]:
        return _dedupe(v)

    @model_validator(mode="after")
    def _check(self) -> "LevelingConfig":
        if self.xp_min > self.xp_max:
            raise ValueError("Минимальный XP не может быть больше максимального")
        if self.announce_mode == "channel" and not self.announce_channel_id:
            raise ValueError("Выберите канал для уведомлений о новом уровне")
        return self


# ---------------------------------------------------------------- Модерация

class WarnAction(Strict):
    count: int = Field(ge=1, le=100)
    action: Literal["timeout", "kick", "ban"]
    duration_minutes: int = Field(60, ge=1, le=40320)  # Discord: тайм-аут до 28 дней


class ModerationConfig(Strict):
    enabled: bool = True
    log_channel_id: OptSnowflake = None
    dm_on_action: bool = True
    warn_actions: list[WarnAction] = Field(default_factory=list, max_length=20)
    warning_expiry_days: int = Field(0, ge=0, le=3650)  # 0 = не истекают

    @model_validator(mode="after")
    def _unique_counts(self) -> "ModerationConfig":
        counts = [a.count for a in self.warn_actions]
        if len(counts) != len(set(counts)):
            raise ValueError("Пороги предупреждений не должны повторяться")
        self.warn_actions.sort(key=lambda a: a.count)
        return self


# ---------------------------------------------------------------- Автомодерация

class RuleBase(Strict):
    enabled: bool = False
    delete_message: bool = True
    warn_user: bool = True  # короткое предупреждение в канале
    add_warning: bool = False  # запись в систему предупреждений
    exempt_roles: list[Snowflake] = Field(default_factory=list, max_length=100)
    exempt_channels: list[Snowflake] = Field(default_factory=list, max_length=200)


class SpamRule(RuleBase):
    max_messages: int = Field(6, ge=2, le=50)
    per_seconds: int = Field(5, ge=1, le=120)


class FloodRule(RuleBase):
    max_lines: int = Field(25, ge=2, le=500)
    max_length: int = Field(1800, ge=50, le=4000)
    max_repeated_chars: int = Field(30, ge=5, le=2000)


class DuplicateRule(RuleBase):
    max_duplicates: int = Field(3, ge=2, le=50)
    per_seconds: int = Field(30, ge=5, le=3600)


class MentionsRule(RuleBase):
    max_mentions: int = Field(6, ge=1, le=100)
    block_everyone: bool = True  # @everyone/@here от участников без права упоминания


class WordsRule(RuleBase):
    words: list[str] = Field(default_factory=list, max_length=1000)
    match_mode: Literal["word", "contains"] = "word"

    @field_validator("words")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        cleaned = [w.strip().lower() for w in v if w.strip()]
        for w in cleaned:
            if len(w) > 100:
                raise ValueError("Запрещённое слово не может быть длиннее 100 символов")
        return _dedupe(cleaned)


class LinksRule(RuleBase):
    allowed_domains: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("allowed_domains")
    @classmethod
    def _domains(cls, v: list[str]) -> list[str]:
        out = []
        for d in v:
            d = d.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
            if d:
                out.append(d.removeprefix("www."))
        return _dedupe(out)


class InvitesRule(RuleBase):
    allowed_codes: list[str] = Field(default_factory=list, max_length=100)


class Escalation(Strict):
    enabled: bool = True
    violations: int = Field(3, ge=1, le=100)
    window_minutes: int = Field(10, ge=1, le=1440)
    timeout_minutes: int = Field(10, ge=1, le=40320)


class AutomodConfig(Strict):
    enabled: bool = False
    ignore_moderators: bool = True  # участники с правом «Управлять сообщениями» не проверяются
    exempt_roles: list[Snowflake] = Field(default_factory=list, max_length=100)
    exempt_channels: list[Snowflake] = Field(default_factory=list, max_length=200)
    log_channel_id: OptSnowflake = None
    warn_message: str = Field("{user}, сообщение нарушает правило «{rule}».", max_length=500)
    spam: SpamRule = Field(default_factory=SpamRule)
    flood: FloodRule = Field(default_factory=FloodRule)
    duplicates: DuplicateRule = Field(default_factory=DuplicateRule)
    mentions: MentionsRule = Field(default_factory=MentionsRule)
    words: WordsRule = Field(default_factory=WordsRule)
    links: LinksRule = Field(default_factory=LinksRule)
    invites: InvitesRule = Field(default_factory=InvitesRule)
    escalation: Escalation = Field(default_factory=Escalation)


# ---------------------------------------------------------------- Приветствия

class CardConfig(Strict):
    """Картинка-баннер с аватаром и именем участника (как в MEE6)."""

    enabled: bool = False
    layout: Literal["center", "left"] = "center"
    font: Literal["segoe", "arial", "verdana", "tahoma", "trebuchet", "georgia", "impact", "comic"] = "segoe"
    title: str = Field("Добро пожаловать!", max_length=100)
    subtitle: str = Field("{username}", max_length=100)
    footer: str = Field("Ты наш {member_count}-й участник", max_length=150)
    text_color: Color = "#FFFFFF"
    background_color: Color = "#5865F2"
    background_color2: Color = "#23272A"
    gradient: bool = True
    background_image: str | None = Field(None, pattern=r"^[A-Za-z0-9_.-]{1,100}$")  # файл в data/cards
    overlay_opacity: int = Field(35, ge=0, le=90)  # затемнение картинки фона, %
    avatar_shape: Literal["circle", "rounded", "square"] = "circle"
    accent_color: Color = "#FFFFFF"  # цвет обводки аватара
    ring_width: int = Field(8, ge=0, le=20)


class GreetingMessage(Strict):
    enabled: bool = False
    channel_id: OptSnowflake = None
    message: str = Field("", max_length=2000)
    embed: EmbedConfig = Field(default_factory=EmbedConfig)
    card: CardConfig = Field(default_factory=CardConfig)

    @model_validator(mode="after")
    def _check(self) -> "GreetingMessage":
        if self.enabled and not self.channel_id:
            raise ValueError("Выберите канал для сообщения")
        if self.enabled and not self.message and not self.embed.enabled and not self.card.enabled:
            raise ValueError("Заполните текст сообщения, включите embed или картинку-баннер")
        return self


class WelcomeConfig(Strict):
    join: GreetingMessage = Field(
        default_factory=lambda: GreetingMessage(message="👋 Добро пожаловать на {server}, {user}! Нас уже {member_count}.")
    )
    leave: GreetingMessage = Field(
        default_factory=lambda: GreetingMessage(message="😢 {username} покинул(а) сервер.", card=CardConfig(
            title="До встречи!", footer="Теперь нас {member_count}", background_color="#ED4245"))
    )
    dm_enabled: bool = False
    dm_message: str = Field("", max_length=2000)


# ---------------------------------------------------------------- Автороли

class AutorolesConfig(Strict):
    enabled: bool = False
    roles: list[Snowflake] = Field(default_factory=list, max_length=20)
    bot_roles: list[Snowflake] = Field(default_factory=list, max_length=20)
    excluded_users: list[Snowflake] = Field(default_factory=list, max_length=500)
    delay_seconds: int = Field(0, ge=0, le=3600)


# ---------------------------------------------------------------- Пользовательские команды

class CustomCommandsConfig(Strict):
    enabled: bool = True
    prefix: str = Field("!", min_length=1, max_length=5)

    @field_validator("prefix")
    @classmethod
    def _prefix(cls, v: str) -> str:
        if any(c.isspace() for c in v):
            raise ValueError("Префикс не может содержать пробелы")
        return v


# ---------------------------------------------------------------- Журналы

LOG_EVENTS = {
    "message_delete": "Удаление сообщений",
    "message_edit": "Редактирование сообщений",
    "member_join": "Вступление участников",
    "member_leave": "Выход участников",
    "nickname_change": "Изменение никнеймов",
    "member_roles": "Изменение ролей участников",
    "role_change": "Создание/изменение/удаление ролей",
    "channel_change": "Изменения каналов",
    "guild_update": "Изменения настроек сервера",
    "moderation": "Модерационные действия",
    "automod": "Автомодерация",
}


class LogEventConfig(Strict):
    enabled: bool = False
    channel_id: OptSnowflake = None


class LoggingConfig(Strict):
    enabled: bool = True
    default_channel_id: OptSnowflake = None
    events: dict[str, LogEventConfig] = Field(default_factory=dict)
    ignored_channels: list[Snowflake] = Field(default_factory=list, max_length=200)

    @field_validator("events")
    @classmethod
    def _events(cls, v: dict[str, LogEventConfig]) -> dict[str, LogEventConfig]:
        unknown = set(v) - set(LOG_EVENTS)
        if unknown:
            raise ValueError(f"Неизвестные типы событий: {', '.join(sorted(unknown))}")
        return {k: v.get(k, LogEventConfig()) for k in LOG_EVENTS}

    @model_validator(mode="after")
    def _fill(self) -> "LoggingConfig":
        for k in LOG_EVENTS:
            self.events.setdefault(k, LogEventConfig())
        return self


# ---------------------------------------------------------------- Тикеты

class TicketsConfig(Strict):
    enabled: bool = False
    category_id: OptSnowflake = None  # категория для каналов тикетов; пусто — без категории
    support_roles: list[Snowflake] = Field(default_factory=list, max_length=20)
    log_channel_id: OptSnowflake = None  # сюда отправляются уведомления и стенограммы закрытых тикетов
    max_open_per_user: int = Field(1, ge=1, le=10)
    ask_topic: bool = True  # спрашивать тему в окне перед созданием тикета
    channel_name: str = Field("тикет-{number}", min_length=1, max_length=90)
    opening_message: str = Field(
        "{user}, спасибо за обращение! Опишите вопрос подробнее — команда поддержки скоро ответит.",
        max_length=2000)
    ping_support: bool = True
    transcript: bool = True
    delete_delay_seconds: int = Field(10, ge=0, le=3600)

    @field_validator("support_roles")
    @classmethod
    def _dedupe_roles(cls, v: list[str]) -> list[str]:
        return _dedupe(v)


# ---------------------------------------------------------------- Музыка

class MusicConfig(Strict):
    enabled: bool = True
    dj_roles: list[Snowflake] = Field(default_factory=list, max_length=20)  # пусто — управлять могут все в канале
    default_volume: int = Field(50, ge=1, le=200)
    max_queue: int = Field(100, ge=1, le=500)
    max_track_minutes: int = Field(0, ge=0, le=600)  # 0 — без ограничения
    idle_minutes: int = Field(5, ge=1, le=120)  # выйти из канала, если ничего не играет


# ---------------------------------------------------------------- Голосовые комнаты

class VoiceHub(Strict):
    """Канал-создатель: зашёл в него — бот создал личную голосовую комнату и перенёс туда."""

    channel_id: Snowflake
    category_id: OptSnowflake = None  # пусто — та же категория, что у канала-создателя
    name_template: str = Field("🔊 Комната {display_name}", min_length=1, max_length=100)
    user_limit: int = Field(0, ge=0, le=99)  # 0 — без ограничения
    owner_manage: bool = True  # владелец может переименовать, ограничить и выгонять из своей комнаты


class TempVoiceConfig(Strict):
    enabled: bool = False
    hubs: list[VoiceHub] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _unique(self) -> "TempVoiceConfig":
        ids = [h.channel_id for h in self.hubs]
        if len(ids) != len(set(ids)):
            raise ValueError("Один канал-создатель добавлен дважды")
        return self


# ---------------------------------------------------------------- Прочее

class UtilityConfig(Strict):
    reminders_enabled: bool = True
    polls_enabled: bool = True


# ---------------------------------------------------------------- Болтовня (бот сам вмешивается в чат)

DEFAULT_CHATTER_PROMPT = (
    "Ты — Мишаня, свой пацан в Discord-чате друзей. Иногда ты сам влезаешь в разговор.\n"
    "Пиши как живой участник чата: одно короткое сообщение (1–2 предложения, до 200 символов), по-русски, "
    "разговорно, с юмором, абсурдом и подколами по теме последних сообщений. Можно сленг и мат.\n"
    "Не говори, что ты ИИ или бот. Не здоровайся, не ставь кавычки и подписи, не пиши своё имя в начале.\n"
    "Без оскорблений по национальности, религии, ориентации и т. п., без угроз, не упоминай @everyone и @here."
)
DEFAULT_CHATTER_PHRASES = [
    "а я говорил", "ну это база", "кто-нибудь видел мои носки?", "так, я ничего не понял, но очень интересно",
    "согласен с предыдущим оратором, кем бы он ни был", "это всё масоны", "пацаны, а вы знали, что огурец на 95% вода?",
    "ору", "звучит как план", "я бы на вашем месте не стал", "ладно, я пошёл", "мда",
    "а можно то же самое, но с пельменями?", "вот из-за таких разговоров у меня и нет девушки", "лол",
]


class VoiceTrigger(Strict):
    """Фраза в голосовом канале → запуск игры Steam на компьютере, где работает бот."""
    phrase: str = Field(min_length=2, max_length=100)
    steam_app_id: int = Field(ge=1, le=99_999_999)
    title: str = Field("", max_length=100)


class PcProgram(Strict):
    """Программа, которую бот может открыть на компьютере владельца: путь к .exe/.lnk/.url или ссылка Steam."""
    name: str = Field(min_length=1, max_length=60)
    target: str = Field(min_length=3, max_length=400,
                        pattern=r"(?i)^(steam://rungameid/\d{1,10}|[A-Za-z]:\\[^<>\"|?*]+\.(exe|lnk|url))$")


class ChatterConfig(Strict):
    enabled: bool = False
    channels: list[Snowflake] = Field(default_factory=list, max_length=50)  # пусто — все текстовые каналы
    chance_percent: float = Field(3.0, ge=0.1, le=100)  # шанс вмешаться после каждого сообщения
    min_messages: int = Field(8, ge=1, le=500)  # не раньше, чем через столько сообщений после прошлого раза
    cooldown_minutes: int = Field(15, ge=0, le=1440)  # пауза между вмешательствами в одном канале
    max_per_hour: int = Field(6, ge=1, le=60)  # на весь сервер
    reply_on_mention: bool = True  # отвечать, если бота упомянули или ответили на его сообщение
    reply_to_message: bool = False  # вмешиваясь, отвечать «ответом» на последнее сообщение
    use_ai: bool = True
    system_prompt: str = Field(DEFAULT_CHATTER_PROMPT, max_length=4000)
    context_messages: int = Field(15, ge=1, le=50)
    temperature: float = Field(1.3, ge=0, le=2)
    max_tokens: int = Field(150, ge=16, le=1000)
    phrases: list[str] = Field(default_factory=lambda: list(DEFAULT_CHATTER_PHRASES), max_length=200)
    fallback_to_phrases: bool = True  # если DeepSeek недоступен — писать фразу из списка
    # Голос: бот сам иногда заходит в голосовой канал с людьми и что-нибудь говорит
    voice_enabled: bool = False
    voice_channels: list[Snowflake] = Field(default_factory=list, max_length=50)  # пусто — любой с людьми
    voice_interval_minutes: int = Field(30, ge=5, le=1440)  # в среднем раз во столько минут, если есть кому слушать
    voice_max_per_hour: int = Field(2, ge=1, le=12)
    voice_min_members: int = Field(1, ge=1, le=50)
    voice_lines: int = Field(2, ge=1, le=5)  # фраз за один заход
    # auto: Microsoft, при сбое — Google; elevenlabs/openai — платные быстрые голоса, при сбое — Microsoft
    voice_engine: Literal["auto", "edge", "google", "elevenlabs", "openai"] = "auto"
    voice_eleven_id: str = Field("pNInz6obpgDQGcFmaJgB", pattern=r"^[A-Za-z0-9]{10,40}$")  # Adam (мужской)
    voice_openai: str = Field("onyx", pattern=r"^[a-z]{2,20}$")
    voice_name: Literal["dmitry", "svetlana"] = "dmitry"  # голос Microsoft
    voice_rate: int = Field(10, ge=-50, le=100)  # скорость речи, %
    voice_pitch: int = Field(-5, ge=-50, le=50)  # высота голоса, Гц
    # Слушание: бот сидит в голосовом канале, распознаёт речь и отвечает, когда слышит слово-обращение
    listen_wake_words: list[str] = Field(default_factory=lambda: ["император"], max_length=20)
    listen_followup_seconds: int = Field(30, ge=0, le=300)  # после ответа слушает без обращения
    listen_min_seconds: float = Field(0.6, ge=0.2, le=5)  # короче — не распознаём (кашель, «ага»)
    listen_max_minutes: int = Field(60, ge=5, le=600)  # потом выходит, чтобы не тратить деньги впустую
    listen_auto: bool = False  # сам садиться в голосовой канал, где больше всего людей
    listen_interject_percent: int = Field(25, ge=0, le=100)  # шанс влезть в разговор без обращения
    listen_interject_seconds: int = Field(45, ge=10, le=3600)  # не чаще, чем раз во столько секунд
    # Диалог в чате
    wake_words_in_text: bool = True  # слова-обращения работают и в тексте («император, ты где?»)
    dialog_seconds: int = Field(120, ge=0, le=900)  # после ответа продолжает разговор с этим человеком без обращения
    reply_cooldown_seconds: int = Field(2, ge=0, le=120)  # между ответами одному человеку
    dialog_max_per_hour: int = Field(120, ge=1, le=600)  # ответов на обращения в час на сервер
    # Память: бот сам запоминает факты о людях и сервере и использует их в разговоре
    memory_enabled: bool = True
    memory_max: int = Field(300, ge=10, le=2000)
    # Картинки: смотрит скриншоты (нужна модель со зрением, VISION_MODEL в .env или в панели)
    vision_enabled: bool = True
    # Обиды: за издевательства и оскорбления — мут и снятие ролей до извинения в отдельном канале
    punish_enabled: bool = False
    punish_min_level: int = Field(2, ge=1, le=3)  # 1 — подколы, 2 — оскорбления, 3 — только жёсткие
    punish_mute_minutes: int = Field(120, ge=0, le=40320)  # максимум мута; срок решает сам бот. 0 — без мута
    punish_remove_roles: bool = True
    apology_channel_id: OptSnowflake = None
    punish_exempt_roles: list[Snowflake] = Field(default_factory=list, max_length=50)
    # Перемещение по голосовым каналам по просьбе в чате или голосом
    move_enabled: bool = True  # голосовые каналы: перекинуть, выкинуть, выключить микрофон
    move_others_requires_permission: bool = False  # True — чужих двигает, только если просит тот, у кого есть право
    # Власть бота: что он может делать сам, на своё усмотрение
    control_music: bool = True
    control_moderation: bool = True  # мут, ник, удаление сообщений
    control_roles: bool = True  # выдавать и забирать роли (кроме ролей с админскими правами)
    control_kick_ban: bool = True  # кикать и банить по своему решению
    kick_ban_per_hour: int = Field(3, ge=1, le=30)  # не больше на сервер в час — защита от безумия модели
    character_ignore: bool = True  # может игнорить людей, которые его достали
    # Доверенные: личная переписка с ботом и его доклады в ЛС
    confidants: list[Snowflake] = Field(default_factory=list, max_length=10)
    confidant_dm_chat: bool = True
    # Личка для всех: участникам сервера — без внутренних дел, посторонним — холодно; о любопытных — доклад
    dm_open: bool = True
    dm_max_per_hour: int = Field(40, ge=1, le=600)  # ответов одному человеку в час
    # Чужак долго переписывается и просится на сервер → бот сам решает, передать ли просьбу доверенным
    join_requests: bool = True
    join_min_messages: int = Field(8, ge=1, le=200)  # сколько он должен написать боту, прежде чем просьбу рассмотрят
    join_retry_hours: int = Field(6, ge=0, le=720)
    # Передать сообщение в личку кому-то с сервера по просьбе любого человека
    relay_dm: bool = True
    relay_per_hour: int = Field(5, ge=1, le=100)  # от одного человека в час — чтобы бота не превратили в спамера
    relay_anonymous: bool = False  # True — не писать получателю, от кого сообщение
    # Компьютер владельца: открыть или закрыть программу по просьбе доверенного (или владельца)
    pc_control: bool = True
    pc_start_menu: bool = True  # всё, что есть в меню «Пуск»
    pc_programs: list[PcProgram] = Field(default_factory=list, max_length=50)  # свои программы и игры  # после отказа доверенных можно просить снова через столько часов
    report_inactive_days: int = Field(7, ge=0, le=365)  # 0 — не докладывать
    gossip_hours: int = Field(12, ge=0, le=168)  # 0 — не сплетничать
    # Голосовые триггеры (работают, пока бот слушает)
    voice_triggers: list[VoiceTrigger] = Field(
        default_factory=lambda: [VoiceTrigger(phrase="аллаху акбар", steam_app_id=376210, title="The Isle")],
        max_length=20)
    trigger_cooldown_minutes: int = Field(10, ge=0, le=1440)

    @field_validator("confidants", "punish_exempt_roles")
    @classmethod
    def _dedupe_ids(cls, v: list[str]) -> list[str]:
        return _dedupe(v)

    @field_validator("listen_wake_words")
    @classmethod
    def _wake(cls, v: list[str]) -> list[str]:
        return [w.strip().lower()[:50] for w in v if w.strip()]

    @field_validator("phrases")
    @classmethod
    def _phrases(cls, v: list[str]) -> list[str]:
        out = [p.strip()[:500] for p in v if p.strip()]
        return out


MODULES: dict[str, type[Strict]] = {
    "leveling": LevelingConfig,
    "moderation": ModerationConfig,
    "automod": AutomodConfig,
    "welcome": WelcomeConfig,
    "autoroles": AutorolesConfig,
    "custom_commands": CustomCommandsConfig,
    "logging": LoggingConfig,
    "utility": UtilityConfig,
    "tickets": TicketsConfig,
    "music": MusicConfig,
    "tempvoice": TempVoiceConfig,
    "chatter": ChatterConfig,
}


def module_enabled(module: str, cfg: Strict) -> bool:
    if module == "welcome":
        return cfg.join.enabled or cfg.leave.enabled or cfg.dm_enabled  # type: ignore[attr-defined]
    if module == "utility":
        return True
    return bool(getattr(cfg, "enabled", True))


# ---------------------------------------------------------------- Глобальные настройки

class GlobalSettings(Strict):
    log_retention_days: int = Field(30, ge=1, le=3650)
    max_event_logs_per_guild: int = Field(20000, ge=100, le=1_000_000)
    store_message_content: bool = False
    auto_backup_enabled: bool = True
    auto_backup_interval_hours: int = Field(24, ge=1, le=720)
    auto_backup_keep: int = Field(10, ge=1, le=200)
    bot_status_text: str = Field("/help | Mishanya", max_length=128)
    auto_update_enabled: bool = True
    update_check_interval_hours: int = Field(6, ge=1, le=168)


# ---------------------------------------------------------------- Меню с кнопками

class MenuButtonConfig(Strict):
    label: str = Field("", max_length=80)
    emoji: str = Field("", max_length=64)
    style: Literal["primary", "secondary", "success", "danger", "link"] = "primary"
    action: Literal["link", "role", "reply", "ticket"] = "reply"
    url: str = Field("", max_length=512)
    role_id: OptSnowflake = None
    reply: str = Field("", max_length=2000)
    row: int | None = Field(None, ge=0, le=4)

    @model_validator(mode="after")
    def _check(self) -> "MenuButtonConfig":
        if not self.label and not self.emoji:
            raise ValueError("У кнопки должна быть подпись или эмодзи")
        if self.action == "link":
            if not self.url.startswith(("https://", "http://", "discord://")):
                raise ValueError(f"Кнопка «{self.label or self.emoji}»: ссылка должна начинаться с https://")
            self.style = "link"
        elif self.style == "link":
            raise ValueError(f"Кнопка «{self.label or self.emoji}»: стиль «ссылка» доступен только для кнопок-ссылок")
        if self.action == "role" and not self.role_id:
            raise ValueError(f"Кнопка «{self.label or self.emoji}»: выберите роль")
        if self.action == "reply" and not self.reply.strip():
            raise ValueError(f"Кнопка «{self.label or self.emoji}»: заполните текст ответа")
        return self


class RoleOption(Strict):
    role_id: Snowflake
    label: str = Field(min_length=1, max_length=100)
    description: str = Field("", max_length=100)
    emoji: str = Field("", max_length=64)


class RoleSelectConfig(Strict):
    """Выпадающий список ролей под сообщением: участник сам выбирает себе роли."""

    enabled: bool = False
    placeholder: str = Field("Выберите роль", max_length=150)
    options: list[RoleOption] = Field(default_factory=list, max_length=25)
    min_values: int = Field(0, ge=0, le=25)  # 0 — можно снять все роли из списка
    max_values: int = Field(1, ge=1, le=25)  # 1 — только одна роль из списка

    @model_validator(mode="after")
    def _check(self) -> "RoleSelectConfig":
        if not self.enabled:
            return self
        if not self.options:
            raise ValueError("Добавьте в выпадающий список хотя бы одну роль")
        ids = [o.role_id for o in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError("Роли в выпадающем списке не должны повторяться")
        self.max_values = min(self.max_values, len(self.options))
        if self.min_values > self.max_values:
            raise ValueError("Минимум выбранных ролей не может быть больше максимума")
        return self


# ---------------------------------------------------------------- Посты (оформленные сообщения)

UPLOAD_RE = r"^[a-z0-9-]{1,80}\.(png|jpg|gif|webp)$"
MAX_POST_EMBED_CHARS = 6000  # лимит Discord на суммарный текст всех embed одного сообщения


def _image_ref(v: str, what: str) -> str:
    """Картинка — ссылка https://… или загруженный файл «upload:имя»."""
    v = v.strip()
    if not v:
        return v
    if v.startswith("upload:"):
        if not re.match(UPLOAD_RE, v[7:]):
            raise ValueError(f"{what}: неверное имя загруженного файла")
        return v
    if not v.startswith(("https://", "http://")):
        raise ValueError(f"{what}: ссылка должна начинаться с https://")
    return v


class PostField(Strict):
    name: str = Field(min_length=1, max_length=256)
    value: str = Field(min_length=1, max_length=1024)
    inline: bool = False


class PostEmbed(Strict):
    color: Color = "#5865F2"
    author_name: str = Field("", max_length=256)
    author_icon: str = Field("", max_length=600)
    author_url: str = Field("", max_length=512)
    title: str = Field("", max_length=256)
    url: str = Field("", max_length=512)
    description: str = Field("", max_length=4096)
    fields: list[PostField] = Field(default_factory=list, max_length=25)
    thumbnail: str = Field("", max_length=600)
    image: str = Field("", max_length=600)
    footer: str = Field("", max_length=2048)
    footer_icon: str = Field("", max_length=600)
    timestamp: bool = False

    @field_validator("author_icon", "thumbnail", "image", "footer_icon")
    @classmethod
    def _img(cls, v: str) -> str:
        return _image_ref(v, "Изображение")

    @field_validator("author_url", "url")
    @classmethod
    def _link(cls, v: str) -> str:
        v = v.strip()
        if v and not v.startswith(("https://", "http://")):
            raise ValueError("Ссылка должна начинаться с https://")
        return v

    @property
    def text_length(self) -> int:
        return (len(self.author_name) + len(self.title) + len(self.description) + len(self.footer)
                + sum(len(f.name) + len(f.value) for f in self.fields))

    @property
    def empty(self) -> bool:
        return not (self.text_length or self.image or self.thumbnail)


class PostConfig(Strict):
    name: str = Field(min_length=1, max_length=100)
    channel_id: OptSnowflake = None
    content: str = Field("", max_length=2000)
    embeds: list[PostEmbed] = Field(default_factory=list, max_length=10)
    attachments: list[str] = Field(default_factory=list, max_length=10)  # «upload:имя» под сообщением
    ping: bool = False  # разрешить упоминаниям в тексте сообщения уведомлять участников и роли

    @field_validator("attachments")
    @classmethod
    def _att(cls, v: list[str]) -> list[str]:
        out = [_image_ref(x, "Вложение") for x in v if x.strip()]
        if any(not x.startswith("upload:") for x in out):
            raise ValueError("Вложения — только загруженные файлы")
        return out

    @model_validator(mode="after")
    def _limits(self) -> "PostConfig":
        total = sum(e.text_length for e in self.embeds)
        if total > MAX_POST_EMBED_CHARS:
            raise ValueError(f"Слишком много текста во всех блоках вместе: {total} из {MAX_POST_EMBED_CHARS} символов")
        if any(e.empty for e in self.embeds):
            raise ValueError("Есть пустой блок embed — заполните его или удалите")
        files = {x for e in self.embeds for x in (e.author_icon, e.thumbnail, e.image, e.footer_icon)
                 if x.startswith("upload:")} | set(self.attachments)
        if len(files) > 10:
            raise ValueError("Discord принимает не больше 10 файлов в одном сообщении")
        return self

    def uploads(self) -> list[str]:
        """Имена всех загруженных файлов, на которые ссылается пост (без повторов, по порядку)."""
        names: list[str] = []
        for e in self.embeds:
            for x in (e.author_icon, e.thumbnail, e.image, e.footer_icon):
                if x.startswith("upload:") and x[7:] not in names:
                    names.append(x[7:])
        for x in self.attachments:
            if x[7:] not in names:
                names.append(x[7:])
        return names


class MenuConfig(Strict):
    name: str = Field(min_length=1, max_length=100)
    channel_id: OptSnowflake = None
    content: str = Field("", max_length=2000)
    embed: EmbedConfig = Field(default_factory=EmbedConfig)
    buttons: list[MenuButtonConfig] = Field(default_factory=list, max_length=25)
    role_select: RoleSelectConfig = Field(default_factory=RoleSelectConfig)

    @model_validator(mode="after")
    def _rows(self) -> "MenuConfig":
        if self.role_select.enabled:
            # Список занимает первый ряд целиком, кнопкам остаются ряды 2–5.
            if len(self.buttons) > 20:
                raise ValueError("Вместе с выпадающим списком можно добавить не больше 20 кнопок")
            if any(b.row == 0 for b in self.buttons):
                raise ValueError("Первый ряд занят выпадающим списком ролей — выберите для кнопок ряды 2–5")
        counts: dict[int, int] = {}
        for b in self.buttons:
            if b.row is not None:
                counts[b.row] = counts.get(b.row, 0) + 1
        if any(n > 5 for n in counts.values()):
            raise ValueError("В одном ряду может быть не больше 5 кнопок")
        return self
