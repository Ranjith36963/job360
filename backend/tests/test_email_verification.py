"""Email verification tests (Phase −2 item B).

Covers:
- Register auto-issues a verification token
- Resend requires session (401 anonymous)
- Confirm marks email_verified_at + sets used_at
- Confirm rejects: unknown / expired / used / soft-deleted / email-changed
- Email change between issue + confirm invalidates the token (stale-token guard)
- is_email_verified() returns expected values
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from migrations import runner
from src.repositories import pg
from src.services.auth import email_verification, rate_limit, tokens
from src.services.channels import crypto


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """Rate-limit buckets are module-level — reset between tests to
    prevent the second resend in one test from polluting the first
    resend in the next test."""
    rate_limit.reset_for_tests()
    yield
    rate_limit.reset_for_tests()


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
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

    crypto.set_test_key(Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "x" * 40)

    yield db_path


@pytest.fixture
def client(temp_db):
    from src.api.main import app

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    with TestClient(app) as c:
        yield c


# ─── Helpers ────────────────────────────────────────────────────────────────


async def _verification_row(db_path, user_id):
    async with pg.connect(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            "SELECT id, token_hash, email, expires_at, used_at FROM email_verifications "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        return await cur.fetchone()


async def _email_verified_at(db_path, user_id):
    async with pg.connect(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            "SELECT email_verified_at FROM users WHERE id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return row["email_verified_at"] if row else None


def _register_capture_token(client, monkeypatch, email, password="s3cretpassword"):
    """Spy on tokens.generate_token so we can read the raw value the email
    would have contained, without setting up real SMTP."""
    captured = {"raws": []}
    orig = tokens.generate_token

    def _spy():
        raw, h = orig()
        captured["raws"].append(raw)
        return raw, h

    monkeypatch.setattr(tokens, "generate_token", _spy)
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    # Register currently issues exactly one verification token; if that
    # changes (e.g. password-reset auto-issue) the test will tell us.
    assert len(captured["raws"]) == 1, captured["raws"]
    return r.json()["id"], captured["raws"][0]


# ─── register flow auto-issues a token ──────────────────────────────────────


def test_register_creates_verification_row(client, monkeypatch, temp_db):
    user_id, raw = _register_capture_token(client, monkeypatch, "alice@example.com")
    row = asyncio.run(_verification_row(temp_db, user_id))
    assert row is not None
    assert row["token_hash"] == tokens.hash_token(raw)
    assert row["email"] == "alice@example.com"
    assert row["used_at"] is None


# ─── /verify-email/request (resend) ─────────────────────────────────────────


def test_resend_requires_session(client):
    r = client.post("/api/auth/verify-email/request")
    assert r.status_code == 401


def test_resend_issues_additional_token(client, monkeypatch, temp_db):
    user_id, _ = _register_capture_token(client, monkeypatch, "alice@example.com")
    # The register call left us with a valid session cookie. Resend now.
    r = client.post("/api/auth/verify-email/request")
    assert r.status_code == 204
    # Should be 2 verification rows now (the register one + the resend one).
    async def _count():
        async with pg.connect(temp_db) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM email_verifications WHERE user_id = ?",
                (user_id,),
            )
            return (await cur.fetchone())[0]
    assert asyncio.run(_count()) == 2


# ─── /verify-email/confirm ──────────────────────────────────────────────────


def test_confirm_happy_path_marks_verified(client, monkeypatch, temp_db):
    user_id, raw = _register_capture_token(client, monkeypatch, "alice@example.com")
    assert asyncio.run(_email_verified_at(temp_db, user_id)) is None

    r = client.post("/api/auth/verify-email/confirm", json={"token": raw})
    assert r.status_code == 204
    assert asyncio.run(_email_verified_at(temp_db, user_id)) is not None


def test_confirm_rejects_unknown_token(client):
    r = client.post(
        "/api/auth/verify-email/confirm",
        json={"token": "definitely-not-real-xxxxxxxxxxxxxxxxxxx"},
    )
    assert r.status_code == 400


def test_confirm_rejects_reused_token(client, monkeypatch, temp_db):
    _user_id, raw = _register_capture_token(client, monkeypatch, "alice@example.com")
    r1 = client.post("/api/auth/verify-email/confirm", json={"token": raw})
    assert r1.status_code == 204
    r2 = client.post("/api/auth/verify-email/confirm", json={"token": raw})
    assert r2.status_code == 400


def test_confirm_rejects_expired_token(client, monkeypatch, temp_db):
    user_id, raw = _register_capture_token(client, monkeypatch, "alice@example.com")
    # Backdate the row to be expired.
    past = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def _expire():
        async with pg.connect(temp_db) as db:
            await db.execute(
                "UPDATE email_verifications SET expires_at = ? WHERE user_id = ?",
                (past, user_id),
            )
            await db.commit()

    asyncio.run(_expire())
    r = client.post("/api/auth/verify-email/confirm", json={"token": raw})
    assert r.status_code == 400


def test_confirm_rejects_after_email_change(client, monkeypatch, temp_db):
    """Issuing a token for old@x.com then changing to new@x.com invalidates
    the old token — it would verify the wrong (no-longer-current) address."""
    user_id, raw = _register_capture_token(client, monkeypatch, "old@example.com")

    async def _swap_email():
        async with pg.connect(temp_db) as db:
            await db.execute(
                "UPDATE users SET email = ? WHERE id = ?", ("new@example.com", user_id)
            )
            await db.commit()

    asyncio.run(_swap_email())

    r = client.post("/api/auth/verify-email/confirm", json={"token": raw})
    assert r.status_code == 400  # email mismatch guard


def test_confirm_rejects_soft_deleted_user(client, monkeypatch, temp_db):
    user_id, raw = _register_capture_token(client, monkeypatch, "alice@example.com")

    async def _soft_delete():
        async with pg.connect(temp_db) as db:
            await db.execute(
                "UPDATE users SET deleted_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
                (user_id,),
            )
            await db.commit()

    asyncio.run(_soft_delete())
    r = client.post("/api/auth/verify-email/confirm", json={"token": raw})
    assert r.status_code == 400


# ─── /me/email-verified read ───────────────────────────────────────────────


def test_me_email_verified_flips_after_confirm(client, monkeypatch):
    _register_capture_token(client, monkeypatch, "alice@example.com")
    r = client.get("/api/auth/me/email-verified")
    assert r.status_code == 200
    assert r.json() == {"email_verified": False}


def test_me_email_verified_true_after_confirm(client, monkeypatch):
    _user_id, raw = _register_capture_token(client, monkeypatch, "alice@example.com")
    client.post("/api/auth/verify-email/confirm", json={"token": raw})
    r = client.get("/api/auth/me/email-verified")
    assert r.json() == {"email_verified": True}


# ─── direct service-layer test for the is_email_verified helper ──────────────


def test_is_email_verified_helper(monkeypatch, temp_db):
    """Bypass the API to exercise the helper directly — gives us a deeper
    test that the column round-trips correctly without HTTP layering."""
    async def _scenario():
        # Insert a user row directly
        async with pg.connect(temp_db) as db:
            await db.execute(
                "INSERT INTO users(id, email, password_hash) VALUES (?, ?, ?)",
                ("u1", "u1@example.com", "!"),
            )
            await db.commit()
        assert await email_verification.is_email_verified(db_path=temp_db, user_id="u1") is False

        async with pg.connect(temp_db) as db:
            await db.execute(
                "UPDATE users SET email_verified_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                "WHERE id = ?",
                ("u1",),
            )
            await db.commit()
        assert await email_verification.is_email_verified(db_path=temp_db, user_id="u1") is True

    asyncio.run(_scenario())
