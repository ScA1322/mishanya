"""Pydantic-схемы настроек модулей сервера.

Discord ID везде передаются строками: JavaScript теряет точность чисел больше 2^53.
"""
from __future__ import annotations

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


# ---------------------------------------------------------------- Прочее

class UtilityConfig(Strict):
    reminders_enabled: bool = True
    polls_enabled: bool = True


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
