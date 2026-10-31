"""Меню с кнопками и изображением: публикация из панели и обработка нажатий."""
from __future__ import annotations

import logging
import re

import discord
from discord.ext import commands
from pydantic import ValidationError

from ...db.models import ButtonMenu
from ...schemas import EmbedConfig, MenuButtonConfig, RoleSelectConfig
from ...services.templates import member_values, render
from ..client import MishanyaBot
from ..utils import UserFacingError, build_embed, channel_from_id, guarded, info_embed, ok_embed, require_guild, respond

log = logging.getLogger(__name__)

STYLES = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
    "link": discord.ButtonStyle.link,
}


class MenuButton(discord.ui.DynamicItem[discord.ui.Button], template=r"mishanya:menu:(?P<menu_id>\d+):(?P<index>\d+)"):
    """Кнопка опубликованного меню. Действие читается из базы при нажатии, поэтому работает после перезапуска."""

    def __init__(self, menu_id: int, index: int, button: discord.ui.Button | None = None):
        super().__init__(button or discord.ui.Button(custom_id=f"mishanya:menu:{menu_id}:{index}"))
        self.menu_id = menu_id
        self.index = index

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item,  # noqa: ARG003
                             match: re.Match[str], /) -> "MenuButton":
        return cls(int(match["menu_id"]), int(match["index"]), item)  # type: ignore[arg-type]

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("Menus")  # type: ignore[union-attr]
        if cog is not None:
            await guarded(interaction, cog.handle_click(interaction, self.menu_id, self.index))


class MenuRoleSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"mishanya:menusel:(?P<menu_id>\d+)"):
    """Выпадающий список ролей опубликованного меню."""

    def __init__(self, menu_id: int, select: discord.ui.Select | None = None):
        super().__init__(select or discord.ui.Select(custom_id=f"mishanya:menusel:{menu_id}"))
        self.menu_id = menu_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item,  # noqa: ARG003
                             match: re.Match[str], /) -> "MenuRoleSelect":
        return cls(int(match["menu_id"]), item)  # type: ignore[arg-type]

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("Menus")  # type: ignore[union-attr]
        if cog is not None:
            await guarded(interaction, cog.handle_select(interaction, self.menu_id, list(self.item.values)))


def parse_emoji(text: str) -> discord.PartialEmoji | None:
    return discord.PartialEmoji.from_str(text.strip()) if text.strip() else None


def build_view(menu_id: int, buttons: list[MenuButtonConfig],
               role_select: RoleSelectConfig | None = None) -> discord.ui.View | None:
    has_select = role_select is not None and role_select.enabled
    if not buttons and not has_select:
        return None
    view = discord.ui.View(timeout=None)
    if has_select:
        assert role_select is not None
        options = [discord.SelectOption(label=o.label, value=o.role_id, description=o.description or None,
                                        emoji=parse_emoji(o.emoji)) for o in role_select.options]
        view.add_item(MenuRoleSelect(menu_id, discord.ui.Select(
            custom_id=f"mishanya:menusel:{menu_id}", placeholder=role_select.placeholder or None, options=options,
            min_values=role_select.min_values, max_values=role_select.max_values, row=0)))
    for i, b in enumerate(buttons):
        kwargs = {"label": b.label or None, "emoji": parse_emoji(b.emoji), "row": b.row}
        if b.action == "link":
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, url=b.url, **kwargs))
        else:
            view.add_item(MenuButton(menu_id, i, discord.ui.Button(
                style=STYLES[b.style], custom_id=f"mishanya:menu:{menu_id}:{i}", **kwargs)))
    return view


class Menus(commands.Cog):
    def __init__(self, bot: MishanyaBot):
        self.bot = bot
        self.app = bot.app

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(MenuButton, MenuRoleSelect)

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(MenuButton, MenuRoleSelect)

    # ------------------------------------------------------------ публикация

    async def publish(self, menu: ButtonMenu) -> discord.Message:
        """Отправляет меню в канал или обновляет уже опубликованное сообщение."""
        guild = self.bot.get_guild(menu.guild_id)
        if guild is None:
            raise UserFacingError("Бот не подключён к этому серверу.")
        channel = channel_from_id(guild, menu.channel_id)
        if channel is None:
            raise UserFacingError("Выберите текстовый канал для меню — текущий не найден.")
        image = self.app.menus.image_path(menu)
        perms = channel.permissions_for(guild.me)  # type: ignore[union-attr]
        needed = {"view_channel": "просматривать канал", "send_messages": "отправлять сообщения",
                  "embed_links": "встраивать ссылки"}
        if image:
            needed["attach_files"] = "прикреплять файлы"
        missing = [label for p, label in needed.items() if not getattr(perms, p)]
        if missing:
            raise UserFacingError(f"У бота нет прав в канале #{getattr(channel, 'name', '?')}: {', '.join(missing)}.")

        try:
            buttons = [MenuButtonConfig.model_validate(b) for b in menu.buttons or []]
            embed_cfg = EmbedConfig.model_validate(menu.embed) if menu.embed else EmbedConfig()
            role_select = RoleSelectConfig.model_validate(menu.role_select) if menu.role_select else None
        except ValidationError as exc:
            raise UserFacingError(f"Настройки меню некорректны: {exc.errors()[0]['msg']}") from None
        values = {"server": guild.name, "server_id": guild.id, "member_count": guild.member_count or 0}
        content = render(menu.content, values)[:2000] if menu.content else None
        embed = build_embed(embed_cfg, values) if embed_cfg.enabled else None
        files: list[discord.File] = []
        if image:
            fname = f"menu{image.suffix}"
            files.append(discord.File(image, filename=fname))
            if embed is not None:
                embed.set_image(url=f"attachment://{fname}")
        if role_select is not None and role_select.enabled and not guild.me.guild_permissions.manage_roles:
            raise UserFacingError("Для выпадающего списка ролей боту нужно право «Управлять ролями».")
        view = build_view(menu.id, buttons, role_select)
        if not (content or embed or files or view):
            raise UserFacingError("Меню пустое: добавьте текст, embed, изображение или кнопки.")

        message: discord.Message | None = None
        if menu.message_id and menu.published_channel_id == channel.id:
            try:
                message = await channel.get_partial_message(menu.message_id).edit(  # type: ignore[union-attr]
                    content=content, embed=embed, view=view, attachments=files)
            except discord.NotFound:
                message = None
        elif menu.message_id and menu.published_channel_id:
            await self._delete_message(guild, menu.published_channel_id, menu.message_id)
        if message is None:
            kwargs: dict = {"content": content, "embed": embed, "allowed_mentions": discord.AllowedMentions.none()}
            if files:
                kwargs["files"] = files
            if view is not None:
                kwargs["view"] = view
            message = await channel.send(**kwargs)
        await self.app.menus.set_published(menu.id, channel.id, message.id)
        return message

    async def unpublish(self, menu: ButtonMenu) -> None:
        guild = self.bot.get_guild(menu.guild_id)
        if guild is not None and menu.message_id and menu.published_channel_id:
            await self._delete_message(guild, menu.published_channel_id, menu.message_id)

    async def _delete_message(self, guild: discord.Guild, channel_id: int, message_id: int) -> None:
        channel = channel_from_id(guild, channel_id)
        if channel is None:
            return
        try:
            await channel.get_partial_message(message_id).delete()  # type: ignore[union-attr]
        except discord.HTTPException:
            pass  # сообщение уже удалено или нет прав — не мешает работе

    # ------------------------------------------------------------ нажатия

    async def handle_click(self, interaction: discord.Interaction, menu_id: int, index: int) -> None:
        guild, member = require_guild(interaction)
        menu = await self.app.menus.get_any(menu_id)
        if menu is None or menu.guild_id != guild.id or index >= len(menu.buttons or []):
            raise UserFacingError("Это меню устарело или удалено. Попросите администратора опубликовать его заново.")
        try:
            button = MenuButtonConfig.model_validate(menu.buttons[index])
        except ValidationError:
            raise UserFacingError("Кнопка настроена некорректно.") from None
        if button.action == "role":
            await self._toggle_role(interaction, guild, member, int(button.role_id or 0))
        elif button.action == "reply":
            text = render(button.reply, member_values(member, channel=interaction.channel))[:4000]
            await respond(interaction, embed=info_embed(text), ephemeral=True)
        elif button.action == "ticket":
            tickets = self.bot.get_cog("Tickets")
            if tickets is None:
                raise UserFacingError("Модуль тикетов недоступен.")
            await tickets.start_ticket(interaction)  # type: ignore[attr-defined]

    async def handle_select(self, interaction: discord.Interaction, menu_id: int, values: list[str]) -> None:
        """Выдаёт выбранные роли из списка и снимает остальные роли этого списка."""
        guild, member = require_guild(interaction)
        menu = await self.app.menus.get_any(menu_id)
        if menu is None or menu.guild_id != guild.id or not menu.role_select:
            raise UserFacingError("Это меню устарело или удалено. Попросите администратора опубликовать его заново.")
        cfg = RoleSelectConfig.model_validate(menu.role_select)
        me = guild.me
        if not me.guild_permissions.manage_roles:
            raise UserFacingError("У бота нет права «Управлять ролями».")
        chosen = set(values)
        add, remove, broken = [], [], []
        for o in cfg.options:
            role = guild.get_role(int(o.role_id))
            if role is None or role.managed or role >= me.top_role:
                if o.role_id in chosen:
                    broken.append(o.label)
                continue
            if o.role_id in chosen and role not in member.roles:
                add.append(role)
            elif o.role_id not in chosen and role in member.roles:
                remove.append(role)
        if remove:
            await member.remove_roles(*remove, reason="Меню Mishanya: выбор роли")
        if add:
            await member.add_roles(*add, reason="Меню Mishanya: выбор роли")
        lines = [f"Выдано: {', '.join(r.mention for r in add)}"] if add else []
        if remove:
            lines.append(f"Снято: {', '.join(r.mention for r in remove)}")
        if broken:
            lines.append(f"Не удалось выдать (роль удалена или выше роли бота): {', '.join(broken)}")
        await respond(interaction, embed=ok_embed("\n".join(lines) or "Ваши роли не изменились."), ephemeral=True)

    async def _toggle_role(self, interaction: discord.Interaction, guild: discord.Guild, member: discord.Member,
                           role_id: int) -> None:
        role = guild.get_role(role_id)
        if role is None:
            raise UserFacingError("Роль этой кнопки удалена с сервера.")
        if role.managed or role.is_default() or role >= guild.me.top_role or not guild.me.guild_permissions.manage_roles:
            raise UserFacingError(f"Бот не может выдать роль {role.mention}: она выше роли бота или у бота нет "
                                  "права «Управлять ролями».")
        if role in member.roles:
            await member.remove_roles(role, reason="Меню Mishanya: снятие роли кнопкой")
            await respond(interaction, embed=ok_embed(f"Роль {role.mention} снята."), ephemeral=True)
        else:
            await member.add_roles(role, reason="Меню Mishanya: выдача роли кнопкой")
            await respond(interaction, embed=ok_embed(f"Роль {role.mention} выдана."), ephemeral=True)


async def setup(bot: MishanyaBot) -> None:
    await bot.add_cog(Menus(bot))
