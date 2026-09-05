"""Brute-force login lockout (LAUNCH_PLAN Phase 2 #10).

After N failed login attempts for an email within a sliding window, further
attempts are locked (HTTP 429) until the window passes — even with the correct
password. A successful login before lockout clears the counter.

In-memory only (shares `services/auth/rate_limit.py`), like the existing
email-endpoint limiter — Redis-backed swap is Phase 3.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from migrations import runner
from src.repositories import pg
from src.services.auth import rate_limit


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    rate_limit.reset_for_tests()
    yield
    rate_limit.reset_for_tests()


# ─── Unit tests: the failure-counting lockout primitives ─────────────────────


def test_under_threshold_not_locked():
    key = "login:a@example.com"
    for _ in range(4):
        rate_limit.record_failure(key)
    assert rate_limit.is_locked(key, max_failures=5, window_seconds=900) is False


def test_at_threshold_locked():
    key = "login:a@example.com"
    for _ in range(5):
        rate_limit.record_failure(key)
    assert rate_limit.is_locked(key, max_failures=5, window_seconds=900) is True


def test_clear_failures_resets():
    key = "login:a@example.com"
    for _ in range(5):
        rate_limit.record_failure(key)
    assert rate_limit.is_locked(key, max_failures=5, window_seconds=900) is True
    rate_limit.clear_failures(key)
    assert rate_limit.is_locked(key, max_failures=5, window_seconds=900) is False


def test_window_expiry_unlocks():
    key = "login:a@example.com"
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    for _ in range(5):
        rate_limit.record_failure(key, now=t0)
    # Still locked at the moment of the burst.
    assert rate_limit.is_locked(key, max_failures=5, window_seconds=900, now=t0) is True
    # 901s later the whole burst has aged out of the 900s window.
    later = t0 + timedelta(seconds=901)
    assert rate_limit.is_locked(key, max_failures=5, window_seconds=900, now=later) is False


def test_failures_are_per_key():
    rate_limit.record_failure("login:a@example.com")
    for _ in range(5):
        rate_limit.record_failure("login:b@example.com")
    assert rate_limit.is_locked("login:a@example.com", max_failures=5, window_seconds=900) is False
    assert rate_limit.is_locked("login:b@example.com", max_failures=5, window_seconds=900) is True


# ─── Route tests: POST /api/auth/login lockout end-to-end ────────────────────


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

    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "x" * 40)

    yield db_path


@pytest.fixture
def client(temp_db):
    from src.api.main import app

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    with TestClient(app) as c:
        yield c


_EMAIL = "victim@example.com"
_PW = "correct-horse-battery"


def _register(client):
    r = client.post("/api/auth/register", json={"email": _EMAIL, "password": _PW})
    assert r.status_code == 201, r.text


def test_login_locks_after_five_failures(client):
    _register(client)
    # 5 wrong passwords → each rejected 401.
    for _ in range(5):
        r = client.post("/api/auth/login", json={"email": _EMAIL, "password": "wrong"})
        assert r.status_code == 401, r.text
    # 6th attempt is locked out — 429, even though we'd give a wrong password.
    r = client.post("/api/auth/login", json={"email": _EMAIL, "password": "wrong"})
    assert r.status_code == 429, r.text
    assert "Retry-After" in r.headers


def test_lock_blocks_even_correct_password(client):
    _register(client)
    for _ in range(5):
        client.post("/api/auth/login", json={"email": _EMAIL, "password": "wrong"})
    # Correct password now, but the account is locked → still 429.
    r = client.post("/api/auth/login", json={"email": _EMAIL, "password": _PW})
    assert r.status_code == 429, r.text


def test_successful_login_clears_failures(client):
    _register(client)
    # 4 wrong (under the 5 threshold)…
    for _ in range(4):
        r = client.post("/api/auth/login", json={"email": _EMAIL, "password": "wrong"})
        assert r.status_code == 401
    # …then a correct login succeeds and clears the counter.
    r = client.post("/api/auth/login", json={"email": _EMAIL, "password": _PW})
    assert r.status_code == 200, r.text
    # Counter was reset, so 4 more wrong attempts still don't lock.
    for _ in range(4):
        r = client.post("/api/auth/login", json={"email": _EMAIL, "password": "wrong"})
        assert r.status_code == 401, r.text
