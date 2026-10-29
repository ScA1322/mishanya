import pytest

from mishanya.api import auth

from .conftest import GUILD_A, GUILD_B, PASSWORD, USER_1

pytestmark = pytest.mark.anyio
GA, GB = str(GUILD_A), str(GUILD_B)


@pytest.fixture(autouse=True)
def reset_limiter():
    auth.limiter.failures.clear()
    yield
    auth.limiter.failures.clear()


async def test_protected_endpoints_require_login(client):
    for path in ["/api/status", "/api/guilds", f"/api/guilds/{GA}/config/leveling", "/api/backups", "/api/settings"]:
        r = await client.get(path)
        assert r.status_code == 401, path
    r = await client.put(f"/api/guilds/{GA}/config/leveling", json={})
    assert r.status_code == 401


async def test_login_wrong_password_and_lockout(client):
    for _ in range(auth.MAX_FAILURES):
        r = await client.post("/api/auth/login", json={"password": "wrong"})
        assert r.status_code == 401
    r = await client.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 429  # блокировка действует даже для верного пароля


async def test_login_sets_httponly_cookie(client):
    r = await client.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert PASSWORD not in r.text
    me = (await client.get("/api/auth/me")).json()
    assert me["authenticated"] and me["csrf_token"] == r.json()["csrf_token"]


async def test_csrf_required_for_mutations(authed):
    token = authed.headers.pop("X-CSRF-Token")
    r = await authed.put(f"/api/guilds/{GA}/config/leveling", json={"xp_min": 1, "xp_max": 2})
    assert r.status_code == 403
    r = await authed.put(f"/api/guilds/{GA}/config/leveling", json={"xp_min": 1, "xp_max": 2},
                         headers={"X-CSRF-Token": "forged"})
    assert r.status_code == 403
    authed.headers["X-CSRF-Token"] = token
    r = await authed.put(f"/api/guilds/{GA}/config/leveling", json={"xp_min": 1, "xp_max": 2})
    assert r.status_code == 200


async def test_logout_invalidates_session(authed):
    assert (await authed.post("/api/auth/logout")).status_code == 200
    assert (await authed.get("/api/status")).status_code == 401


async def test_foreign_host_rejected(client):
    r = await client.get("/api/auth/me", headers={"Host": "evil.example.com"})
    assert r.status_code == 400


async def test_security_headers(authed):
    r = await authed.get("/api/status")
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


async def test_status_and_guilds(authed):
    s = (await authed.get("/api/status")).json()
    assert s["bot"]["token_configured"] is False and s["db"]["ok"] is True
    guilds = (await authed.get("/api/guilds")).json()
    assert {g["id"] for g in guilds} == {GA, GB}
    assert (await authed.get("/api/guilds/123456789012345678")).status_code == 404
    assert (await authed.get("/api/guilds/notanid")).status_code == 422


async def test_config_roundtrip_and_isolation(authed):
    r = await authed.get(f"/api/guilds/{GA}/config/leveling")
    cfg = r.json()
    cfg["xp_min"], cfg["xp_max"] = 2, 4
    r = await authed.put(f"/api/guilds/{GA}/config/leveling", json=cfg)
    assert r.status_code == 200 and r.json()["config"]["xp_max"] == 4
    assert (await authed.get(f"/api/guilds/{GA}/config/leveling")).json()["xp_max"] == 4
    assert (await authed.get(f"/api/guilds/{GB}/config/leveling")).json()["xp_max"] == 25


async def test_config_validation_errors(authed):
    r = await authed.put(f"/api/guilds/{GA}/config/leveling", json={"xp_min": 50, "xp_max": 10})
    assert r.status_code == 422
    assert any("Минимальный XP" in d["msg"] for d in r.json()["detail"])
    r = await authed.put(f"/api/guilds/{GA}/config/autoroles", json={"roles": ["<script>"]})
    assert r.status_code == 422
    assert (await authed.get(f"/api/guilds/{GA}/config/nonexistent")).status_code == 404


async def test_formula_change_recalculates(authed, app):
    await authed.put(f"/api/guilds/{GA}/members/{USER_1}/xp", json={"mode": "set", "value": 1000})
    r = await authed.put(f"/api/guilds/{GA}/config/leveling", json={"formula_a": 0, "formula_b": 0, "formula_c": 10})
    assert r.json()["recalculated"] == 1
    assert (await app.leveling.get_member(GUILD_A, USER_1)).level == 100


async def test_leaderboard_and_xp_editing(authed):
    r = await authed.put(f"/api/guilds/{GA}/members/{USER_1}/xp", json={"mode": "level", "value": 3})
    assert r.status_code == 200 and r.json()["level"] == 3
    r = await authed.put(f"/api/guilds/{GA}/members/{USER_1}/xp", json={"mode": "add", "value": 10})
    assert r.json()["xp"] == 485
    lb = (await authed.get(f"/api/guilds/{GA}/leaderboard")).json()
    assert lb["total"] == 1 and lb["items"][0]["rank"] == 1 and lb["items"][0]["level"] == 3
    assert (await authed.get(f"/api/guilds/{GB}/leaderboard")).json()["total"] == 0
    assert (await authed.delete(f"/api/guilds/{GA}/members/{USER_1}/xp")).json()["removed"] is True


async def test_reset_requires_confirmation(authed):
    assert (await authed.post(f"/api/guilds/{GA}/leveling/reset", json={"confirm": "да"})).status_code == 400
    assert (await authed.post(f"/api/guilds/{GA}/leveling/reset", json={"confirm": "СБРОС"})).status_code == 200


async def test_export_import(authed):
    await authed.put(f"/api/guilds/{GA}/members/{USER_1}/xp", json={"mode": "set", "value": 777})
    data = (await authed.get(f"/api/guilds/{GA}/leveling/export")).json()
    r = await authed.post(f"/api/guilds/{GB}/leveling/import?mode=replace", json=data)
    assert r.json()["imported"] == 1
    lb = (await authed.get(f"/api/guilds/{GB}/leaderboard")).json()
    assert lb["items"][0]["xp"] == 777
    bad = await authed.post(f"/api/guilds/{GB}/leveling/import", json={"format": "other", "entries": []})
    assert bad.status_code == 400


async def test_rewards_api(authed):
    r = await authed.post(f"/api/guilds/{GA}/rewards", json={"level": 5, "role_id": "100000000000000001"})
    assert r.status_code == 200
    rid = r.json()["id"]
    dup = await authed.post(f"/api/guilds/{GA}/rewards", json={"level": 5, "role_id": "100000000000000001"})
    assert dup.status_code == 409
    assert (await authed.patch(f"/api/guilds/{GA}/rewards/{rid}", json={"enabled": False})).json()["enabled"] is False
    assert (await authed.delete(f"/api/guilds/{GB}/rewards/{rid}")).status_code == 404
    assert (await authed.delete(f"/api/guilds/{GA}/rewards/{rid}")).status_code == 200


async def test_custom_commands_api(authed):
    body = {"name": "rules", "response": "Правила {server}", "cooldown_seconds": 5}
    r = await authed.post(f"/api/guilds/{GA}/commands", json=body)
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    assert (await authed.post(f"/api/guilds/{GA}/commands", json=body)).status_code == 409
    assert (await authed.post(f"/api/guilds/{GA}/commands", json={"name": "kick", "response": "x"})).status_code == 422
    body["response"] = "Новые правила"
    assert (await authed.put(f"/api/guilds/{GA}/commands/{cid}", json=body)).json()["response"] == "Новые правила"
    assert (await authed.get(f"/api/guilds/{GB}/commands")).json() == []
    assert (await authed.delete(f"/api/guilds/{GA}/commands/{cid}")).status_code == 200


async def test_warnings_and_cases_api(authed, app):
    w = await app.moderation.add_warning(GUILD_A, USER_1, 5, "спам")
    data = (await authed.get(f"/api/guilds/{GA}/warnings")).json()
    assert data["total"] == 1
    assert (await authed.delete(f"/api/guilds/{GB}/warnings/{w.id}")).status_code == 404
    assert (await authed.delete(f"/api/guilds/{GA}/warnings/{w.id}")).status_code == 200
    cases = (await authed.get(f"/api/guilds/{GA}/cases")).json()
    assert cases["items"][0]["action"] == "unwarn"


async def test_logs_api(authed, app):
    await app.eventlog.add(GUILD_A, "member_join", "вступил")
    assert (await authed.get(f"/api/guilds/{GA}/logs?type=member_join")).json()["total"] == 1
    assert (await authed.get(f"/api/guilds/{GA}/logs?type=bad")).status_code == 422
    assert (await authed.delete(f"/api/guilds/{GA}/logs")).json()["removed"] == 1


async def test_preview_renders_like_bot(authed):
    r = await authed.post("/api/preview", json={"text": "Привет, {user}! Уровень {level}", "extra": {"level": 7},
                                                "embed": {"enabled": True, "title": "{server}", "color": "#ff0000"}})
    data = r.json()
    assert data["text"] == "Привет, @Участник! Уровень 7"
    assert data["embed"]["title"] == "Мой сервер"


async def test_backup_restore_api(authed, app):
    await authed.put(f"/api/guilds/{GA}/config/leveling", json={"xp_min": 3, "xp_max": 3})
    name = (await authed.post("/api/backups")).json()["name"]
    await authed.put(f"/api/guilds/{GA}/config/leveling", json={"xp_min": 9, "xp_max": 9})
    r = await authed.post("/api/backups/restore", json={"name": name})
    assert r.status_code == 200, r.text
    # сессия хранится в базе: копия создана после входа, поэтому сессия сохранилась
    assert (await authed.get(f"/api/guilds/{GA}/config/leveling")).json()["xp_min"] == 3
    assert (await authed.post("/api/backups/restore", json={"name": "../../etc"})).status_code == 400
    assert any(b["name"] == name for b in (await authed.get("/api/backups")).json())


async def test_delete_guild_data_api(authed):
    assert (await authed.delete(f"/api/guilds/{GB}")).status_code == 200
    assert (await authed.get(f"/api/guilds/{GB}")).status_code == 404


async def test_global_settings_api(authed):
    s = (await authed.get("/api/settings")).json()
    s["log_retention_days"] = 0
    assert (await authed.put("/api/settings", json=s)).status_code == 422
    s["log_retention_days"] = 14
    assert (await authed.put("/api/settings", json=s)).json()["log_retention_days"] == 14


async def test_spa_fallback_without_build(client):
    r = await client.get("/servers/123")
    assert r.status_code == 200 and "Mishanya" in r.text
    assert (await client.get("/api/unknown")).status_code == 404
