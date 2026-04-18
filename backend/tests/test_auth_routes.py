"""FastAPI auth + channels route integration tests.

Uses TestClient with a temporary DB path patched via ``DB_PATH`` env override.
HTTP responses are exercised end-to-end through the real session flow.
"""
import os
import tempfile
from unittest.mock import patch

import aiosqlite
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from migrations import runner
from src.services.channels import crypto


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Replace DB_PATH globally and run migrations in a fresh file."""
    db_path = str(tmp_path / "test.db")
    # Seed legacy tables that Batch 2 migrations rebuild.
    import asyncio

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

    asyncio.get_event_loop().run_until_complete(_bootstrap()) if False else asyncio.run(_bootstrap())

    # Patch every module that has already imported DB_PATH.
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

    # Fresh Fernet key per test (no leakage between runs).
    crypto.set_test_key(Fernet.generate_key().decode("ascii"))
    # Session secret
    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "x" * 40)

    yield db_path


@pytest.fixture
def client(temp_db):
    """TestClient with lifespan skipped — migrations already applied."""
    from src.api.main import app

    # Strip the lifespan so TestClient doesn't re-run init_db against the
    # real DB_PATH (we've patched it already).
    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    with TestClient(app) as c:
        yield c


from contextlib import asynccontextmanager


@asynccontextmanager
async def _noop_lifespan(app):
    yield


# ----- register --------------------------------------------------------

def test_register_creates_user_and_returns_cookie(client):
    r = client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "s3cretpassword"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["email"] == "alice@example.com"
    assert "job360_session" in r.cookies


def test_register_rejects_duplicate_email(client):
    client.post(
        "/api/auth/register",
        json={"email": "dup@example.com", "password": "s3cretpassword"},
    )
    r = client.post(
        "/api/auth/register",
        json={"email": "dup@example.com", "password": "anothers3cretpassword"},
    )
    assert r.status_code == 409


def test_register_rejects_short_password(client):
    r = client.post(
        "/api/auth/register",
        json={"email": "short@example.com", "password": "short"},
    )
    assert r.status_code == 422  # pydantic min_length


# ----- login -----------------------------------------------------------

def test_login_wrong_password_rejected(client):
    client.post(
        "/api/auth/register",
        json={"email": "x@example.com", "password": "s3cretpassword"},
    )
    r = client.post(
        "/api/auth/login",
        json={"email": "x@example.com", "password": "wrongpassword"},
    )
    assert r.status_code == 401


def test_login_happy_path_sets_cookie(client):
    client.post(
        "/api/auth/register",
        json={"email": "y@example.com", "password": "s3cretpassword"},
    )
    client.cookies.clear()
    r = client.post(
        "/api/auth/login",
        json={"email": "y@example.com", "password": "s3cretpassword"},
    )
    assert r.status_code == 200
    assert "job360_session" in r.cookies


# ----- me / logout -----------------------------------------------------

def test_me_requires_session(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_me_returns_user_with_valid_session(client):
    client.post(
        "/api/auth/register",
        json={"email": "z@example.com", "password": "s3cretpassword"},
    )
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == "z@example.com"


def test_logout_revokes_session(client):
    client.post(
        "/api/auth/register",
        json={"email": "lo@example.com", "password": "s3cretpassword"},
    )
    r1 = client.post("/api/auth/logout")
    assert r1.status_code == 204
    client.cookies.clear()
    r2 = client.get("/api/auth/me")
    assert r2.status_code == 401
