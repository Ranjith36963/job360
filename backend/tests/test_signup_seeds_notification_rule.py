"""Issue #318 — a job-ALERT product that had never alerted anyone.

Root cause pinned here: **nothing in the product ever created a
``notification_rules`` row**. ``save_user_notification_rule`` had exactly one
caller (the PUT route), and the frontend hid that form until the user had
already added a channel. So both delivery tables stayed empty forever, the
5-minute ``notification_tick`` cron found nobody, and ``_enqueue_notifications``
returned 0 before dispatch was ever reached.

These tests pin the seeding contract at BOTH user-creation paths:

* ``POST /api/auth/register``  — seeds the rulebook only (email unproven).
* ``POST /api/auth/magic-link/consume`` — creates users too (``INSERT OR
  IGNORE INTO users``) and stamps ``email_verified_at``, so it seeds the
  rulebook AND the account-email channel.

Rule #23 (UNIQUE(user_id), one row per user) is asserted by registering /
signing in twice and demanding the row count stay at 1.

Rule #21 — value-presence, not schema-presence: every assertion below checks a
real non-default value (mode ``daily``, the env-driven threshold, a credential
that decrypts to a deliverable Apprise URL), never merely that a column exists.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from migrations import runner
from src.repositories import pg
from src.services.auth import tokens
from src.services.channels import crypto
from src.services.notifications import defaults as notif_defaults


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture(autouse=True)
def _no_real_email(monkeypatch):
    """Rule #4 — the suite runs offline. No magic-link email ever leaves."""

    async def _fake_send(**_kwargs):
        return True

    monkeypatch.setattr("src.services.auth.magic_link.send_system_email", _fake_send)
    monkeypatch.setattr(
        "src.services.auth.email_verification.send_system_email", _fake_send
    )


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Fresh migrated DB with DB_PATH patched into every importer."""
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
    # A Resend key makes the account-email channel buildable without SMTP.
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key_123")
    monkeypatch.setenv("EMAIL_FROM", "alerts@job360.uk")

    yield db_path


@pytest.fixture
def client(temp_db):
    from src.api.main import app

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    with TestClient(app) as c:
        yield c


# ── helpers ──────────────────────────────────────────────────────────────────


async def _rules_for_email(db_path: str, email: str) -> list[dict]:
    async with pg.connect(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            "SELECT r.* FROM notification_rules r "
            "JOIN users u ON u.id = r.user_id WHERE LOWER(u.email) = LOWER(?)",
            (email,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def _channels_for_email(db_path: str, email: str) -> list[dict]:
    async with pg.connect(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            "SELECT c.* FROM user_channels c "
            "JOIN users u ON u.id = c.user_id WHERE LOWER(u.email) = LOWER(?)",
            (email,),
        )
        return [dict(r) for r in await cur.fetchall()]


def _consume_magic_link(client, monkeypatch, email: str) -> None:
    """Drive request → consume, capturing the raw token the email would carry."""
    captured: list[str] = []
    orig = tokens.generate_token

    def _spy():
        raw, h = orig()
        captured.append(raw)
        return raw, h

    monkeypatch.setattr(tokens, "generate_token", _spy)
    r = client.post("/api/auth/magic-link/request", json={"email": email})
    assert r.status_code == 204, r.text
    monkeypatch.setattr(tokens, "generate_token", orig)
    r = client.post("/api/auth/magic-link/consume", json={"token": captured[-1]})
    assert r.status_code == 200, r.text


# ── register seeds the rulebook ──────────────────────────────────────────────


def test_register_seeds_exactly_one_enabled_rule(client, temp_db):
    """The whole issue in one assertion: signing up now produces a rulebook.

    Before this fix the count was 0 — for every user, forever.
    """
    r = client.post(
        "/api/auth/register",
        json={"email": "seed@example.com", "password": "hunter2-hunter2"},
    )
    assert r.status_code == 201, r.text

    rules = asyncio.run(_rules_for_email(temp_db, "seed@example.com"))
    assert len(rules) == 1, f"expected exactly one seeded rule, got {rules}"
    rule = rules[0]
    # Rule #21 — real values, not just column presence.
    assert bool(rule["enabled"]) is True
    assert rule["notify_mode"] == "daily", (
        "must default to 'daily', never 'instant' — an unattended cron on "
        "instant mode fans out hundreds of separate emails per user per night"
    )
    assert int(rule["score_threshold"]) == notif_defaults.DEFAULT_SCORE_THRESHOLD


def test_register_twice_keeps_exactly_one_rule(client, temp_db):
    """Rule #23 — UNIQUE(user_id). Register is retryable and the duplicate-email
    path deliberately does not raise, so the seed must be ON CONFLICT DO NOTHING.
    """
    body = {"email": "dupe@example.com", "password": "hunter2-hunter2"}
    assert client.post("/api/auth/register", json=body).status_code == 201
    assert client.post("/api/auth/register", json=body).status_code == 201

    rules = asyncio.run(_rules_for_email(temp_db, "dupe@example.com"))
    assert len(rules) == 1, f"duplicate register created a second rule: {rules}"


def test_register_does_not_seed_a_channel(client, temp_db):
    """Security shape: at register the address is UNPROVEN.

    Seeding a channel here would let anyone register with a victim's address
    and aim job alerts at a mailbox they do not own.
    """
    r = client.post(
        "/api/auth/register",
        json={"email": "unproven@example.com", "password": "hunter2-hunter2"},
    )
    assert r.status_code == 201, r.text
    assert asyncio.run(_channels_for_email(temp_db, "unproven@example.com")) == []


# ── magic-link is the OTHER user-creation path ───────────────────────────────


def test_magic_link_signup_seeds_rule_and_channel(client, monkeypatch, temp_db):
    """``consume_magic_link`` does ``INSERT OR IGNORE INTO users`` — it creates
    users without ever touching ``register``. Seeding only in register would
    miss every passwordless signup, which is how this product actually onboards.

    Clicking the emailed link proves ownership, so the account-email channel is
    seeded here.
    """
    _consume_magic_link(client, monkeypatch, "magic@example.com")

    rules = asyncio.run(_rules_for_email(temp_db, "magic@example.com"))
    assert len(rules) == 1, f"magic-link signup seeded no rulebook: {rules}"
    assert bool(rules[0]["enabled"]) is True
    assert rules[0]["notify_mode"] == "daily"

    channels = asyncio.run(_channels_for_email(temp_db, "magic@example.com"))
    assert len(channels) == 1, f"expected one account-email channel, got {channels}"
    ch = channels[0]
    assert ch["channel_type"] == "email"
    assert bool(ch["enabled"]) is True
    # Rule #21 — the credential must decrypt to a URL that can actually deliver,
    # not merely be non-null.
    url = crypto.decrypt(ch["credential_encrypted"])
    assert url.startswith("resend://"), (
        f"account-email channel must use Resend's HTTPS API, got {url[:20]!r} — "
        "Railway blocks outbound SMTP ports so mailtos:// times out in prod"
    )
    assert "magic@example.com" in url


def test_magic_link_twice_keeps_one_rule_and_one_channel(client, monkeypatch, temp_db):
    """Signing in again must not accumulate duplicate rules or channels."""
    _consume_magic_link(client, monkeypatch, "repeat@example.com")
    _consume_magic_link(client, monkeypatch, "repeat@example.com")

    assert len(asyncio.run(_rules_for_email(temp_db, "repeat@example.com"))) == 1
    assert len(asyncio.run(_channels_for_email(temp_db, "repeat@example.com"))) == 1


def test_existing_user_without_rule_gets_one_on_next_signin(
    client, monkeypatch, temp_db
):
    """Backfill-by-login: the 11 users already in prod have no rulebook.

    They never pass through register again, so sign-in is the only place that
    can heal them. Without this they stay silent forever.
    """
    async def _make_legacy_user():
        async with pg.connect(temp_db) as db:
            await db.execute(
                "INSERT INTO users(id, email, password_hash) VALUES(?, ?, ?)",
                ("legacyid", "legacy@example.com", "!"),
            )
            await db.commit()

    asyncio.run(_make_legacy_user())
    assert asyncio.run(_rules_for_email(temp_db, "legacy@example.com")) == []

    _consume_magic_link(client, monkeypatch, "legacy@example.com")

    rules = asyncio.run(_rules_for_email(temp_db, "legacy@example.com"))
    assert len(rules) == 1, "an existing user signing in must be healed"
    assert rules[0]["user_id"] == "legacyid"


# ── the kill switch ──────────────────────────────────────────────────────────


def test_seeding_can_be_switched_off(client, monkeypatch, temp_db):
    """Seeding is a PARAMETER, not a hardcode — the owner can disable it
    without a code deploy if the on-by-default behaviour is ever wrong.
    """
    monkeypatch.setenv("NOTIFY_SEED_DEFAULTS", "0")
    r = client.post(
        "/api/auth/register",
        json={"email": "nosee@example.com", "password": "hunter2-hunter2"},
    )
    assert r.status_code == 201, r.text
    assert asyncio.run(_rules_for_email(temp_db, "nosee@example.com")) == []


def test_seed_failure_never_breaks_signup(client, monkeypatch, temp_db):
    """A broken seed must not 500 a register that already committed the user."""

    async def _boom(*_a, **_kw):
        raise RuntimeError("seed exploded")

    monkeypatch.setattr(notif_defaults, "seed_notification_defaults", _boom)
    monkeypatch.setattr(
        "src.api.routes.auth.seed_notification_defaults", _boom, raising=False
    )
    r = client.post(
        "/api/auth/register",
        json={"email": "boom@example.com", "password": "hunter2-hunter2"},
    )
    assert r.status_code == 201, r.text


# ── the cron can finally see somebody ────────────────────────────────────────


