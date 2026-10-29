"""Проверка запуска приложения: конфигурация, старт панели в одном процессе, остановка через stop.flag."""
import asyncio
import socket

import httpx
import pytest

from mishanya import main as main_mod
from mishanya.config import ConfigError, Settings, load_settings
from mishanya.core import App, RedactingFilter
from mishanya.db import Database

pytestmark = pytest.mark.anyio


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_settings_validation():
    assert Settings(panel_password="12345678").validate() == []
    assert any("PANEL_PASSWORD" in p for p in Settings(panel_password="short").validate())
    problems = Settings(panel_password="12345678", panel_host="0.0.0.0").validate()
    assert any("ALLOW_REMOTE_PANEL" in p for p in problems)
    assert Settings(panel_password="12345678", panel_host="0.0.0.0", allow_remote_panel=True).validate() == []
    assert not Settings(discord_token="PASTE_YOUR_TOKEN_HERE").token_configured


def test_load_settings_from_env_file(tmp_path, monkeypatch):
    for k in ("DISCORD_TOKEN", "PANEL_PASSWORD", "PANEL_PORT", "OWNER_ID", "DATA_DIR"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text("PANEL_PASSWORD=секретный-пароль\nPANEL_PORT=9000\nOWNER_ID=123456789012345678\n", encoding="utf-8")
    s = load_settings(env)
    assert s.panel_password == "секретный-пароль" and s.panel_port == 9000 and s.owner_id == 123456789012345678
    monkeypatch.setenv("OWNER_ID", "abc")
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "missing.env")


def test_main_refuses_without_password(monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "load_settings", lambda: Settings(panel_password=""))
    assert main_mod.main() == 2
    assert "PANEL_PASSWORD" in capsys.readouterr().out


def test_token_redaction():
    import logging

    token = "MTIzNDU2Nzg5MDEyMzQ1Njc4.GabcdE.abcdefghijklmnopqrstuvwxyz0123456789"
    rec = logging.LogRecord("x", logging.INFO, "", 0, f"token={token} pass=hunter22", None, None)
    RedactingFilter(["hunter22"]).filter(rec)
    assert token not in rec.getMessage() and "hunter22" not in rec.getMessage()


async def test_full_startup_and_stop_flag(tmp_path):
    port = _free_port()
    settings = Settings(panel_password="12345678", panel_port=port, data_dir=tmp_path / "data",
                        backup_dir=tmp_path / "b", frontend_dist=tmp_path / "dist", open_browser=False)
    settings.data_dir.mkdir(parents=True)
    app = App(settings, Database(settings.db_path))
    task = asyncio.create_task(main_mod.run(settings, app))
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
        for _ in range(100):
            try:
                r = await c.get("/api/auth/me")
                break
            except httpx.TransportError:
                await asyncio.sleep(0.1)
        else:
            pytest.fail("Панель не запустилась")
        assert r.status_code == 200 and r.json() == {"authenticated": False, "csrf_token": None}
        login = await c.post("/api/auth/login", json={"password": "12345678"})
        status = (await c.get("/api/status")).json()
        assert login.status_code == 200
        assert status["bot"]["token_configured"] is False and "DISCORD_TOKEN" in status["bot"]["error"]
    assert (settings.data_dir / "mishanya.pid").exists()
    (settings.data_dir / "stop.flag").touch()
    assert await asyncio.wait_for(task, 10) == 0
    assert not (settings.data_dir / "mishanya.pid").exists()
    assert settings.db_path.exists()
