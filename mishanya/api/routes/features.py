"""API тикетов, меню с кнопками, музыки и картинок приветствий."""
from __future__ import annotations

import asyncio
import base64
import binascii

import discord
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, ValidationError

from ...bot.utils import UserFacingError, human_error
from ...schemas import CardConfig, MenuConfig, TicketsConfig, WelcomeConfig
from ...services.cards import background_path, render_card, save_background
from ...services.menus import MAX_IMAGE_BYTES, MenuLimit
from ...services.templates import SAMPLE_VALUES, member_values
from ..auth import require_auth
from ..deps import discord_guild, get_app, guild_id_dep, iso, user_label
from .guilds import validation_detail

router = APIRouter(prefix="/api/guilds/{guild_id}", tags=["features"], dependencies=[Depends(require_auth)])
_background: set[asyncio.Task] = set()


def _bot_cog(request: Request, name: str):  # noqa: ANN202
    app = get_app(request)
    if app.bot is None or not app.bot.is_ready():
        raise HTTPException(409, "Бот не подключён к Discord")
    cog = app.bot.get_cog(name)
    if cog is None:
        raise HTTPException(503, f"Модуль {name} не загружен")
    return cog


async def _discord_call(coro):  # noqa: ANN001, ANN202
    """Переводит ошибки бота в понятные ответы API."""
    try:
        return await coro
    except UserFacingError as exc:
        raise HTTPException(400, str(exc)) from None
    except discord.HTTPException as exc:
        raise HTTPException(502, human_error(exc)) from None


# ---------------------------------------------------------------- тикеты

@router.get("/tickets")
async def list_tickets(request: Request, guild_id: int = Depends(guild_id_dep),
                       status: str | None = Query(None, pattern=r"^(open|closed)$"),
                       offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)) -> dict:
    app = get_app(request)
    rows, total = await app.tickets.list(guild_id, status, limit, offset)
    return {"total": total, "open": await app.tickets.open_count(guild_id), "items": [{
        "id": t.id, "number": t.number, "status": t.status, "topic": t.topic,
        "owner_id": str(t.owner_id), "owner_name": user_label(app, guild_id, t.owner_id) or t.owner_name,
        "channel_id": str(t.channel_id) if t.channel_id else None,
        "claimed_by": str(t.claimed_by) if t.claimed_by else None,
        "claimed_name": user_label(app, guild_id, t.claimed_by),
        "closed_by_name": user_label(app, guild_id, t.closed_by) if t.closed_by else None,
        "close_reason": t.close_reason, "created_at": iso(t.created_at), "closed_at": iso(t.closed_at),
    } for t in rows]}


class CloseTicketIn(BaseModel):
    reason: str = Field("", max_length=500)


@router.post("/tickets/{ticket_id}/close")
async def close_ticket(ticket_id: int, body: CloseTicketIn, request: Request,
                       guild_id: int = Depends(guild_id_dep)) -> dict:
    app = get_app(request)
    ticket = await app.tickets.get(guild_id, ticket_id)
    if ticket is None or ticket.status != "open":
        raise HTTPException(404, "Открытый тикет не найден")
    reason = body.reason.strip() or "Закрыт из панели"
    dg = discord_guild(app, guild_id)
    channel = dg.get_channel(ticket.channel_id) if dg and ticket.channel_id else None
    if isinstance(channel, discord.TextChannel):
        cog = _bot_cog(request, "Tickets")
        cfg = await app.config.get_typed(guild_id, "tickets", TicketsConfig)
        # Удаление канала идёт с задержкой — не держим запрос панели.
        cfg = cfg.model_copy(update={"delete_delay_seconds": min(cfg.delete_delay_seconds, 5)})
        task = asyncio.create_task(cog.close_ticket(channel, ticket, cfg, None, reason))
        _background.add(task)
        task.add_done_callback(_background.discard)
    else:
        await app.tickets.close(ticket.id, None, reason)
    return {"ok": True}


# ---------------------------------------------------------------- меню с кнопками

def _menu_out(request: Request, guild_id: int, m) -> dict:  # noqa: ANN001
    app = get_app(request)
    jump = None
    if m.message_id and m.published_channel_id:
        jump = f"https://discord.com/channels/{guild_id}/{m.published_channel_id}/{m.message_id}"
    return {
        "id": m.id, "name": m.name, "channel_id": str(m.channel_id) if m.channel_id else None,
        "content": m.content, "embed": m.embed, "buttons": m.buttons, "role_select": m.role_select,
        "image_url": (f"/api/guilds/{guild_id}/menus/{m.id}/image?v={m.image_file}"
                      if app.menus.image_path(m) else None),
        "published": bool(m.message_id), "message_url": jump,
        "published_channel_id": str(m.published_channel_id) if m.published_channel_id else None,
        "published_at": iso(m.published_at), "updated_at": iso(m.updated_at),
    }


def _parse_menu(body: dict) -> MenuConfig:
    try:
        return MenuConfig.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(422, validation_detail(exc)) from None


async def _get_menu(request: Request, guild_id: int, menu_id: int):  # noqa: ANN202
    m = await get_app(request).menus.get(guild_id, menu_id)
    if m is None:
        raise HTTPException(404, "Меню не найдено")
    return m


@router.get("/menus")
async def list_menus(request: Request, guild_id: int = Depends(guild_id_dep)) -> list[dict]:
    return [_menu_out(request, guild_id, m) for m in await get_app(request).menus.list(guild_id)]


@router.post("/menus")
async def create_menu(body: dict, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    data = _parse_menu(body)
    try:
        m = await get_app(request).menus.create(guild_id, data)
    except MenuLimit as exc:
        raise HTTPException(409, str(exc)) from None
    return _menu_out(request, guild_id, m)


@router.put("/menus/{menu_id}")
async def update_menu(menu_id: int, body: dict, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    data = _parse_menu(body)
    m = await get_app(request).menus.update(guild_id, menu_id, data)
    if m is None:
        raise HTTPException(404, "Меню не найдено")
    return _menu_out(request, guild_id, m)


@router.delete("/menus/{menu_id}")
async def delete_menu(menu_id: int, request: Request, guild_id: int = Depends(guild_id_dep),
                      remove_message: bool = True) -> dict:
    app = get_app(request)
    m = await _get_menu(request, guild_id, menu_id)
    if remove_message and m.message_id and app.bot is not None and app.bot.is_ready():
        cog = app.bot.get_cog("Menus")
        if cog is not None:
            await cog.unpublish(m)  # type: ignore[attr-defined]
    await app.menus.delete(guild_id, menu_id)
    return {"ok": True}


@router.post("/menus/{menu_id}/publish")
async def publish_menu(menu_id: int, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    m = await _get_menu(request, guild_id, menu_id)
    cog = _bot_cog(request, "Menus")
    await _discord_call(cog.publish(m))
    return _menu_out(request, guild_id, await _get_menu(request, guild_id, menu_id))


class ImageIn(BaseModel):
    data: str = Field(max_length=(MAX_IMAGE_BYTES * 4) // 3 + 64)  # base64


@router.put("/menus/{menu_id}/image")
async def upload_image(menu_id: int, body: ImageIn, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    raw = body.data.split(",", 1)[1] if body.data.startswith("data:") else body.data
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "Файл изображения повреждён") from None
    try:
        m = await get_app(request).menus.set_image(guild_id, menu_id, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if m is None:
        raise HTTPException(404, "Меню не найдено")
    return _menu_out(request, guild_id, m)


@router.delete("/menus/{menu_id}/image")
async def delete_image(menu_id: int, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    m = await get_app(request).menus.clear_image(guild_id, menu_id)
    if m is None:
        raise HTTPException(404, "Меню не найдено")
    return _menu_out(request, guild_id, m)


@router.get("/menus/{menu_id}/image")
async def get_image(menu_id: int, request: Request, guild_id: int = Depends(guild_id_dep)) -> FileResponse:
    m = await _get_menu(request, guild_id, menu_id)
    path = get_app(request).menus.image_path(m)
    if path is None:
        raise HTTPException(404, "Изображение не загружено")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


# ---------------------------------------------------------------- музыка

@router.get("/music")
async def music_state(request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    app = get_app(request)
    cog = app.bot.get_cog("Music") if app.bot is not None and app.bot.is_ready() else None
    if cog is None:
        return {"connected": False, "channel": None, "playing": False, "paused": False, "current": None,
                "elapsed": 0, "queue": [], "loop": "off", "volume": None, "bot": False}
    return {**cog.state(guild_id), "bot": True}  # type: ignore[attr-defined]


@router.post("/music/{action}")
async def music_control(request: Request, action: str = Path(pattern=r"^(skip|stop|pause|resume|leave|shuffle)$"),
                        guild_id: int = Depends(guild_id_dep)) -> dict:
    cog = _bot_cog(request, "Music")
    await _discord_call(cog.control(guild_id, action))
    return {**cog.state(guild_id), "bot": True}


# ---------------------------------------------------------------- картинки приветствий

class CardPreviewIn(BaseModel):
    kind: str = Field("join", pattern=r"^(join|leave)$")
    card: CardConfig


def _card_dir(request: Request):  # noqa: ANN202
    return get_app(request).settings.data_dir / "cards"


@router.post("/welcome/card-preview")
async def card_preview(body: CardPreviewIn, request: Request, guild_id: int = Depends(guild_id_dep)) -> Response:
    """Рисует баннер так же, как бот, — на примере аватара и имени бота."""
    app = get_app(request)
    values = dict(SAMPLE_VALUES)
    avatar = None
    dg = discord_guild(app, guild_id)
    if dg is not None:
        values.update(member_values(dg.me, guild=dg))
        try:
            avatar = await dg.me.display_avatar.replace(size=256, static_format="png").read()
        except discord.HTTPException:
            avatar = None
    png = await asyncio.to_thread(render_card, body.card, values, avatar,
                                  background_path(_card_dir(request), body.card.background_image))
    return Response(png, media_type="image/png")


@router.put("/welcome/{kind}/background")
async def upload_background(body: ImageIn, request: Request, kind: str = Path(pattern=r"^(join|leave)$"),
                            guild_id: int = Depends(guild_id_dep)) -> dict:
    """Загружает картинку фона. В настройки она попадает при сохранении страницы приветствий."""
    app = get_app(request)
    raw = body.data.split(",", 1)[1] if body.data.startswith("data:") else body.data
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "Файл изображения повреждён") from None
    card_dir = _card_dir(request)
    # Неиспользуемые загрузки этого же баннера удаляем, сохранённую в настройках — оставляем.
    cfg = await app.config.get_typed(guild_id, "welcome", WelcomeConfig)
    keep = (cfg.join if kind == "join" else cfg.leave).card.background_image
    try:
        name = save_background(card_dir, guild_id, kind, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    for old in card_dir.glob(f"{guild_id}-{kind}-*"):
        if old.name not in (name, keep):
            old.unlink(missing_ok=True)
    return {"background_image": name}


@router.get("/welcome/background/{name}")
async def get_background(request: Request, name: str = Path(pattern=r"^[A-Za-z0-9_.-]{1,100}$"),
                         guild_id: int = Depends(guild_id_dep)) -> FileResponse:
    path = background_path(_card_dir(request), name) if name.startswith(f"{guild_id}-") else None
    if path is None:
        raise HTTPException(404, "Картинка не найдена")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})
