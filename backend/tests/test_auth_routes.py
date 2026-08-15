"""FastAPI auth + channels route integration tests.

Uses TestClient with a temporary DB path patched via ``DB_PATH`` env override.
HTTP responses are exercised end-to-end through the real session flow.
"""

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from migrations import runner
from src.repositories import pg
from src.services.channels import crypto


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Replace DB_PATH globally and run migrations in a fresh file."""
    db_path = str(tmp_path / "test.db")
    # Seed legacy tables that Batch 2 migrations rebuild.
    import asyncio

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

def test_register_creates_user_but_does_not_auto_login(client):
    """M2 — register succeeds (201) but no longer sets a session cookie. A cookie
    for a new user but none for a duplicate would itself enumerate accounts, so
    NEITHER path sets one; the user signs in next. The user IS created — they can
    log in immediately."""
    r = client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "password": "s3cretpassword"},
    )
    assert r.status_code == 201, r.text
    assert "job360_session" not in r.cookies
    # The account was really created — login works.
    lr = client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "s3cretpassword"},
    )
    assert lr.status_code == 200, lr.text
    assert "job360_session" in lr.cookies


def test_register_does_not_reveal_existing_email(client):
    """M2 — registering a NEW email and re-registering an EXISTING one must be
    indistinguishable (same status, same body, no cookie) so /register can't be
    used to enumerate accounts. The existing account is NOT modified."""
    new = client.post(
        "/api/auth/register",
        json={"email": "dup@example.com", "password": "s3cretpassword"},
    )
    dup = client.post(
        "/api/auth/register",
        json={"email": "dup@example.com", "password": "anothers3cretpassword"},
    )
    assert new.status_code == dup.status_code == 201
    assert new.json() == dup.json(), "responses differ → email existence is observable"
    assert "job360_session" not in dup.cookies
    # The existing account's password was NOT overwritten by the duplicate attempt.
    ok = client.post("/api/auth/login", json={"email": "dup@example.com", "password": "s3cretpassword"})
    assert ok.status_code == 200
    bad = client.post("/api/auth/login", json={"email": "dup@example.com", "password": "anothers3cretpassword"})
    assert bad.status_code == 401


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


def test_login_cookie_round_trips_to_me_and_names_the_owner(client):
    """CONTRACT the live synthetic monitor stands on — do not break silently.

    ``.github/workflows/synthetic-live.yml`` used to run on a ``job360_session``
    cookie the owner pasted into a repo secret by hand every 30 days
    (``SESSION_MAX_AGE_DAYS``). It now logs itself in instead, which needs exactly
    two things to stay true:

      1. ``POST /api/auth/login`` returns the session in a ``Set-Cookie`` header
         named ``job360_session`` — the monitor reads the raw HEADER, not a
         client-side cookie jar, so assert on the header.
      2. Sending that value back on ``GET /api/auth/me`` returns the OWNER'S
         EMAIL. That is the monitor's safety gate: it refuses to walk production
         unless the backend itself says the session belongs to a synthetic
         account. If ``/me`` stopped returning ``email``, the gate would refuse
         every run — or, worse, a laxer gate would let the robot loose on a real
         user's profile.

    Value-presence, not schema-presence (rule #21): the email asserted here is
    the real registered address, so a serializer returning ``""`` fails.
    """
    email = "synthetic+roundtrip@example.com"
    client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"})
    client.cookies.clear()

    login = client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    assert login.status_code == 200, login.text

    # (1) the raw Set-Cookie header, parsed the way the monitor parses it.
    set_cookie = login.headers.get("set-cookie", "")
    assert "job360_session=" in set_cookie, set_cookie
    cookie_value = set_cookie.split("job360_session=", 1)[1].split(";", 1)[0]
    assert cookie_value, "login returned an EMPTY session cookie"

    # (2) that value alone identifies the owner — no cookie jar, header only.
    client.cookies.clear()
    me = client.get("/api/auth/me", headers={"Cookie": f"job360_session={cookie_value}"})
    assert me.status_code == 200, me.text
    assert me.json()["email"] == email

    # And a session that is NOT this one must not resolve — the gate has to be
    # able to say no, or it is decoration.
    dead = client.get(
        "/api/auth/me", headers={"Cookie": "job360_session=deadbeef.an2pww.not-a-signature"}
    )
    assert dead.status_code == 401


# ----- me / logout -----------------------------------------------------

def test_me_requires_session(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_me_returns_user_with_valid_session(client):
    client.post(
        "/api/auth/register",
        json={"email": "z@example.com", "password": "s3cretpassword"},
    )
    # M2 — register no longer auto-logs-in; sign in for the session.
    client.post(
        "/api/auth/login",
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
    # M2 — register no longer auto-logs-in; sign in so there's a real session to revoke.
    client.post(
        "/api/auth/login",
        json={"email": "lo@example.com", "password": "s3cretpassword"},
    )
    r1 = client.post("/api/auth/logout")
    assert r1.status_code == 204
    client.cookies.clear()
    r2 = client.get("/api/auth/me")
    assert r2.status_code == 401
