"""OAuth flow tests for Slack one-click channel connect.

All tests are offline:
- No live HTTP — _exchange_slack_code is monkeypatched.
- Slack settings attrs are monkeypatched via the module reference so that
  _slack_oauth_enabled() picks up the patched values.

Auth fixture is reused from test_channels_routes.py:
- A fresh tmp SQLite DB is bootstrapped via migrations runner.
- The DB_PATH is patched on every importer module.
- Tests call /api/auth/register then hit the OAuth routes as that user.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from migrations import runner
from src.api.routes import channels as channels_route
from src.repositories import pg
from src.services.channels import crypto

# ---------------------------------------------------------------------------
# Shared fixture (mirrors test_channels_routes.py exactly)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _register(client: TestClient, email: str, password: str = "s3cretpassword") -> None:
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    # M2 — register no longer auto-logs-in; sign in so the client is authenticated.
    lr = client.post("/api/auth/login", json={"email": email, "password": password})
    assert lr.status_code == 200, lr.text


_FAKE_SLACK_DATA = {
    "ok": True,
    "incoming_webhook": {
        "url": "https://hooks.slack.com/services/T111/B222/zzz333",
        "channel": "#jobs",
    },
}


# ---------------------------------------------------------------------------
# Test 1: connect/slack when configured → 302 to Slack + state row persisted
# ---------------------------------------------------------------------------

def test_connect_slack_when_configured(api, monkeypatch):
    """GET /connect/slack redirects to Slack authorize URL and saves a state."""
    from src.api.routes import channels as ch
    from src.core import settings as s

    monkeypatch.setattr(s, "SLACK_CLIENT_ID", "TEST_CLIENT_ID", raising=True)
    monkeypatch.setattr(s, "SLACK_CLIENT_SECRET", "TEST_CLIENT_SECRET", raising=True)
    monkeypatch.setattr(s, "OAUTH_REDIRECT_BASE", "https://job360.example.com", raising=True)

    _register(api, "alice@example.com")

    # follow_redirects=False so we inspect the 302 Location header.
    r = api.get("/api/settings/channels/connect/slack", follow_redirects=False)

    assert r.status_code == 302, r.text
    location = r.headers["location"]
    assert location.startswith("https://slack.com/oauth/v2/authorize"), location
    assert "TEST_CLIENT_ID" in location
    assert "state=" in location

    # Extract the state value from the Location header query string.
    from urllib.parse import parse_qs, urlparse
    qs = parse_qs(urlparse(location).query)
    state = qs["state"][0]

    # Confirm the state was written to oauth_states.
    db_path = str(ch.DB_PATH)
    async def _check():
        async with pg.connect(db_path) as db:
            db.row_factory = pg.Row
            cur = await db.execute(
                "SELECT user_id, channel_type FROM oauth_states WHERE state = ?",
                (state,),
            )
            return await cur.fetchone()

    row = asyncio.run(_check())
    assert row is not None, "state row missing from oauth_states"
    assert row["channel_type"] == "slack"


# ---------------------------------------------------------------------------
# Test 2: connect/slack when NOT configured → 404
# ---------------------------------------------------------------------------

def test_connect_slack_not_configured(api, monkeypatch):
    """GET /connect/slack returns 404 when Slack OAuth settings are blank."""
    from src.core import settings as s

    monkeypatch.setattr(s, "SLACK_CLIENT_ID", "", raising=True)
    monkeypatch.setattr(s, "SLACK_CLIENT_SECRET", "", raising=True)
    monkeypatch.setattr(s, "OAUTH_REDIRECT_BASE", "", raising=True)

    _register(api, "alice@example.com")
    r = api.get("/api/settings/channels/connect/slack", follow_redirects=False)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Test 3: callback happy path → channel row created with correct values
# ---------------------------------------------------------------------------

def test_callback_slack_happy_path(api, monkeypatch):
    """Full happy-path: state seeded → callback → channel row created."""
    from src.api.routes import channels as ch
    from src.core import settings as s

    monkeypatch.setattr(s, "SLACK_CLIENT_ID", "TEST_CLIENT_ID", raising=True)
    monkeypatch.setattr(s, "SLACK_CLIENT_SECRET", "TEST_CLIENT_SECRET", raising=True)
    monkeypatch.setattr(s, "OAUTH_REDIRECT_BASE", "https://job360.example.com", raising=True)

    _register(api, "alice@example.com")

    # Seed a valid state row directly.
    db_path = str(ch.DB_PATH)
    test_state = "validstate_" + "a" * 20

    async def _seed():
        now = datetime.now(timezone.utc).isoformat()
        async with pg.connect(db_path) as db:
            # Select by email to avoid the DEFAULT_TENANT_ID placeholder row
            # inserted by migrations (which has a lower rowid than Alice).
            cur = await db.execute(
                "SELECT id FROM users WHERE email = 'alice@example.com'"
            )
            row = await cur.fetchone()
            user_id = row[0]
            await db.execute(
                "INSERT INTO oauth_states(state, user_id, channel_type, created_at) "
                "VALUES(?, ?, 'slack', ?)",
                (test_state, user_id, now),
            )
            await db.commit()
        return user_id

    asyncio.run(_seed())

    # Monkeypatch the HTTP exchange so no live call goes out.
    async def _fake_exchange(code: str) -> dict:
        return _FAKE_SLACK_DATA

    monkeypatch.setattr(ch, "_exchange_slack_code", _fake_exchange)

    r = api.get(
        f"/api/settings/channels/callback/slack?code=anycode&state={test_state}",
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    assert "connected=slack" in r.headers["location"]

    # Check DB for the new channel row.
    async def _check():
        async with pg.connect(db_path) as db:
            db.row_factory = pg.Row
            cur = await db.execute(
                "SELECT channel_type, connection_status, target_label, "
                "credential_encrypted FROM user_channels WHERE channel_type='slack'"
            )
            return await cur.fetchone()

    row = asyncio.run(_check())
    assert row is not None, "channel row not created"
    assert row["channel_type"] == "slack"
    assert row["connection_status"] == "connected"
    assert row["target_label"] == "#jobs"

    # Verify the decrypted credential is the expected Apprise URL.
    decrypted = crypto.decrypt(row["credential_encrypted"])
    assert decrypted == "slack://T111/B222/zzz333"


# ---------------------------------------------------------------------------
# Test 4: callback with unknown/missing state → 400, no channel created
# ---------------------------------------------------------------------------

def test_callback_unknown_state(api, monkeypatch):
    """Callback with a state that was never stored → 400."""
    from src.api.routes import channels as ch
    from src.core import settings as s

    monkeypatch.setattr(s, "SLACK_CLIENT_ID", "TEST_CLIENT_ID", raising=True)
    monkeypatch.setattr(s, "SLACK_CLIENT_SECRET", "TEST_CLIENT_SECRET", raising=True)
    monkeypatch.setattr(s, "OAUTH_REDIRECT_BASE", "https://job360.example.com", raising=True)

    _register(api, "alice@example.com")

    r = api.get(
        "/api/settings/channels/callback/slack?code=anycode&state=doesnotexist",
        follow_redirects=False,
    )
    assert r.status_code == 400

    # Confirm no channel row was created.
    db_path = str(ch.DB_PATH)

    async def _check():
        async with pg.connect(db_path) as db:
            cur = await db.execute("SELECT COUNT(*) FROM user_channels")
            return (await cur.fetchone())[0]

    assert asyncio.run(_check()) == 0


# ---------------------------------------------------------------------------
# Test 5: callback with expired state → 400, state row cleaned up
# ---------------------------------------------------------------------------

def test_callback_expired_state(api, monkeypatch):
    """State older than 10 minutes is rejected and the row is removed."""
    from src.api.routes import channels as ch
    from src.core import settings as s

    monkeypatch.setattr(s, "SLACK_CLIENT_ID", "TEST_CLIENT_ID", raising=True)
    monkeypatch.setattr(s, "SLACK_CLIENT_SECRET", "TEST_CLIENT_SECRET", raising=True)
    monkeypatch.setattr(s, "OAUTH_REDIRECT_BASE", "https://job360.example.com", raising=True)

    _register(api, "alice@example.com")

    db_path = str(ch.DB_PATH)
    test_state = "expiredstate_" + "b" * 20

    async def _seed():
        # Set created_at 11 minutes ago.
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
        async with pg.connect(db_path) as db:
            # Select by email to avoid the DEFAULT_TENANT_ID placeholder row.
            cur = await db.execute(
                "SELECT id FROM users WHERE email = 'alice@example.com'"
            )
            user_id = (await cur.fetchone())[0]
            await db.execute(
                "INSERT INTO oauth_states(state, user_id, channel_type, created_at) "
                "VALUES(?, ?, 'slack', ?)",
                (test_state, user_id, old_ts),
            )
            await db.commit()

    asyncio.run(_seed())

    async def _fake_exchange(code: str) -> dict:
        return _FAKE_SLACK_DATA

    monkeypatch.setattr(ch, "_exchange_slack_code", _fake_exchange)

    r = api.get(
        f"/api/settings/channels/callback/slack?code=anycode&state={test_state}",
        follow_redirects=False,
    )
    assert r.status_code == 400

    # State row should have been deleted.
    async def _check():
        async with pg.connect(db_path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM oauth_states WHERE state = ?", (test_state,)
            )
            return (await cur.fetchone())[0]

    assert asyncio.run(_check()) == 0, "expired state row was not deleted"


# ---------------------------------------------------------------------------
# Test 6: IDOR — user B cannot complete user A's pending state
# ---------------------------------------------------------------------------

def test_callback_idor_rejected(api, monkeypatch):
    """A state belonging to user A is rejected when user B hits the callback."""
    from src.api.routes import channels as ch
    from src.core import settings as s

    monkeypatch.setattr(s, "SLACK_CLIENT_ID", "TEST_CLIENT_ID", raising=True)
    monkeypatch.setattr(s, "SLACK_CLIENT_SECRET", "TEST_CLIENT_SECRET", raising=True)
    monkeypatch.setattr(s, "OAUTH_REDIRECT_BASE", "https://job360.example.com", raising=True)

    # Register two users.
    _register(api, "alice@example.com")
    # Log out alice.
    api.post("/api/auth/logout")
    api.cookies.clear()

    _register(api, "bob@example.com")
    # Log out bob (so we can log back in as alice to seed the state).
    api.post("/api/auth/logout")
    api.cookies.clear()

    # Log in as alice to trigger the connect flow (creates a state for alice).
    r_login = api.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "s3cretpassword"},
    )
    assert r_login.status_code == 200, r_login.text

    # Hit /connect/slack as alice — captures the state.
    r_connect = api.get(
        "/api/settings/channels/connect/slack", follow_redirects=False
    )
    assert r_connect.status_code == 302

    from urllib.parse import parse_qs, urlparse
    qs = parse_qs(urlparse(r_connect.headers["location"]).query)
    alice_state = qs["state"][0]

    # Switch to bob.
    api.post("/api/auth/logout")
    api.cookies.clear()
    r_bob = api.post(
        "/api/auth/login",
        json={"email": "bob@example.com", "password": "s3cretpassword"},
    )
    assert r_bob.status_code == 200, r_bob.text

    async def _fake_exchange(code: str) -> dict:
        return _FAKE_SLACK_DATA

    monkeypatch.setattr(ch, "_exchange_slack_code", _fake_exchange)

    # Bob tries to complete Alice's state.
    r = api.get(
        f"/api/settings/channels/callback/slack?code=anycode&state={alice_state}",
        follow_redirects=False,
    )
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"

    # Bob must not have gained a channel row.
    db_path = str(ch.DB_PATH)

    async def _check_bob():
        async with pg.connect(db_path) as db:
            db.row_factory = pg.Row
            cur = await db.execute(
                "SELECT id FROM users WHERE email = 'bob@example.com'"
            )
            bob_row = await cur.fetchone()
            bob_id = bob_row["id"]
            cur2 = await db.execute(
                "SELECT COUNT(*) FROM user_channels WHERE user_id = ?", (bob_id,)
            )
            return (await cur2.fetchone())[0]

    assert asyncio.run(_check_bob()) == 0, "Bob must not have a channel row"


# ===========================================================================
# Discord OAuth tests (mirror the Slack suite)
# ===========================================================================

_FAKE_DISCORD_DATA = {
    "webhook": {
        "url": "https://discord.com/api/webhooks/123/abctoken",
        "name": "#general",
    }
}


# ---------------------------------------------------------------------------
# Test D1: connect/discord when configured → 302 to Discord + state stored
# ---------------------------------------------------------------------------

def test_connect_discord_when_configured(api, monkeypatch):
    """GET /connect/discord redirects to Discord authorize URL and saves a state."""
    from src.api.routes import channels as ch
    from src.core import settings as s

    monkeypatch.setattr(s, "DISCORD_CLIENT_ID", "DISC_CLIENT_ID", raising=True)
    monkeypatch.setattr(s, "DISCORD_CLIENT_SECRET", "DISC_CLIENT_SECRET", raising=True)
    monkeypatch.setattr(s, "OAUTH_REDIRECT_BASE", "https://job360.example.com", raising=True)

    _register(api, "alice@example.com")

    r = api.get("/api/settings/channels/connect/discord", follow_redirects=False)

    assert r.status_code == 302, r.text
    location = r.headers["location"]
    assert location.startswith("https://discord.com/api/oauth2/authorize"), location
    assert "DISC_CLIENT_ID" in location
    assert "state=" in location
    assert "webhook.incoming" in location

    from urllib.parse import parse_qs, urlparse
    qs = parse_qs(urlparse(location).query)
    state = qs["state"][0]

    db_path = str(ch.DB_PATH)

    async def _check():
        async with pg.connect(db_path) as db:
            db.row_factory = pg.Row
            cur = await db.execute(
                "SELECT user_id, channel_type FROM oauth_states WHERE state = ?",
                (state,),
            )
            return await cur.fetchone()

    row = asyncio.run(_check())
    assert row is not None, "state row missing from oauth_states"
    assert row["channel_type"] == "discord"


# ---------------------------------------------------------------------------
# Test D2: connect/discord when NOT configured → 404
# ---------------------------------------------------------------------------

def test_connect_discord_not_configured(api, monkeypatch):
    """GET /connect/discord returns 404 when Discord OAuth settings are blank."""
    from src.core import settings as s

    monkeypatch.setattr(s, "DISCORD_CLIENT_ID", "", raising=True)
    monkeypatch.setattr(s, "DISCORD_CLIENT_SECRET", "", raising=True)
    monkeypatch.setattr(s, "OAUTH_REDIRECT_BASE", "", raising=True)

    _register(api, "alice@example.com")
    r = api.get("/api/settings/channels/connect/discord", follow_redirects=False)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Test D3: callback happy path → channel row created with correct values
# ---------------------------------------------------------------------------

def test_callback_discord_happy_path(api, monkeypatch):
    """Full happy-path: state seeded → callback → channel row with correct creds."""
    from src.api.routes import channels as ch
    from src.core import settings as s

    monkeypatch.setattr(s, "DISCORD_CLIENT_ID", "DISC_CLIENT_ID", raising=True)
    monkeypatch.setattr(s, "DISCORD_CLIENT_SECRET", "DISC_CLIENT_SECRET", raising=True)
    monkeypatch.setattr(s, "OAUTH_REDIRECT_BASE", "https://job360.example.com", raising=True)

    _register(api, "alice@example.com")

    db_path = str(ch.DB_PATH)
    test_state = "disc_validstate_" + "a" * 20

    async def _seed():
        now = datetime.now(timezone.utc).isoformat()
        async with pg.connect(db_path) as db:
            cur = await db.execute(
                "SELECT id FROM users WHERE email = 'alice@example.com'"
            )
            row = await cur.fetchone()
            user_id = row[0]
            await db.execute(
                "INSERT INTO oauth_states(state, user_id, channel_type, created_at) "
                "VALUES(?, ?, 'discord', ?)",
                (test_state, user_id, now),
            )
            await db.commit()

    asyncio.run(_seed())

    async def _fake_exchange(code: str) -> dict:
        return _FAKE_DISCORD_DATA

    monkeypatch.setattr(ch, "_exchange_discord_code", _fake_exchange)

    r = api.get(
        f"/api/settings/channels/callback/discord?code=anycode&state={test_state}",
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    assert "connected=discord" in r.headers["location"]

    async def _check():
        async with pg.connect(db_path) as db:
            db.row_factory = pg.Row
            cur = await db.execute(
                "SELECT channel_type, connection_status, target_label, "
                "credential_encrypted FROM user_channels WHERE channel_type='discord'"
            )
            return await cur.fetchone()

    row = asyncio.run(_check())
    assert row is not None, "channel row not created"
    assert row["channel_type"] == "discord"
    assert row["connection_status"] == "connected"
    assert row["target_label"] == "#general"

    decrypted = crypto.decrypt(row["credential_encrypted"])
    assert decrypted == "discord://123/abctoken"


# ---------------------------------------------------------------------------
# Test D4: unknown state → 400
# ---------------------------------------------------------------------------

def test_callback_discord_unknown_state(api, monkeypatch):
    """Discord callback with a state that was never stored → 400."""
    from src.core import settings as s

    monkeypatch.setattr(s, "DISCORD_CLIENT_ID", "DISC_CLIENT_ID", raising=True)
    monkeypatch.setattr(s, "DISCORD_CLIENT_SECRET", "DISC_CLIENT_SECRET", raising=True)
    monkeypatch.setattr(s, "OAUTH_REDIRECT_BASE", "https://job360.example.com", raising=True)

    _register(api, "alice@example.com")

    r = api.get(
        "/api/settings/channels/callback/discord?code=anycode&state=doesnotexist",
        follow_redirects=False,
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Test D5: cross-type state (a 'slack' state used on /callback/discord) → 400
# ---------------------------------------------------------------------------

def test_callback_discord_cross_type_state_rejected(api, monkeypatch):
    """A 'slack' state must be rejected on /callback/discord (channel_type guard)."""
    from src.api.routes import channels as ch
    from src.core import settings as s

    monkeypatch.setattr(s, "SLACK_CLIENT_ID", "TEST_CLIENT_ID", raising=True)
    monkeypatch.setattr(s, "SLACK_CLIENT_SECRET", "TEST_CLIENT_SECRET", raising=True)
    monkeypatch.setattr(s, "DISCORD_CLIENT_ID", "DISC_CLIENT_ID", raising=True)
    monkeypatch.setattr(s, "DISCORD_CLIENT_SECRET", "DISC_CLIENT_SECRET", raising=True)
    monkeypatch.setattr(s, "OAUTH_REDIRECT_BASE", "https://job360.example.com", raising=True)

    _register(api, "alice@example.com")

    # Seed a 'slack' state row.
    db_path = str(ch.DB_PATH)
    test_state = "slack_state_for_discord_test_" + "x" * 10

    async def _seed():
        now = datetime.now(timezone.utc).isoformat()
        async with pg.connect(db_path) as db:
            cur = await db.execute(
                "SELECT id FROM users WHERE email = 'alice@example.com'"
            )
            row = await cur.fetchone()
            user_id = row[0]
            await db.execute(
                "INSERT INTO oauth_states(state, user_id, channel_type, created_at) "
                "VALUES(?, ?, 'slack', ?)",
                (test_state, user_id, now),
            )
            await db.commit()

    asyncio.run(_seed())

    # Try to use it on the discord callback — must be rejected.
    r = api.get(
        f"/api/settings/channels/callback/discord?code=anycode&state={test_state}",
        follow_redirects=False,
    )
    assert r.status_code == 400, (
        f"expected 400 for cross-type state, got {r.status_code}: {r.text}"
    )


# ===========================================================================
# GET /providers endpoint
# ===========================================================================

def test_providers_discord_configured(api, monkeypatch):
    """GET /providers returns discord=true when Discord env vars are set."""
    from src.core import settings as s

    monkeypatch.setattr(s, "SLACK_CLIENT_ID", "", raising=True)
    monkeypatch.setattr(s, "SLACK_CLIENT_SECRET", "", raising=True)
    monkeypatch.setattr(s, "DISCORD_CLIENT_ID", "DISC_CLIENT_ID", raising=True)
    monkeypatch.setattr(s, "DISCORD_CLIENT_SECRET", "DISC_CLIENT_SECRET", raising=True)
    monkeypatch.setattr(s, "OAUTH_REDIRECT_BASE", "https://job360.example.com", raising=True)
    monkeypatch.setattr(s, "TELEGRAM_BOT_TOKEN", "", raising=True)
    monkeypatch.setattr(s, "TELEGRAM_BOT_USERNAME", "", raising=True)

    _register(api, "alice@example.com")
    r = api.get("/api/settings/channels/providers")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["discord"] is True
    assert body["slack"] is False
    assert body["telegram"] is False


def test_providers_both_configured(api, monkeypatch):
    """GET /providers returns slack=true + discord=true when both are configured."""
    from src.core import settings as s

    monkeypatch.setattr(s, "SLACK_CLIENT_ID", "TEST_CLIENT_ID", raising=True)
    monkeypatch.setattr(s, "SLACK_CLIENT_SECRET", "TEST_CLIENT_SECRET", raising=True)
    monkeypatch.setattr(s, "DISCORD_CLIENT_ID", "DISC_CLIENT_ID", raising=True)
    monkeypatch.setattr(s, "DISCORD_CLIENT_SECRET", "DISC_CLIENT_SECRET", raising=True)
    monkeypatch.setattr(s, "OAUTH_REDIRECT_BASE", "https://job360.example.com", raising=True)
    monkeypatch.setattr(s, "TELEGRAM_BOT_TOKEN", "", raising=True)
    monkeypatch.setattr(s, "TELEGRAM_BOT_USERNAME", "", raising=True)

    _register(api, "alice@example.com")
    r = api.get("/api/settings/channels/providers")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["discord"] is True
    assert body["slack"] is True
    assert body["telegram"] is False


def test_providers_requires_auth(api):
    """GET /providers without auth → 401."""
    r = api.get("/api/settings/channels/providers")
    assert r.status_code == 401


# ===========================================================================
# Telegram connect + poll tests
# ===========================================================================


def _patch_telegram(monkeypatch, token: str = "FAKE_TOKEN", username: str = "my_jobs_bot"):
    """Patch both Telegram settings attrs."""
    from src.core import settings as s
    monkeypatch.setattr(s, "TELEGRAM_BOT_TOKEN", token, raising=True)
    monkeypatch.setattr(s, "TELEGRAM_BOT_USERNAME", username, raising=True)


def _clear_telegram(monkeypatch):
    """Ensure Telegram is NOT configured."""
    from src.core import settings as s
    monkeypatch.setattr(s, "TELEGRAM_BOT_TOKEN", "", raising=True)
    monkeypatch.setattr(s, "TELEGRAM_BOT_USERNAME", "", raising=True)


# ---------------------------------------------------------------------------
# T1: GET /connect/telegram when configured → 200 + deep_link + state stored
# ---------------------------------------------------------------------------

def test_connect_telegram_when_configured(api, monkeypatch):
    """GET /connect/telegram returns deep_link containing bot username + state."""
    from src.api.routes import channels as ch

    _patch_telegram(monkeypatch)

    _register(api, "alice@example.com")
    r = api.get("/api/settings/channels/connect/telegram")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "deep_link" in body
    assert "state" in body
    state = body["state"]
    assert "my_jobs_bot" in body["deep_link"]
    assert state in body["deep_link"]

    # Confirm the state row was stored with channel_type='telegram'.
    db_path = str(ch.DB_PATH)

    async def _check():
        async with pg.connect(db_path) as db:
            db.row_factory = pg.Row
            cur = await db.execute(
                "SELECT user_id, channel_type FROM oauth_states WHERE state = ?",
                (state,),
            )
            return await cur.fetchone()

    row = asyncio.run(_check())
    assert row is not None, "state row missing from oauth_states"
    assert row["channel_type"] == "telegram"


# ---------------------------------------------------------------------------
# T2: GET /connect/telegram when NOT configured → 404
# ---------------------------------------------------------------------------

def test_connect_telegram_not_configured(api, monkeypatch):
    """GET /connect/telegram returns 404 when Telegram bot settings are blank."""
    _clear_telegram(monkeypatch)
    _register(api, "alice@example.com")
    r = api.get("/api/settings/channels/connect/telegram")
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# T3: GET /providers with Telegram configured → telegram=true
# ---------------------------------------------------------------------------

def test_providers_telegram_configured(api, monkeypatch):
    """GET /providers returns telegram=true when bot token + username are set."""
    from src.core import settings as s

    monkeypatch.setattr(s, "SLACK_CLIENT_ID", "", raising=True)
    monkeypatch.setattr(s, "SLACK_CLIENT_SECRET", "", raising=True)
    monkeypatch.setattr(s, "DISCORD_CLIENT_ID", "", raising=True)
    monkeypatch.setattr(s, "DISCORD_CLIENT_SECRET", "", raising=True)
    monkeypatch.setattr(s, "OAUTH_REDIRECT_BASE", "", raising=True)
    monkeypatch.setattr(s, "TELEGRAM_BOT_TOKEN", "FAKE_TOKEN", raising=True)
    monkeypatch.setattr(s, "TELEGRAM_BOT_USERNAME", "my_jobs_bot", raising=True)

    _register(api, "alice@example.com")
    r = api.get("/api/settings/channels/providers")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["telegram"] is True
    assert body["slack"] is False
    assert body["discord"] is False


# ---------------------------------------------------------------------------
# T4: Poll happy path — Telegram message found → channel created, state consumed
# ---------------------------------------------------------------------------

def test_poll_telegram_happy_path(api, monkeypatch):
    """Poll finds /start <state> in updates → connected=true, channel row created."""
    from src.api.routes import channels as ch

    token = "FAKE_TOKEN"
    _patch_telegram(monkeypatch, token=token)

    _register(api, "alice@example.com")

    # Step 1: initiate connect to get a state.
    r = api.get("/api/settings/channels/connect/telegram")
    assert r.status_code == 200, r.text
    state = r.json()["state"]

    # Step 2: monkeypatch _telegram_get_updates to return the /start message.
    async def _fake_updates() -> list:
        return [
            {
                "message": {
                    "text": f"/start {state}",
                    "chat": {
                        "id": 98765,
                        "title": "My Jobs",
                    },
                }
            }
        ]

    monkeypatch.setattr(ch, "_telegram_get_updates", _fake_updates)

    r2 = api.get(f"/api/settings/channels/connect/telegram/poll?state={state}")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["connected"] is True
    assert body["target_label"] == "My Jobs"

    # Verify the channel row in the DB.
    db_path = str(ch.DB_PATH)

    async def _check():
        async with pg.connect(db_path) as db:
            db.row_factory = pg.Row
            cur = await db.execute(
                "SELECT channel_type, connection_status, target_label, "
                "credential_encrypted FROM user_channels WHERE channel_type='telegram'"
            )
            ch_row = await cur.fetchone()
            state_row = await (
                await db.execute(
                    "SELECT COUNT(*) FROM oauth_states WHERE state = ?", (state,)
                )
            ).fetchone()
            return ch_row, state_row[0]

    ch_row, state_count = asyncio.run(_check())
    assert ch_row is not None, "telegram channel row not created"
    assert ch_row["connection_status"] == "connected"
    assert ch_row["target_label"] == "My Jobs"
    # Decrypted credential must be tgram://<token>/<chat_id>
    decrypted = crypto.decrypt(ch_row["credential_encrypted"])
    assert decrypted == f"tgram://{token}/98765"
    # State must have been consumed.
    assert state_count == 0, "state row was not consumed after successful poll"


# ---------------------------------------------------------------------------
# T5: Poll not-yet — no matching update → connected=false, state kept
# ---------------------------------------------------------------------------

def test_poll_telegram_not_yet(api, monkeypatch):
    """Poll with no matching Telegram update → connected=false, state still alive."""
    from src.api.routes import channels as ch

    _patch_telegram(monkeypatch)

    _register(api, "alice@example.com")

    r = api.get("/api/settings/channels/connect/telegram")
    state = r.json()["state"]

    # No updates from Telegram yet.
    async def _fake_updates() -> list:
        return []

    monkeypatch.setattr(ch, "_telegram_get_updates", _fake_updates)

    r2 = api.get(f"/api/settings/channels/connect/telegram/poll?state={state}")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["connected"] is False

    # State row must still be present.
    db_path = str(ch.DB_PATH)

    async def _check():
        async with pg.connect(db_path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM oauth_states WHERE state = ?", (state,)
            )
            return (await cur.fetchone())[0]

    assert asyncio.run(_check()) == 1, "state row was consumed prematurely"


# ---------------------------------------------------------------------------
# T6: Poll IDOR — user B polls user A's state → 400
# ---------------------------------------------------------------------------

def test_poll_telegram_idor_rejected(api, monkeypatch):
    """User B cannot poll user A's Telegram state (IDOR guard)."""
    from src.api.routes import channels as ch

    _patch_telegram(monkeypatch)

    # Register Alice and start her connect flow.
    _register(api, "alice@example.com")
    r = api.get("/api/settings/channels/connect/telegram")
    alice_state = r.json()["state"]

    # Switch to Bob.
    api.post("/api/auth/logout")
    api.cookies.clear()
    _register(api, "bob@example.com")

    async def _fake_updates() -> list:
        return []

    monkeypatch.setattr(ch, "_telegram_get_updates", _fake_updates)

    r2 = api.get(f"/api/settings/channels/connect/telegram/poll?state={alice_state}")
    assert r2.status_code == 400, f"expected 400, got {r2.status_code}: {r2.text}"

    # Bob must have no channel row.
    db_path = str(ch.DB_PATH)

    async def _check():
        async with pg.connect(db_path) as db:
            db.row_factory = pg.Row
            cur = await db.execute("SELECT id FROM users WHERE email = 'bob@example.com'")
            bob_row = await cur.fetchone()
            bob_id = bob_row["id"]
            cur2 = await db.execute(
                "SELECT COUNT(*) FROM user_channels WHERE user_id = ?", (bob_id,)
            )
            return (await cur2.fetchone())[0]

    assert asyncio.run(_check()) == 0, "Bob must not have a channel row"


# ===========================================================================
# list_channels returns connection_status + target_label
# ===========================================================================

def test_list_channels_returns_connection_status_and_target_label(api, monkeypatch):
    """Channels created via OAuth expose connection_status + target_label."""
    from src.api.routes import channels as ch
    from src.core import settings as s

    monkeypatch.setattr(s, "DISCORD_CLIENT_ID", "DISC_CLIENT_ID", raising=True)
    monkeypatch.setattr(s, "DISCORD_CLIENT_SECRET", "DISC_CLIENT_SECRET", raising=True)
    monkeypatch.setattr(s, "OAUTH_REDIRECT_BASE", "https://job360.example.com", raising=True)

    _register(api, "alice@example.com")

    db_path = str(ch.DB_PATH)
    test_state = "disc_list_test_state_" + "b" * 20

    async def _seed():
        now = datetime.now(timezone.utc).isoformat()
        async with pg.connect(db_path) as db:
            cur = await db.execute(
                "SELECT id FROM users WHERE email = 'alice@example.com'"
            )
            row = await cur.fetchone()
            user_id = row[0]
            await db.execute(
                "INSERT INTO oauth_states(state, user_id, channel_type, created_at) "
                "VALUES(?, ?, 'discord', ?)",
                (test_state, user_id, now),
            )
            await db.commit()

    asyncio.run(_seed())

    async def _fake_exchange(code: str) -> dict:
        return _FAKE_DISCORD_DATA

    monkeypatch.setattr(ch, "_exchange_discord_code", _fake_exchange)

    r_cb = api.get(
        f"/api/settings/channels/callback/discord?code=anycode&state={test_state}",
        follow_redirects=False,
    )
    assert r_cb.status_code == 302, r_cb.text

    r = api.get("/api/settings/channels")
    assert r.status_code == 200, r.text
    channels = r.json()
    assert len(channels) == 1
    ch_row = channels[0]
    assert ch_row["connection_status"] == "connected"
    assert ch_row["target_label"] == "#general"
