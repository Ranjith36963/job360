"""Gap roadmap slice 3 — an agent token can never reach the account itself.

A personal ``j360_`` token (or an OAuth access token) never expires on its own,
so a leaked one used to download (export) or erase (delete, clear-all) the whole
account, or move it (email/password change). Those five routes are web-session
only now (``require_session_user``: 403 ``session_required``).

Also here: a magic-link-only account (``users.has_password`` FALSE, migration
0069) can delete itself from a FRESH email-link session (GDPR Art. 17), and a
``/profile/clear`` ERASES the user-info memory's ``profile_edits`` history
(owner decision 2026-10-26: "Clear memory should erase").
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.repositories import pgsync

CONTACT = "user_info.contact"
EQ = "user_info.equality"

# (verb, path, request kwargs) — the five session-only account routes.
ACCOUNT_ROUTES = [
    ("GET", "/api/auth/users/me/export", {}),
    ("DELETE", "/api/auth/users/me", {"json": {"current_password": "s3cretpassword"}}),
    (
        "PATCH", "/api/auth/users/me/password",
        {"json": {"current_password": "s3cretpassword", "new_password": "an0therpassword"}},
    ),
    (
        "PATCH", "/api/auth/users/me/email",
        {"json": {"current_password": "s3cretpassword", "new_email": "moved@example.com"}},
    ),
    ("POST", "/api/profile/clear", {"data": {"section": "all"}}),
]


def _client(**kwargs) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", **kwargs)


async def _mint(user_id: str) -> str:
    from src.services.auth import api_tokens

    made = await api_tokens.mint(str(settings.DB_PATH), user_id=user_id, name="agent")
    assert made is not None
    return str(made["token"])


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserInfo, UserProfile
    from src.services.profile.storage import save_profile, save_user_info

    save_profile(UserProfile(cv_data=CVData(raw_text="Python engineer.", name="Ada")), user_id, "cv_upload")
    save_user_info(
        user_id,
        UserInfo(contact={"preferred_name": "Ada", "phone": "+44 7700 900123"}, equality={"gender": "Woman"}),
        "test",
    )


def _sql(sql: str, params: tuple) -> list:
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        cur = conn.execute(sql, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
    return rows


def _user_exists(user_id: str) -> bool:
    return bool(_sql("SELECT 1 FROM users WHERE id = ?", (user_id,)))


@pytest.mark.asyncio
@pytest.mark.parametrize("verb,path,kwargs", ACCOUNT_ROUTES, ids=[r[1] + ":" + r[0] for r in ACCOUNT_ROUTES])
async def test_an_agent_token_is_refused_on_every_account_route(
    authenticated_async_context, fixture_user_id, verb, path, kwargs
):
    async with authenticated_async_context():
        _seed_profile(fixture_user_id)
    token = await _mint(fixture_user_id)
    async with _client(headers={"Authorization": f"Bearer {token}"}) as agent:
        resp = await agent.request(verb, path, **kwargs)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "session_required"
    # Nothing happened: the account, its email and its memory are all still there.
    assert _user_exists(fixture_user_id)
    assert _sql("SELECT email FROM users WHERE id = ?", (fixture_user_id,))[0][0] == "test@example.com"
    from src.services.profile.storage import load_profile

    assert load_profile(fixture_user_id, with_overlay=False).user_info.equality == {"gender": "Woman"}


@pytest.mark.asyncio
async def test_the_web_session_still_reaches_export_and_clear(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        export = await client.get("/api/auth/users/me/export")
        assert export.status_code == 200, export.text
        assert export.json()["user"]["id"] == fixture_user_id
        cleared = await client.post("/api/profile/clear", data={"section": "memory"})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["user_info"]["contact"] == {}
        me = (await client.get("/api/auth/me")).json()
        assert me["has_password"] is True, "a registered account has a password"


@pytest.mark.asyncio
async def test_a_password_account_still_needs_its_password_to_delete(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        missing = await client.request("DELETE", "/api/auth/users/me", json={})
        assert missing.status_code == 401, missing.text
        wrong = await client.request("DELETE", "/api/auth/users/me", json={"current_password": "nope-nope"})
        assert wrong.status_code == 401, wrong.text
        assert _user_exists(fixture_user_id)
        ok = await client.request("DELETE", "/api/auth/users/me", json={"current_password": "s3cretpassword"})
        assert ok.status_code == 204, ok.text
    assert not _user_exists(fixture_user_id)


async def _magic_link_sign_in(email: str) -> tuple[str, str]:
    """Sign in through the real consume route; returns (user_id, session cookie)."""
    from src.services.auth import tokens

    raw = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _sql(
        "INSERT INTO magic_link_tokens (email, token_hash, expires_at) VALUES (?, ?, ?)",
        (email, tokens.hash_token(raw), expires),
    )
    async with _client() as anon:
        resp = await anon.post("/api/auth/magic-link/consume", json={"token": raw})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"], resp.cookies["job360_session"]


@pytest.mark.asyncio
async def test_a_magic_link_account_deletes_from_a_fresh_link_without_a_password(authenticated_async_context):
    async with authenticated_async_context():
        user_id, cookie = await _magic_link_sign_in("linkonly@example.com")
    async with _client(cookies={"job360_session": cookie}) as client:
        me = (await client.get("/api/auth/me")).json()
        assert me["id"] == user_id and me["has_password"] is False
        resp = await client.request("DELETE", "/api/auth/users/me", json={})
    assert resp.status_code == 204, resp.text
    assert not _user_exists(user_id)


@pytest.mark.asyncio
async def test_a_magic_link_account_on_a_stale_session_is_told_to_sign_in_again(authenticated_async_context):
    async with authenticated_async_context():
        user_id, cookie = await _magic_link_sign_in("stale@example.com")
    old = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    _sql("UPDATE sessions SET created_at = ? WHERE user_id = ?", (old, user_id))
    async with _client(cookies={"job360_session": cookie}) as client:
        # A guessed password does not help: the account has none to check.
        resp = await client.request("DELETE", "/api/auth/users/me", json={"current_password": "whatever1"})
    assert resp.status_code == 403, resp.text
    assert "fresh email link" in resp.json()["detail"] and "10 minutes" in resp.json()["detail"]
    assert _user_exists(user_id)


@pytest.mark.asyncio
async def test_a_password_reset_gives_a_magic_link_account_a_password(authenticated_async_context):
    from src.services.auth import password_reset

    async with authenticated_async_context():
        user_id, _ = await _magic_link_sign_in("resetter@example.com")
        raw = secrets.token_urlsafe(32)
        from src.services.auth import tokens

        expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _sql(
            "INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
            (user_id, tokens.hash_token(raw), expires),
        )
        assert await password_reset.confirm_password_reset(
            db_path=str(settings.DB_PATH), raw_token=raw, new_password="br4ndnewpass"
        ) == user_id
    assert _sql("SELECT has_password FROM users WHERE id = ?", (user_id,))[0][0] is True


@pytest.mark.asyncio
async def test_clear_erases_the_memory_from_history(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await client.post("/api/profile/clear", data={"section": "all"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["user_info"]["contact"] == {}, "the clear really ran"
        rows = (await client.get("/api/profile/edits/history", params={"path": CONTACT})).json()["rows"]
        # Owner decision 2026-10-26: the wiped value is not copied anywhere; one clearing row, no value.
        assert [r["value"] for r in rows] == [None]
        eq = (await client.get("/api/profile/edits/history", params={"path": EQ})).json()["rows"]
        assert [r["value"] for r in eq] == [None]
        # An empty field leaves no row at all (rule #29: nothing was there).
        langs = (await client.get("/api/profile/edits/history", params={"path": "user_info.languages"})).json()
        assert langs["rows"] == []
    stored = _sql(
        "SELECT path, value FROM profile_edits WHERE user_id = ? AND value IS NOT NULL", (fixture_user_id,)
    )
    assert [r for r in stored if r[0].startswith("user_info.")] == []
