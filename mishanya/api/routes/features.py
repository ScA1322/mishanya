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
from ...schemas import CardConfig, ChatterConfig, MenuConfig, PostConfig, TempVoiceConfig, TicketsConfig, VoiceHub, WelcomeConfig
from ...services.cards import background_path, render_card, save_background
from ...services.chatter import ChatterError, format_history, generate, pick_phrase
from ...services.menus import MAX_IMAGE_BYTES, MenuLimit
from ...services.music import self_test, voice_available
from ...services.posts import PostLimit
from ...services.tts import TtsError, synthesize
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


def _decode_image(body: ImageIn) -> bytes:
    raw = body.data.split(",", 1)[1] if body.data.startswith("data:") else body.data
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "Файл изображения повреждён") from None


# ---------------------------------------------------------------- посты

def _post_out(guild_id: int, p) -> dict:  # noqa: ANN001
    jump = None
    if p.message_id and p.published_channel_id:
        jump = f"https://discord.com/channels/{guild_id}/{p.published_channel_id}/{p.message_id}"
    return {
        "id": p.id, "name": p.name, "channel_id": str(p.channel_id) if p.channel_id else None,
        "content": (p.data or {}).get("content", ""), "embeds": (p.data or {}).get("embeds", []),
        "attachments": (p.data or {}).get("attachments", []), "ping": (p.data or {}).get("ping", False),
        "published": bool(p.message_id), "message_url": jump,
        "published_channel_id": str(p.published_channel_id) if p.published_channel_id else None,
        "published_at": iso(p.published_at), "updated_at": iso(p.updated_at),
    }


def _parse_post(body: dict) -> PostConfig:
    try:
        return PostConfig.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(422, validation_detail(exc)) from None


async def _get_post(request: Request, guild_id: int, post_id: int):  # noqa: ANN202
    p = await get_app(request).posts.get(guild_id, post_id)
    if p is None:
        raise HTTPException(404, "Пост не найден")
    return p


@router.get("/posts")
async def list_posts(request: Request, guild_id: int = Depends(guild_id_dep)) -> list[dict]:
    return [_post_out(guild_id, p) for p in await get_app(request).posts.list(guild_id)]


@router.post("/posts")
async def create_post(body: dict, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    data = _parse_post(body)
    try:
        p = await get_app(request).posts.create(guild_id, data)
    except ValueError as exc:  # PostLimit или пропавший файл
        raise HTTPException(409 if isinstance(exc, PostLimit) else 422, str(exc)) from None
    return _post_out(guild_id, p)


@router.put("/posts/{post_id}")
async def update_post(post_id: int, body: dict, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    data = _parse_post(body)
    try:
        p = await get_app(request).posts.update(guild_id, post_id, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if p is None:
        raise HTTPException(404, "Пост не найден")
    return _post_out(guild_id, p)


@router.delete("/posts/{post_id}")
async def delete_post(post_id: int, request: Request, guild_id: int = Depends(guild_id_dep),
                      remove_message: bool = True) -> dict:
    app = get_app(request)
    p = await _get_post(request, guild_id, post_id)
    if remove_message and p.message_id and app.bot is not None and app.bot.is_ready():
        cog = app.bot.get_cog("Posts")
        if cog is not None:
            await cog.unpublish(p)  # type: ignore[attr-defined]
    await app.posts.delete(guild_id, post_id)
    return {"ok": True}


@router.post("/posts/{post_id}/publish")
async def publish_post(post_id: int, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    p = await _get_post(request, guild_id, post_id)
    cog = _bot_cog(request, "Posts")
    await _discord_call(cog.publish(p))
    return _post_out(guild_id, await _get_post(request, guild_id, post_id))


@router.post("/posts/{post_id}/unpublish")
async def unpublish_post(post_id: int, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    p = await _get_post(request, guild_id, post_id)
    cog = _bot_cog(request, "Posts")
    await _discord_call(cog.unpublish(p))
    return _post_out(guild_id, await _get_post(request, guild_id, post_id))


@router.post("/posts/files")
async def upload_post_file(body: ImageIn, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    try:
        name = get_app(request).posts.save_upload(guild_id, _decode_image(body))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"ref": f"upload:{name}", "url": f"/api/guilds/{guild_id}/posts/files/{name}"}


@router.get("/posts/files/{name}")
async def get_post_file(name: str, request: Request, guild_id: int = Depends(guild_id_dep)) -> FileResponse:
    path = get_app(request).posts.file_path(guild_id, name)
    if path is None:
        raise HTTPException(404, "Изображение не найдено")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=604800, immutable"})


# ---------------------------------------------------------------- музыка

@router.get("/music")
async def music_state(request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    app = get_app(request)
    cog = app.bot.get_cog("Music") if app.bot is not None and app.bot.is_ready() else None
    if cog is None:
        return {"connected": False, "channel": None, "playing": False, "paused": False, "current": None,
                "elapsed": 0, "queue": [], "loop": "off", "volume": None, "bot": False,
                "voice_available": voice_available()}
    return {**cog.state(guild_id), "bot": True}  # type: ignore[attr-defined]


# ---------------------------------------------------------------- болтовня

SAMPLE_CHAT = [("Вася", "кто сегодня в доту?", False), ("Петя", "я после 9, у меня созвон", False),
               ("Вася", "опять созвон, ты там президентом работаешь?", False), ("Петя", "почти", False)]


def _chatter_status(app) -> dict:  # noqa: ANN001
    s = app.settings
    key = s.deepseek_api_key
    return {"ai_configured": bool(key), "key_hint": f"…{key[-4:]}" if len(key) >= 8 else None,
            "model": s.deepseek_model, "base_url": s.deepseek_base_url, "vision_model": s.vision_model,
            "last_error": app.chatter_error}


@router.get("/chatter/status")
async def chatter_status(request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:  # noqa: ARG001
    return _chatter_status(get_app(request))


class ChatterTestIn(BaseModel):
    config: dict
    channel_id: str | None = Field(None, pattern=r"^\d{15,22}$")
    mentioned: bool = False


@router.post("/chatter/test")
async def chatter_test(body: ChatterTestIn, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    """Придумывает реплику по текущим (даже несохранённым) настройкам. В Discord ничего не отправляет."""
    app = get_app(request)
    try:
        cfg = ChatterConfig.model_validate(body.config)
    except ValidationError as exc:
        raise HTTPException(422, validation_detail(exc)) from None
    s = app.settings
    if not (cfg.use_ai and s.deepseek_api_key):
        phrase = pick_phrase(cfg.phrases)
        return {"text": phrase, "source": "phrase", "error": None if phrase else "Список фраз пуст",
                "context": None}
    dg = discord_guild(app, guild_id)
    channel = dg.get_channel(int(body.channel_id)) if dg and body.channel_id else None
    history, channel_name, me = SAMPLE_CHAT, "общий", "Мишаня"
    if isinstance(channel, (discord.TextChannel, discord.Thread)) and channel.permissions_for(channel.guild.me).read_message_history:
        me = channel.guild.me.display_name
        history = [(m.author.display_name, m.clean_content, m.author.id == channel.guild.me.id)
                   async for m in channel.history(limit=cfg.context_messages)][::-1]
        channel_name = channel.name
    context = format_history(history, me)
    try:
        text = await generate(api_key=s.deepseek_api_key, base_url=s.deepseek_base_url, model=s.deepseek_model,
                              system_prompt=cfg.system_prompt, history=context, channel=channel_name, bot_name=me,
                              mentioned=body.mentioned, temperature=cfg.temperature, max_tokens=cfg.max_tokens)
    except ChatterError as exc:
        app.chatter_error = str(exc)
        return {"text": None, "source": "ai", "error": str(exc), "context": context}
    app.chatter_error = None
    return {"text": text, "source": "ai", "error": None, "context": context}


class TtsPreviewIn(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    config: dict


@router.post("/chatter/tts-preview")
async def chatter_tts_preview(body: TtsPreviewIn, guild_id: int = Depends(guild_id_dep)) -> Response:  # noqa: ARG001
    """Озвучивает текст голосом из настроек и отдаёт MP3 в браузер. В Discord ничего не отправляет."""
    try:
        cfg = ChatterConfig.model_validate(body.config)
    except ValidationError as exc:
        raise HTTPException(422, validation_detail(exc)) from None
    try:
        data, engine = await synthesize(body.text, cfg.voice_name, cfg.voice_rate, cfg.voice_pitch, cfg.voice_engine)
    except TtsError as exc:
        raise HTTPException(502, str(exc)) from None
    return Response(data, media_type="audio/mpeg", headers={"X-TTS-Engine": engine, "Cache-Control": "no-store"})


class VoiceSayIn(BaseModel):
    config: dict
    channel_id: str = Field(pattern=r"^\d{15,22}$")
    text: str = Field("", max_length=400)


@router.post("/chatter/voice-say")
async def chatter_voice_say(body: VoiceSayIn, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    """Бот заходит в выбранный голосовой канал и говорит текст (или придумывает сам), затем выходит."""
    app = get_app(request)
    try:
        cfg = ChatterConfig.model_validate(body.config)
    except ValidationError as exc:
        raise HTTPException(422, validation_detail(exc)) from None
    cog = _bot_cog(request, "Chatter")
    dg = discord_guild(app, guild_id)
    channel = dg.get_channel(int(body.channel_id)) if dg else None
    if not isinstance(channel, discord.VoiceChannel):
        raise HTTPException(404, "Голосовой канал не найден")
    said = await _discord_call(cog.visit(channel, cfg, [body.text.strip()] if body.text.strip() else None))
    return {"said": said}


# ---------------------------------------------------------------- слушание голоса

@router.get("/listen")
async def listen_status(request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    app = get_app(request)
    cog = app.bot.get_cog("Listener") if app.bot is not None and app.bot.is_ready() else None
    if cog is None:
        s = app.settings
        return {"available": False, "stt_configured": bool(s.stt_key), "stt_model": s.stt_model,
                "stt_url": s.stt_url, "listening": False}
    return cog.status(guild_id)  # type: ignore[attr-defined]


class ListenStartIn(BaseModel):
    channel_id: str = Field(pattern=r"^\d{15,22}$")


@router.post("/listen/start")
async def listen_start(body: ListenStartIn, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    app = get_app(request)
    cog = _bot_cog(request, "Listener")
    dg = discord_guild(app, guild_id)
    channel = dg.get_channel(int(body.channel_id)) if dg else None
    if not isinstance(channel, discord.VoiceChannel):
        raise HTTPException(404, "Голосовой канал не найден")
    await _discord_call(cog.start(channel))
    return cog.status(guild_id)


@router.post("/listen/stop")
async def listen_stop(request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    cog = _bot_cog(request, "Listener")
    await cog.stop(guild_id, "из панели")
    return cog.status(guild_id)


@router.post("/music-selftest")
async def music_selftest(request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    steps = await self_test()
    app = get_app(request)
    cog = app.bot.get_cog("Music") if app.bot is not None and app.bot.is_ready() else None
    if cog is None:
        steps.append({"name": "Подключение к голосовому каналу", "ok": False, "detail": "бот не подключён к Discord"})
    else:
        if voice_available():
            steps.append(await cog.voice_test(guild_id))  # type: ignore[attr-defined]
        try:
            steps.extend(await app.bot.command_health())  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            steps.append({"name": "Slash-команды", "ok": False, "detail": f"проверка не удалась: {exc}"})
    return {"ok": all(s["ok"] for s in steps), "steps": steps}


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


# ---------------------------------------------------------------- голосовые комнаты

@router.get("/tempvoice/rooms")
async def tempvoice_rooms(request: Request, guild_id: int = Depends(guild_id_dep)) -> list[dict]:
    app = get_app(request)
    dg = discord_guild(app, guild_id)
    out = []
    for r in await app.tempvoice.list(guild_id):
        ch = dg.get_channel(r.channel_id) if dg else None
        out.append({"channel_id": str(r.channel_id), "name": ch.name if ch else None,
                    "members": len(ch.members) if isinstance(ch, discord.VoiceChannel) else None,
                    "owner_id": str(r.owner_id), "owner_name": user_label(app, guild_id, r.owner_id),
                    "created_at": iso(r.created_at)})
    return out


class CreateHubIn(BaseModel):
    name: str = Field("➕ Создать комнату", min_length=1, max_length=100)
    category_id: str | None = Field(None, pattern=r"^\d{15,22}$")


@router.post("/tempvoice/hub")
async def tempvoice_create_hub(body: CreateHubIn, request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    """Бот сам создаёт голосовой канал-создатель и добавляет его в настройки (модуль включается)."""
    app = get_app(request)
    cfg = await app.config.get_typed(guild_id, "tempvoice", TempVoiceConfig)
    if len(cfg.hubs) >= 10:
        raise HTTPException(409, "Максимум 10 каналов-создателей")
    cog = _bot_cog(request, "TempVoice")
    channel = await _discord_call(cog.create_hub(guild_id, int(body.category_id) if body.category_id else None,
                                                 body.name.strip()))
    new = cfg.model_copy(deep=True)
    new.enabled = True
    new.hubs.append(VoiceHub(channel_id=str(channel.id), category_id=body.category_id))
    await app.config.set(guild_id, "tempvoice", new)
    return {"channel_id": str(channel.id), "name": channel.name, "config": new.model_dump(mode="json")}


# ---------------------------------------------------------------- «разум»: память, обиды, доверенные

def _member_info(dg, user_id: int | None) -> dict | None:  # noqa: ANN001
    if user_id is None:
        return None
    m = dg.get_member(user_id) if dg else None
    return {"id": str(user_id), "name": m.display_name if m else f"участник {user_id}",
            "avatar_url": m.display_avatar.url if m else None}


@router.get("/mind")
async def mind_overview(request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    app = get_app(request)
    dg = discord_guild(app, guild_id)
    cfg = await app.config.get_typed(guild_id, "chatter", ChatterConfig)
    memories = await app.mind.memories(guild_id, limit=2000)
    grudges = await app.mind.grudges(guild_id)
    return {
        "memories": [{"id": m.id, "text": m.text, "user": _member_info(dg, m.user_id), "created_at": iso(m.created_at)}
                     for m in memories],
        "grudges": [{"user": _member_info(dg, g.user_id), "roles": g.role_ids, "reason": g.reason,
                     "created_at": iso(g.created_at)} for g in grudges],
        "inactive": [{"user": _member_info(dg, r.user_id), "last_seen": iso(r.last_seen)}
                     for r in await app.mind.inactive(guild_id, cfg.report_inactive_days or 7)
                     if dg is None or dg.get_member(r.user_id) is not None][:50],
        "vision_model": app.settings.vision_model,
    }


@router.delete("/mind/memories/{memory_id}")
async def mind_forget(request: Request, memory_id: int, guild_id: int = Depends(guild_id_dep)) -> dict:
    n = await get_app(request).mind.forget(guild_id, memory_id)
    if not n:
        raise HTTPException(404, "Запись не найдена")
    return {"ok": True}


@router.delete("/mind/memories")
async def mind_forget_all(request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    return {"removed": await get_app(request).mind.forget(guild_id)}


@router.post("/mind/learn")
async def mind_learn(request: Request, guild_id: int = Depends(guild_id_dep)) -> dict:
    """Заставляет бота прямо сейчас «обдумать» свежие сообщения и пополнить память."""
    app = get_app(request)
    cog = _bot_cog(request, "Mind")
    dg = discord_guild(app, guild_id)
    cfg = await app.config.get_typed(guild_id, "chatter", ChatterConfig)
    if not app.settings.deepseek_api_key:
        raise HTTPException(400, "Нужен ключ ИИ — без него бот не может ничего запоминать")
    try:
        added = await cog.learn(dg, cfg)
    except ChatterError as exc:
        raise HTTPException(502, str(exc)) from None
    cog.unlearned[guild_id] = 0
    return {"added": added}


@router.post("/mind/forgive/{user_id}")
async def mind_forgive(request: Request, user_id: int, guild_id: int = Depends(guild_id_dep)) -> dict:
    """Снять обиду вручную: вернуть роли и снять мут."""
    app = get_app(request)
    dg = discord_guild(app, guild_id)
    grudge = await app.mind.grudge(guild_id, user_id)
    if grudge is None:
        raise HTTPException(404, "Обиды на этого участника нет")
    cog = app.bot.get_cog("Mind") if app.bot is not None else None
    if dg is None or cog is None:
        await app.mind.drop_grudge(guild_id, user_id)
        return {"restored": 0}
    restored = await _discord_call(cog.forgive(dg, user_id))  # type: ignore[union-attr]
    cfg = await app.config.get_typed(guild_id, "chatter", ChatterConfig)
    who = _member_info(dg, user_id)["name"]  # type: ignore[index]
    await cog.notify(dg, cfg, f"🕊️ Простил **{who}** («{dg.name}») по приказу из панели. "  # type: ignore[union-attr]
                              f"Обижался за: {grudge.reason or '?'}.")
    return {"restored": restored}
