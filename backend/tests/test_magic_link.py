"""Passwordless magic-link login tests.

Covers:
- request: mints a token row + calls send, returns 204
- request: rate-limit returns 204 but issues no extra token after the cap
- consume: valid token → creates user + session cookie + email_verified_at set
- consume: existing user → email_verified_at set, no duplicate user
- consume: token is single-use (second consume fails)
- consume: expired token fails
- consume: unknown token → 400
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
from src.services.auth import rate_limit, tokens
from src.services.channels import crypto


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    rate_limit.reset_for_tests()
    yield
    rate_limit.reset_for_tests()


@pytest.fixture(autouse=True)
def _no_real_email(monkeypatch):
    """Never hit a real email backend — pretend the send always succeeds."""
    async def _fake_send(**_kwargs):
        return True

    monkeypatch.setattr(
        "src.services.auth.magic_link.send_system_email", _fake_send
    )


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


async def _latest_token_row(db_path, email):
    async with pg.connect(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            "SELECT id, token_hash, email, expires_at, used_at FROM magic_link_tokens "
            "WHERE email = ? ORDER BY id DESC LIMIT 1",
            (email,),
        )
        return await cur.fetchone()


async def _count_tokens(db_path, email):
    async with pg.connect(db_path) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM magic_link_tokens WHERE email = ?", (email,)
        )
        return (await cur.fetchone())[0]


async def _user_row(db_path, email):
    async with pg.connect(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            "SELECT id, email, email_verified_at FROM users WHERE email = ?", (email,)
        )
        return await cur.fetchone()


def _request_capture_token(client, monkeypatch, email):
    """Spy on tokens.generate_token so we can read the raw value the email
    would have contained, without setting up real SMTP."""
    captured = {"raws": []}
    orig = tokens.generate_token

    def _spy():
        raw, h = orig()
        captured["raws"].append(raw)
        return raw, h

    monkeypatch.setattr(tokens, "generate_token", _spy)
    r = client.post("/api/auth/magic-link/request", json={"email": email})
    assert r.status_code == 204, r.text
    assert len(captured["raws"]) == 1, captured["raws"]
    return captured["raws"][0]


# ─── request ────────────────────────────────────────────────────────────────


def test_request_mints_token_and_returns_204(client, monkeypatch, temp_db):
    raw = _request_capture_token(client, monkeypatch, "alice@example.com")
    row = asyncio.run(_latest_token_row(temp_db, "alice@example.com"))
    assert row is not None
    assert row["token_hash"] == tokens.hash_token(raw)
    assert row["used_at"] is None


def test_request_rate_limited_after_cap(client, temp_db):
    email = "spammer@example.com"
    # Cap is 3 per 5 min. First three allowed, fourth suppressed.
    for _ in range(3):
        r = client.post("/api/auth/magic-link/request", json={"email": email})
        assert r.status_code == 204
    r = client.post("/api/auth/magic-link/request", json={"email": email})
    assert r.status_code == 204  # still 204 (no enumeration)
    # But the fourth issued no token — only 3 rows exist.
    assert asyncio.run(_count_tokens(temp_db, email)) == 3


# ─── consume ────────────────────────────────────────────────────────────────


def test_consume_creates_user_sets_cookie_and_verifies(client, monkeypatch, temp_db):
    raw = _request_capture_token(client, monkeypatch, "newbie@example.com")
    assert asyncio.run(_user_row(temp_db, "newbie@example.com")) is None

    r = client.post("/api/auth/magic-link/consume", json={"token": raw})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == "newbie@example.com"
    assert body["id"]
    # Session cookie was set.
    assert "job360_session" in r.cookies

    user = asyncio.run(_user_row(temp_db, "newbie@example.com"))
    assert user is not None
    assert user["id"] == body["id"]
    assert user["email_verified_at"] is not None  # auto-verified


def test_consume_existing_user_verifies_no_duplicate(client, monkeypatch, temp_db):
    # Pre-create an unverified user.
    async def _seed():
        async with pg.connect(temp_db) as db:
            await db.execute(
                "INSERT INTO users(id, email, password_hash) VALUES (?, ?, ?)",
                ("existing-id", "existing@example.com", "!"),
            )
            await db.commit()

    asyncio.run(_seed())

    raw = _request_capture_token(client, monkeypatch, "existing@example.com")
    r = client.post("/api/auth/magic-link/consume", json={"token": raw})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "existing-id"  # reused, not a new account

    user = asyncio.run(_user_row(temp_db, "existing@example.com"))
    assert user["email_verified_at"] is not None


def test_consume_reuses_soft_deleted_user(client, monkeypatch, temp_db):
    # Regression: a soft-deleted row still owns the email (users.email UNIQUE
    # ignores deleted_at). consume must REUSE + reactivate it, not INSERT a
    # duplicate — that was a 500 UniqueViolation in prod.
    async def _seed():
        async with pg.connect(temp_db) as db:
            await db.execute(
                "INSERT INTO users(id, email, password_hash, deleted_at) "
                "VALUES (?, ?, ?, ?)",
                ("deleted-id", "gone@example.com", "!", "2026-01-01T00:00:00Z"),
            )
            await db.commit()

    asyncio.run(_seed())

    raw = _request_capture_token(client, monkeypatch, "gone@example.com")
    r = client.post("/api/auth/magic-link/consume", json={"token": raw})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "deleted-id"  # reused, not a duplicate
    assert "job360_session" in r.cookies

    async def _deleted_at():
        async with pg.connect(temp_db) as db:
            db.row_factory = pg.Row
            cur = await db.execute(
                "SELECT email_verified_at, deleted_at FROM users WHERE id = ?",
                ("deleted-id",),
            )
            return await cur.fetchone()

    row = asyncio.run(_deleted_at())
    assert row["email_verified_at"] is not None  # verified
    assert row["deleted_at"] is None  # reactivated


def test_consume_is_single_use(client, monkeypatch, temp_db):
    raw = _request_capture_token(client, monkeypatch, "once@example.com")
    r1 = client.post("/api/auth/magic-link/consume", json={"token": raw})
    assert r1.status_code == 200
    r2 = client.post("/api/auth/magic-link/consume", json={"token": raw})
    assert r2.status_code == 400


def test_consume_rejects_expired_token(client, monkeypatch, temp_db):
    raw = _request_capture_token(client, monkeypatch, "slow@example.com")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    async def _expire():
        async with pg.connect(temp_db) as db:
            await db.execute(
                "UPDATE magic_link_tokens SET expires_at = ? WHERE email = ?",
                (past, "slow@example.com"),
            )
            await db.commit()

    asyncio.run(_expire())
    r = client.post("/api/auth/magic-link/consume", json={"token": raw})
    assert r.status_code == 400


def test_consume_rejects_unknown_token(client):
    r = client.post(
        "/api/auth/magic-link/consume",
        json={"token": "definitely-not-real-xxxxxxxxxxxxxxxxxxx"},
    )
    assert r.status_code == 400
