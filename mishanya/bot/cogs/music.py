"""Музыка из SoundCloud в голосовых каналах: /play, очередь, повтор, громкость, автовыход."""
from __future__ import annotations

import asyncio
import logging
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

from ...schemas import MusicConfig
from ...services.music import (
    FFMPEG_BEFORE,
    VOICE_MISSING,
    MusicError,
    MusicQueue,
    Track,
    ffmpeg_executable,
    format_duration,
    resolve_stream,
    search,
    voice_available,
)
from ..client import MishanyaBot
from ..utils import COLOR_INFO, UserFacingError, guarded, ok_embed, require_guild, respond, safe_send

log = logging.getLogger(__name__)
ALONE_LEAVE_SECONDS = 60
LOOP_NAMES = {"off": "выключен", "track": "текущий трек", "queue": "вся очередь"}


class GuildPlayer:
    def __init__(self, guild_id: int, volume: int):
        self.guild_id = guild_id
        self.queue = MusicQueue(volume=volume)
        self.text_channel_id: int | None = None
        self.started_at: float | None = None
        self.paused_at: float | None = None
        self.skip_requested = False
        self.idle_task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.np_message: discord.Message | None = None  # сообщение «Сейчас играет» с кнопками

    def cancel_idle(self) -> None:
        if self.idle_task is not None and not self.idle_task.done():
            self.idle_task.cancel()
        self.idle_task = None

    @property
    def elapsed(self) -> int:
        if self.started_at is None:
            return 0
        end = self.paused_at or time.monotonic()
        return int(end - self.started_at)


def track_line(t: Track) -> str:
    return f"[{t.title}]({t.page_url}) · {t.duration_text}" + (f" · <@{t.requester_id}>" if t.requester_id else "")


LOOP_NEXT = {"off": "track", "track": "queue", "queue": "off"}
LOOP_SHORT = {"off": "выкл", "track": "трек", "queue": "очередь"}
VOLUME_STEP = 10


def np_embed(player: GuildPlayer, paused: bool) -> discord.Embed:
    track = player.queue.current
    embed = discord.Embed(title="⏸️ На паузе" if paused else "🎵 Сейчас играет",
                          description=track_line(track) if track else "—", color=COLOR_INFO)
    if track and track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    parts = [track.artist] if track and track.artist else []
    parts += [f"🔊 {player.queue.volume}%", f"🔁 {LOOP_SHORT[player.queue.loop]}"]
    if player.queue.tracks:
        parts.append(f"в очереди: {len(player.queue.tracks)}")
    embed.set_footer(text=" · ".join(parts))
    return embed


class MusicControl(discord.ui.DynamicItem[discord.ui.Button],
                   template=r"mishanya:music:(?P<action>pause|skip|stop|loop|shuffle|queue|voldown|volup)"):
    """Кнопка под «Сейчас играет». Состояние берётся из плеера при нажатии, поэтому кнопки переживают перезапуск."""

    def __init__(self, action: str, button: discord.ui.Button | None = None):
        super().__init__(button or discord.ui.Button(custom_id=f"mishanya:music:{action}"))
        self.action = action

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item,  # noqa: ARG003
                             match: re.Match[str], /) -> "MusicControl":
        return cls(match["action"], item)  # type: ignore[arg-type]

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("Music")  # type: ignore[union-attr]
        if cog is not None:
            await guarded(interaction, cog.handle_control(interaction, self.action))


def controls_view(player: GuildPlayer, paused: bool) -> discord.ui.View:
    def btn(action: str, emoji: str, label: str | None, row: int,
            style: discord.ButtonStyle = discord.ButtonStyle.secondary, disabled: bool = False) -> MusicControl:
        return MusicControl(action, discord.ui.Button(custom_id=f"mishanya:music:{action}", emoji=emoji, label=label,
                                                      style=style, row=row, disabled=disabled))

    loop = player.queue.loop
    view = discord.ui.View(timeout=None)
    for item in (
        btn("pause", "▶️" if paused else "⏸️", "Продолжить" if paused else "Пауза", 0,
            discord.ButtonStyle.success if paused else discord.ButtonStyle.primary),
        btn("skip", "⏭️", "Пропустить", 0),
        btn("stop", "⏹️", "Стоп", 0, discord.ButtonStyle.danger),
        btn("loop", "🔂" if loop == "track" else "🔁", f"Повтор: {LOOP_SHORT[loop]}", 1,
            discord.ButtonStyle.success if loop != "off" else discord.ButtonStyle.secondary),
        btn("shuffle", "🔀", "Перемешать", 1, disabled=len(player.queue.tracks) < 2),
        btn("queue", "📜", "Очередь", 1),
        btn("voldown", "🔉", None, 1, disabled=player.queue.volume <= VOLUME_STEP),
        btn("volup", "🔊", None, 1, disabled=player.queue.volume >= 200),
    ):
        view.add_item(item)
    return view


class Music(commands.Cog):
    def __init__(self, bot: MishanyaBot):
        self.bot = bot
        self.app = bot.app
        self.players: dict[int, GuildPlayer] = {}

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(MusicControl)

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(MusicControl)
        for vc in list(self.bot.voice_clients):
            try:
                await vc.disconnect(force=True)
            except Exception:  # noqa: BLE001
                pass

    async def _cfg(self, guild_id: int) -> MusicConfig:
        return await self.app.config.get_typed(guild_id, "music", MusicConfig)

    def _player(self, guild_id: int, volume: int = 50) -> GuildPlayer:
        if guild_id not in self.players:
            self.players[guild_id] = GuildPlayer(guild_id, volume)
        return self.players[guild_id]

    @staticmethod
    def _voice(guild: discord.Guild) -> discord.VoiceClient | None:
        vc = guild.voice_client
        return vc if isinstance(vc, discord.VoiceClient) and vc.is_connected() else None

    # ------------------------------------------------------------ проверки

    async def _check_enabled(self, guild: discord.Guild) -> MusicConfig:
        cfg = await self._cfg(guild.id)
        if not cfg.enabled:
            raise UserFacingError("Музыка на этом сервере отключена.")
        return cfg

    def _require_same_channel(self, guild: discord.Guild, member: discord.Member) -> discord.VoiceClient:
        vc = self._voice(guild)
        if vc is None:
            raise UserFacingError("Сейчас ничего не играет.")
        if member.voice is None or member.voice.channel != vc.channel:
            raise UserFacingError(f"Зайдите в голосовой канал {vc.channel.mention}, чтобы управлять музыкой.")
        return vc

    def _require_dj(self, member: discord.Member, cfg: MusicConfig, player: GuildPlayer | None) -> None:
        if not cfg.dj_roles:
            return
        p = member.guild_permissions
        if p.administrator or p.manage_guild or any(str(r.id) in cfg.dj_roles for r in member.roles):
            return
        if player and player.queue.current and player.queue.current.requester_id == member.id:
            return  # свой трек можно пропустить
        raise UserFacingError("Управлять музыкой могут только участники с ролью диджея.")

    async def _connect(self, guild: discord.Guild, member: discord.Member) -> discord.VoiceClient:
        if member.voice is None or member.voice.channel is None:
            raise UserFacingError("Сначала зайдите в голосовой канал.")
        channel = member.voice.channel
        vc = self._voice(guild)
        if vc is not None:
            if vc.channel == channel:
                return vc
            if vc.is_playing() or vc.is_paused():
                raise UserFacingError(f"Бот уже играет музыку в {vc.channel.mention}.")
            await vc.move_to(channel)
            return vc
        perms = channel.permissions_for(guild.me)
        if not (perms.connect and perms.speak):
            raise UserFacingError(f"У бота нет прав подключаться и говорить в {channel.mention}.")
        try:
            return await channel.connect(timeout=20, self_deaf=True)
        except asyncio.TimeoutError:
            raise UserFacingError("Не удалось подключиться к голосовому каналу: Discord не ответил за 20 секунд. "
                                  "Проверьте, что антивирус или брандмауэр не блокирует Python (UDP-трафик).") from None
        except RuntimeError as exc:  # discord.py: нет PyNaCl/davey
            log.error("Голос недоступен: %s", exc)
            raise UserFacingError(VOICE_MISSING) from None
        except Exception as exc:  # noqa: BLE001
            log.exception("Сервер %s: ошибка подключения к голосовому каналу", guild.id)
            raise UserFacingError(f"Не удалось подключиться к голосовому каналу: {exc}") from None

    # ------------------------------------------------------------ воспроизведение

    async def _play_next(self, guild_id: int) -> None:
        guild = self.bot.get_guild(guild_id)
        player = self.players.get(guild_id)
        if guild is None or player is None:
            return
        async with player.lock:
            vc = self._voice(guild)
            if vc is None or vc.is_playing() or vc.is_paused():
                return
            for _ in range(5):  # несколько недоступных треков подряд пропускаем
                if player.skip_requested and player.queue.loop == "track":
                    player.queue.current = None  # /skip при повторе трека — к следующему
                player.skip_requested = False
                track = player.queue.next()
                if track is None:
                    player.started_at = None
                    await self._close_np(player)
                    self._schedule_idle(guild_id)
                    return
                try:
                    stream_url, _headers = await resolve_stream(track)
                    # Запуск FFmpeg — в отдельном потоке: на Windows антивирус может проверять ffmpeg.exe
                    # несколько секунд, и бот всё это время не отвечал бы ни на одну команду.
                    audio = await asyncio.to_thread(
                        lambda: discord.FFmpegPCMAudio(stream_url, executable=ffmpeg_executable(),
                                                       before_options=FFMPEG_BEFORE, options="-vn"))
                    source = discord.PCMVolumeTransformer(audio, volume=player.queue.volume / 100)
                except MusicError as exc:
                    await self._notify(guild, player, f"⚠️ Пропускаю «{track.title}»: {exc}")
                    player.queue.current = None
                    continue
                except Exception as exc:  # noqa: BLE001
                    log.exception("Сервер %s: не удалось подготовить трек %s", guild_id, track.page_url)
                    await self._notify(guild, player, f"⚠️ Пропускаю «{track.title}»: {exc}")
                    player.queue.current = None
                    continue
                player.cancel_idle()
                player.started_at, player.paused_at = time.monotonic(), None
                try:
                    vc.play(source, after=lambda err, gid=guild_id: self._after(gid, err))
                except Exception as exc:  # noqa: BLE001 — например, не загрузилась библиотека Opus
                    log.exception("Сервер %s: ошибка воспроизведения", guild_id)
                    source.cleanup()
                    player.queue.clear()
                    await self._notify(guild, player, f"❌ Не удалось включить музыку: {exc}")
                    return
                await self._close_np(player)
                player.np_message = await self._notify(guild, player, embed=np_embed(player, paused=False),
                                                       view=controls_view(player, paused=False))
                return
            player.queue.current = None
            self._schedule_idle(guild_id)

    async def _close_np(self, player: GuildPlayer, footer: str | None = None) -> None:
        """Убирает кнопки с прошлого сообщения «Сейчас играет» (трек закончился, музыка остановлена)."""
        msg, player.np_message = player.np_message, None
        if msg is None:
            return
        kwargs: dict = {"view": None}
        if footer and msg.embeds:
            embed = msg.embeds[0]
            embed.title = "🎵 Играло"
            embed.set_footer(text=footer)
            kwargs["embed"] = embed
        try:
            await msg.edit(**kwargs)
        except discord.HTTPException:
            pass

    async def _refresh_np(self, guild_id: int) -> None:
        """Обновляет кнопки и подпись «Сейчас играет» после паузы, повтора или громкости из команды/панели."""
        player = self.players.get(guild_id)
        guild = self.bot.get_guild(guild_id)
        vc = self._voice(guild) if guild else None
        if player is None or player.np_message is None or vc is None:
            return
        paused = vc.is_paused()
        try:
            await player.np_message.edit(embed=np_embed(player, paused), view=controls_view(player, paused))
        except discord.HTTPException:
            player.np_message = None

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not voice_available():
            log.warning("Музыка не будет работать: %s", VOICE_MISSING)

    def _after(self, guild_id: int, error: Exception | None) -> None:
        """Вызывается discord.py из потока воспроизведения после окончания трека."""
        if error:
            log.warning("Ошибка воспроизведения на сервере %s: %s", guild_id, error)
            player = self.players.get(guild_id)
            guild = self.bot.get_guild(guild_id)
            if player is not None and guild is not None:
                asyncio.run_coroutine_threadsafe(
                    self._notify(guild, player, f"⚠️ Ошибка воспроизведения: {error}"), self.bot.loop)
        asyncio.run_coroutine_threadsafe(self._play_next(guild_id), self.bot.loop)

    async def _notify(self, guild: discord.Guild, player: GuildPlayer, text: str | None = None,
                      embed: discord.Embed | None = None,
                      view: discord.ui.View | None = None) -> discord.Message | None:
        channel = guild.get_channel_or_thread(player.text_channel_id or 0)
        if channel is None or not hasattr(channel, "send"):
            return None
        kwargs: dict = {"content": text, "embed": embed, "allowed_mentions": discord.AllowedMentions.none()}
        if view is not None:
            kwargs["view"] = view
        return await safe_send(channel, **kwargs)  # type: ignore[arg-type]

    def _schedule_idle(self, guild_id: int, seconds: float | None = None) -> None:
        player = self.players.get(guild_id)
        if player is None:
            return
        player.cancel_idle()

        async def leave_later() -> None:
            delay = seconds
            if delay is None:
                delay = (await self._cfg(guild_id)).idle_minutes * 60
            await asyncio.sleep(delay)
            guild = self.bot.get_guild(guild_id)
            vc = self._voice(guild) if guild else None
            if vc is not None and not vc.is_playing():
                await self._disconnect(guild_id)  # type: ignore[arg-type]

        player.idle_task = asyncio.create_task(leave_later())

    async def _disconnect(self, guild_id: int) -> None:
        guild = self.bot.get_guild(guild_id)
        player = self.players.pop(guild_id, None)
        if player is not None:
            player.cancel_idle()
            player.queue.clear()
            await self._close_np(player, "👋 Бот вышел из голосового канала")
        vc = self._voice(guild) if guild else None
        if vc is not None:
            await vc.disconnect(force=False)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState) -> None:
        guild = member.guild
        if self.bot.user and member.id == self.bot.user.id:
            if after.channel is None:  # бота отключили вручную
                player = self.players.pop(guild.id, None)
                if player is not None:
                    player.cancel_idle()
                    await self._close_np(player, "👋 Бот отключён от голосового канала")
            return
        vc = self._voice(guild)
        if vc is None:
            return
        humans = [m for m in vc.channel.members if not m.bot]
        if not humans:
            self._schedule_idle(guild.id, ALONE_LEAVE_SECONDS)
        elif guild.id in self.players and (vc.is_playing() or vc.is_paused()):
            self.players[guild.id].cancel_idle()

    # ------------------------------------------------------------ управление (общие для команд и панели)

    def state(self, guild_id: int) -> dict:
        guild = self.bot.get_guild(guild_id)
        vc = self._voice(guild) if guild else None
        player = self.players.get(guild_id)

        def t(x: Track) -> dict:
            return {"title": x.title, "url": x.page_url, "artist": x.artist, "duration": x.duration,
                    "thumbnail": x.thumbnail, "requester_id": str(x.requester_id) if x.requester_id else None}

        current = player.queue.current if player and vc and (vc.is_playing() or vc.is_paused()) else None
        return {
            "connected": vc is not None, "channel": vc.channel.name if vc else None,
            "playing": bool(vc and vc.is_playing()), "paused": bool(vc and vc.is_paused()),
            "current": t(current) if current else None, "elapsed": player.elapsed if player and current else 0,
            "queue": [t(x) for x in player.queue.tracks] if player else [],
            "loop": player.queue.loop if player else "off", "volume": player.queue.volume if player else None,
            "voice_available": voice_available(),
        }

    async def control(self, guild_id: int, action: str, by: str | None = None) -> None:
        """Управление плеером из команд, кнопок и панели. by — кто нажал (для подписи под треком)."""
        guild = self.bot.get_guild(guild_id)
        vc = self._voice(guild) if guild else None
        player = self.players.get(guild_id)
        if vc is None:
            raise UserFacingError("Бот не находится в голосовом канале.")
        suffix = f" — {by}" if by else ""
        if action == "skip":
            if not (vc.is_playing() or vc.is_paused()):
                raise UserFacingError("Сейчас ничего не играет.")
            if player:
                player.skip_requested = True
                await self._close_np(player, f"⏭️ Пропущено{suffix}")
            vc.stop()
        elif action == "stop":
            if player:
                player.queue.clear()
                player.queue.loop = "off"
                await self._close_np(player, f"⏹️ Музыка остановлена{suffix}")
            vc.stop()
        elif action in ("loop", "voldown", "volup"):
            if player is None or player.queue.current is None:
                raise UserFacingError("Сейчас ничего не играет.")
            if action == "loop":
                player.queue.loop = LOOP_NEXT[player.queue.loop]
            else:
                step = VOLUME_STEP if action == "volup" else -VOLUME_STEP
                player.queue.volume = max(VOLUME_STEP, min(200, player.queue.volume + step))
                if isinstance(vc.source, discord.PCMVolumeTransformer):
                    vc.source.volume = player.queue.volume / 100
        elif action == "pause":
            if not vc.is_playing():
                raise UserFacingError("Сейчас ничего не играет.")
            vc.pause()
            if player:
                player.paused_at = time.monotonic()
        elif action == "resume":
            if not vc.is_paused():
                raise UserFacingError("Воспроизведение не на паузе.")
            vc.resume()
            if player and player.paused_at and player.started_at:
                player.started_at += time.monotonic() - player.paused_at
                player.paused_at = None
        elif action == "leave":
            await self._disconnect(guild_id)
        elif action == "shuffle":
            if not player or not player.queue.tracks:
                raise UserFacingError("Очередь пуста.")
            player.queue.shuffle()
        else:
            raise UserFacingError("Неизвестное действие.")
        if action not in ("skip", "stop", "leave"):
            await self._refresh_np(guild_id)

    async def handle_control(self, interaction: discord.Interaction, action: str) -> None:
        """Нажатие кнопки под «Сейчас играет»: те же проверки, что у slash-команд."""
        guild, member = require_guild(interaction)
        cfg = await self._check_enabled(guild)
        player = self.players.get(guild.id)
        if action == "queue":
            await respond(interaction, embed=self._queue_embed(player), ephemeral=True)
            return
        vc = self._require_same_channel(guild, member)
        if action != "pause":
            self._require_dj(member, cfg, player)
        if action == "pause":
            action = "resume" if vc.is_paused() else "pause"
        if player is not None and interaction.message is not None and player.np_message is None:
            player.np_message = interaction.message  # кнопки старого сообщения после перезапуска бота
        await self.control(guild.id, action, by=member.display_name)
        if not interaction.response.is_done():
            await interaction.response.defer()

    def _queue_embed(self, player: GuildPlayer | None) -> discord.Embed:
        if player is None or (player.queue.current is None and not player.queue.tracks):
            raise UserFacingError("Очередь пуста. Включите трек командой /play.")
        lines = []
        if player.queue.current:
            lines.append(f"**Сейчас:** {track_line(player.queue.current)}\n")
        for i, t in enumerate(list(player.queue.tracks)[:15], 1):
            lines.append(f"`{i}.` {track_line(t)}")
        rest = len(player.queue.tracks) - 15
        if rest > 0:
            lines.append(f"…и ещё {rest}")
        embed = discord.Embed(title="🎶 Очередь", description="\n".join(lines)[:4000], color=COLOR_INFO)
        embed.set_footer(text=f"Треков в очереди: {len(player.queue.tracks)} · "
                              f"длительность {format_duration(player.queue.total_seconds)} · "
                              f"повтор: {LOOP_NAMES[player.queue.loop]}")
        return embed

    async def voice_test(self, guild_id: int) -> dict:
        """Ненадолго заходит в пустой голосовой канал, чтобы проверить соединение с голосовыми серверами Discord."""
        name = "Подключение к голосовому каналу"
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return {"name": name, "ok": False, "detail": "бот не подключён к серверу"}
        if self._voice(guild) is not None:
            return {"name": name, "ok": True, "detail": "бот уже в голосовом канале — соединение работает"}
        channels = [c for c in guild.voice_channels
                    if c.permissions_for(guild.me).connect and c.permissions_for(guild.me).speak]
        if not channels:
            return {"name": name, "ok": False, "detail": "у бота нет права подключаться ни к одному голосовому каналу"}
        channel = next((c for c in channels if not c.members), channels[0])
        try:
            vc = await channel.connect(timeout=15, self_deaf=True)
        except asyncio.TimeoutError:
            return {"name": name, "ok": False, "detail": (
                f"#{channel.name}: голосовой сервер Discord не ответил за 15 секунд. Обычно это значит, что "
                "UDP-трафик голоса блокируется (провайдер, брандмауэр или антивирус). Включите VPN, который "
                "пропускает UDP (не только браузерный), или разрешите Python в брандмауэре Windows.")}
        except Exception as exc:  # noqa: BLE001
            log.exception("Проверка голоса на сервере %s", guild_id)
            return {"name": name, "ok": False, "detail": f"#{channel.name}: {exc}"}
        await vc.disconnect(force=True)
        return {"name": name, "ok": True, "detail": f"успешно зашёл в #{channel.name} и вышел"}

    # ------------------------------------------------------------ slash-команды

    @app_commands.command(name="play", description="Включить трек или плейлист с SoundCloud")
    @app_commands.describe(query="Название трека или ссылка на SoundCloud")
    @app_commands.guild_only()
    async def play(self, interaction: discord.Interaction, query: app_commands.Range[str, 1, 300]) -> None:
        guild, member = require_guild(interaction)
        # Отвечаем Discord сразу: на ответ даётся 3 секунды, а проверки и поиск могут занять дольше.
        try:
            await interaction.response.defer(thinking=True)
        except discord.NotFound:
            # Discord не дождался ответа (медленная сеть/VPN). Музыку всё равно включаем,
            # а ответы пишем обычными сообщениями в канал.
            log.warning("/play: Discord не дождался ответа за 3 секунды — отвечаю сообщением в канал")
            channel = interaction.channel
            try:
                await self._play(interaction, guild, member, query)
            except UserFacingError as exc:
                if channel is not None:
                    await safe_send(channel, embed=discord.Embed(description=f"❌ {member.mention}: {exc}",  # type: ignore[arg-type]
                                                                 color=discord.Color.red()))
            return
        await self._play(interaction, guild, member, query)

    async def _play(self, interaction: discord.Interaction, guild: discord.Guild, member: discord.Member,
                    query: str) -> None:
        text, idle = await self.enqueue(guild, member, query, interaction.channel_id)
        embed = discord.Embed(description=text, color=COLOR_INFO)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed)
        elif interaction.channel is not None:
            await safe_send(interaction.channel, embed=embed)  # type: ignore[arg-type]
        if idle:
            await self._play_next(guild.id)

    async def enqueue(self, guild: discord.Guild, member: discord.Member, query: str,
                      text_channel_id: int | None) -> tuple[str, bool]:
        """Ищет и ставит в очередь трек от имени участника. Возвращает (текст для чата, надо ли запускать)."""
        cfg = await self._check_enabled(guild)
        if not voice_available():
            raise UserFacingError(VOICE_MISSING)
        if member.voice is None or member.voice.channel is None:
            raise UserFacingError("Сначала зайдите в голосовой канал.")
        try:
            tracks = await search(query, member.id)
        except MusicError as exc:
            raise UserFacingError(str(exc)) from None
        if cfg.max_track_minutes:
            limit = cfg.max_track_minutes * 60
            long = [t for t in tracks if t.duration and t.duration > limit]
            tracks = [t for t in tracks if not (t.duration and t.duration > limit)]
            if not tracks:
                raise UserFacingError(f"Трек длиннее разрешённых {cfg.max_track_minutes} мин.")
        else:
            long = []
        vc = await self._connect(guild, member)
        player = self._player(guild.id, cfg.default_volume)
        player.text_channel_id = text_channel_id
        added = player.queue.add(tracks, cfg.max_queue)
        if not added:
            raise UserFacingError(f"Очередь заполнена (максимум {cfg.max_queue} треков).")
        idle = not (vc.is_playing() or vc.is_paused())
        if len(tracks) == 1:
            text = ("▶️ Включаю: " if idle else "➕ В очереди: ") + track_line(tracks[0])
        else:
            text = f"➕ Добавлено треков: **{added}**" + (f" (ещё {len(tracks) - added} не влезли в очередь)"
                                                        if added < len(tracks) else "")
        if long:
            text += f"\nПропущено длинных треков: {len(long)}"
        return text, idle

    async def set_volume(self, guild_id: int, percent: int) -> None:
        guild = self.bot.get_guild(guild_id)
        vc = self._voice(guild) if guild else None
        player = self.players.get(guild_id)
        if vc is None or player is None:
            raise UserFacingError("Сейчас ничего не играет.")
        player.queue.volume = max(1, min(200, percent))
        if isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = player.queue.volume / 100
        await self._refresh_np(guild_id)

    async def _simple(self, interaction: discord.Interaction, action: str, done: str, dj: bool = True) -> None:
        guild, member = require_guild(interaction)
        cfg = await self._check_enabled(guild)
        self._require_same_channel(guild, member)
        if dj:
            self._require_dj(member, cfg, self.players.get(guild.id))
        await self.control(guild.id, action, by=member.display_name)
        await respond(interaction, embed=ok_embed(done))

    @app_commands.command(name="skip", description="Пропустить текущий трек")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction) -> None:
        await self._simple(interaction, "skip", "⏭️ Трек пропущен.")

    @app_commands.command(name="stop", description="Остановить музыку и очистить очередь")
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        await self._simple(interaction, "stop", "⏹️ Музыка остановлена, очередь очищена.")

    @app_commands.command(name="pause", description="Поставить музыку на паузу")
    @app_commands.guild_only()
    async def pause(self, interaction: discord.Interaction) -> None:
        await self._simple(interaction, "pause", "⏸️ Пауза.", dj=False)

    @app_commands.command(name="resume", description="Продолжить воспроизведение")
    @app_commands.guild_only()
    async def resume(self, interaction: discord.Interaction) -> None:
        await self._simple(interaction, "resume", "▶️ Продолжаю.", dj=False)

    @app_commands.command(name="shuffle", description="Перемешать очередь")
    @app_commands.guild_only()
    async def shuffle(self, interaction: discord.Interaction) -> None:
        await self._simple(interaction, "shuffle", "🔀 Очередь перемешана.")

    @app_commands.command(name="leave", description="Выгнать бота из голосового канала")
    @app_commands.guild_only()
    async def leave(self, interaction: discord.Interaction) -> None:
        await self._simple(interaction, "leave", "👋 Отключаюсь.")

    @app_commands.command(name="loop", description="Режим повтора")
    @app_commands.describe(mode="Что повторять")
    @app_commands.choices(mode=[app_commands.Choice(name="выключить", value="off"),
                                app_commands.Choice(name="текущий трек", value="track"),
                                app_commands.Choice(name="всю очередь", value="queue")])
    @app_commands.guild_only()
    async def loop(self, interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
        guild, member = require_guild(interaction)
        cfg = await self._check_enabled(guild)
        self._require_same_channel(guild, member)
        player = self.players.get(guild.id)
        self._require_dj(member, cfg, player)
        if player is None:
            raise UserFacingError("Сейчас ничего не играет.")
        player.queue.loop = mode.value
        await self._refresh_np(guild.id)
        await respond(interaction, embed=ok_embed(f"🔁 Повтор: {LOOP_NAMES[mode.value]}."))

    @app_commands.command(name="volume", description="Громкость музыки")
    @app_commands.describe(percent="Громкость в процентах (1–200)")
    @app_commands.guild_only()
    async def volume(self, interaction: discord.Interaction, percent: app_commands.Range[int, 1, 200]) -> None:
        guild, member = require_guild(interaction)
        cfg = await self._check_enabled(guild)
        vc = self._require_same_channel(guild, member)
        player = self._player(guild.id, cfg.default_volume)
        self._require_dj(member, cfg, player)
        player.queue.volume = percent
        if isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = percent / 100
        await self._refresh_np(guild.id)
        await respond(interaction, embed=ok_embed(f"🔊 Громкость: {percent}%"))

    @app_commands.command(name="queue", description="Очередь треков")
    @app_commands.guild_only()
    async def queue_cmd(self, interaction: discord.Interaction) -> None:
        guild, _ = require_guild(interaction)
        embed = self._queue_embed(self.players.get(guild.id))
        await respond(interaction, embed=embed)

    @app_commands.command(name="nowplaying", description="Что сейчас играет")
    @app_commands.guild_only()
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        guild, _ = require_guild(interaction)
        player = self.players.get(guild.id)
        vc = self._voice(guild)
        if player is None or player.queue.current is None or vc is None:
            raise UserFacingError("Сейчас ничего не играет.")
        t = player.queue.current
        total = t.duration or 0
        pos = min(player.elapsed, total) if total else player.elapsed
        bar = ""
        if total:
            filled = round(pos / total * 16)
            bar = f"\n`{'▬' * filled}🔘{'▬' * (16 - filled)}` {format_duration(pos)} / {t.duration_text}"
        embed = discord.Embed(title="⏸️ На паузе" if vc.is_paused() else "🎵 Сейчас играет",
                              description=track_line(t) + bar, color=COLOR_INFO)
        if t.thumbnail:
            embed.set_thumbnail(url=t.thumbnail)
        await respond(interaction, embed=embed)


async def setup(bot: MishanyaBot) -> None:
    await bot.add_cog(Music(bot))
