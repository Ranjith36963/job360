"""Minting a personal token needs a SESSION, not a verified email.

`.claude/skills/verify-job360/SKILL.md` told the nightly verifier that a fresh
registered user gets **403 `email_not_verified`** from `POST /api/tokens`, and
therefore to run `UPDATE users SET email_verified_at = now()` before touching
the MCP surface. `src/api/routes/tokens.py` depends on ``require_session_user``
only — the verification gate guards the routes that SPEND an LLM call
(`test_email_enforcement.py::test_a_gated_route_blocks_an_unverified_user`),
and minting a credential spends nothing. A verifier following the doc would
have reported a false failure the first time the 201 arrived.

The doc now cites this file instead of restating the gate.
"""
from __future__ import annotations

import pytest

import src.core.settings as settings_mod
from src.repositories import pgsync


def _unverify(user_id: str) -> None:
    conn = pgsync.connect(str(settings_mod.DB_PATH))
    conn.execute("UPDATE users SET email_verified_at = NULL WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_an_unverified_user_can_mint_a_token(authenticated_async_context):
    """The claim under test: no email verification is required to mint."""
    _unverify(authenticated_async_context.fixture_user_id)

    async with authenticated_async_context() as client:
        resp = await client.post("/api/tokens", json={"name": "agent"})

    assert resp.status_code == 201, resp.text
    assert resp.json()["token"].startswith("j360_")


@pytest.mark.asyncio
async def test_an_unverified_user_can_list_and_revoke_tokens(authenticated_async_context):
    """The whole router is session-gated, not just the mint route."""
    _unverify(authenticated_async_context.fixture_user_id)

    async with authenticated_async_context() as client:
        made = await client.post("/api/tokens", json={"name": "agent"})
        assert made.status_code == 201, made.text

        listed = await client.get("/api/tokens")
        assert listed.status_code == 200, listed.text

        revoked = await client.delete(f"/api/tokens/{made.json()['id']}")
        assert revoked.status_code == 204, revoked.text


@pytest.mark.asyncio
async def test_minting_still_needs_a_session(authenticated_async_context):
    """Session-gated is not ungated: no cookie → 401, never 201."""
    from httpx import ASGITransport, AsyncClient

    from src.api.main import app

    async with authenticated_async_context():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
            resp = await anon.post("/api/tokens", json={"name": "agent"})

    assert resp.status_code == 401, resp.text
