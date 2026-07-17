"""Integration tests for Step-3 B-11..13 account-management endpoints.

B-11: DELETE /api/auth/users/me — soft-delete (GDPR Article 17)
B-12: PATCH  /api/auth/users/me/password — authenticated password change
B-13: PATCH  /api/auth/users/me/email    — email change (confirm via current password)
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Optional

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from httpx import Response

from migrations import runner
from src.repositories import pg
from src.services.channels import crypto

# ---------------------------------------------------------------------------
# Shared fixtures (mirrors test_auth_routes.py pattern)
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Fresh SQLite DB with all migrations applied, DB_PATH globally patched."""
    db_path = str(tmp_path / "test_acct.db")

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


def _delete_me(client: TestClient, password: Optional[str] = None) -> Response:
    """Send DELETE /api/auth/users/me.

    Starlette's TestClient.delete() does not expose a body parameter — use
    ``client.request("DELETE", ...)`` with an explicit JSON body instead.
    """
    import json as _json

    if password is not None:
        return client.request(
            "DELETE",
            "/api/auth/users/me",
            content=_json.dumps({"current_password": password}),
            headers={"Content-Type": "application/json"},
        )
    return client.delete("/api/auth/users/me")


def test_delete_account_requires_auth(client):
    """DELETE without a session cookie must return 401."""
    r = _delete_me(client, "s3cretpassword")
    assert r.status_code == 401


def test_delete_account_soft_deletes(client, temp_db):
    """DELETE with correct password sets deleted_at; subsequent login fails (rule #26)."""
    _register(client, "del@example.com", "s3cretpassword")
    # Sanity: authenticated GET /me works before delete
    assert client.get("/api/auth/me").status_code == 200

    r = _delete_me(client, "s3cretpassword")
    assert r.status_code == 204

    # Session cookie cleared — /me is now 401
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401

    # Login must fail because deleted_at IS NOT NULL
    assert _login(client, "del@example.com") == 401


def test_delete_account_wrong_password_is_rejected(client, temp_db):
    """DELETE with a wrong password must return 401 and NOT soft-delete the account.

    This is the regression test for the confirmed HIGH-severity security bug where
    the handler previously accepted any body (including no body / wrong password)
    and deleted the account immediately. Hard rule #26.
    """
    _register(client, "del_wrong@example.com", "s3cretpassword")

    r = _delete_me(client, "WRONGPASSWORD")
    assert r.status_code == 401, "wrong password must be rejected"

    # The account must NOT have been soft-deleted — login still works
    client.cookies.clear()
    assert _login(client, "del_wrong@example.com", "s3cretpassword") == 200


def test_delete_account_wrong_password_does_not_soft_delete_db(client, temp_db):
    """Verify at the DB level that deleted_at stays NULL after a wrong-password attempt."""
    _register(client, "del_db_check@example.com", "s3cretpassword")

    _delete_me(client, "WRONGPASSWORD")

    async def _check():
        async with pg.connect(temp_db) as db:
            db.row_factory = pg.Row
            cur = await db.execute(
                "SELECT deleted_at FROM users WHERE email = ?",
                ("del_db_check@example.com",),
            )
            row = await cur.fetchone()
        return row

    row = asyncio.run(_check())
    assert row is not None, "user row missing from DB"
    assert row["deleted_at"] is None, "deleted_at must remain NULL after wrong-password attempt"


def test_delete_account_missing_body_is_422(client):
    """DELETE with no body at all must return 422 (Pydantic validation).

    The old handler ignored the body and would 204; the new one requires
    `current_password` so an empty request must be rejected before any DB call.
    """
    _register(client, "del_nobody@example.com", "s3cretpassword")
    # Send DELETE with no JSON body
    r = _delete_me(client)
    assert r.status_code == 422


def test_delete_account_clears_session_cookie(client, temp_db):
    """Successful delete must clear the session cookie (rule #26)."""
    _register(client, "del_cookie@example.com", "s3cretpassword")
    r = _delete_me(client, "s3cretpassword")
    assert r.status_code == 204
    # The Set-Cookie header must clear the job360_session cookie
    assert "job360_session=" in r.headers.get("set-cookie", ""), (
        "response must clear job360_session cookie on successful delete"
    )


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


def test_change_password_invalidates_session(client):
    """Rule #26: a successful password change MUST invalidate the session
    cookie (force re-login), like email change already does."""
    _register(client, "pw_session@example.com", "oldpassword1")
    # Authenticated before the change.
    assert client.get("/api/auth/me").status_code == 200
    r = client.patch(
        "/api/auth/users/me/password",
        json={"current_password": "oldpassword1", "new_password": "newpassword1"},
    )
    assert r.status_code == 204
    # The response must clear the session cookie.
    assert "job360_session=" in r.headers.get("set-cookie", "")
    # And the same client can no longer reach a protected route.
    assert client.get("/api/auth/me").status_code == 401


def test_change_password_revokes_all_user_sessions(client):
    """Rule #26 (full intent): a password change must terminate ALL of the
    user's sessions server-side, not just the current browser cookie — so a
    session left open on another device / a stolen cookie cannot survive it.
    """
    _register(client, "multi_sess@example.com", "oldpassword1")
    cookie_a = client.cookies.get("job360_session")
    # A second login → a second, independent server-side session for the user.
    client.cookies.clear()
    assert _login(client, "multi_sess@example.com", "oldpassword1") == 200
    cookie_b = client.cookies.get("job360_session")
    assert cookie_a and cookie_b and cookie_a != cookie_b

    # Change the password while authenticated with session B.
    r = client.patch(
        "/api/auth/users/me/password",
        json={"current_password": "oldpassword1", "new_password": "newpassword1"},
    )
    assert r.status_code == 204

    # BOTH sessions must now be dead server-side (use each cookie explicitly).
    client.cookies.clear()
    client.cookies.set("job360_session", cookie_a)
    assert client.get("/api/auth/me").status_code == 401, "old device session A survived"
    client.cookies.clear()
    client.cookies.set("job360_session", cookie_b)
    assert client.get("/api/auth/me").status_code == 401, "session B survived"


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

    # The response itself must clear the session cookie (rule #26) — don't
    # manually clear, or the test can't tell whether the server did it.
    assert "job360_session=" in r.headers.get("set-cookie", "")
    # The same client (cookie now cleared by the response) is logged out.
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
