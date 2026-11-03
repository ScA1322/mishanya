"""Болтовня: генерация реплик через DeepSeek (OpenAI-совместимый API) и запасные фразы."""
from __future__ import annotations

import logging
import random
import re
from pathlib import Path

import aiohttp

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 40
MAX_LINE = 400  # символов одного сообщения из чата в контексте


class ChatterError(Exception):
    """Ошибка генерации; текст показывается в панели и в журнале."""


def format_history(lines: list[tuple[str, str, bool]], bot_name: str) -> str:
    """lines: (автор, текст, это_бот). Возвращает переписку для промпта, старые сообщения сверху."""
    out = []
    for author, text, is_me in lines:
        text = " ".join(text.split())[:MAX_LINE]
        if text:
            out.append(f"{bot_name if is_me else author}: {text}")
    return "\n".join(out)


def clean_reply(text: str, bot_name: str) -> str:
    """Убирает то, что модели любят добавлять: своё имя в начале, кавычки, массовые упоминания."""
    text = text.strip()
    text = re.sub(rf"^(?:{re.escape(bot_name)}|Мишаня|Mishanya)\s*:\s*", "", text, flags=re.IGNORECASE)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'«»":
        text = text[1:-1].strip()
    if text.startswith("«") and text.endswith("»"):
        text = text[1:-1].strip()
    text = text.replace("@everyone", "everyone").replace("@here", "here")
    return text[:2000]


def pick_phrase(phrases: list[str]) -> str | None:
    return random.choice(phrases) if phrases else None


async def generate(*, api_key: str, base_url: str, model: str, system_prompt: str, history: str, channel: str,
                   bot_name: str, mentioned: bool, temperature: float, max_tokens: int,
                   task: str | None = None, memory: list[str] | None = None, images: list[str] | None = None,
                   raw: bool = False, context: str = "") -> str:
    """Реплика модели. raw=True — вернуть ответ как есть (JSON с действиями), images — ссылки на картинки
    (нужна модель со зрением), memory — что бот помнит, context — служебные сведения (люди, каналы)."""
    if not api_key:
        raise ChatterError("Ключ DeepSeek не задан")
    task = task or ("Тебя упомянули или ответили на твоё сообщение — ответь на последнее сообщение."
                    if mentioned else "Влезь в разговор: напиши одно своё сообщение по теме последних сообщений.")
    if memory:
        system_prompt += ("\n\nЧто ты помнишь о сервере и людях (узнал сам; используй к месту, не перечисляй):\n"
                          + "\n".join(f"- {m}" for m in memory))
    user = (f"{context}\n\n" if context else "") + \
        f"Канал #{channel}. Последние сообщения (сверху старые):\n{history or '(сообщений нет)'}\n\n{task}"
    content: str | list = user
    if images:
        content = [{"type": "text", "text": user + "\nК последнему сообщению приложены картинки — посмотри их."}]
        content += [{"type": "image_url", "image_url": {"url": u}} for u in images[:4]]
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as s:
            async with s.post(f"{base_url}/chat/completions", json=body, headers=headers) as r:
                if r.status == 401:
                    raise ChatterError("DeepSeek не принял ключ (401). Проверьте DEEPSEEK_API_KEY.")
                if r.status == 402:
                    raise ChatterError("На балансе DeepSeek закончились деньги (402). Пополните его на "
                                       "platform.deepseek.com.")
                if r.status == 429:
                    raise ChatterError("DeepSeek просит подождать: слишком много запросов (429).")
                if r.status >= 400:
                    detail = (await r.text())[:200]
                    raise ChatterError(f"DeepSeek ответил ошибкой {r.status}: {detail}")
                data = await r.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise ChatterError(f"Нет связи с DeepSeek: {exc or 'тайм-аут'}") from None
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise ChatterError("DeepSeek вернул ответ непонятного формата") from None
    if raw:
        if not text.strip():
            raise ChatterError("DeepSeek вернул пустой ответ")
        return text
    text = clean_reply(text, bot_name)
    if not text:
        raise ChatterError("DeepSeek вернул пустой ответ")
    return text


def vision_test_image() -> tuple[str, str]:
    """Картинка для проверки зрения: крупная надпись со случайным числом. Возвращает (data URL, что на ней написано)."""
    import base64
    import io

    from PIL import Image, ImageDraw, ImageFont

    secret = f"КОТ {random.randint(10, 99)}"
    img = Image.new("RGB", (640, 260), (250, 245, 230))
    draw = ImageDraw.Draw(img)
    draw.text((320, 130), secret, fill=(20, 20, 20), anchor="mm", font=ImageFont.load_default(size=120))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), secret


def explain_vision_error(error: str, model: str) -> str:
    """Человеческое объяснение, почему модель не смотрит картинки."""
    low = error.lower()
    kind = model.lower()
    if any(k in kind for k in ("image", "dall-e", "dalle", "flux", "midjourney", "stable-diffusion", "sdxl")):
        return (f"«{model}» — модель, которая РИСУЕТ картинки, а не смотрит их: через чат она не работает. "
                "Нужна обычная чат-модель со зрением: gpt-4o-mini, gpt-4o, gpt-4.1-mini или gemini-…-flash.")
    if any(k in kind for k in ("tts", "whisper", "transcribe", "embedding", "moderation")):
        return f"«{model}» — не чат-модель (озвучка, распознавание речи или эмбеддинги). Нужна чат-модель со зрением, например gpt-4o-mini."
    if "404" in low or "not found" in low or "does not exist" in low or "unknown model" in low:
        return f"У сервиса нет модели «{model}». Проверьте название в его списке моделей."
    if "image" in low or "vision" in low or "multimodal" in low or "content" in low and "400" in low:
        return f"Модель «{model}» не принимает картинки. Выберите модель со зрением (например gpt-4o-mini)."
    if "401" in low:
        return "Сервис не принял ключ."
    return error


def set_env_value(env_path: Path, key: str, value: str) -> None:
    """Записывает KEY=value в .env (заменяет существующую строку или добавляет в конец)."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    pattern = re.compile(rf"^\s*#?\s*{re.escape(key)}\s*=")
    new = f"{key}={value}"
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = new
            break
    else:
        lines.append(new)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
