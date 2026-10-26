"""Pre-account hijack (AUTH-1): a magic link must not hand the mailbox owner an
account a stranger already holds credentials for.

Anyone can `/auth/register` an address they do not own (no verification needed)
and sign in to it with their own password. When the real owner later signs in
with a magic link, consume finds that UNVERIFIED row and verifies it. Before the
fix the stranger's password, sessions, `j360_` tokens and OAuth grants all kept
working, so they read everything the owner then stored.

The fix: on consume, an existing row that was not yet verified is treated as
unproven. Its password is replaced with an unusable random hash
(`has_password` FALSE), every session is deleted and all agent access is
revoked, before the owner's new session is created. An ALREADY-verified
account is the owner's own: a normal magic sign-in must not touch its password
or sign it out elsewhere.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from migrations import runner
from src.repositories import pg
from src.services.auth import api_tokens, rate_limit, tokens

EMAIL = "owner@example.com"
ATTACKER_PW = "attacker-pw-123456"  # noqa: S105 — obviously fake test value


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
    async def _fake_send(**_kwargs):
        return True

    for mod in ("magic_link", "email_verification", "security_notices"):
        monkeypatch.setattr(f"src.services.auth.{mod}.send_system_email", _fake_send)


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "preaccount.db")

    async def _bootstrap():
        # The legacy tables an early migration reads (same bootstrap as test_magic_link).
        async with pg.connect(db_path) as db:
            await db.executescript(
                """
                CREATE TABLE user_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,
                    action TEXT NOT NULL, notes TEXT DEFAULT '', created_at TEXT NOT NULL,
                    UNIQUE(job_id)
                );
                CREATE TABLE applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,
                    stage TEXT NOT NULL DEFAULT 'applied', notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(job_id)
                );
                """
            )
            await db.commit()
        await runner.up(db_path)

    asyncio.run(_bootstrap())

    from src.api import auth_deps, dependencies
    from src.api.routes import auth as auth_route
    from src.api.routes import tokens as tokens_route
    from src.core import settings

    patched = Path(db_path)
    for mod in (settings, dependencies, auth_deps, auth_route, tokens_route):
        monkeypatch.setattr(mod, "DB_PATH", patched, raising=True)
    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "x" * 40)
    yield db_path


@pytest.fixture
def app(temp_db):
    from src.api.main import app as fastapi_app

    fastapi_app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    return fastapi_app


async def _query_one(db_path, sql, params=()):
    async with pg.connect(db_path) as db:
        db.row_factory = pg.Row
        cur = await db.execute(sql, params)
        return await cur.fetchone()


async def _exec(db_path, sql, params=()):
    async with pg.connect(db_path) as db:
        await db.execute(sql, params)
        await db.commit()


def _register_and_sign_in(client: TestClient) -> str:
    """Register EMAIL with the attacker's password, sign in, return session cookie A."""
    r = client.post("/api/auth/register", json={"email": EMAIL, "password": ATTACKER_PW})
    assert r.status_code == 201, r.text
    r = client.post("/api/auth/login", json={"email": EMAIL, "password": ATTACKER_PW})
    assert r.status_code == 200, r.text
    assert client.get("/api/auth/me").status_code == 200
    return r.cookies["job360_session"]


def _seed_oauth_grant(db_path, user_id):
    async def _seed():
        async with pg.connect(db_path) as db:
            await db.execute(
                "INSERT INTO oauth_clients (id, client_name, redirect_uris) VALUES (?, ?, ?)",
                ("j360c_test", "Test client", '["https://claude.ai/cb"]'),
            )
            await db.execute(
                "INSERT INTO oauth_grants (user_id, client_id, scope) VALUES (?, ?, ?)",
                (user_id, "j360c_test", "mcp"),
            )
            await db.commit()

    asyncio.run(_seed())


def _magic_sign_in(client: TestClient, monkeypatch) -> dict:
    captured: list[str] = []
    orig = tokens.generate_token

    def _spy():
        raw, h = orig()
        captured.append(raw)
        return raw, h

    monkeypatch.setattr(tokens, "generate_token", _spy)
    r = client.post("/api/auth/magic-link/request", json={"email": EMAIL})
    assert r.status_code == 204, r.text
    monkeypatch.setattr(tokens, "generate_token", orig)
    r = client.post("/api/auth/magic-link/consume", json={"token": captured[-1]})
    assert r.status_code == 200, r.text
    return r.json()


def test_magic_consume_locks_the_pre_registrant_out(app, temp_db, monkeypatch):
    # The escape hatch prod can run with: an unverified account may mint a token.
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "false")
    with TestClient(app) as attacker, TestClient(app) as owner:
        cookie_a = _register_and_sign_in(attacker)
        minted = attacker.post("/api/tokens", json={"name": "attacker-agent"})
        assert minted.status_code == 201, minted.text
        token = minted.json()["token"]
        user_id = asyncio.run(_query_one(temp_db, "SELECT id FROM users WHERE email = ?", (EMAIL,)))["id"]
        _seed_oauth_grant(temp_db, user_id)

        body = _magic_sign_in(owner, monkeypatch)
        assert body["id"] == user_id  # the same row, now the owner's

        # The attacker's password no longer opens the account ...
        relogin = attacker.post("/api/auth/login", json={"email": EMAIL, "password": ATTACKER_PW})
        assert relogin.status_code == 401, relogin.text
        # ... their earlier session is gone ...
        attacker.cookies.clear()
        attacker.cookies.set("job360_session", cookie_a)
        stale = attacker.get("/api/auth/me")
        assert stale.status_code == 401, stale.text
        # ... and every agent credential is revoked.
        assert asyncio.run(api_tokens.resolve(temp_db, token)) is None
        grant = asyncio.run(_query_one(temp_db, "SELECT revoked_at FROM oauth_grants WHERE user_id = ?", (user_id,)))
        assert grant["revoked_at"] is not None

        # The owner is signed in, verified, and can delete without a password.
        assert owner.get("/api/auth/me").status_code == 200
        row = asyncio.run(
            _query_one(temp_db, "SELECT has_password, email_verified_at FROM users WHERE id = ?", (user_id,))
        )
        assert row["has_password"] is False
        assert row["email_verified_at"] is not None


def test_magic_consume_keeps_a_verified_account_signed_in_elsewhere(app, temp_db, monkeypatch):
    monkeypatch.delenv("REQUIRE_EMAIL_VERIFICATION", raising=False)
    with TestClient(app) as laptop, TestClient(app) as phone:
        r = laptop.post("/api/auth/register", json={"email": EMAIL, "password": ATTACKER_PW})
        assert r.status_code == 201, r.text
        asyncio.run(_exec(temp_db, "UPDATE users SET email_verified_at = '2026-01-01T00:00:00Z' WHERE email = ?", (EMAIL,)))
        assert laptop.post("/api/auth/login", json={"email": EMAIL, "password": ATTACKER_PW}).status_code == 200
        minted = laptop.post("/api/tokens", json={"name": "my-agent"})
        assert minted.status_code == 201, minted.text

        _magic_sign_in(phone, monkeypatch)

        assert laptop.get("/api/auth/me").status_code == 200  # other session survives
        assert asyncio.run(api_tokens.resolve(temp_db, minted.json()["token"])) is not None
        row = asyncio.run(_query_one(temp_db, "SELECT has_password FROM users WHERE email = ?", (EMAIL,)))
        assert row["has_password"] is True
        again = laptop.post("/api/auth/login", json={"email": EMAIL, "password": ATTACKER_PW})
        assert again.status_code == 200, again.text


def test_update_user_email_stores_lowercase(temp_db):
    from src.repositories.database import JobDatabase

    asyncio.run(_exec(temp_db, "INSERT INTO users(id, email, password_hash) VALUES (?, ?, ?)", ("u1", "a@example.com", "!")))

    async def _change():
        async with pg.connect(temp_db) as conn:
            await JobDatabase.from_connection(temp_db, conn).update_user_email("u1", "Mixed.Case@Example.COM")

    asyncio.run(_change())
    row = asyncio.run(_query_one(temp_db, "SELECT email FROM users WHERE id = ?", ("u1",)))
    assert row["email"] == "mixed.case@example.com"
