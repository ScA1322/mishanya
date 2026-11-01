"""Посты: схема, API, загрузка изображений и сборка embed."""
from __future__ import annotations

import base64
import os
import time

import pytest
from pydantic import ValidationError

from mishanya.bot.cogs.posts import build_post_embed
from mishanya.schemas import PostConfig, PostEmbed

pytestmark = pytest.mark.anyio

GID = "111111111111111111"
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 64).decode()


def test_post_schema_limits():
    PostConfig(name="x", embeds=[{"description": "a"}] * 10)
    with pytest.raises(ValidationError):
        PostConfig(name="x", embeds=[{"description": "a"}] * 11)
    with pytest.raises(ValidationError, match="пустой блок"):
        PostConfig(name="x", embeds=[{}])
    with pytest.raises(ValidationError, match="Слишком много текста"):
        PostConfig(name="x", embeds=[{"description": "a" * 4000}, {"description": "b" * 2500}])
    with pytest.raises(ValidationError, match="https"):
        PostConfig(name="x", embeds=[{"image": "javascript:alert(1)"}])
    with pytest.raises(ValidationError, match="имя загруженного"):
        PostConfig(name="x", embeds=[{"image": "upload:../../.env"}])
    with pytest.raises(ValidationError, match="только загруженные"):
        PostConfig(name="x", attachments=["https://example.com/a.png"])
    cfg = PostConfig(name="x", embeds=[{"image": "upload:a.png", "thumbnail": "upload:a.png"}],
                     attachments=["upload:b.jpg"])
    assert cfg.uploads() == ["a.png", "b.jpg"]


def test_build_post_embed():
    e = PostEmbed(color="#ff0000", title="Привет {server}", description="**текст**", author_name="Автор",
                  fields=[{"name": "A", "value": "1", "inline": True}], image="upload:a.png",
                  thumbnail="https://example.com/t.png", footer="низ", timestamp=True)
    emb = build_post_embed(e, {"server": "Сервер"}, {"a.png": "image1.png"})
    assert emb.title == "Привет Сервер" and emb.color.value == 0xFF0000
    assert emb.image.url == "attachment://image1.png" and emb.thumbnail.url == "https://example.com/t.png"
    assert emb.fields[0].inline and emb.author.name == "Автор" and emb.footer.text == "низ" and emb.timestamp


async def test_posts_api(authed, app):
    r = await authed.post(f"/api/guilds/{GID}/posts/files", json={"data": f"data:image/png;base64,{PNG}"})
    assert r.status_code == 200, r.text
    ref = r.json()["ref"]
    assert (await authed.get(r.json()["url"])).status_code == 200
    bad = await authed.post(f"/api/guilds/{GID}/posts/files", json={"data": base64.b64encode(b"hello").decode()})
    assert bad.status_code == 422

    body = {"name": "Спонсорство", "channel_id": None, "content": "", "ping": False,
            "embeds": [{"color": "#2B2D31", "description": "*текст* <@&123456789012345678>", "image": ref}],
            "attachments": [ref]}
    r = await authed.post(f"/api/guilds/{GID}/posts", json=body)
    assert r.status_code == 200, r.text
    post = r.json()
    assert post["embeds"][0]["image"] == ref and post["attachments"] == [ref] and not post["published"]

    body["name"] = "Новое имя"
    body["embeds"][0]["title"] = "Заголовок"
    r = await authed.put(f"/api/guilds/{GID}/posts/{post['id']}", json=body)
    assert r.status_code == 200 and r.json()["name"] == "Новое имя"
    assert [p["name"] for p in (await authed.get(f"/api/guilds/{GID}/posts")).json()] == ["Новое имя"]

    missing = {**body, "attachments": ["upload:img-000000000000.png"]}
    assert (await authed.put(f"/api/guilds/{GID}/posts/{post['id']}", json=missing)).status_code == 422
    assert (await authed.post(f"/api/guilds/{GID}/posts", json={**body, "embeds": [{}]})).status_code == 422
    # без бота опубликовать нельзя — понятная ошибка, а не падение
    assert (await authed.post(f"/api/guilds/{GID}/posts/{post['id']}/publish")).status_code == 409
    assert (await authed.delete(f"/api/guilds/{GID}/posts/{post['id']}")).status_code == 200
    assert (await authed.get(f"/api/guilds/{GID}/posts")).json() == []


async def test_post_upload_cleanup(app):
    gid = int(GID)
    keep = app.posts.save_upload(gid, base64.b64decode(PNG))
    old = app.posts.save_upload(gid, base64.b64decode(PNG))
    fresh = app.posts.save_upload(gid, base64.b64decode(PNG))
    week_ago = time.time() - 7 * 86400
    for name in (keep, old):
        os.utime(app.posts.file_path(gid, name), (week_ago, week_ago))
    await app.posts.create(gid, PostConfig(name="p", attachments=[f"upload:{keep}"]))
    assert app.posts.file_path(gid, keep) is not None  # используется
    assert app.posts.file_path(gid, old) is None  # старый и ничей — удалён
    assert app.posts.file_path(gid, fresh) is not None  # свежая загрузка ждёт сохранения поста
