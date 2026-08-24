"""Channel config route integration tests — proves tenant isolation at API layer.

Delivery is ``email`` + ``webhook`` and nothing else (2026-08-24). ``slack`` /
``discord`` / ``telegram`` are rejected by the ``ChannelIn.channel_type``
pattern before the handler runs, so they return **422**, not 400 — there is no
longer a Connect flow to redirect them to. Rationale and evidence:
``docs/plans/2026-08-24-email-webhook-only-delivery.md``.

Tests that once posted a raw ``slack://`` credential use ``webhook`` with an
https URL. That is NOT a weakening: they proved POST "" accepts a credential and
stores it, and they still prove exactly that — for a type that still exists.
"""
import asyncio
import socket
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from migrations import runner
from src.repositories import pg
from src.services.channels import crypto

# A stable public IP used to make webhook host resolution offline+deterministic.
_PUBLIC_IP = "93.184.216.34"


def _fake_getaddrinfo_public(host, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, 0))]


@pytest.fixture(autouse=True)
def _mock_dns(monkeypatch):
    """Resolve every hostname to a public IP so webhook SSRF checks run offline.

    Individual SSRF tests override this (or use IP-literal hosts, which skip
    resolution entirely).
    """
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo_public)


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
    # M2 — register no longer auto-logs-in; sign in so the client is authenticated.
    lr = client.post("/api/auth/login", json={"email": email, "password": password})
    assert lr.status_code == 200, lr.text
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


def test_list_channels_returns_connection_status_and_target_label(api):
    """The two columns migration 0019 added must still be serialized.

    Relocated from the deleted ``test_channels_oauth.py``. The original proved
    this by driving the Discord OAuth callback; that route is gone, but the
    columns are NOT — 0031 deliberately kept ``connection_status`` and
    ``target_label`` on ``user_channels`` rather than churning a live table.
    So the guard is re-expressed through the only path that still creates a
    channel. Without it, a serializer that silently dropped both fields would
    have lost its only test when the OAuth file was removed.
    """
    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "webhook",
            "display_name": "My Webhook",
            "credential": "https://hooks.example.com/notify",
        },
    )
    assert r.status_code == 201, r.text

    rows = api.get("/api/settings/channels").json()
    assert len(rows) == 1
    # 'connected' is the column default — a manually added channel is live the
    # moment it is created; there is no handshake left to be pending on.
    assert rows[0]["connection_status"] == "connected"
    # NULL for a channel the user typed in themselves: the label only ever came
    # from an OAuth provider naming its own destination (e.g. "#general").
    assert rows[0]["target_label"] is None


@pytest.mark.parametrize("dead_type", ["slack", "discord", "telegram"])
def test_deleted_chat_channel_types_are_rejected(api, dead_type):
    """The three removed channels must not be creatable by any route.

    422, not 400: rejection happens in the ``ChannelIn`` pattern during request
    validation, before the handler body runs. Asserting the status code pins
    WHERE the refusal lives — if someone re-adds the type to the pattern and
    lets the handler reject it instead, this test catches the drift.
    """
    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": dead_type,
            "display_name": f"My {dead_type}",
            "credential": "https://hooks.example.com/notify",
        },
    )
    assert r.status_code == 422, r.text


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


def _pin_email_env(monkeypatch, *, resend=None, smtp=None):
    """Pin the email transport env so these tests never read the real .env.

    #318 — the URL builder reads ``os.environ`` at call time (like
    ``auth/email_sender.py``, the transport that actually delivers in prod)
    rather than the import-time ``settings`` snapshot the route used before.
    Without pinning, a developer's real RESEND_API_KEY leaks into the assertion
    messages of a failing test.
    """
    for var in ("RESEND_API_KEY", "SMTP_EMAIL", "SMTP_PASSWORD", "SMTP_FROM"):
        monkeypatch.delenv(var, raising=False)
    if resend:
        monkeypatch.setenv("RESEND_API_KEY", resend)
        monkeypatch.setenv("SMTP_FROM", "alerts@job360.uk")
    if smtp:
        monkeypatch.setenv("SMTP_EMAIL", smtp[0])
        monkeypatch.setenv("SMTP_PASSWORD", smtp[1])


def test_create_email_prefers_resend_over_smtp(api, monkeypatch):
    """An email channel must be built on a transport this host can actually use.

    Railway blocks outbound SMTP (25/465/587) — the reason
    ``auth/email_sender.py`` moved off smtplib onto Resend's HTTPS API. So when
    a Resend key is present the channel is ``resend://`` even if SMTP creds are
    also set; ``mailtos://`` would time out and the user would see silence.
    """
    from src.api.routes import channels as ch

    _pin_email_env(
        monkeypatch, resend="re_fake_test_key", smtp=("sender@gmail.com", "app_pass_word")
    )

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
    # Assert on the SCHEME only — never echo the full URL, it carries the key.
    assert decrypted.startswith("resend://"), decrypted.split("://")[0]
    assert "me@example.com" in decrypted


def test_create_email_falls_back_to_mailtos_without_resend(api, monkeypatch):
    """No Resend key but real SMTP creds (local / self-hosted) → mailtos://."""
    from src.api.routes import channels as ch

    _pin_email_env(monkeypatch, smtp=("sender@gmail.com", "app_pass_word"))

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
    # Scheme only in the message — the full URL embeds the SMTP password.
    assert decrypted.startswith("mailtos://"), decrypted.split("://")[0]
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
    """No Resend key AND no SMTP creds → 503, not a channel that cannot deliver."""
    _pin_email_env(monkeypatch)

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


def test_chat_channel_types_no_longer_exist(api):
    """slack/discord/telegram are GONE — the API must not know the words.

    They used to be accepted-but-redirected (400 "use the Connect flow").
    Delivery is now email + webhook only, so these are simply not valid
    values of ``channel_type`` and Pydantic rejects them at the schema
    boundary (422) before any handler runs. A 400 here would mean the
    Connect-flow branch is still alive.
    """
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
        assert r.status_code == 422, f"expected 422 for {ct}, got {r.status_code}: {r.text}"


def test_only_email_and_webhook_are_valid_channel_types(api):
    """The positive half of the cut: the two survivors still work.

    Guards the opposite failure — a regex tightened so far it locks the
    user out of the product entirely.
    """
    _register(api, "survivors@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "webhook",
            "display_name": "Raw feed",
            "credential": "https://example.com/hook",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["channel_type"] == "webhook"


def test_chat_connect_routes_are_removed(api):
    """The OAuth/deep-link endpoints must 404 — not 503, not 400.

    503 would mean the route still exists and is merely unconfigured, which
    is how these three spent their whole life in production. Gone means gone.
    """
    _register(api, "gone@example.com")
    for path in (
        "/api/settings/channels/connect/slack",
        "/api/settings/channels/connect/discord",
        "/api/settings/channels/connect/telegram",
        "/api/settings/channels/connect/telegram/poll",
        "/api/settings/channels/callback/slack",
        "/api/settings/channels/callback/discord",
        "/api/settings/channels/providers",
    ):
        r = api.get(path)
        assert r.status_code == 404, f"{path} still answers {r.status_code}: {r.text[:120]}"


# ===========================================================================
# NEW — SSRF guard on webhook create (M1)
# ===========================================================================

@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1/x",          # loopback
        "http://169.254.169.254/x",    # cloud metadata (link-local)
        "http://10.0.0.5/x",           # RFC1918 private
        "http://[::1]/x",              # IPv6 loopback literal
    ],
)
def test_create_webhook_private_ip_rejected(api, bad_url):
    """Webhook pointing at an internal/loopback/metadata address → 422."""
    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "webhook",
            "display_name": "SSRF",
            "credential": bad_url,
        },
    )
    assert r.status_code == 422, f"{bad_url} should be rejected: {r.text}"


def test_create_webhook_private_ip_not_stored(api):
    """A rejected webhook must not create a channel row."""
    _register(api, "alice@example.com")
    api.post(
        "/api/settings/channels",
        json={
            "channel_type": "webhook",
            "display_name": "SSRF",
            "credential": "http://127.0.0.1/x",
        },
    )
    r = api.get("/api/settings/channels")
    assert r.json() == []


def test_create_webhook_public_host_accepted(api):
    """A normal public https webhook is accepted (host mocked to a public IP)."""
    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "webhook",
            "display_name": "Good Hook",
            "credential": "https://hooks.example.com/x",
        },
    )
    assert r.status_code == 201, r.text


def test_create_webhook_host_resolving_to_private_rejected(api, monkeypatch):
    """Host that DNS-resolves to a private IP is rejected even if name looks public."""
    def _private(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _private)

    _register(api, "alice@example.com")
    r = api.post(
        "/api/settings/channels",
        json={
            "channel_type": "webhook",
            "display_name": "Sneaky",
            "credential": "https://internal.attacker.example/x",
        },
    )
    assert r.status_code == 422, r.text
