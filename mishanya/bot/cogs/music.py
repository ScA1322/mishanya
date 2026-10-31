"""Музыка из SoundCloud в голосовых каналах: /play, очередь, повтор, громкость, автовыход."""
from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from ...schemas import MusicConfig
from ...services.music import (
    FFMPEG_BEFORE,
    MusicError,
    MusicQueue,
    Track,
    ffmpeg_executable,
    format_duration,
    resolve_stream,
    search,
)
from ..client import MishanyaBot
from ..utils import COLOR_INFO, UserFacingError, ok_embed, require_guild, respond, safe_send

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


class Music(commands.Cog):
    def __init__(self, bot: MishanyaBot):
        self.bot = bot
        self.app = bot.app
        self.players: dict[int, GuildPlayer] = {}

    async def cog_unload(self) -> None:
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
        except (asyncio.TimeoutError, discord.ClientException) as exc:
            raise UserFacingError(f"Не удалось подключиться к голосовому каналу: {exc or 'тайм-аут'}") from None

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
                    self._schedule_idle(guild_id)
                    return
                try:
                    stream_url, _headers = await resolve_stream(track)
                    source = discord.PCMVolumeTransformer(
                        discord.FFmpegPCMAudio(stream_url, executable=ffmpeg_executable(),
                                               before_options=FFMPEG_BEFORE, options="-vn"),
                        volume=player.queue.volume / 100)
                except MusicError as exc:
                    await self._notify(guild, player, f"⚠️ Пропускаю «{track.title}»: {exc}")
                    player.queue.current = None
                    continue
                player.cancel_idle()
                player.started_at, player.paused_at = time.monotonic(), None
                vc.play(source, after=lambda err, gid=guild_id: self._after(gid, err))
                embed = discord.Embed(title="🎵 Сейчас играет", description=track_line(track), color=COLOR_INFO)
                if track.artist:
                    embed.set_footer(text=track.artist)
                if track.thumbnail:
                    embed.set_thumbnail(url=track.thumbnail)
                await self._notify(guild, player, embed=embed)
                return
            player.queue.current = None
            self._schedule_idle(guild_id)

    def _after(self, guild_id: int, error: Exception | None) -> None:
        """Вызывается discord.py из потока воспроизведения после окончания трека."""
        if error:
            log.warning("Ошибка воспроизведения на сервере %s: %s", guild_id, error)
        asyncio.run_coroutine_threadsafe(self._play_next(guild_id), self.bot.loop)

    async def _notify(self, guild: discord.Guild, player: GuildPlayer, text: str | None = None,
                      embed: discord.Embed | None = None) -> None:
        channel = guild.get_channel_or_thread(player.text_channel_id or 0)
        if channel is not None and hasattr(channel, "send"):
            await safe_send(channel, content=text, embed=embed,  # type: ignore[arg-type]
                            allowed_mentions=discord.AllowedMentions.none())

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
        }

    async def control(self, guild_id: int, action: str) -> None:
        guild = self.bot.get_guild(guild_id)
        vc = self._voice(guild) if guild else None
        player = self.players.get(guild_id)
        if vc is None:
            raise UserFacingError("Бот не находится в голосовом канале.")
        if action == "skip":
            if not (vc.is_playing() or vc.is_paused()):
                raise UserFacingError("Сейчас ничего не играет.")
            if player:
                player.skip_requested = True
            vc.stop()
        elif action == "stop":
            if player:
                player.queue.clear()
                player.queue.loop = "off"
            vc.stop()
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

    # ------------------------------------------------------------ slash-команды

    @app_commands.command(name="play", description="Включить трек или плейлист с SoundCloud")
    @app_commands.describe(query="Название трека или ссылка на SoundCloud")
    @app_commands.guild_only()
    async def play(self, interaction: discord.Interaction, query: app_commands.Range[str, 1, 300]) -> None:
        guild, member = require_guild(interaction)
        cfg = await self._check_enabled(guild)
        if member.voice is None or member.voice.channel is None:
            raise UserFacingError("Сначала зайдите в голосовой канал.")
        await interaction.response.defer(thinking=True)
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
        player.text_channel_id = interaction.channel_id
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
        await interaction.followup.send(embed=discord.Embed(description=text, color=COLOR_INFO))
        if idle:
            await self._play_next(guild.id)

    async def _simple(self, interaction: discord.Interaction, action: str, done: str, dj: bool = True) -> None:
        guild, member = require_guild(interaction)
        cfg = await self._check_enabled(guild)
        self._require_same_channel(guild, member)
        if dj:
            self._require_dj(member, cfg, self.players.get(guild.id))
        await self.control(guild.id, action)
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
        await respond(interaction, embed=ok_embed(f"🔊 Громкость: {percent}%"))

    @app_commands.command(name="queue", description="Очередь треков")
    @app_commands.guild_only()
    async def queue_cmd(self, interaction: discord.Interaction) -> None:
        guild, _ = require_guild(interaction)
        player = self.players.get(guild.id)
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
