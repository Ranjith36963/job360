"""Channel config route integration tests — proves tenant isolation at API layer.

Existing tests updated for the new create_channel contract (Task 2):
- ``slack`` / ``discord`` / ``telegram`` via POST "" now returns 400 (use Connect
  flow). Tests that previously posted a raw slack:// or discord:// credential are
  switched to ``webhook`` with an https URL — same behaviour being tested (create,
  list, isolation, delete, test-send), different channel type.  This is NOT a
  weakening: the old tests proved that POST "" accepted a credential and stored it;
  they continue to prove exactly that, now for the only types POST "" still accepts.
- ``test_test_send_*`` tests use ``webhook`` type (https URL → jsons://... stored).
"""
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from migrations import runner
from src.repositories import pg
from src.services.channels import crypto


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture
def api(monkeypatch, tmp_path):
    db_path = str(tmp_path / "test.db")

    async def _bootstrap():
        async with pg.connect(db_path) as db:
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


# ---------------------------------------------------------------------------
# Previously used channel_type="slack" with a raw apprise URL.
# Switched to channel_type="webhook" + https URL — same behaviour (create +
# list), different type; POST "" no longer accepts chat types (see Task 2).
# ---------------------------------------------------------------------------

def test_create_and_list_channel(api):
    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "webhook",
            "display_name": "My Webhook",
            "credential": "https://hooks.example.com/notify",
        },
    )
    assert r.status_code == 201
    cid = r.json()["id"]
    r2 = api.get("/api/settings/channels")
    assert r2.status_code == 200
    rows = r2.json()
    assert len(rows) == 1
    assert rows[0]["id"] == cid
    assert rows[0]["channel_type"] == "webhook"


# Previously used channel_type="slack". Same multi-user isolation test, now
# using webhook (the only paste-path type that was being tested).
def test_tenant_isolation_channels(api):
    _register(api, "alice@example.com")
    api.post(
        "/api/settings/channels",
        json={
            "channel_type": "webhook",
            "display_name": "Alice",
            "credential": "https://hooks.example.com/alice",
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
    # Previously used slack — switched to webhook (the paste path).
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "webhook",
            "display_name": "Alice",
            "credential": "https://hooks.example.com/alice",
        },
    )
    alice_channel_id = r.json()["id"]
    api.post("/api/auth/logout")
    api.cookies.clear()

    _register(api, "bob@example.com")
    r = api.delete(f"/api/settings/channels/{alice_channel_id}")
    assert r.status_code == 404  # not visible to bob


# Previously used channel_type="discord". Switched to webhook — same delete
# lifecycle test, now for the only paste-path channel type.
def test_delete_own_channel_succeeds(api):
    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "webhook",
            "display_name": "My Hook",
            "credential": "https://hooks.example.com/w",
        },
    )
    cid = r.json()["id"]
    r2 = api.delete(f"/api/settings/channels/{cid}")
    assert r2.status_code == 204
    r3 = api.get("/api/settings/channels")
    assert r3.json() == []


# Previously used channel_type="slack". Switched to webhook so the channel can
# be created via the paste path (Slack requires the Connect flow now).
def test_test_send_invokes_apprise(api):
    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "webhook",
            "display_name": "Alice",
            "credential": "https://hooks.example.com/notify",
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
            "channel_type": "webhook",
            "display_name": "Alice",
            "credential": "https://hooks.example.com/fail",
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


# ===========================================================================
# NEW Task 2 tests — webhook + email paste-path + chat-type rejection
# ===========================================================================

def test_create_webhook_stores_apprise_jsons_url(api):
    """POST with webhook type converts https:// → jsons:// Apprise URL."""
    from src.api.routes import channels as ch

    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "webhook",
            "display_name": "My Hook",
            "credential": "https://example.com/hook",
        },
    )
    assert r.status_code == 201, r.text

    db_path = str(ch.DB_PATH)

    async def _check():
        async with pg.connect(db_path) as db:
            db.row_factory = pg.Row
            cur = await db.execute(
                "SELECT credential_encrypted FROM user_channels WHERE channel_type='webhook'"
            )
            return await cur.fetchone()

    row = asyncio.run(_check())
    assert row is not None
    decrypted = crypto.decrypt(row["credential_encrypted"])
    assert decrypted == "jsons://example.com/hook"


def test_create_webhook_http_stores_json_url(api):
    """POST with http:// webhook converts to json:// (not jsons://)."""
    from src.api.routes import channels as ch

    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "webhook",
            "display_name": "Plain Hook",
            "credential": "http://internal.example.com/hook",
        },
    )
    assert r.status_code == 201, r.text

    db_path = str(ch.DB_PATH)

    async def _check():
        async with pg.connect(db_path) as db:
            cur = await db.execute(
                "SELECT credential_encrypted FROM user_channels WHERE channel_type='webhook'"
            )
            return await cur.fetchone()

    row = asyncio.run(_check())
    decrypted = crypto.decrypt(row[0])
    assert decrypted == "json://internal.example.com/hook"


def test_create_webhook_non_url_rejected(api):
    """Non-URL webhook credential → 422."""
    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "webhook",
            "display_name": "Bad",
            "credential": "slack://a/b/c",
        },
    )
    assert r.status_code == 422, r.text


def test_create_email_stores_mailtos_url(api, monkeypatch):
    """POST with email type builds mailtos:// Apprise URL."""
    from src.api.routes import channels as ch
    from src.core import settings as s

    monkeypatch.setattr(s, "SMTP_EMAIL", "sender@gmail.com", raising=True)
    monkeypatch.setattr(s, "SMTP_PASSWORD", "app_pass_word", raising=True)

    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "email",
            "display_name": "My Email",
            "credential": "me@example.com",
        },
    )
    assert r.status_code == 201, r.text

    db_path = str(ch.DB_PATH)

    async def _check():
        async with pg.connect(db_path) as db:
            cur = await db.execute(
                "SELECT credential_encrypted FROM user_channels WHERE channel_type='email'"
            )
            return await cur.fetchone()

    row = asyncio.run(_check())
    decrypted = crypto.decrypt(row[0])
    assert decrypted.startswith("mailtos://"), decrypted
    assert "to=me%40example.com" in decrypted or "to=me@example.com" in decrypted


def test_create_email_bad_address_rejected(api, monkeypatch):
    """Non-email credential → 422."""
    from src.core import settings as s

    monkeypatch.setattr(s, "SMTP_EMAIL", "sender@gmail.com", raising=True)
    monkeypatch.setattr(s, "SMTP_PASSWORD", "app_pass_word", raising=True)

    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "email",
            "display_name": "Bad",
            "credential": "notanemail",
        },
    )
    assert r.status_code == 422, r.text


def test_create_email_smtp_not_configured(api, monkeypatch):
    """Email channel when SMTP not configured → 503."""
    from src.core import settings as s

    monkeypatch.setattr(s, "SMTP_EMAIL", "", raising=True)
    monkeypatch.setattr(s, "SMTP_PASSWORD", "", raising=True)

    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "email",
            "display_name": "My Email",
            "credential": "me@example.com",
        },
    )
    assert r.status_code == 503, r.text


def test_create_chat_type_via_paste_rejected(api):
    """POST "" with slack/discord/telegram → 400 'use the Connect flow'."""
    _register(api, "alice@example.com")
    for ct in ("slack", "discord", "telegram"):
        r = api.post(
            "/api/settings/channels",
            json={
                "channel_type": ct,
                "display_name": "Direct",
                "credential": f"{ct}://a/b/c",
            },
        )
        assert r.status_code == 400, f"expected 400 for {ct}, got {r.status_code}: {r.text}"
        assert "Connect flow" in r.json()["detail"] or "connect" in r.json()["detail"].lower()
