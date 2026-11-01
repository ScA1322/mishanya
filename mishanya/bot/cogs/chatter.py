"""Болтовня: бот иногда сам вмешивается в разговор и отвечает, когда его упоминают."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque

import discord
from discord.ext import commands

from ...schemas import ChatterConfig
from ...services.chatter import ChatterError, format_history, generate, pick_phrase
from ..client import MishanyaBot

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
        self._tasks: set[asyncio.Task] = set()

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
        task = asyncio.create_task(self._speak(message, cfg, mentioned))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

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


async def setup(bot: MishanyaBot) -> None:
    await bot.add_cog(Chatter(bot))
