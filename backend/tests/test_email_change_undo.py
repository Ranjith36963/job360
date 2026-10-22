"""Email change: unverify the new address, tell the old one, offer an undo link
(gap roadmap slice 35, migration 0076).

Before: PATCH /users/me/email left ``email_verified_at`` set, so the account stayed
"verified" for an address nobody proved, and the old address was never told.
"""
from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from migrations import runner
from src.repositories import pg
from src.services.auth import email_change_undo, email_verification, tokens

OLD = "owner@example.com"
NEW = "newaddress@example.org"
PW = "s3cretpassword"
VERIFIED_AT = "2026-01-02T03:04:05Z"


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "undo.db")

    async def _bootstrap():
        # Legacy tables an early migration still reads (same bootstrap as test_account_mgmt).
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

    from src.api import auth_deps, dependencies
    from src.api.routes import auth as auth_route
    from src.core import settings

    patched = Path(db_path)
    monkeypatch.setattr(settings, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(dependencies, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(auth_deps, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(auth_route, "DB_PATH", patched, raising=True)
    monkeypatch.setattr(dependencies, "_db", None)
    monkeypatch.setenv("SESSION_SECRET", "test-secret-" + "x" * 40)
    monkeypatch.setenv("FRONTEND_ORIGIN", "https://app.example.test")
    yield db_path


@pytest.fixture
def sent(monkeypatch):
    """Every system email, as (to, subject, text). Patched where it is imported."""
    box: list[tuple[str, str, str]] = []

    async def _fake(*, to_email, subject, body_text, body_html=None):
        box.append((to_email, subject, body_text))
        return True

    monkeypatch.setattr(email_verification, "send_system_email", _fake)
    monkeypatch.setattr(email_change_undo, "send_system_email", _fake)
    return box


@pytest.fixture
def client(temp_db, sent):
    from src.api.main import app

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    with TestClient(app) as c:
        yield c


def _register(client, email=OLD):
    assert client.post("/api/auth/register", json={"email": email, "password": PW}).status_code == 201
    lr = client.post("/api/auth/login", json={"email": email, "password": PW})
    assert lr.status_code == 200, lr.text
    return lr.json()


def _q(db_path, sql, params=()):
    async def _run():
        async with pg.connect(db_path) as db:
            db.row_factory = pg.Row
            cur = await db.execute(sql, params)
            return await cur.fetchall()

    return asyncio.run(_run())


def _x(db_path, sql, params=()):
    async def _run():
        async with pg.connect(db_path) as db:
            await db.execute(sql, params)
            await db.commit()

    asyncio.run(_run())


def _mark_verified(db_path, user_id):
    _x(db_path, "UPDATE users SET email_verified_at = ? WHERE id = ?", (VERIFIED_AT, user_id))


def _change(client, new_email=NEW):
    return client.patch("/api/auth/users/me/email", json={"current_password": PW, "new_email": new_email})


def _undo_token(sent) -> str:
    notice = [m for m in sent if "was changed" in m[1]]
    assert len(notice) == 1, sent
    m = re.search(r"/undo-email-change\?token=([\w-]+)", notice[0][2])
    assert m, notice[0][2]
    return m.group(1)


def test_change_clears_verified_and_verifies_new_address(client, temp_db, sent):
    user = _register(client)
    _mark_verified(temp_db, user["id"])

    assert _change(client).status_code == 204

    row = _q(temp_db, "SELECT email, email_verified_at FROM users WHERE id = ?", (user["id"],))[0]
    assert row["email"] == NEW
    assert row["email_verified_at"] is None, "the new address must start unverified"

    # The existing verification mail went to the NEW address, with a stored row for it.
    verify = [m for m in sent if m[0] == NEW]
    assert len(verify) == 1 and "/verify-email?token=" in verify[0][2], sent
    rows = _q(temp_db, "SELECT email FROM email_verifications WHERE user_id = ? AND email = ?", (user["id"], NEW))
    assert len(rows) == 1


def test_old_address_gets_masked_notice_with_undo_link(client, temp_db, sent):
    user = _register(client)
    assert _change(client).status_code == 204

    to, subject, text = next(m for m in sent if m[0] == OLD and "changed" in m[1])
    assert to == OLD
    assert "n***@example.org" in text, text  # mask_email(NEW)
    assert NEW not in text and "newaddress" not in text, "the full new address must never be mailed"
    assert " UTC" in text and "24 hours" in text
    assert "https://app.example.test/undo-email-change?token=" in text

    # Stored hashed, never raw; remembers what to restore.
    raw = _undo_token(sent)
    rows = _q(temp_db, "SELECT token_hash, previous_email, used_at FROM email_change_undos WHERE user_id = ?",
              (user["id"],))
    assert len(rows) == 1 and rows[0]["token_hash"] == tokens.hash_token(raw) != raw
    assert rows[0]["previous_email"] == OLD and rows[0]["used_at"] is None


def test_send_failure_never_breaks_the_change(client, temp_db, monkeypatch):
    async def _boom(**_kw):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(email_verification, "send_system_email", _boom)
    monkeypatch.setattr(email_change_undo, "send_system_email", _boom)
    user = _register(client)

    assert _change(client).status_code == 204
    assert _q(temp_db, "SELECT email FROM users WHERE id = ?", (user["id"],))[0]["email"] == NEW


def test_undo_restores_email_and_verified_state_and_revokes_everything(client, temp_db, sent):
    from src.services.auth import api_tokens
    from tests.test_account_mgmt import _give_agent_access

    user = _register(client)
    _mark_verified(temp_db, user["id"])
    assert _change(client).status_code == 204  # the change itself revokes; grant access AFTER it
    agent = asyncio.run(_give_agent_access(temp_db, user["id"]))
    assert asyncio.run(api_tokens.resolve(temp_db, agent)) is not None
    token = _undo_token(sent)

    r = client.post("/api/auth/email-change/undo", json={"token": token})
    assert r.status_code == 204, r.text
    assert "job360_session=" in r.headers.get("set-cookie", "")

    row = _q(temp_db, "SELECT email, email_verified_at FROM users WHERE id = ?", (user["id"],))[0]
    assert row["email"] == OLD and row["email_verified_at"] == VERIFIED_AT
    assert asyncio.run(api_tokens.resolve(temp_db, agent)) is None, "agent token survived the undo"
    assert _q(temp_db, "SELECT COUNT(*) AS n FROM oauth_grants WHERE user_id = ? AND revoked_at IS NULL",
              (user["id"],))[0]["n"] == 0
    assert _q(temp_db, "SELECT COUNT(*) AS n FROM sessions WHERE user_id = ?", (user["id"],))[0]["n"] == 0
    assert _q(temp_db, "SELECT used_at FROM email_change_undos WHERE user_id = ?", (user["id"],))[0]["used_at"]

    # Signing in with the restored address works again.
    assert client.post("/api/auth/login", json={"email": OLD, "password": PW}).status_code == 200

    # Single use.
    assert client.post("/api/auth/email-change/undo", json={"token": token}).status_code == 400


def test_undo_of_an_unverified_account_restores_unverified(client, temp_db, sent):
    user = _register(client)  # never verified
    assert _change(client).status_code == 204
    token = _undo_token(sent)
    assert client.post("/api/auth/email-change/undo", json={"token": token}).status_code == 204
    row = _q(temp_db, "SELECT email, email_verified_at FROM users WHERE id = ?", (user["id"],))[0]
    assert row["email"] == OLD and row["email_verified_at"] is None


def test_expired_unknown_and_short_tokens_are_400(client, temp_db, sent):
    user = _register(client)
    assert _change(client).status_code == 204
    token = _undo_token(sent)
    _x(temp_db, "UPDATE email_change_undos SET expires_at = ? WHERE user_id = ?", ("2000-01-01T00:00:00Z", user["id"]))

    assert client.post("/api/auth/email-change/undo", json={"token": token}).status_code == 400
    assert client.post("/api/auth/email-change/undo", json={"token": "x" * 43}).status_code == 400
    assert client.post("/api/auth/email-change/undo", json={"token": "short"}).status_code == 422
    # Nothing was restored.
    assert _q(temp_db, "SELECT email FROM users WHERE id = ?", (user["id"],))[0]["email"] == NEW


def test_previous_address_taken_by_another_account_is_409_and_changes_nothing(client, temp_db, sent):
    user = _register(client)
    assert _change(client).status_code == 204
    token = _undo_token(sent)
    client.cookies.clear()
    _register(client, OLD)  # someone else now owns the freed address

    r = client.post("/api/auth/email-change/undo", json={"token": token})
    assert r.status_code == 409, r.text
    assert _q(temp_db, "SELECT email FROM users WHERE id = ?", (user["id"],))[0]["email"] == NEW
    assert _q(temp_db, "SELECT used_at FROM email_change_undos WHERE user_id = ?", (user["id"],))[0]["used_at"] is None


def test_undoing_a_change_voids_the_links_of_later_changes(client, temp_db, sent):
    """A->B->C by an attacker: once the owner restores A, the B-link (mailed to
    the attacker's B) must not take the account back to B."""
    user = _register(client)
    assert _change(client).status_code == 204
    assert client.post("/api/auth/login", json={"email": NEW, "password": PW}).status_code == 200
    assert _change(client, "third@example.net").status_code == 204
    notices = {m[0]: m[2] for m in sent if "was changed" in m[1]}
    owner_link, attacker_link = (
        re.search(r"token=([\w-]+)", notices[addr]).group(1) for addr in (OLD, NEW)
    )

    assert client.post("/api/auth/email-change/undo", json={"token": owner_link}).status_code == 204
    assert client.post("/api/auth/email-change/undo", json={"token": attacker_link}).status_code == 400
    assert _q(temp_db, "SELECT email FROM users WHERE id = ?", (user["id"],))[0]["email"] == OLD


def test_undoing_a_later_change_keeps_the_owners_earlier_link(client, temp_db, sent):
    user = _register(client)
    assert _change(client).status_code == 204
    assert client.post("/api/auth/login", json={"email": NEW, "password": PW}).status_code == 200
    assert _change(client, "third@example.net").status_code == 204
    notices = {m[0]: m[2] for m in sent if "was changed" in m[1]}
    owner_link, attacker_link = (
        re.search(r"token=([\w-]+)", notices[addr]).group(1) for addr in (OLD, NEW)
    )

    assert client.post("/api/auth/email-change/undo", json={"token": attacker_link}).status_code == 204
    assert client.post("/api/auth/email-change/undo", json={"token": owner_link}).status_code == 204
    assert _q(temp_db, "SELECT email FROM users WHERE id = ?", (user["id"],))[0]["email"] == OLD


def test_undo_route_is_rate_limited(client):
    codes = [client.post("/api/auth/email-change/undo", json={"token": "y" * 43}).status_code for _ in range(11)]
    assert codes[:10] == [400] * 10 and codes[10] == 429


def test_change_email_still_needs_a_session(client):
    client.cookies.clear()
    assert _change(client).status_code == 401
