"""Password reset flow tests (Phase −2 item A).

Exercises:
- request endpoint always returns 204 (no enumeration)
- confirm endpoint validates token, rehashes password, revokes sessions
- expired tokens rejected
- used tokens rejected
- tokens for soft-deleted users rejected
- token storage uses SHA256 (raw never persists)
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
    """Rate-limit buckets are module-level — reset between tests so the
    /password-reset/request rate limit (1/min per IP) doesn't cross-
    contaminate. Test client uses 127.0.0.1 → same bucket key across tests."""
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
    # No SMTP_EMAIL/SMTP_PASSWORD set — send_system_email returns False silently,
    # which is exactly what we want under test (avoids real email side effects).

    yield db_path


@pytest.fixture
def client(temp_db):
    from src.api.main import app

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    with TestClient(app) as c:
        yield c


# ─── Helpers ────────────────────────────────────────────────────────────────


async def _read_reset_row(db_path, email):
    async with pg.connect(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(
            """
            SELECT pr.token_hash, pr.expires_at, pr.used_at, pr.user_id
            FROM password_resets pr
            INNER JOIN users u ON u.id = pr.user_id
            WHERE u.email = ?
            ORDER BY pr.id DESC LIMIT 1
            """,
            (email,),
        )
        return await cur.fetchone()


async def _set_expired(db_path, user_id):
    """Backdate the user's most recent reset token to be already expired."""
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with pg.connect(db_path) as db:
        await db.execute(
            "UPDATE password_resets SET expires_at = ? "
            "WHERE id = (SELECT id FROM password_resets WHERE user_id = ? "
            "            ORDER BY id DESC LIMIT 1)",
            (past, user_id),
        )
        await db.commit()


async def _count_sessions(db_path, user_id):
    async with pg.connect(db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return row[0]


def _register(client, email, password="s3cretpassword"):
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _issue_reset_and_get_raw_token(client, monkeypatch, email, db_path):
    """Helper: trigger reset request and capture the raw token via a monkey-
    patched ``generate_token`` so tests don't have to read it from email.
    """
    captured = {}
    orig = tokens.generate_token

    def _spy():
        raw, h = orig()
        captured["raw"] = raw
        return raw, h

    monkeypatch.setattr(tokens, "generate_token", _spy)
    # Also patch in the password_reset module (it imports the symbol at
    # module level via ``from .. import tokens``, which is referenced as
    # ``tokens.generate_token`` — so monkeypatching the module attribute is
    # sufficient).
    r = client.post("/api/auth/password-reset/request", json={"email": email})
    assert r.status_code == 204
    assert "raw" in captured, "expected reset to issue a token"
    return captured["raw"]


# ─── /password-reset/request ────────────────────────────────────────────────


def test_reset_request_returns_204_for_unknown_email(client):
    r = client.post(
        "/api/auth/password-reset/request",
        json={"email": "nobody@example.com"},
    )
    assert r.status_code == 204


def test_reset_request_returns_204_for_known_email(client):
    _register(client, "alice@example.com")
    r = client.post(
        "/api/auth/password-reset/request",
        json={"email": "alice@example.com"},
    )
    assert r.status_code == 204


def test_reset_request_persists_hashed_token_not_raw(client, monkeypatch, temp_db):
    _register(client, "alice@example.com")
    raw = _issue_reset_and_get_raw_token(client, monkeypatch, "alice@example.com", temp_db)
    row = asyncio.run(_read_reset_row(temp_db, "alice@example.com"))
    assert row is not None
    # The stored value must be the hash, NEVER the raw token.
    assert row["token_hash"] != raw
    assert row["token_hash"] == tokens.hash_token(raw)
    assert row["used_at"] is None


# ─── /password-reset/confirm ────────────────────────────────────────────────


def test_reset_confirm_happy_path_rehashes_and_revokes_sessions(
    client, monkeypatch, temp_db
):
    user_id = _register(client, "alice@example.com", "originalpassword")
    # The register call set a session cookie; count it.
    assert asyncio.run(_count_sessions(temp_db, user_id)) == 1

    raw = _issue_reset_and_get_raw_token(client, monkeypatch, "alice@example.com", temp_db)

    r = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": raw, "new_password": "newpasswordvalue"},
    )
    assert r.status_code == 204, r.text

    # Old password no longer works
    client.cookies.clear()
    r = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "originalpassword"},
    )
    assert r.status_code == 401

    # New password works
    r = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "newpasswordvalue"},
    )
    assert r.status_code == 200

    # Sessions table: the original (from register) was revoked; login above
    # created one new one. So exactly 1 session for that user.
    assert asyncio.run(_count_sessions(temp_db, user_id)) == 1


def test_reset_confirm_rejects_unknown_token(client):
    r = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": "not-a-real-token-xxxxxxxxxxxxxxxxxxxxxxx", "new_password": "newpasswordvalue"},
    )
    assert r.status_code == 400


def test_reset_confirm_rejects_expired_token(client, monkeypatch, temp_db):
    user_id = _register(client, "alice@example.com")
    raw = _issue_reset_and_get_raw_token(client, monkeypatch, "alice@example.com", temp_db)
    asyncio.run(_set_expired(temp_db, user_id))

    r = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": raw, "new_password": "newpasswordvalue"},
    )
    assert r.status_code == 400


def test_reset_confirm_rejects_reused_token(client, monkeypatch, temp_db):
    _register(client, "alice@example.com")
    raw = _issue_reset_and_get_raw_token(client, monkeypatch, "alice@example.com", temp_db)

    # First use succeeds
    r1 = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": raw, "new_password": "newpassword1"},
    )
    assert r1.status_code == 204

    # Second use fails
    r2 = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": raw, "new_password": "anothernewpassword"},
    )
    assert r2.status_code == 400


def test_reset_confirm_rejects_short_password(client, monkeypatch, temp_db):
    _register(client, "alice@example.com")
    raw = _issue_reset_and_get_raw_token(client, monkeypatch, "alice@example.com", temp_db)
    r = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": raw, "new_password": "short"},
    )
    assert r.status_code == 422  # pydantic min_length


def test_reset_confirm_rejects_soft_deleted_user(client, monkeypatch, temp_db):
    user_id = _register(client, "alice@example.com")
    raw = _issue_reset_and_get_raw_token(client, monkeypatch, "alice@example.com", temp_db)

    async def _soft_delete():
        async with pg.connect(temp_db) as db:
            await db.execute(
                "UPDATE users SET deleted_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
                (user_id,),
            )
            await db.commit()

    asyncio.run(_soft_delete())

    r = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": raw, "new_password": "newpasswordvalue"},
    )
    assert r.status_code == 400


# ─── Token helpers ───────────────────────────────────────────────────────────


def test_token_generate_returns_raw_and_hash():
    raw, h = tokens.generate_token()
    assert isinstance(raw, str) and len(raw) > 30
    assert h == tokens.hash_token(raw)
    # Hash is 64 hex chars (SHA256)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_token_generate_uniqueness():
    seen = {tokens.generate_token()[0] for _ in range(50)}
    assert len(seen) == 50  # vanishingly small collision probability


def test_hash_token_is_deterministic():
    assert tokens.hash_token("hello") == tokens.hash_token("hello")
    assert tokens.hash_token("hello") != tokens.hash_token("world")
