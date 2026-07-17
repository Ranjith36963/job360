"""Registration + password-reset rate-limit regression tests.

Covers two hardening fixes in ``src/api/routes/auth.py``:

* **M3** — ``POST /api/auth/register`` had ZERO rate limit, so one host could
  mass-create accounts. Now throttled per client IP (10 / hour). The 11th
  attempt from the same IP gets 429.
* **LOCKOUT** — ``POST /api/auth/password-reset/request`` was throttled on the
  client IP only, so rotating IPs let an attacker email-bomb one address. Now
  there is ALSO a per-email throttle (3 / hour) that fires even when every
  request comes from a different IP — and stays silent (204, no enumeration).

The limiter (``services/auth/rate_limit.py``) keeps state in process-global
dicts, so ``reset_for_tests()`` is called in setup (there is also an autouse
reset in conftest, but this file is explicit per the task).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from migrations import runner
from src.repositories import pg
from src.services.auth import rate_limit
from src.services.channels import crypto


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    rate_limit.reset_for_tests()
    yield
    rate_limit.reset_for_tests()


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Fresh migrated DB with DB_PATH patched on every importer."""
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


def _client_from_ip(host: str) -> TestClient:
    """A TestClient whose requests present ``host`` as ``request.client.host``.

    ``password_reset_request`` derives its per-IP throttle bucket from
    ``request.client.host`` directly (it does not read X-Forwarded-For), so to
    prove the per-EMAIL throttle bites across DIFFERENT source IPs we need each
    request to actually arrive from a different client host."""
    from src.api.main import app

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    return TestClient(app, client=(host, 50000))


# ─── M3: register per-IP throttle ────────────────────────────────────────────


def test_register_throttled_after_ten_from_one_ip(client):
    """10 registrations from one IP succeed; the 11th is 429."""
    # All 10 use distinct emails so nothing 409s — only the IP throttle can bite.
    for i in range(10):
        r = client.post(
            "/api/auth/register",
            json={"email": f"user{i}@example.com", "password": "s3cretpassword"},
        )
        assert r.status_code == 201, r.text
        client.cookies.clear()

    r = client.post(
        "/api/auth/register",
        json={"email": "user10@example.com", "password": "s3cretpassword"},
    )
    assert r.status_code == 429, r.text
    assert "registration attempts" in r.json()["detail"].lower()


def test_register_429_does_not_leak_email_existence(client):
    """When throttled, an already-registered email and a brand-new email both
    get the SAME 429 — the throttle fires before any DB lookup, so it can't be
    used to enumerate accounts."""
    for i in range(10):
        r = client.post(
            "/api/auth/register",
            json={"email": f"seed{i}@example.com", "password": "s3cretpassword"},
        )
        assert r.status_code == 201, r.text
        client.cookies.clear()

    # Existing email → still 429 (not 409), so no "email already registered" leak.
    r_existing = client.post(
        "/api/auth/register",
        json={"email": "seed0@example.com", "password": "s3cretpassword"},
    )
    r_new = client.post(
        "/api/auth/register",
        json={"email": "never-seen@example.com", "password": "s3cretpassword"},
    )
    assert r_existing.status_code == 429, r_existing.text
    assert r_new.status_code == 429, r_new.text
    assert r_existing.json()["detail"] == r_new.json()["detail"]


# ─── LOCKOUT: password-reset per-email throttle across IPs ────────────────────


def test_password_reset_throttled_per_email_across_ips(temp_db, monkeypatch):
    """One email can only trigger 3 reset requests / hour even when every
    request arrives from a DIFFERENT source IP — the per-IP throttle (1/60s)
    never fires here because each IP is a fresh bucket, so this isolates the
    per-EMAIL dimension.

    The endpoint stays silent (always 204, no enumeration): we assert the email
    SERVICE is skipped once the email throttle trips, not any status change."""
    import src.api.routes.auth as auth_route

    calls: list[str] = []

    async def _fake_request_password_reset(*, db_path, email, frontend_origin):
        calls.append(email)

    monkeypatch.setattr(
        auth_route.auth_password_reset,
        "request_password_reset",
        _fake_request_password_reset,
    )

    email = "target@example.com"
    # 3 requests, each from a DISTINCT client IP → every per-IP bucket is fresh
    # and passes; only the shared per-email bucket accumulates.
    for i in range(3):
        r = _client_from_ip(f"10.0.0.{i}").post(
            "/api/auth/password-reset/request", json={"email": email}
        )
        assert r.status_code == 204, r.text

    # 4th request, yet another fresh IP: still 204 (silent), but the reset
    # service must NOT have been invoked — the per-email throttle suppressed it.
    r = _client_from_ip("10.0.0.99").post(
        "/api/auth/password-reset/request", json={"email": email}
    )
    assert r.status_code == 204, r.text
    assert calls == [email, email, email], f"expected 3 sends before throttle, got {calls}"


def test_password_reset_email_throttle_is_per_email(temp_db, monkeypatch):
    """A different email is unaffected by another email's throttle."""
    import src.api.routes.auth as auth_route

    calls: list[str] = []

    async def _fake_request_password_reset(*, db_path, email, frontend_origin):
        calls.append(email)

    monkeypatch.setattr(
        auth_route.auth_password_reset,
        "request_password_reset",
        _fake_request_password_reset,
    )

    # Exhaust email A's throttle — each from a fresh IP so only the email bucket bites.
    for i in range(4):
        _client_from_ip(f"172.16.0.{i}").post(
            "/api/auth/password-reset/request", json={"email": "a@example.com"}
        )

    # Email B, first ever request, still goes through.
    r = _client_from_ip("172.16.0.200").post(
        "/api/auth/password-reset/request", json={"email": "b@example.com"}
    )
    assert r.status_code == 204, r.text
    assert "b@example.com" in calls
