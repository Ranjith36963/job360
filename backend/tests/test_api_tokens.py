"""Personal API tokens — the credential an agent uses instead of a browser cookie.

Contract (docs/plans/2026-09-03-mcp-server/spec.md R1–R3, R6):
  * the secret is returned ONCE and only its sha256 is stored;
  * a bearer token authenticates exactly like the cookie, a wrong one is 401
    even when a valid cookie rides along (no silent downgrade);
  * revocation is immediate; a token can never mint/list/revoke tokens;
  * the cap and the failed-attempt limiter are parameters, not constants;
  * the table is erased with the account.
"""
from __future__ import annotations

import hashlib

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import dependencies as api_deps
from src.core import settings


async def _mint(client, name: str = "claude code") -> dict:
    resp = await client.post("/api/tokens", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _bearer_client(token: str) -> AsyncClient:
    """A client with NO cookie — the token is the only credential it carries."""
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_mint_returns_the_secret_once_and_stores_only_its_hash(authenticated_async_context):
    async with authenticated_async_context() as client:
        made = await _mint(client)
        token = made["token"]
        assert token.startswith("j360_") and len(token) >= 40
        assert made["prefix"] == token[:12]
        assert made["name"] == "claude code"

        listed = await client.get("/api/tokens")
        assert listed.status_code == 200
        rows = listed.json()["tokens"]
        assert [r["id"] for r in rows] == [made["id"]]
        assert rows[0]["prefix"] == token[:12]
        assert "token" not in rows[0] and "token_hash" not in rows[0]

        db = await api_deps.get_db()
        cur = await db._conn.execute("SELECT token_hash FROM api_tokens WHERE id = ?", (made["id"],))
        (stored,) = await cur.fetchone()
        assert stored == hashlib.sha256(token.encode()).hexdigest()
        assert token not in stored


@pytest.mark.asyncio
async def test_bearer_authenticates_like_the_cookie(authenticated_async_context):
    async with authenticated_async_context() as client:
        token = (await _mint(client))["token"]
        me = await client.get("/api/auth/me")
        email = me.json()["email"]

    async with _bearer_client(token) as agent:
        resp = await agent.get("/api/auth/me")
        assert resp.status_code == 200, resp.text
        assert resp.json()["email"] == email

    async with _bearer_client("") as anon:
        anon.headers.pop("Authorization")
        assert (await anon.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_wrong_bearer_is_401_even_with_a_valid_cookie(authenticated_async_context):
    async with authenticated_async_context() as client:
        assert (await client.get("/api/auth/me")).status_code == 200
        resp = await client.get("/api/auth/me", headers={"Authorization": "Bearer j360_not_a_real_token"})
        assert resp.status_code == 401, "a bad explicit credential must not fall back to the cookie"


@pytest.mark.asyncio
async def test_revoked_token_stops_working_immediately(authenticated_async_context):
    async with authenticated_async_context() as client:
        made = await _mint(client)
        async with _bearer_client(made["token"]) as agent:
            assert (await agent.get("/api/auth/me")).status_code == 200
            gone = await client.delete(f"/api/tokens/{made['id']}")
            assert gone.status_code == 204, gone.text
            assert (await agent.get("/api/auth/me")).status_code == 401
        assert (await client.get("/api/tokens")).json()["tokens"] == []
        # Revoking twice / a stranger's id is a 404, never a 500.
        assert (await client.delete(f"/api/tokens/{made['id']}")).status_code == 404
        assert (await client.delete("/api/tokens/999999")).status_code == 404


@pytest.mark.asyncio
async def test_a_token_cannot_manage_tokens(authenticated_async_context):
    async with authenticated_async_context() as client:
        made = await _mint(client)
    async with _bearer_client(made["token"]) as agent:
        create = await agent.post("/api/tokens", json={"name": "escalate"})
        assert create.status_code == 403 and create.json()["detail"] == "session_required"
        assert (await agent.get("/api/tokens")).status_code == 403
        assert (await agent.delete(f"/api/tokens/{made['id']}")).status_code == 403


@pytest.mark.asyncio
async def test_active_token_cap_is_a_parameter(authenticated_async_context, monkeypatch):
    monkeypatch.setattr(settings, "API_TOKENS_PER_USER", 2)
    async with authenticated_async_context() as client:
        first = await _mint(client, "one")
        await _mint(client, "two")
        third = await client.post("/api/tokens", json={"name": "three"})
        assert third.status_code == 409, third.text
        # Revoking one frees a slot.
        assert (await client.delete(f"/api/tokens/{first['id']}")).status_code == 204
        assert (await client.post("/api/tokens", json={"name": "three"})).status_code == 201
        # Input guards.
        assert (await client.post("/api/tokens", json={"name": "   "})).status_code == 422
        assert (await client.post("/api/tokens", json={"name": "x" * 101})).status_code == 422


@pytest.mark.asyncio
async def test_failed_bearer_attempts_are_rate_limited(authenticated_async_context, monkeypatch):
    monkeypatch.setattr(settings, "API_TOKEN_FAIL_MAX_PER_MIN", 3)
    async with authenticated_async_context():
        pass  # the fixture just needs to have set the DB up
    async with _bearer_client("j360_guess") as attacker:
        for _ in range(3):
            assert (await attacker.get("/api/auth/me")).status_code == 401
        assert (await attacker.get("/api/auth/me")).status_code == 429


@pytest.mark.asyncio
async def test_tokens_die_with_the_account_and_export_hides_the_hash(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        await _mint(client, "will be erased")
        export = await client.get("/api/auth/users/me/export")
        assert export.status_code == 200, export.text
        body = export.text
        assert "will be erased" in body, "the user's own token names are their data"
        rows = export.json()["api_tokens"]
        assert rows and all(r["token_hash"] == "[redacted]" for r in rows)

        db = await api_deps.get_db()
        assert "api_tokens" in db._PER_USER_TABLES
        await db.hard_delete_user(fixture_user_id)
        cur = await db._conn.execute("SELECT count(*) FROM api_tokens WHERE user_id = ?", (fixture_user_id,))
        (left,) = await cur.fetchone()
        assert left == 0
