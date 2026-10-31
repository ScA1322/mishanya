"""Тикеты, меню с кнопками/списком ролей и автообновление через git."""
import base64
import shutil
import subprocess

import pytest
from pydantic import ValidationError

from mishanya.schemas import MenuConfig, TicketsConfig
from mishanya.services.updater import DEPS_FLAG, UpdateError, UpdateService

from .conftest import GUILD_A, GUILD_B, USER_1, USER_2

pytestmark = pytest.mark.anyio

ROLE_1 = "444444444444444441"
ROLE_2 = "444444444444444442"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


# ---------------------------------------------------------------- тикеты

async def test_ticket_numbers_and_limits(app):
    svc = app.tickets
    t1 = await svc.create(GUILD_A, USER_1, "user1", "Вопрос")
    t2 = await svc.create(GUILD_A, USER_2, "user2")
    other = await svc.create(GUILD_B, USER_1, "user1")
    assert (t1.number, t2.number, other.number) == (1, 2, 1)
    await svc.set_channel(t1.id, 555555555555555551)
    assert (await svc.by_channel(555555555555555551)).id == t1.id
    assert await svc.open_count(GUILD_A, USER_1) == 1 and await svc.open_count(GUILD_A) == 2
    assert await svc.close(t1.id, USER_2, "решено")
    assert not await svc.close(t1.id, USER_2)  # повторно не закрывается
    assert await svc.by_channel(555555555555555551) is None
    rows, total = await svc.list(GUILD_A, "closed")
    assert total == 1 and rows[0].close_reason == "решено"
    await svc.delete(t2.id)
    assert (await svc.create(GUILD_A, USER_2, "user2")).number == 2


async def test_tickets_api(authed):
    r = await authed.put(f"/api/guilds/{GUILD_A}/config/tickets",
                         json={**TicketsConfig().model_dump(), "enabled": True, "support_roles": [ROLE_1, ROLE_1]})
    assert r.status_code == 200 and r.json()["config"]["support_roles"] == [ROLE_1]
    data = (await authed.get(f"/api/guilds/{GUILD_A}/tickets")).json()
    assert data == {"total": 0, "open": 0, "items": []}


async def test_close_ticket_from_panel_without_bot(app, authed):
    t = await app.tickets.create(GUILD_A, USER_1, "user1")
    r = await authed.post(f"/api/guilds/{GUILD_A}/tickets/{t.id}/close", json={})
    assert r.status_code == 200
    items = (await authed.get(f"/api/guilds/{GUILD_A}/tickets?status=closed")).json()["items"]
    assert items[0]["close_reason"] == "Закрыт из панели"
    assert (await authed.post(f"/api/guilds/{GUILD_A}/tickets/{t.id}/close", json={})).status_code == 404


# ---------------------------------------------------------------- меню

def menu_body(**kw) -> dict:
    body = {"name": "Роли", "channel_id": "555555555555555551", "content": "Выберите роль",
            "buttons": [{"label": "Сайт", "action": "link", "url": "https://example.com"},
                        {"label": "Правила", "action": "reply", "reply": "Будьте вежливы, {user}"},
                        {"label": "Тикет", "emoji": "🎫", "action": "ticket", "style": "success"}],
            "role_select": {"enabled": True, "options": [{"role_id": ROLE_1, "label": "Игрок"},
                                                         {"role_id": ROLE_2, "label": "Художник", "emoji": "🎨"}]}}
    body.update(kw)
    return body


def test_menu_validation():
    cfg = MenuConfig.model_validate(menu_body())
    assert cfg.buttons[0].style == "link" and cfg.role_select.max_values == 1
    bad = [
        {"buttons": [{"label": "x", "action": "link", "url": "ftp://x"}]},
        {"buttons": [{"label": "x", "action": "role"}]},
        {"buttons": [{"label": "x", "action": "reply", "style": "link", "reply": "a"}]},
        {"buttons": [{"action": "reply", "reply": "a"}]},
        {"buttons": [{"label": str(i), "action": "ticket", "row": 1} for i in range(6)]},
        {"buttons": [{"label": "x", "action": "ticket", "row": 0}]},  # первый ряд занят списком
        {"role_select": {"enabled": True, "options": []}},
        {"role_select": {"enabled": True, "options": [{"role_id": ROLE_1, "label": "a"},
                                                      {"role_id": ROLE_1, "label": "b"}]}},
    ]
    for patch in bad:
        with pytest.raises(ValidationError):
            MenuConfig.model_validate(menu_body(**patch))
    # Максимум выбора не может превышать число ролей
    capped = MenuConfig.model_validate(menu_body(role_select={
        "enabled": True, "max_values": 10, "options": [{"role_id": ROLE_1, "label": "a"}]}))
    assert capped.role_select.max_values == 1


async def test_menus_api_crud_and_image(app, authed):
    base = f"/api/guilds/{GUILD_A}/menus"
    r = await authed.post(base, json=menu_body())
    assert r.status_code == 200, r.text
    menu = r.json()
    assert menu["published"] is False and menu["role_select"]["options"][1]["emoji"] == "🎨"
    mid = menu["id"]
    assert (await authed.put(f"{base}/{mid}", json=menu_body(name="Новое", role_select={"enabled": False}))
            ).json()["role_select"] is None
    assert (await authed.post(base, json=menu_body(buttons=[{"label": "x", "action": "role"}]))).status_code == 422

    # изображение: проверка сигнатуры и раздача файла
    bad = await authed.put(f"{base}/{mid}/image", json={"data": base64.b64encode(b"not an image").decode()})
    assert bad.status_code == 422
    up = await authed.put(f"{base}/{mid}/image",
                          json={"data": "data:image/png;base64," + base64.b64encode(PNG).decode()})
    assert up.status_code == 200 and up.json()["image_url"]
    img = await authed.get(up.json()["image_url"])
    assert img.status_code == 200 and img.content == PNG
    files = list((app.settings.data_dir / "menus").iterdir())
    assert len(files) == 1
    assert (await authed.delete(f"{base}/{mid}/image")).json()["image_url"] is None
    assert not list((app.settings.data_dir / "menus").iterdir())

    # публикация без бота — понятная ошибка
    assert (await authed.post(f"{base}/{mid}/publish")).status_code == 409
    # чужой сервер не видит меню
    assert (await authed.put(f"/api/guilds/{GUILD_B}/menus/{mid}", json=menu_body())).status_code == 404
    assert (await authed.delete(f"{base}/{mid}")).status_code == 200
    assert (await authed.get(base)).json() == []


async def test_guild_delete_cascades(app):
    from mishanya.schemas import MenuConfig as MC

    await app.tickets.create(GUILD_B, USER_1, "u")
    await app.menus.create(GUILD_B, MC.model_validate(menu_body()))
    await app.guilds.delete_data(GUILD_B)
    assert (await app.tickets.list(GUILD_B))[1] == 0 and await app.menus.list(GUILD_B) == []


# ---------------------------------------------------------------- автообновление

def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


@pytest.fixture
def repos(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git не установлен")
    remote, dev, local = tmp_path / "remote.git", tmp_path / "dev", tmp_path / "local"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    _git(tmp_path, "clone", "-q", str(remote), str(dev))
    _git(dev, "checkout", "-q", "-b", "main")
    (dev / "requirements.txt").write_text("a==1\n")
    (dev / "app.txt").write_text("v1\n")
    _git(dev, "add", ".")
    _git(dev, "commit", "-q", "-m", "v1")
    _git(dev, "push", "-q", "-u", "origin", "main")
    _git(tmp_path, "clone", "-q", str(remote), str(local))
    return dev, local


def push_change(dev, name, text, msg):
    (dev / name).write_text(text)
    _git(dev, "commit", "-q", "-am", msg)
    _git(dev, "push", "-q")


async def test_update_check_and_apply(repos, tmp_path):
    dev, local = repos
    db = tmp_path / "db.sqlite"
    import sqlite3

    sqlite3.connect(db).close()
    svc = UpdateService(local, db, tmp_path / "backups")
    state = await svc.check()
    assert state.supported and state.behind == 0 and state.last_error is None
    with pytest.raises(UpdateError, match="Обновлений нет"):
        await svc.apply()

    push_change(dev, "app.txt", "v2\n", "Новая функция")
    state = await svc.check()
    assert state.behind == 1 and state.commits[0]["subject"] == "Новая функция"
    await svc.apply()
    assert (local / "app.txt").read_text() == "v2\n" and svc.state.restart_required
    assert not (local / DEPS_FLAG).exists()
    assert any("update" in p.name for p in (tmp_path / "backups").iterdir())

    push_change(dev, "requirements.txt", "a==2\n", "deps")
    await svc.check()
    await svc.apply()
    assert (local / DEPS_FLAG).exists()


async def test_update_refuses_local_changes(repos, tmp_path):
    dev, local = repos
    push_change(dev, "app.txt", "v2\n", "v2")
    (local / "app.txt").write_text("мои правки\n", encoding="utf-8")
    svc = UpdateService(local, tmp_path / "none.db", tmp_path / "b")
    with pytest.raises(UpdateError, match="изменены вручную"):
        await svc.apply()
    assert (local / "app.txt").read_text(encoding="utf-8") == "мои правки\n"


async def test_update_not_git(tmp_path):
    state = await UpdateService(tmp_path, tmp_path / "db", tmp_path / "b").check()
    assert not state.supported and "git clone" in state.reason


async def test_update_api(authed, app, monkeypatch):
    monkeypatch.setattr(app.updater, "repo_dir", app.settings.data_dir)  # не git-репозиторий
    data = (await authed.post("/api/update/check")).json()
    assert data["supported"] is False and data["launcher"] is False
    r = await authed.post("/api/update/apply")
    assert r.status_code == 409
    s = (await authed.get("/api/settings")).json()
    assert s["auto_update_enabled"] is True and s["update_check_interval_hours"] == 6


# ---------------------------------------------------------------- музыка

def test_music_query_and_queue():
    from mishanya.services.music import MusicError, MusicQueue, Track, classify_query, format_duration

    assert classify_query("lofi beats") == "scsearch1:lofi beats"
    assert classify_query("https://soundcloud.com/artist/song?si=123") == "https://soundcloud.com/artist/song"
    with pytest.raises(MusicError):
        classify_query("https://www.youtube.com/watch?v=x")
    with pytest.raises(MusicError):
        classify_query("  ")
    assert format_duration(65) == "1:05" and format_duration(3725) == "1:02:05" and format_duration(None) == "—"

    q = MusicQueue()
    tracks = [Track(title=str(i), page_url=f"https://soundcloud.com/a/{i}") for i in range(3)]
    assert q.add(tracks, limit=2) == 2
    assert q.next().title == "0"
    q.loop = "track"
    assert q.next().title == "0"
    q.loop = "queue"
    assert q.next().title == "1" and [t.title for t in q.tracks] == ["0"]
    q.loop = "off"
    assert q.next().title == "0" and q.next() is None


async def test_music_api_without_bot(authed):
    data = (await authed.get(f"/api/guilds/{GUILD_A}/music")).json()
    assert data["bot"] is False and data["queue"] == []
    assert (await authed.post(f"/api/guilds/{GUILD_A}/music/skip")).status_code == 409
    assert (await authed.post(f"/api/guilds/{GUILD_A}/music/hack")).status_code == 422
    r = await authed.put(f"/api/guilds/{GUILD_A}/config/music", json={"enabled": True, "default_volume": 300})
    assert r.status_code == 422


# ---------------------------------------------------------------- картинки приветствий

def _png(size=(64, 48), color=(200, 30, 30)) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_render_card_variants(tmp_path):
    import io

    from PIL import Image

    from mishanya.schemas import CardConfig, GreetingMessage
    from mishanya.services.cards import HEIGHT, WIDTH, render_card, save_background

    values = {"username": "очень_длинное_имя_пользователя_" * 4, "member_count": 7}
    bg = tmp_path / save_background(tmp_path, GUILD_A, "join", _png())
    for cfg, background, avatar in [
        (CardConfig(enabled=True), None, None),
        (CardConfig(enabled=True, layout="left", avatar_shape="square", gradient=False, ring_width=0), None, _png()),
        (CardConfig(enabled=True, font="impact", avatar_shape="rounded"), bg, b"broken"),
    ]:
        img = Image.open(io.BytesIO(render_card(cfg, values, avatar, background)))
        assert img.size == (WIDTH, HEIGHT) and img.format == "PNG"
    with pytest.raises(ValueError):
        save_background(tmp_path, GUILD_A, "join", b"not an image")
    # Только картинка, без текста — тоже допустимое приветствие
    GreetingMessage(enabled=True, channel_id="555555555555555551", card=CardConfig(enabled=True))
    with pytest.raises(ValidationError):
        CardConfig(background_image="../../.env")


async def test_card_api(authed, app):
    base = f"/api/guilds/{GUILD_A}/welcome"
    r = await authed.post(f"{base}/card-preview", json={"kind": "join", "card": {"enabled": True}})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    up = await authed.put(f"{base}/leave/background", json={"data": base64.b64encode(_png()).decode()})
    assert up.status_code == 200
    name = up.json()["background_image"]
    cfg = (await authed.get(f"/api/guilds/{GUILD_A}/config/welcome")).json()
    assert cfg["leave"]["card"]["background_image"] is None  # сохраняется только кнопкой «Сохранить»
    cfg["leave"]["card"]["background_image"] = name
    assert (await authed.put(f"/api/guilds/{GUILD_A}/config/welcome", json=cfg)).status_code == 200
    assert (await authed.get(f"{base}/background/{name}")).status_code == 200
    assert (await authed.get(f"/api/guilds/{GUILD_B}/welcome/background/{name}")).status_code == 404
    r = await authed.post(f"{base}/card-preview", json={"kind": "leave", "card": cfg["leave"]["card"]})
    assert r.status_code == 200
    # две новые загрузки подряд: сохранённая остаётся, промежуточная удаляется
    first = (await authed.put(f"{base}/leave/background", json={"data": base64.b64encode(_png()).decode()})).json()
    second = (await authed.put(f"{base}/leave/background", json={"data": base64.b64encode(_png()).decode()})).json()
    files = {p.name for p in (app.settings.data_dir / "cards").iterdir()}
    assert files == {name, second["background_image"]} and first["background_image"] not in files
    bad = await authed.put(f"{base}/join/background", json={"data": base64.b64encode(b"zzz").decode()})
    assert bad.status_code == 422
