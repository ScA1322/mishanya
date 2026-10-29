from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from mishanya.api.app import create_app
from mishanya.config import Settings
from mishanya.core import App
from mishanya.db import Database

PASSWORD = "test-password-123"
GUILD_A = 111111111111111111
GUILD_B = 222222222222222222
USER_1 = 333333333333333331
USER_2 = 333333333333333332


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(panel_password=PASSWORD, data_dir=tmp_path / "data", backup_dir=tmp_path / "backups",
                    frontend_dist=tmp_path / "dist", open_browser=False)


@pytest.fixture
async def app(settings: Settings):
    db = Database(settings.db_path)
    await db.init()
    application = App(settings, db)
    await application.guilds.upsert(GUILD_A, "Guild A")
    await application.guilds.upsert(GUILD_B, "Guild B")
    yield application
    await db.dispose()


@pytest.fixture
async def client(app: App):
    api = create_app(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://127.0.0.1") as c:
        yield c


@pytest.fixture
async def authed(client: httpx.AsyncClient):
    """Клиент с выполненным входом и CSRF-заголовком."""
    r = await client.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return client
