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
from ...services.tts import TtsError, ffmpeg_options, speak
from ..client import MishanyaBot
from ..utils import UserFacingError, ok_embed, require_guild, respond

log = logging.getLogger(__name__)

OPUS_SILENCE = b"\xf8\xff\xfe"
DAVE_MAGIC = b"\xfa\xfa"  # маркер в конце кадра, зашифрованного DAVE
ALONE_LEAVE_SECONDS = 60
MAX_PARALLEL_STT = 5
FALLBACK_STT = "gpt-4o-transcribe"
STALE_SECONDS = 12  # фраза ждала распознавания дольше — пропускаем: отвечать на старое бессмысленно
FRAME = 3840  # 20 мс стерео 48 кГц, 16 бит
SENTENCE_END = re.compile(r"[.!?…]+[»\")]*\s")  # конец предложения — можно отдавать в озвучку
MIN_SENTENCE = 12  # слишком короткие куски не озвучиваем отдельно — звучит рвано

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
    pending: tuple | None = None  # обращение, пришедшее, пока бот говорил: (участник, как обратились)
    heard_at: float = 0.0  # когда закончилась фраза, на которую отвечаем (для замера задержки)
    last_decided: float = 0.0  # когда бот последний раз решал, влезать ли в разговор
    last_spoke: float = 0.0  # когда бот последний раз говорил
    heard_since: int = 0  # реплик людей с тех пор
    task: asyncio.Task | None = None
    error: str | None = None


class DuckingMixer(discord.AudioSource):
    """Голос бота поверх музыки: на время речи музыка приглушается, звуки смешиваются. Подменяет источник плеера."""

    def __init__(self, music: discord.AudioSource, duck: float = 0.3):
        import threading

        self.music, self.duck = music, duck
        self.frames: deque[bytes] = deque()
        self.drained = threading.Event()

    def add(self, pcm: bytes) -> None:
        self.drained.clear()
        for i in range(0, len(pcm), FRAME):
            chunk = pcm[i:i + FRAME]
            self.frames.append(chunk + b"\0" * (FRAME - len(chunk)))

    def read(self) -> bytes:
        from ...services.stt import audioop

        m = self.music.read()
        if not self.frames:
            self.drained.set()
            return m
        t = self.frames.popleft()
        if not self.frames:
            self.drained.set()
        if not m or audioop is None:
            return t
        m = m[:FRAME] + b"\0" * (FRAME - len(m))
        return audioop.add(audioop.mul(m, 2, self.duck), t, 2)

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        self.music.cleanup()


async def decode_pcm(data: bytes, opts: str) -> bytes:
    """MP3 → PCM 48 кГц стерео (как ждёт Discord), с теми же фильтрами, что при обычном воспроизведении."""
    import shlex

    filters = shlex.split(opts)
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_executable(), "-loglevel", "error", "-i", "pipe:0", *filters, "-f", "s16le", "-ar", "48000", "-ac", "2",
        "pipe:1", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate(data)
    return out


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
        cfg = await self._cfg(guild.id)
        session = Session(vc=vc, channel_id=channel.id, started=time.monotonic(),
                          collector=UtteranceCollector(end_silence=cfg.listen_end_silence_ms / 1000))
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
                        self._bg(self._handle(guild_id, user_id, pcm, cfg, now))
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            log.exception("Слушание: ошибка цикла")
            await self.stop(guild_id, "ошибка")

    async def _handle(self, guild_id: int, user_id: int, pcm: bytes, cfg: ChatterConfig,
                      queued: float | None = None) -> None:
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
                if queued is not None and time.monotonic() - queued > STALE_SECONDS:
                    log.debug("Слушание: пропускаю фразу — ждала распознавания %.0f с", time.monotonic() - queued)
                    return
                wav = await asyncio.to_thread(pcm_to_wav, pcm)
                try:
                    text = await transcribe(wav, api_key=s.stt_key, base_url=s.stt_url, model=s.stt_model)
                except SttError as exc:
                    if s.stt_model_explicit or s.stt_model == FALLBACK_STT or "ключ" in str(exc):
                        raise
                    log.warning("Слушание: модель %s не подошла (%s) — перехожу на %s", s.stt_model, exc, FALLBACK_STT)
                    s.stt_model = FALLBACK_STT
                    text = await transcribe(wav, api_key=s.stt_key, base_url=s.stt_url, model=s.stt_model)
        except SttError as exc:
            session.error = str(exc)
            log.warning("Слушание: %s", exc)
            return
        session.error = None
        if not text:
            return
        now = time.monotonic()
        mind = self.bot.get_cog("Mind")
        how = mind.address(text, member, cfg) if mind is not None else (  # type: ignore[attr-defined]
            "wake" if any(w in text.lower() for w in cfg.listen_wake_words) else None)
        addressed = how is not None or now < session.open_until
        session.recent.append((time.time(), name, text, addressed))
        session.dialog.append((name, text))
        session.heard_since += 1
        log.info("Слушание: %s (распознано за %.1f с): %s", name, time.monotonic() - t0, text[:150])
        if mind is not None:
            mind.heard_speech(guild, session.vc.channel, user_id, name, text)  # type: ignore[attr-defined]
            for game in await mind.check_triggers(guild, text, cfg):  # type: ignore[attr-defined]
                session.recent.append((time.time(), "▶", f"запустил {game}", False))
        quiet = now - max(session.last_spoke, session.last_decided) >= cfg.listen_interject_seconds
        if cfg.listen_interject_mode == "ai":
            interject = not addressed and quiet  # сам решит, есть ли что сказать (может промолчать)
        elif cfg.listen_interject_mode == "chance":
            interject = (not addressed and session.heard_since >= 2 and quiet
                         and random.random() * 100 < cfg.listen_interject_percent)
        else:
            interject = False
        if addressed:
            log.info("Слушание: обращение от %s: %s", name, text[:200])
        heard = queued or now
        if session.replying:
            if addressed:  # говорит — ответит сразу после, не потеряем (и с учётом сказанного)
                session.pending = (member, how, heard)
            return
        if not (addressed or interject):
            return
        session.replying = True
        try:
            todo: tuple | None = (member, how, heard, not addressed)
            while todo is not None:
                who, kind, heard_at, inter = todo
                if inter:
                    session.last_decided = time.monotonic()
                session.heard_at = heard_at
                spoke = await self._reply(guild, session, cfg, who, interject=inter, how=kind)
                if spoke:
                    session.last_spoke, session.heard_since = time.monotonic(), 0
                    session.open_until = time.monotonic() + cfg.listen_followup_seconds
                todo = (*session.pending, False) if session.pending else None
                session.pending = None
        finally:
            session.replying = False

    async def _reply(self, guild: discord.Guild, session: Session, cfg: ChatterConfig,
                     member: discord.Member | None = None, interject: bool = False, how: str | None = None) -> bool:
        """Отвечает голосом: озвучивает фразы по мере того, как ИИ их пишет (поверх музыки, если играет).
        Возвращает, сказал ли что-нибудь."""
        vc = session.vc
        if not vc.is_connected():
            return False
        t0 = time.monotonic()
        s = self.app.settings
        me = guild.me.display_name
        queue: asyncio.Queue = asyncio.Queue()
        stats = {"first_text": None, "first_audio": None}
        player = asyncio.create_task(self._play_queue(vc, queue, cfg, stats, session))
        buf = {"text": "", "said": ""}

        def push(sentence: str) -> None:
            sentence = sentence.strip()
            if sentence:
                buf["said"] += sentence + " "
                queue.put_nowait(asyncio.create_task(speak(sentence, cfg)))

        async def on_reply(piece: str) -> None:
            if stats["first_text"] is None:
                stats["first_text"] = time.monotonic()
            buf["text"] += piece
            while True:  # готовое предложение — сразу в озвучку, не дожидаясь остального ответа
                m = SENTENCE_END.search(buf["text"])
                if not m or m.end() < MIN_SENTENCE:
                    break
                push(buf["text"][:m.end()])
                buf["text"] = buf["text"][m.end():]

        text: str | None = None
        ai_failed = not (cfg.use_ai and s.deepseek_api_key)
        try:
            if cfg.use_ai and s.deepseek_api_key:
                dialog = "\n".join(f"{n}: {t}" for n, t in session.dialog)
                people = ", ".join(m.display_name for m in vc.channel.members if not m.bot)
                task = (f"Ты сидишь в голосовом канале «{vc.channel.name}» (там: {people}) и слышишь разговор — это "
                        "расшифровка речи, в ней бывают ошибки распознавания. Это живой разговор вслух: отвечай сразу и "
                        "коротко, первая фраза — самая важная. "
                        + ("К тебе прямо не обращались. Реши сам: если последняя реплика всё-таки тебе (по смыслу), "
                           "или тебе реально интересно, или есть что сказать смешного и дерзкого — скажи. Если нет — "
                           "оставь reply пустым, молчать нормально, не влезай ради галочки. " if interject else
                           "Последняя реплика обращена к тебе — ответь на неё. ")
                        + (self.bot.get_cog("Mind").alias_scold(cfg) + " " if how == "alias_bad" else "")  # type: ignore[union-attr]
                        + "Говори вслух 1–2 короткими фразами (до 200 символов) — ответ озвучивается голосом, поэтому "
                        "без смайликов, разметки и ссылок.")
                mind = self.bot.get_cog("Mind")
                try:
                    if mind is not None:  # с памятью и действиями: обиды, музыка, «открой спотик»…
                        text, notes = await mind.brain(guild, member, cfg, history=dialog,  # type: ignore[attr-defined]
                                                       channel_name=vc.channel.name, task=task, voice=True,
                                                       on_reply=on_reply)
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
                    ai_failed = True
                    if interject or not cfg.fallback_to_phrases:
                        return False
            if not text and ai_failed and not interject:
                text = pick_phrase(cfg.phrases)
            if buf["said"] or buf["text"].strip():
                push(buf["text"])  # хвост, который пришёл без точки в конце
            elif text:  # ответ пришёл не потоком (картинки, запасная фраза) — режем на фразы сами
                for part in re.split(r"(?<=[.!?…])\s+", text)[:4]:
                    push(part)
            said = (buf["said"] or text or "").strip()
            if said:
                session.dialog.append((me, said))
                session.recent.append((time.time(), me, said, False))
        finally:
            queue.put_nowait(None)
        spoke = await player
        if spoke and stats["first_audio"]:
            ft = stats["first_text"]
            log.info("Слушание: голос через %.1f с после конца фразы (распознавание %.1f с, ИИ до первых слов %s, "
                     "озвучка первой фразы %.1f с)", stats["first_audio"] - (session.heard_at or t0),
                     t0 - (session.heard_at or t0), f"{ft - t0:.1f} с" if ft else "—",
                     stats["first_audio"] - (ft or t0))
        return spoke

    async def _play_queue(self, vc, queue: asyncio.Queue, cfg: ChatterConfig, stats: dict,  # noqa: ANN001
                          session: Session | None = None) -> bool:
        """Проигрывает озвученные фразы строго по порядку, пока в очереди не окажется None. Не перебивает: ждёт,
        пока люди договорят; если человек продолжил мысль — бросает устаревший ответ; если бота перебили — замолкает."""
        loop = asyncio.get_running_loop()
        spoke = False
        dropped = False

        async def drain() -> None:
            while True:
                job = await queue.get()
                if job is None:
                    return
                job.cancel()

        while True:
            job = await queue.get()
            if job is None:
                return spoke
            if dropped:
                job.cancel()
                continue
            if session is not None and not spoke:
                # Кто-то ещё говорит — ждём паузу (до 8 с), а не влезаем посреди фразы.
                waited = time.monotonic()
                while session.collector.speaking(time.monotonic()) and time.monotonic() - waited < 8:
                    await asyncio.sleep(0.1)
                if session.pending is not None:  # человек договорил ещё что-то — ответим на всё сразу
                    log.info("Слушание: собеседник продолжил мысль — отвечу на всё целиком")
                    job.cancel()
                    dropped = True
                    await drain()
                    return spoke
            try:
                data, engine = await job
            except TtsError as exc:
                log.warning("Слушание: %s", exc)
                continue
            except asyncio.CancelledError:
                continue
            if not vc.is_connected():
                continue
            if stats["first_audio"] is None:
                stats["first_audio"] = time.monotonic()
            opts = ffmpeg_options(engine, cfg.voice_rate)
            if vc.is_playing():
                # Играет музыка: говорим поверх неё, приглушив на время речи.
                pcm = await decode_pcm(data, opts)
                if not pcm or not vc.is_playing():
                    continue
                mixer = vc.source if isinstance(vc.source, DuckingMixer) else DuckingMixer(vc.source)
                mixer.add(pcm)
                if vc.source is not mixer:
                    vc.source = mixer
                spoke = True
                await asyncio.to_thread(mixer.drained.wait, 60)
                if vc.is_playing() and vc.source is mixer:
                    vc.source = mixer.music
                continue
            done = asyncio.Event()
            source = await asyncio.to_thread(lambda d=data, o=opts: discord.FFmpegPCMAudio(
                io.BytesIO(d), pipe=True, executable=ffmpeg_executable(), options=o))
            vc.play(source, after=lambda _e: loop.call_soon_threadsafe(done.set))
            spoke = True
            started = time.monotonic()
            while not done.is_set() and time.monotonic() - started < 60:
                await asyncio.sleep(0.1)
                # Перебили (говорят дольше ~0,3 с) — замолкаем и слушаем, как живой человек.
                if session is not None and cfg.listen_barge_in and session.collector.speaking(
                        time.monotonic(), window=0.25, min_voiced=15):
                    log.info("Слушание: меня перебили — замолкаю")
                    vc.stop()
                    await drain()
                    return spoke
            if not done.is_set():
                vc.stop()

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

    @app_commands.command(name="audience", description="Попросить аудиенции у Императора — он решит, зайти ли к вам в войс")
    @app_commands.guild_only()
    async def audience(self, interaction: discord.Interaction) -> None:
        guild, member = require_guild(interaction)
        channel = member.voice.channel if member.voice else None
        if not isinstance(channel, discord.VoiceChannel):
            raise UserFacingError("Сначала зайдите в голосовой канал — аудиенции даются там.")
        cfg = await self._cfg(guild.id)
        mind = self.bot.get_cog("Mind")
        await interaction.response.defer()
        allowed, attitude = await mind.may_summon(member, cfg) if mind else (True, 0)  # type: ignore[attr-defined]
        text = None
        if mind is not None and cfg.use_ai and self.app.settings.deepseek_api_key:
            task = (f"{member.display_name} просит у тебя аудиенции — зайти к нему в голосовой канал «{channel.name}». "
                    + ("Ты соглашаешься (зайдёшь сразу после ответа) — скажи что-нибудь величественное, одно предложение."
                       if allowed else f"Ты ОТКАЗЫВАЕШЬ: он недостоин (твоё отношение к нему {attitude:+d}). Съязви, "
                                       "одно-два предложения."))
            try:
                text = await mind._llm(cfg, history="", channel="аудиенция", task=task,  # type: ignore[attr-defined]  # noqa: SLF001
                                       bot_name=guild.me.display_name, context=mind.family_context(member, cfg, guild))  # type: ignore[attr-defined]
            except ChatterError:
                text = None
        if allowed:
            await self.start(channel)
            await respond(interaction, content=f"🎙️ {text or 'Император снизошёл.'}")
        else:
            await respond(interaction, content=f"🚫 {text or 'Император не принимает. Заслужи сначала.'}")

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
