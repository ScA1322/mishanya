"""Картинки-баннеры приветствия и прощания: фон, аватар участника, заголовок и подписи."""
from __future__ import annotations

import io
import os
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from ..schemas import CardConfig
from .templates import render

WIDTH, HEIGHT = 1024, 450
AVATAR_SIZE = 200
MAX_BACKGROUND_BYTES = 8 * 1024 * 1024

# Шрифты, которые есть в любой Windows 10/11 и поддерживают кириллицу: (обычный, жирный).
FONTS: dict[str, tuple[str, str]] = {
    "segoe": ("segoeui.ttf", "segoeuib.ttf"),
    "arial": ("arial.ttf", "arialbd.ttf"),
    "verdana": ("verdana.ttf", "verdanab.ttf"),
    "tahoma": ("tahoma.ttf", "tahomabd.ttf"),
    "trebuchet": ("trebuc.ttf", "trebucbd.ttf"),
    "georgia": ("georgia.ttf", "georgiab.ttf"),
    "impact": ("impact.ttf", "impact.ttf"),
    "comic": ("comic.ttf", "comicbd.ttf"),
}
FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


def _rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


@lru_cache(maxsize=128)
def _font(name: str, bold: bool, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    regular, heavy = FONTS.get(name, FONTS["segoe"])
    for file in (heavy if bold else regular, FONTS["segoe"][1 if bold else 0], "arial.ttf"):
        path = FONT_DIR / file
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


def _fit(draw: ImageDraw.ImageDraw, text: str, font_name: str, bold: bool, size: int, max_width: int):  # noqa: ANN202
    """Уменьшает шрифт, пока текст не поместится; если не помогло — обрезает с многоточием."""
    while size > 14:
        font = _font(font_name, bold, size)
        if draw.textlength(text, font=font) <= max_width:
            return text, font
        size -= 2
    font = _font(font_name, bold, size)
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…", font


def _background(cfg: CardConfig, bg_path: Path | None) -> Image.Image:
    if bg_path is not None:
        try:
            with Image.open(bg_path) as src:
                img = ImageOps.fit(ImageOps.exif_transpose(src).convert("RGB"), (WIDTH, HEIGHT),
                                   Image.Resampling.LANCZOS)
            if cfg.overlay_opacity:
                shade = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
                img = Image.blend(img, shade, cfg.overlay_opacity / 100)
            return img
        except OSError:
            pass  # файл повреждён — рисуем цветной фон
    c1 = _rgb(cfg.background_color)
    if not cfg.gradient:
        return Image.new("RGB", (WIDTH, HEIGHT), c1)
    c2 = _rgb(cfg.background_color2)
    # Диагональный градиент: яркость маски пропорциональна x + y (аффинное отображение горизонтальной полосы).
    strip = Image.linear_gradient("L").transpose(Image.Transpose.ROTATE_90).resize((WIDTH + HEIGHT, 1))
    mask = strip.transform((WIDTH, HEIGHT), Image.Transform.AFFINE, (1, 1, 0, 0, 0, 0), Image.Resampling.BILINEAR)
    return Image.composite(Image.new("RGB", (WIDTH, HEIGHT), c2), Image.new("RGB", (WIDTH, HEIGHT), c1), mask)


def _shape_mask(shape: str, size: int) -> Image.Image:
    """Маска формы аватара со сглаженными краями (рисуется в 4× и уменьшается)."""
    big = size * 4
    mask = Image.new("L", (big, big), 0)
    md = ImageDraw.Draw(mask)
    if shape == "circle":
        md.ellipse((0, 0, big - 1, big - 1), fill=255)
    elif shape == "rounded":
        md.rounded_rectangle((0, 0, big - 1, big - 1), radius=big // 5, fill=255)
    else:
        md.rectangle((0, 0, big, big), fill=255)
    return mask.resize((size, size), Image.Resampling.LANCZOS)


def _avatar(cfg: CardConfig, data: bytes | None, size: int) -> Image.Image:
    if data:
        try:
            with Image.open(io.BytesIO(data)) as src:
                av = ImageOps.fit(src.convert("RGBA"), (size, size), Image.Resampling.LANCZOS)
        except OSError:
            data = None
    if not data:
        av = Image.new("RGBA", (size, size), (88, 101, 242, 255))
        d = ImageDraw.Draw(av)
        d.ellipse((size * 0.3, size * 0.16, size * 0.7, size * 0.56), fill=(255, 255, 255, 230))
        d.ellipse((size * 0.14, size * 0.6, size * 0.86, size * 1.2), fill=(255, 255, 255, 230))
    av.putalpha(_shape_mask(cfg.avatar_shape, size))
    return av


def _paste_avatar(img: Image.Image, cfg: CardConfig, avatar: Image.Image, x: int, y: int) -> None:
    ring = cfg.ring_width
    if ring:
        size = avatar.width + ring * 2
        border = Image.new("RGBA", (size, size), (*_rgb(cfg.accent_color), 255))
        border.putalpha(_shape_mask(cfg.avatar_shape, size))
        img.paste(border, (x - ring, y - ring), border)
    img.paste(avatar, (x, y), avatar)


def _text(img: Image.Image, xy: tuple[int, int], text: str, font, fill: tuple[int, int, int], anchor: str) -> None:  # noqa: ANN001
    """Текст с мягкой тенью для читаемости на любом фоне."""
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((xy[0] + 2, xy[1] + 3), text, font=font, fill=(0, 0, 0, 170), anchor=anchor)
    shadow = shadow.filter(ImageFilter.GaussianBlur(3))
    img.alpha_composite(shadow)
    ImageDraw.Draw(img).text(xy, text, font=font, fill=fill, anchor=anchor)


def render_card(cfg: CardConfig, values: dict[str, object], avatar: bytes | None,
                background: Path | None = None) -> bytes:
    """Рисует баннер и возвращает PNG. Вызывать в отдельном потоке (asyncio.to_thread)."""
    img = _background(cfg, background).convert("RGBA")
    draw = ImageDraw.Draw(img)
    fill = _rgb(cfg.text_color)
    lines = [(render(cfg.title, values), True, 54), (render(cfg.subtitle, values), True, 40),
             (render(cfg.footer, values), False, 28)]
    lines = [(t.strip(), b, s) for t, b, s in lines]

    if cfg.layout == "center":
        av = _avatar(cfg, avatar, AVATAR_SIZE)
        _paste_avatar(img, cfg, av, (WIDTH - AVATAR_SIZE) // 2, 34)
        y = 34 + AVATAR_SIZE + cfg.ring_width + 18
        for text, bold, size in lines:
            if not text:
                continue
            text, font = _fit(draw, text, cfg.font, bold, size, WIDTH - 80)
            _text(img, (WIDTH // 2, y), text, font, fill, "ma")
            y += int(size * 1.25)
    else:
        size = 240
        av = _avatar(cfg, avatar, size)
        x0 = 60
        _paste_avatar(img, cfg, av, x0, (HEIGHT - size) // 2)
        tx = x0 + size + cfg.ring_width + 50
        present = [(t, b, s) for t, b, s in lines if t]
        total = sum(int(s * 1.3) for _, _, s in present)
        y = (HEIGHT - total) // 2
        for text, bold, s in present:
            text, font = _fit(draw, text, cfg.font, bold, s, WIDTH - tx - 40)
            _text(img, (tx, y), text, font, fill, "la")
            y += int(s * 1.3)

    out = io.BytesIO()
    img.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def save_background(card_dir: Path, guild_id: int, kind: str, data: bytes) -> str:
    """Сохраняет картинку фона и возвращает имя файла для CardConfig.background_image."""
    from .menus import detect_image

    if len(data) > MAX_BACKGROUND_BYTES:
        raise ValueError(f"Картинка больше {MAX_BACKGROUND_BYTES // 1024 // 1024} МБ")
    ext = detect_image(data)
    if ext is None:
        raise ValueError("Поддерживаются только изображения PNG, JPEG, GIF и WebP")
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
    except OSError:
        raise ValueError("Файл изображения повреждён") from None
    card_dir.mkdir(parents=True, exist_ok=True)
    name = f"{guild_id}-{kind}-{os.urandom(4).hex()}.{ext}"
    (card_dir / name).write_bytes(data)
    return name


def background_path(card_dir: Path, name: str | None) -> Path | None:
    if not name:
        return None
    path = card_dir / name
    return path if path.is_file() else None
