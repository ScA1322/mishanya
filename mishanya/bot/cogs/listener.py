"""Слушание (эксперимент): бот сидит в голосовом канале, распознаёт речь и отвечает голосом на обращение.

Приём звука — discord-ext-voice-recv. Голос в Discord шифруется end-to-end (DAVE), а voice-recv этого
не умеет, поэтому после снятия транспортного шифрования расшифровываем кадр сами через сессию davey,
которую ведёт discord.py.
"""
from __future__ import annotations

import asyncio
import io
import logging
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ...schemas import ChatterConfig
from ...services.chatter import ChatterError, generate, pick_phrase
from ...services.music import VOICE_MISSING, ffmpeg_executable, voice_available
from ...services.stt import SttError, UtteranceCollector, pcm_to_wav, transcribe
from ...services.tts import TtsError, ffmpeg_options, synthesize
from ..client import MishanyaBot
from ..utils import UserFacingError, ok_embed, require_guild, respond

log = logging.getLogger(__name__)

OPUS_SILENCE = b"\xf8\xff\xfe"
DAVE_MAGIC = b"\xfa\xfa"  # маркер в конце кадра, зашифрованного DAVE
ALONE_LEAVE_SECONDS = 60
MAX_PARALLEL_STT = 2

try:
    from discord.ext import voice_recv
except ImportError:  # pragma: no cover — пакет ставится из requirements.txt
    voice_recv = None  # type: ignore[assignment]


def receive_available() -> bool:
    return voice_recv is not None and voice_available()


def install_dave_decrypt(vc) -> None:  # noqa: ANN001 — VoiceRecvClient
    """Встраивает расшифровку DAVE в приём voice-recv (он её не поддерживает)."""
    import davey

    decryptor = vc._reader.decryptor  # noqa: SLF001
    original = decryptor.decrypt_rtp
    failures = {"n": 0}

    def decrypt_rtp(packet):  # noqa: ANN001, ANN202
        data = original(packet)
        conn = vc._connection  # noqa: SLF001
        session = conn.dave_session
        if session is None or not conn.dave_protocol_version or data == OPUS_SILENCE:
            return data
        user_id = vc._ssrc_to_id.get(packet.ssrc)  # noqa: SLF001
        if user_id is None:
            # Чей это звук, ещё неизвестно — зашифрованный кадр не расшифровать, отдаём тишину.
            return OPUS_SILENCE if data.endswith(DAVE_MAGIC) else data
        try:
            return session.decrypt(user_id, davey.MediaType.audio, data)
        except Exception:  # noqa: BLE001
            if not data.endswith(DAVE_MAGIC) and session.can_passthrough(user_id):
                return data
            failures["n"] += 1
            if failures["n"] in (1, 100, 1000):
                log.warning("Слушание: не удалось расшифровать звук DAVE (%d раз)", failures["n"])
            return OPUS_SILENCE

    decryptor.decrypt_rtp = decrypt_rtp


class CollectorSink(voice_recv.AudioSink if voice_recv else object):  # type: ignore[misc]
    def __init__(self, collector: UtteranceCollector):
        super().__init__()
        self.collector = collector

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data) -> None:  # noqa: ANN001 — вызывается из потока приёма звука
        if user is not None and not getattr(user, "bot", False) and data.pcm:
            self.collector.feed(user.id, data.pcm, time.monotonic())

    def cleanup(self) -> None:
        self.collector.clear()


@dataclass
class Session:
    vc: discord.VoiceClient
    channel_id: int
    started: float
    collector: UtteranceCollector
    recent: deque = field(default_factory=lambda: deque(maxlen=30))  # (unix_time, имя, текст, обращение)
    dialog: deque = field(default_factory=lambda: deque(maxlen=12))  # реплики разговора для ИИ
    open_until: float = 0.0  # до этого момента отвечаем без слова-обращения
    alone_since: float | None = None
    replying: bool = False
    last_spoke: float = 0.0  # когда бот последний раз говорил
    heard_since: int = 0  # реплик людей с тех пор
    task: asyncio.Task | None = None
    error: str | None = None


class Listener(commands.Cog):
    def __init__(self, bot: MishanyaBot):
        self.bot = bot
        self.app = bot.app
        self.sessions: dict[int, Session] = {}
        self.stt_sem = asyncio.Semaphore(MAX_PARALLEL_STT)
        self._tasks: set[asyncio.Task] = set()

    async def cog_load(self) -> None:
        self.auto_join.start()

    async def cog_unload(self) -> None:
        self.auto_join.cancel()
        for gid in list(self.sessions):
            await self.stop(gid)

    def _bg(self, coro) -> None:  # noqa: ANN001
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _cfg(self, guild_id: int) -> ChatterConfig:
        return await self.app.config.get_typed(guild_id, "chatter", ChatterConfig)

    # ------------------------------------------------------------ запуск и остановка

    async def start(self, channel: discord.VoiceChannel) -> Session:
        guild = channel.guild
        if voice_recv is None:
            raise UserFacingError("Не установлен пакет для приёма звука (discord-ext-voice-recv). "
                                  "Перезапустите бота через start.bat — он доустановит зависимости.")
        if not voice_available():
            raise UserFacingError(VOICE_MISSING)
        if not self.app.settings.stt_key:
            raise UserFacingError("Не задан ключ для распознавания речи: вставьте ключ в разделе «Болтовня (ИИ)» "
                                  "или укажите STT_API_KEY в .env.")
        current = self.sessions.get(guild.id)
        if current is not None:
            if current.channel_id == channel.id and current.vc.is_connected():
                return current
            await self.stop(guild.id)
        perms = channel.permissions_for(guild.me)
        if not (perms.connect and perms.speak):
            raise UserFacingError(f"У бота нет прав заходить и говорить в «{channel.name}».")
        vc = guild.voice_client
        if vc is not None:
            if isinstance(vc, voice_recv.VoiceRecvClient) and vc.channel == channel:
                pass
            elif isinstance(vc, discord.VoiceClient) and (vc.is_playing() or vc.is_paused()):
                raise UserFacingError("Бот сейчас играет музыку в другом режиме. Остановите её (/stop, /leave) и "
                                      "повторите.")
            else:
                await vc.disconnect(force=True)
                vc = None
        if vc is None:
            try:
                vc = await channel.connect(cls=voice_recv.VoiceRecvClient, timeout=20, self_deaf=False)
            except asyncio.TimeoutError:
                raise UserFacingError("Не удалось зайти в голосовой канал: Discord не ответил за 20 секунд.") from None
        session = Session(vc=vc, channel_id=channel.id, started=time.monotonic(),
                          collector=UtteranceCollector(end_silence=0.6))
        vc.listen(CollectorSink(session.collector))  # type: ignore[attr-defined]
        install_dave_decrypt(vc)
        self.sessions[guild.id] = session
        session.task = asyncio.create_task(self._loop(guild.id))
        log.info("Слушание: начал слушать «%s» (%s)", channel.name, guild.name)
        return session

    async def stop(self, guild_id: int, reason: str = "") -> None:
        session = self.sessions.pop(guild_id, None)
        if session is None:
            return
        if session.task is not None and session.task is not asyncio.current_task():
            session.task.cancel()
        vc = session.vc
        try:
            if getattr(vc, "is_listening", lambda: False)():
                vc.stop_listening()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        music = self.bot.get_cog("Music")
        if vc.is_connected() and not (music and guild_id in getattr(music, "players", {})):
            try:
                await vc.disconnect(force=False)
            except Exception:  # noqa: BLE001
                pass
        log.info("Слушание: перестал слушать (%s)", reason or "по команде")

    # ------------------------------------------------------------ обработка речи

    async def _loop(self, guild_id: int) -> None:
        try:
            while True:
                await asyncio.sleep(0.25)
                session = self.sessions.get(guild_id)
                if session is None:
                    return
                vc = session.vc
                if not vc.is_connected():
                    await self.stop(guild_id, "бот отключён от канала")
                    return
                now = time.monotonic()
                cfg = await self._cfg(guild_id)
                if now - session.started > cfg.listen_max_minutes * 60:
                    await self.stop(guild_id, f"прошло {cfg.listen_max_minutes} мин")
                    return
                humans = [m for m in vc.channel.members if not m.bot]
                if not humans:
                    session.alone_since = session.alone_since or now
                    if now - session.alone_since > ALONE_LEAVE_SECONDS:
                        await self.stop(guild_id, "в канале никого")
                        return
                else:
                    session.alone_since = None
                for user_id, pcm, seconds in session.collector.pop_finished(now):
                    if seconds >= cfg.listen_min_seconds:
                        self._bg(self._handle(guild_id, user_id, pcm, cfg))
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            log.exception("Слушание: ошибка цикла")
            await self.stop(guild_id, "ошибка")

    async def _handle(self, guild_id: int, user_id: int, pcm: bytes, cfg: ChatterConfig) -> None:
        session = self.sessions.get(guild_id)
        guild = self.bot.get_guild(guild_id)
        if session is None or guild is None:
            return
        member = guild.get_member(user_id)
        name = member.display_name if member else f"участник {user_id}"
        s = self.app.settings
        t0 = time.monotonic()
        try:
            async with self.stt_sem:
                wav = await asyncio.to_thread(pcm_to_wav, pcm)
                text = await transcribe(wav, api_key=s.stt_key, base_url=s.stt_url, model=s.stt_model)
        except SttError as exc:
            session.error = str(exc)
            log.warning("Слушание: %s", exc)
            return
        session.error = None
        if not text:
            return
        low = text.lower()
        now = time.monotonic()
        addressed = any(w in low for w in cfg.listen_wake_words) or now < session.open_until
        session.recent.append((time.time(), name, text, addressed))
        session.dialog.append((name, text))
        session.heard_since += 1
        log.debug("Слушание: распознано за %.1f с: %s", time.monotonic() - t0, text[:100])
        mind = self.bot.get_cog("Mind")
        if mind is not None:
            mind.heard_speech(guild, session.vc.channel, user_id, name, text)  # type: ignore[attr-defined]
            for game in await mind.check_triggers(guild, text, cfg):  # type: ignore[attr-defined]
                session.recent.append((time.time(), "▶", f"запустил {game}", False))
        interject = (not addressed and session.heard_since >= 2 and cfg.listen_interject_percent > 0
                     and now - session.last_spoke >= cfg.listen_interject_seconds
                     and random.random() * 100 < cfg.listen_interject_percent)
        if (addressed or interject) and not session.replying:
            if addressed:
                log.info("Слушание: обращение от %s: %s", name, text[:200])
            session.replying = True
            try:
                spoke = await self._reply(guild, session, cfg, member, interject=not addressed)
            finally:
                session.replying = False
            if spoke:
                session.last_spoke, session.heard_since = time.monotonic(), 0
                session.open_until = time.monotonic() + cfg.listen_followup_seconds

    async def _reply(self, guild: discord.Guild, session: Session, cfg: ChatterConfig,
                     member: discord.Member | None = None, interject: bool = False) -> bool:
        """Отвечает голосом. Возвращает, сказал ли что-нибудь."""
        vc = session.vc
        if not vc.is_connected() or vc.is_playing():
            return False  # играет музыка — не перебиваем
        t0 = time.monotonic()
        s = self.app.settings
        me = guild.me.display_name
        text: str | None = None
        if cfg.use_ai and s.deepseek_api_key:
            dialog = "\n".join(f"{n}: {t}" for n, t in session.dialog)
            people = ", ".join(m.display_name for m in vc.channel.members if not m.bot)
            task = (f"Ты сидишь в голосовом канале «{vc.channel.name}» (там: {people}) и слышишь разговор — это "
                    "расшифровка речи, в ней бывают ошибки распознавания. "
                    + ("К тебе никто не обращался — влезь в разговор своей репликой по теме, если есть что сказать "
                       "смешного или дерзкого; если сказать нечего — оставь reply пустым. " if interject else
                       "Последняя реплика обращена к тебе — ответь на неё. ")
                    + "Говори вслух 1–2 короткими фразами (до 200 символов) — ответ озвучивается голосом, поэтому "
                    "без смайликов, разметки и ссылок.")
            mind = self.bot.get_cog("Mind")
            try:
                if mind is not None:  # с памятью и действиями: обиды, «перекинь меня в …»
                    text, notes = await mind.brain(guild, member, cfg, history=dialog,  # type: ignore[attr-defined]
                                                   channel_name=vc.channel.name, task=task, voice=True)
                    for note in notes:
                        session.recent.append((time.time(), "⚙", note, False))
                    if not text and notes:
                        text = "Сделано."
                else:
                    text = await generate(api_key=s.deepseek_api_key, base_url=s.deepseek_base_url,
                                          model=s.deepseek_model, system_prompt=cfg.system_prompt, history=dialog,
                                          channel=vc.channel.name, bot_name=me, mentioned=True,
                                          temperature=cfg.temperature, max_tokens=cfg.max_tokens, task=task)
            except ChatterError as exc:
                log.warning("Слушание: %s", exc)
                self.app.chatter_error = str(exc)
                if interject or not cfg.fallback_to_phrases:
                    return False
        if not text and not interject:
            text = pick_phrase(cfg.phrases)
        if not text:
            return False
        t_ai = time.monotonic()
        # Озвучиваем фразы параллельно и начинаем говорить, как только готова первая.
        parts = [p for p in re.split(r"(?<=[.!?…])\s+", text) if p.strip()] or [text]
        jobs = [asyncio.create_task(synthesize(p, cfg.voice_name, cfg.voice_rate, cfg.voice_pitch, cfg.voice_engine))
                for p in parts[:4]]
        session.dialog.append((me, text))
        session.recent.append((time.time(), me, text, False))
        loop = asyncio.get_running_loop()
        spoke = False
        for i, job in enumerate(jobs):
            try:
                data, engine = await job
            except TtsError as exc:
                log.warning("Слушание: %s", exc)
                continue
            if i == 0:
                log.info("Слушание: ответ за %.1f с (ИИ %.1f с, озвучка %.1f с)", time.monotonic() - t0,
                         t_ai - t0, time.monotonic() - t_ai)
            if not vc.is_connected() or (vc.is_playing() and not spoke):
                break
            done = asyncio.Event()
            opts = ffmpeg_options(engine, cfg.voice_rate)
            source = await asyncio.to_thread(lambda d=data, o=opts: discord.FFmpegPCMAudio(
                io.BytesIO(d), pipe=True, executable=ffmpeg_executable(), options=o))
            vc.play(source, after=lambda _e: loop.call_soon_threadsafe(done.set))
            spoke = True
            try:
                await asyncio.wait_for(done.wait(), 60)
            except asyncio.TimeoutError:
                vc.stop()
        for job in jobs:
            job.cancel()
        return spoke

    # ------------------------------------------------------------ автоматически и из Discord

    @tasks.loop(seconds=30)
    async def auto_join(self) -> None:
        for guild in self.bot.guilds:
            try:
                if guild.id in self.sessions or guild.voice_client is not None or not receive_available():
                    continue
                cfg = await self._cfg(guild.id)
                if not (cfg.enabled and cfg.listen_auto) or not self.app.settings.stt_key:
                    continue
                options = [c for c in guild.voice_channels if c != guild.afk_channel
                           and (not cfg.voice_channels or str(c.id) in cfg.voice_channels)
                           and c.permissions_for(guild.me).connect and c.permissions_for(guild.me).speak
                           and len([m for m in c.members if not m.bot]) >= cfg.voice_min_members]
                if options:
                    best = max(options, key=lambda c: (len([m for m in c.members if not m.bot]), random.random()))
                    await self.start(best)
            except UserFacingError as exc:
                log.info("Слушание: автозапуск не удался: %s", exc)
            except Exception:  # noqa: BLE001
                log.exception("Слушание: ошибка автозапуска")

    @auto_join.before_loop
    async def _ready(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="listen", description="Бот зайдёт в ваш голосовой канал и будет слушать (повторно — выйдет)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def listen(self, interaction: discord.Interaction) -> None:
        guild, member = require_guild(interaction)
        if guild.id in self.sessions:
            await self.stop(guild.id, f"команда от {member}")
            await respond(interaction, embed=ok_embed("👂 Больше не слушаю."), ephemeral=True)
            return
        channel = member.voice.channel if member.voice else None
        if not isinstance(channel, discord.VoiceChannel):
            raise UserFacingError("Зайдите в голосовой канал.")
        await interaction.response.defer(ephemeral=True)
        cfg = await self._cfg(guild.id)
        await self.start(channel)
        words = ", ".join(f"«{w}»" for w in cfg.listen_wake_words) or "—"
        await respond(interaction, embed=ok_embed(
            f"👂 Слушаю «{channel.name}». Отвечаю, когда слышу {words}. Остановить — ещё раз /listen."), ephemeral=True)

    # ------------------------------------------------------------ для панели

    def status(self, guild_id: int) -> dict:
        session = self.sessions.get(guild_id)
        base = {"available": receive_available(), "stt_configured": bool(self.app.settings.stt_key),
                "stt_model": self.app.settings.stt_model, "stt_url": self.app.settings.stt_url}
        if session is None:
            return {**base, "listening": False}
        ch = session.vc.channel
        return {**base, "listening": True, "channel_id": str(ch.id), "channel": ch.name,
                "minutes": int((time.monotonic() - session.started) / 60), "error": session.error,
                "recent": [{"time": t, "name": n, "text": x, "addressed": a} for t, n, x, a in list(session.recent)[::-1]]}


async def setup(bot: MishanyaBot) -> None:
    await bot.add_cog(Listener(bot))
