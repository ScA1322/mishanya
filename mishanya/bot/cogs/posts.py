"""Посты: оформленные сообщения (текст, до 10 embed, картинки-вложения), публикуемые из панели."""
from __future__ import annotations

import logging

import discord
from discord.ext import commands
from pydantic import ValidationError

from ...db.models import Post
from ...schemas import PostEmbed
from ...services.templates import render
from ..client import MishanyaBot
from ..utils import UserFacingError, channel_from_id

log = logging.getLogger(__name__)


def _image(ref: str, files: dict[str, str]) -> str | None:
    """«upload:имя» → attachment://имя (файл прикладывается к сообщению), ссылка — как есть."""
    if not ref:
        return None
    if ref.startswith("upload:"):
        return f"attachment://{files[ref[7:]]}"
    return ref


def build_post_embed(cfg: PostEmbed, values: dict, files: dict[str, str]) -> discord.Embed:
    def r(text: str, limit: int) -> str | None:
        return render(text, values)[:limit] or None if text else None

    embed = discord.Embed(color=int(cfg.color.lstrip("#"), 16), title=r(cfg.title, 256),
                          url=cfg.url or None, description=r(cfg.description, 4096))
    if cfg.timestamp:
        embed.timestamp = discord.utils.utcnow()
    if cfg.author_name:
        embed.set_author(name=r(cfg.author_name, 256) or "​", url=cfg.author_url or None,
                         icon_url=_image(cfg.author_icon, files))
    for f in cfg.fields:
        embed.add_field(name=r(f.name, 256) or "​", value=r(f.value, 1024) or "​", inline=f.inline)
    if cfg.thumbnail:
        embed.set_thumbnail(url=_image(cfg.thumbnail, files))
    if cfg.image:
        embed.set_image(url=_image(cfg.image, files))
    if cfg.footer or cfg.footer_icon:
        embed.set_footer(text=r(cfg.footer, 2048) or "​", icon_url=_image(cfg.footer_icon, files))
    return embed


class Posts(commands.Cog):
    def __init__(self, bot: MishanyaBot):
        self.bot = bot
        self.app = bot.app

    async def publish(self, post: Post) -> discord.Message:
        """Отправляет пост в канал или обновляет уже опубликованное сообщение."""
        guild = self.bot.get_guild(post.guild_id)
        if guild is None:
            raise UserFacingError("Бот не подключён к этому серверу.")
        channel = channel_from_id(guild, post.channel_id)
        if channel is None:
            raise UserFacingError("Выберите текстовый канал для поста — текущий не найден.")
        try:
            cfg = self.app.posts.config(post)
        except ValidationError as exc:
            raise UserFacingError(f"Пост настроен некорректно: {exc.errors()[0]['msg']}") from None

        uploads = cfg.uploads()
        perms = channel.permissions_for(guild.me)  # type: ignore[union-attr]
        needed = {"view_channel": "просматривать канал", "send_messages": "отправлять сообщения"}
        if cfg.embeds:
            needed["embed_links"] = "встраивать ссылки"
        if uploads:
            needed["attach_files"] = "прикреплять файлы"
        missing = [label for p, label in needed.items() if not getattr(perms, p)]
        if missing:
            raise UserFacingError(f"У бота нет прав в канале #{getattr(channel, 'name', '?')}: {', '.join(missing)}.")

        files: list[discord.File] = []
        names: dict[str, str] = {}
        for i, name in enumerate(uploads, 1):
            path = self.app.posts.file_path(guild.id, name)
            if path is None:
                raise UserFacingError("Загруженное изображение пропало — загрузите его в пост заново.")
            names[name] = f"image{i}{path.suffix}"
            files.append(discord.File(path, filename=names[name]))

        values = {"server": guild.name, "server_id": guild.id, "member_count": guild.member_count or 0}
        content = render(cfg.content, values)[:2000] or None if cfg.content else None
        embeds = [build_post_embed(e, values, names) for e in cfg.embeds]
        if not (content or embeds or files):
            raise UserFacingError("Пост пустой: добавьте текст, блок embed или изображение.")
        mentions = (discord.AllowedMentions(everyone=False, roles=True, users=True) if cfg.ping
                    else discord.AllowedMentions.none())

        message: discord.Message | None = None
        if post.message_id and post.published_channel_id == channel.id:
            try:
                message = await channel.get_partial_message(post.message_id).edit(  # type: ignore[union-attr]
                    content=content, embeds=embeds, attachments=files, allowed_mentions=mentions)
            except discord.NotFound:
                message = None
            except discord.Forbidden:
                raise UserFacingError("Бот не может изменить старое сообщение. Удалите его и опубликуйте пост "
                                      "заново.") from None
        elif post.message_id and post.published_channel_id:
            await self._delete_message(guild, post.published_channel_id, post.message_id)
        if message is None:
            try:
                message = await channel.send(content=content, embeds=embeds, files=files,  # type: ignore[union-attr]
                                             allowed_mentions=mentions)
            except discord.HTTPException as exc:
                raise UserFacingError(f"Discord не принял сообщение: {exc.text or exc}") from None
        await self.app.posts.set_published(post.id, channel.id, message.id)
        return message

    async def unpublish(self, post: Post) -> None:
        guild = self.bot.get_guild(post.guild_id)
        if guild is not None and post.message_id and post.published_channel_id:
            await self._delete_message(guild, post.published_channel_id, post.message_id)
        await self.app.posts.set_published(post.id, None, None)

    async def _delete_message(self, guild: discord.Guild, channel_id: int, message_id: int) -> None:
        channel = channel_from_id(guild, channel_id)
        if channel is None:
            return
        try:
            await channel.get_partial_message(message_id).delete()  # type: ignore[union-attr]
        except discord.HTTPException:
            pass  # сообщение уже удалено или нет прав — не мешает работе


async def setup(bot: MishanyaBot) -> None:
    await bot.add_cog(Posts(bot))

