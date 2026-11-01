"""Болтовня: бот иногда сам вмешивается в разговор и отвечает, когда его упоминают."""
from __future__ import annotations

import asyncio
import io
import logging
import random
import time
from collections import deque

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ...schemas import ChatterConfig
from ...services.chatter import ChatterError, format_history, generate, pick_phrase
from ...services.music import VOICE_MISSING, ffmpeg_executable, voice_available
from ...services.tts import TtsError, ffmpeg_options, synthesize
from ..client import MishanyaBot
from ..utils import UserFacingError, ok_embed, require_guild, respond

log = logging.getLogger(__name__)
MENTION_COOLDOWN = 8  # секунд между ответами на упоминания одному участнику


class ChannelState:
    def __init__(self) -> None:
        self.messages_since = 0
        self.last_at = 0.0


class Chatter(commands.Cog):
    def __init__(self, bot: MishanyaBot):
        self.bot = bot
        self.app = bot.app
        self.channels: dict[int, ChannelState] = {}
        self.hourly: dict[int, deque[float]] = {}
        self.mention_cd: dict[tuple[int, int], float] = {}
        self.busy: set[int] = set()  # каналы, где ответ уже готовится
        self.speaking: set[int] = set()  # серверы, где бот сейчас говорит в голосовом канале
        self._tasks: set[asyncio.Task] = set()

    async def cog_load(self) -> None:
        self.voice_tick.start()

    async def cog_unload(self) -> None:
        self.voice_tick.cancel()

    def _background(self, coro) -> None:  # noqa: ANN001
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def handle_message(self, message: discord.Message) -> None:
        assert message.guild is not None
        cfg = await self.app.config.get_typed(message.guild.id, "chatter", ChatterConfig)
        if not cfg.enabled or not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return
        parent_id = getattr(message.channel, "parent_id", None)
        if cfg.channels and str(message.channel.id) not in cfg.channels and str(parent_id) not in cfg.channels:
            return
        if not message.content and not message.attachments:
            return
        state = self.channels.setdefault(message.channel.id, ChannelState())
        state.messages_since += 1
        now = time.monotonic()

        mentioned = cfg.reply_on_mention and self._addressed(message)
        if mentioned:
            key = (message.guild.id, message.author.id)
            if now - self.mention_cd.get(key, 0) < MENTION_COOLDOWN:
                return
            self.mention_cd[key] = now
        else:
            if state.messages_since < cfg.min_messages or now - state.last_at < cfg.cooldown_minutes * 60:
                return
            if random.random() * 100 >= cfg.chance_percent:
                return
        if message.channel.id in self.busy or not self._take_hourly(message.guild.id, cfg.max_per_hour, now):
            return
        perms = message.channel.permissions_for(message.guild.me)
        if not perms.send_messages:
            return
        state.messages_since, state.last_at = 0, now
        self.busy.add(message.channel.id)
        self._background(self._speak(message, cfg, mentioned))

    def _addressed(self, message: discord.Message) -> bool:
        me = self.bot.user
        if me is None:
            return False
        if me in message.mentions:
            return True
        ref = message.reference.resolved if message.reference else None
        return isinstance(ref, discord.Message) and ref.author.id == me.id

    def _take_hourly(self, guild_id: int, limit: int, now: float) -> bool:
        q = self.hourly.setdefault(guild_id, deque())
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    async def _speak(self, message: discord.Message, cfg: ChatterConfig, mentioned: bool) -> None:
        channel = message.channel
        try:
            text = await self.compose(channel, cfg, mentioned)  # type: ignore[arg-type]
            if not text:
                return
            kwargs: dict = {"allowed_mentions": discord.AllowedMentions.none()}
            if mentioned or cfg.reply_to_message:
                await message.reply(text, mention_author=False, **kwargs)
            else:
                await channel.send(text, **kwargs)
        except discord.HTTPException as exc:
            log.warning("Болтовня: не удалось отправить сообщение в #%s: %s", getattr(channel, "name", "?"), exc)
        except Exception:  # noqa: BLE001
            log.exception("Болтовня: ошибка")
        finally:
            self.busy.discard(channel.id)

    async def compose(self, channel: discord.TextChannel | discord.Thread, cfg: ChatterConfig,
                      mentioned: bool) -> str | None:
        """Придумывает реплику: DeepSeek по последним сообщениям или запасная фраза."""
        s = self.app.settings
        if cfg.use_ai and s.deepseek_api_key:
            history: list[tuple[str, str, bool]] = []
            if channel.permissions_for(channel.guild.me).read_message_history:
                async for m in channel.history(limit=cfg.context_messages):
                    history.append((m.author.display_name, m.clean_content, m.author.id == self.bot.user.id))  # type: ignore[union-attr]
                history.reverse()
            me = channel.guild.me.display_name
            try:
                async with channel.typing():
                    return await generate(api_key=s.deepseek_api_key, base_url=s.deepseek_base_url,
                                          model=s.deepseek_model, system_prompt=cfg.system_prompt,
                                          history=format_history(history, me), channel=channel.name, bot_name=me,
                                          mentioned=mentioned, temperature=cfg.temperature, max_tokens=cfg.max_tokens)
            except ChatterError as exc:
                log.warning("Болтовня: %s", exc)
                self.app.chatter_error = str(exc)
                if not cfg.fallback_to_phrases:
                    return None
        return pick_phrase(cfg.phrases)


    # ------------------------------------------------------------ голос

    @tasks.loop(minutes=1)
    async def voice_tick(self) -> None:
        """Раз в минуту с вероятностью 1/интервал решает, не зайти ли поболтать в голосовой канал."""
        for guild in self.bot.guilds:
            try:
                cfg = await self.app.config.get_typed(guild.id, "chatter", ChatterConfig)
                if not (cfg.enabled and cfg.voice_enabled) or guild.id in self.speaking or guild.voice_client:
                    continue
                if random.random() >= 1 / cfg.voice_interval_minutes:
                    continue
                channel = self._pick_voice_channel(guild, cfg)
                if channel is None or not voice_available():
                    continue
                if not self._take_hourly(-guild.id, cfg.voice_max_per_hour, time.monotonic()):
                    continue
                self._background(self.visit(channel, cfg))
            except Exception:  # noqa: BLE001
                log.exception("Болтовня в голосе: ошибка на сервере %s", guild.id)

    @voice_tick.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    @staticmethod
    def _humans(channel: discord.VoiceChannel | discord.StageChannel) -> list[discord.Member]:
        return [m for m in channel.members if not m.bot]

    def _pick_voice_channel(self, guild: discord.Guild, cfg: ChatterConfig) -> discord.VoiceChannel | None:
        options = []
        for ch in guild.voice_channels:
            if ch == guild.afk_channel or (cfg.voice_channels and str(ch.id) not in cfg.voice_channels):
                continue
            perms = ch.permissions_for(guild.me)
            if perms.connect and perms.speak and len(self._humans(ch)) >= cfg.voice_min_members:
                if ch.user_limit and len(ch.members) >= ch.user_limit and not perms.move_members:
                    continue
                options.append(ch)
        return random.choice(options) if options else None

    async def voice_lines(self, channel: discord.VoiceChannel, cfg: ChatterConfig, count: int) -> list[str]:
        """Придумывает, что сказать вслух: DeepSeek по составу канала и его текстовому чату, иначе фразы."""
        s = self.app.settings
        if cfg.use_ai and s.deepseek_api_key:
            history: list[tuple[str, str, bool]] = []
            if channel.permissions_for(channel.guild.me).read_message_history:
                try:
                    async for m in channel.history(limit=min(cfg.context_messages, 15)):
                        history.append((m.author.display_name, m.clean_content, m.author.id == self.bot.user.id))  # type: ignore[union-attr]
                except discord.HTTPException:
                    pass
                history.reverse()
            people = ", ".join(m.display_name for m in self._humans(channel)) or "никого"
            me = channel.guild.me.display_name
            task = (f"Ты зашёл в голосовой канал «{channel.name}», там сидят: {people}. Скажи вслух {count} "
                    f"{'фразу' if count == 1 else 'фразы подряд'} — они будут озвучены голосом. Каждая фраза с новой строки, "
                    "до 150 символов, без смайликов, разметки и ссылок, можно обращаться к сидящим по именам.")
            try:
                text = await generate(api_key=s.deepseek_api_key, base_url=s.deepseek_base_url, model=s.deepseek_model,
                                      system_prompt=cfg.system_prompt, history=format_history(history, me),
                                      channel=channel.name, bot_name=me, mentioned=False,
                                      temperature=cfg.temperature, max_tokens=max(cfg.max_tokens, 60 * count), task=task)
                lines = [ln.strip(" -•\t") for ln in text.splitlines() if ln.strip(" -•\t")]
                if lines:
                    return lines[:count]
            except ChatterError as exc:
                log.warning("Болтовня в голосе: %s", exc)
                self.app.chatter_error = str(exc)
                if not cfg.fallback_to_phrases:
                    return []
        return random.sample(cfg.phrases, min(count, len(cfg.phrases)))

    async def visit(self, channel: discord.VoiceChannel, cfg: ChatterConfig, lines: list[str] | None = None) -> list[str]:
        """Заходит в канал, говорит фразы и выходит. Возвращает сказанное."""
        guild = channel.guild
        if not voice_available():
            raise UserFacingError(VOICE_MISSING)
        if guild.id in self.speaking:
            raise UserFacingError("Бот уже говорит в голосовом канале.")
        if guild.voice_client is not None:
            raise UserFacingError("Бот уже в голосовом канале (например, играет музыку).")
        self.speaking.add(guild.id)
        vc: discord.VoiceClient | None = None
        try:
            lines = [ln for ln in (lines or await self.voice_lines(channel, cfg, cfg.voice_lines)) if ln.strip()]
            if not lines:
                return []
            audio: list[tuple[bytes, str]] = []
            for line in lines:
                try:
                    audio.append(await synthesize(line, cfg.voice_name, cfg.voice_rate, cfg.voice_pitch,
                                                  cfg.voice_engine))
                except TtsError as exc:
                    log.warning("Болтовня в голосе: %s", exc)
            if not audio:
                raise UserFacingError("Не удалось озвучить текст: сервис синтеза речи не ответил. Попробуйте ещё раз.")
            try:
                vc = await channel.connect(timeout=20, self_deaf=True)
            except asyncio.TimeoutError:
                raise UserFacingError("Не удалось зайти в голосовой канал: Discord не ответил за 20 секунд.") from None
            except discord.ClientException as exc:
                raise UserFacingError(f"Не удалось зайти в голосовой канал: {exc}") from None
            await asyncio.sleep(0.8)
            for data, engine in audio:
                if not vc.is_connected():
                    break
                done = asyncio.Event()
                loop = asyncio.get_running_loop()
                opts = ffmpeg_options(engine, cfg.voice_rate)
                source = await asyncio.to_thread(lambda d=data, o=opts: discord.FFmpegPCMAudio(
                    io.BytesIO(d), pipe=True, executable=ffmpeg_executable(), options=o))
                vc.play(source, after=lambda _err: loop.call_soon_threadsafe(done.set))
                try:
                    await asyncio.wait_for(done.wait(), 60)
                except asyncio.TimeoutError:
                    vc.stop()
                await asyncio.sleep(random.uniform(0.6, 1.4))
            log.info("Болтовня в голосе: сказал %d фраз в «%s» (%s)", len(audio), channel.name, guild.name)
            return lines
        finally:
            self.speaking.discard(guild.id)
            # Если пока бот говорил, кто-то включил музыку — остаёмся, иначе выходим.
            music = self.bot.get_cog("Music")
            if vc is not None and vc.is_connected() and not (music and guild.id in getattr(music, "players", {})):
                try:
                    await vc.disconnect(force=False)
                except Exception:  # noqa: BLE001
                    pass

    @app_commands.command(name="say", description="Бот зайдёт в ваш голосовой канал и скажет текст голосом")
    @app_commands.describe(text="Что сказать. Пусто — бот придумает сам")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def say(self, interaction: discord.Interaction, text: app_commands.Range[str, 1, 400] | None = None) -> None:
        guild, member = require_guild(interaction)
        channel = member.voice.channel if member.voice else None
        if not isinstance(channel, discord.VoiceChannel):
            raise UserFacingError("Зайдите в голосовой канал.")
        await interaction.response.defer(ephemeral=True)
        cfg = await self.app.config.get_typed(guild.id, "chatter", ChatterConfig)
        said = await self.visit(channel, cfg, [text] if text else None)
        await respond(interaction, embed=ok_embed("🗣️ Сказал:\n" + "\n".join(f"> {s}" for s in said) if said
                                                  else "Нечего сказать — список фраз пуст."), ephemeral=True)


async def setup(bot: MishanyaBot) -> None:
    await bot.add_cog(Chatter(bot))
