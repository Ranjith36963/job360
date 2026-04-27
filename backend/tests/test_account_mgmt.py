"""Integration tests for Step-3 B-11..13 account-management endpoints.

B-11: DELETE /api/auth/users/me — soft-delete (GDPR Article 17)
B-12: PATCH  /api/auth/users/me/password — authenticated password change
B-13: PATCH  /api/auth/users/me/email    — email change (confirm via current password)
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import aiosqlite
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from migrations import runner
from src.services.channels import crypto

# ---------------------------------------------------------------------------
# Shared fixtures (mirrors test_auth_routes.py pattern)
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Fresh SQLite DB with all migrations applied, DB_PATH globally patched."""
    db_path = str(tmp_path / "test_acct.db")

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

    # Reset the JobDatabase singleton so get_db() creates a fresh one
    # pointing at this test's DB (not a previous test's DB).
    monkeypatch.setattr(dependencies, "_db", None)

    crypto.set_test_key(Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "x" * 40)

    yield db_path


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture
def client(temp_db):
    from src.api.main import app

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _register(client: TestClient, email: str, password: str = "s3cretpassword") -> dict:
    """Register a user and return the JSON body. Cookie is set on client."""
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()


def _login(client: TestClient, email: str, password: str = "s3cretpassword") -> int:
    """Login; returns HTTP status code."""
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    return r.status_code


# ---------------------------------------------------------------------------
# B-11: DELETE /api/auth/users/me
# ---------------------------------------------------------------------------


def test_delete_account_requires_auth(client):
    """DELETE without a session cookie must return 401."""
    r = client.delete("/api/auth/users/me")
    assert r.status_code == 401


def test_delete_account_soft_deletes(client, temp_db):
    """DELETE sets deleted_at; subsequent login with the same credentials fails."""
    _register(client, "del@example.com", "s3cretpassword")
    # Sanity: authenticated GET /me works before delete
    assert client.get("/api/auth/me").status_code == 200

    r = client.delete("/api/auth/users/me")
    assert r.status_code == 204

    # Session cookie cleared — /me is now 401
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401

    # Login must fail because deleted_at IS NOT NULL
    assert _login(client, "del@example.com") == 401


def test_delete_account_no_user_id_url_param(client):
    """The delete route MUST NOT accept a user_id URL parameter (rule #12).

    The endpoint is /api/auth/users/me — there is no /{user_id} segment.
    Attempting to call a hypothetical /api/auth/users/{id} must 404.
    """
    _register(client, "notme@example.com")
    user_id = client.get("/api/auth/me").json()["id"]
    # Try to hit a URL that would be an IDOR risk — it should 404
    r = client.delete(f"/api/auth/users/{user_id}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# B-12: PATCH /api/auth/users/me/password
# ---------------------------------------------------------------------------


def test_change_password_requires_auth(client):
    r = client.patch(
        "/api/auth/users/me/password",
        json={"current_password": "s3cretpassword", "new_password": "newpassword1"},
    )
    assert r.status_code == 401


def test_change_password_wrong_current(client):
    _register(client, "pw_wrong@example.com")
    r = client.patch(
        "/api/auth/users/me/password",
        json={"current_password": "WRONGPASSWORD", "new_password": "newpassword1"},
    )
    assert r.status_code == 401


def test_change_password_success(client):
    """Correct current password → 204; re-login with old password fails; new password works."""
    _register(client, "pw_ok@example.com", "oldpassword1")
    r = client.patch(
        "/api/auth/users/me/password",
        json={"current_password": "oldpassword1", "new_password": "newpassword1"},
    )
    assert r.status_code == 204

    # Old password should now fail login
    client.cookies.clear()
    assert _login(client, "pw_ok@example.com", "oldpassword1") == 401

    # New password must work
    assert _login(client, "pw_ok@example.com", "newpassword1") == 200


def test_change_password_short_new_password(client):
    """new_password shorter than 8 chars is rejected by Pydantic (422)."""
    _register(client, "pw_short@example.com")
    r = client.patch(
        "/api/auth/users/me/password",
        json={"current_password": "s3cretpassword", "new_password": "short"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# B-13: PATCH /api/auth/users/me/email
# ---------------------------------------------------------------------------


def test_change_email_requires_auth(client):
    r = client.patch(
        "/api/auth/users/me/email",
        json={"current_password": "s3cretpassword", "new_email": "newemail@example.com"},
    )
    assert r.status_code == 401


def test_change_email_wrong_password(client):
    _register(client, "em_wrong@example.com")
    r = client.patch(
        "/api/auth/users/me/email",
        json={"current_password": "WRONGPASSWORD", "new_email": "newemail@example.com"},
    )
    assert r.status_code == 401


def test_change_email_duplicate(client):
    """New email already registered by another user → 409."""
    _register(client, "first@example.com", "s3cretpassword")
    # Register second user and switch to their session
    client.cookies.clear()
    _register(client, "second@example.com", "s3cretpassword")

    r = client.patch(
        "/api/auth/users/me/email",
        json={"current_password": "s3cretpassword", "new_email": "first@example.com"},
    )
    assert r.status_code == 409


def test_change_email_success(client):
    """Correct password → 204; session cookie cleared; re-login with new email works."""
    _register(client, "em_ok@example.com", "s3cretpassword")
    r = client.patch(
        "/api/auth/users/me/email",
        json={"current_password": "s3cretpassword", "new_email": "new_em_ok@example.com"},
    )
    assert r.status_code == 204

    # Session cookie must be cleared → /me is 401
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401

    # Login with old email fails
    assert _login(client, "em_ok@example.com") == 401

    # Login with new email works
    assert _login(client, "new_em_ok@example.com") == 200


# ---------------------------------------------------------------------------
# B-11 IDOR safety: no user_id URL param exists anywhere on account routes
# ---------------------------------------------------------------------------


def test_idor_cannot_delete_other_user(client):
    """There is no /{user_id} segment on the delete route.

    Register two users; user B cannot delete user A by guessing A's id
    because the endpoint is /api/auth/users/me (session-scoped).
    Hitting /api/auth/users/<id> returns 404, not 204 or 403.
    """
    # Register user A
    _register(client, "userA@example.com", "passwordA1")
    user_a_id = client.get("/api/auth/me").json()["id"]
    client.cookies.clear()

    # Register user B
    _register(client, "userB@example.com", "passwordB1")

    # User B tries to delete user A via a guessed URL — must 404
    r = client.delete(f"/api/auth/users/{user_a_id}")
    assert r.status_code == 404

    # User A's account must be intact (soft-delete was NOT triggered)
    client.cookies.clear()
    assert _login(client, "userA@example.com", "passwordA1") == 200
