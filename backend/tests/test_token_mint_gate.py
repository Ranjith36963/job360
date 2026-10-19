"""Which gates guard `POST /api/tokens` — a browser session AND a confirmed email.

Since 2026-10-14 an account whose email is not confirmed (`users.email_verified_at`
NULL) cannot mint a personal `j360_` token: 403 with a plain sentence the
settings/connect page shows as-is. `REQUIRE_EMAIL_VERIFICATION=false` (the
escape hatch read by `auth_deps.email_verification_satisfied`) switches this
off together with every other verification gate. A token can still never make
another token (`test_api_tokens.py::test_a_token_cannot_manage_tokens`).
OAuth grants are NOT gated here (pending owner decision).
"""
import pytest

import src.core.settings as settings_mod
from src.repositories import pgsync


def _unverify(user_id: str) -> None:
    conn = pgsync.connect(str(settings_mod.DB_PATH))
    conn.execute("UPDATE users SET email_verified_at = NULL WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_unverified_cannot_mint_token(authenticated_async_context, monkeypatch):
    monkeypatch.delenv("REQUIRE_EMAIL_VERIFICATION", raising=False)
    _unverify(authenticated_async_context.fixture_user_id)  # conftest verifies the fixture user

    async with authenticated_async_context() as client:
        resp = await client.post("/api/tokens", json={"name": "agent"})
        listed = await client.get("/api/tokens")

    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert "confirm your email" in detail.lower() and "j360_" not in resp.text
    assert listed.status_code == 200 and listed.json()["tokens"] == []


@pytest.mark.asyncio
async def test_verification_escape_hatch_still_allows(authenticated_async_context, monkeypatch):
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "false")
    _unverify(authenticated_async_context.fixture_user_id)

    async with authenticated_async_context() as client:
        resp = await client.post("/api/tokens", json={"name": "agent"})

    assert resp.status_code == 201, resp.text
    assert resp.json()["token"].startswith("j360_")


@pytest.mark.asyncio
async def test_a_verified_session_mints(authenticated_async_context, monkeypatch):
    monkeypatch.delenv("REQUIRE_EMAIL_VERIFICATION", raising=False)
    async with authenticated_async_context() as client:
        resp = await client.post("/api/tokens", json={"name": "agent"})
    assert resp.status_code == 201, resp.text
