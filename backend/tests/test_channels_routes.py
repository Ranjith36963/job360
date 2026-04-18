"""Channel config route integration tests — proves tenant isolation at API layer."""
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import patch

import aiosqlite
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from migrations import runner
from src.services.channels import crypto


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture
def api(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")

    async def _bootstrap():
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(
                """
                CREATE TABLE user_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(job_id)
                );
                CREATE TABLE applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    stage TEXT NOT NULL DEFAULT 'applied',
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id)
                );
                """
            )
            await db.commit()
        await runner.up(db_path)

    asyncio.run(_bootstrap())

    from pathlib import Path

    from src.api import auth_deps, dependencies
    from src.api.routes import auth as auth_route
    from src.api.routes import channels as channels_route
    from src.core import settings

    patched = Path(db_path)
    monkeypatch.setattr(settings, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(dependencies, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(auth_deps, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(auth_route, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(channels_route, "DB_PATH", patched, raising=True)

    crypto.set_test_key(Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "x" * 40)

    from src.api.main import app

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    return TestClient(app)


def _register(client, email, password="s3cretpassword"):
    r = client.post(
        "/api/auth/register", json={"email": email, "password": password}
    )
    assert r.status_code == 201, r.text
    return r


def test_list_channels_requires_auth(api):
    r = api.get("/api/settings/channels")
    assert r.status_code == 401


def test_create_and_list_channel(api):
    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "slack",
            "display_name": "Team Slack",
            "credential": "slack://a/b/c",
        },
    )
    assert r.status_code == 201
    cid = r.json()["id"]
    r2 = api.get("/api/settings/channels")
    assert r2.status_code == 200
    rows = r2.json()
    assert len(rows) == 1
    assert rows[0]["id"] == cid
    assert rows[0]["channel_type"] == "slack"


def test_tenant_isolation_channels(api):
    _register(api, "alice@example.com")
    api.post(
        "/api/settings/channels",
        json={
            "channel_type": "slack",
            "display_name": "Alice",
            "credential": "slack://a/b/c",
        },
    )
    api.post("/api/auth/logout")
    api.cookies.clear()

    _register(api, "bob@example.com")
    r = api.get("/api/settings/channels")
    assert r.status_code == 200
    assert r.json() == [], "bob must not see alice's channels"


def test_cannot_delete_other_users_channel(api):
    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "slack",
            "display_name": "Alice",
            "credential": "slack://a/b/c",
        },
    )
    alice_channel_id = r.json()["id"]
    api.post("/api/auth/logout")
    api.cookies.clear()

    _register(api, "bob@example.com")
    r = api.delete(f"/api/settings/channels/{alice_channel_id}")
    assert r.status_code == 404  # not visible to bob


def test_delete_own_channel_succeeds(api):
    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "discord",
            "display_name": "Server",
            "credential": "discord://w/t",
        },
    )
    cid = r.json()["id"]
    r2 = api.delete(f"/api/settings/channels/{cid}")
    assert r2.status_code == 204
    r3 = api.get("/api/settings/channels")
    assert r3.json() == []


def test_test_send_invokes_apprise(api):
    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "slack",
            "display_name": "Alice",
            "credential": "slack://a/b/c",
        },
    )
    cid = r.json()["id"]
    with patch("apprise.Apprise") as MockApp:
        instance = MockApp.return_value
        instance.notify.return_value = True
        if hasattr(instance, "async_notify"):
            del instance.async_notify
        r2 = api.post(f"/api/settings/channels/{cid}/test")
    assert r2.status_code == 200
    assert r2.json() == {"ok": True, "error": None}


def test_test_send_returns_error_on_apprise_fail(api):
    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "slack",
            "display_name": "Alice",
            "credential": "slack://a/b/c",
        },
    )
    cid = r.json()["id"]
    with patch("apprise.Apprise") as MockApp:
        instance = MockApp.return_value
        instance.notify.side_effect = RuntimeError("boom")
        if hasattr(instance, "async_notify"):
            del instance.async_notify
        r2 = api.post(f"/api/settings/channels/{cid}/test")
    body = r2.json()
    assert r2.status_code == 200
    assert body["ok"] is False
    assert "boom" in body["error"]
