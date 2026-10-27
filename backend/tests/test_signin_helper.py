"""The shared magic-link sign-in helper (``tests/_signin.py``) really signs a user in."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from tests._signin import async_sign_in, sign_in


@pytest.mark.asyncio
async def test_async_sign_in_creates_a_verified_user_and_a_working_session(authenticated_async_context):
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        cookie, user_id = await async_sign_in(client, "Helper@Example.com")
        me = await client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["id"] == user_id
    assert me.json()["email"] == "helper@example.com"
    assert cookie


@pytest.mark.asyncio
async def test_signing_the_same_address_in_twice_gives_one_user(authenticated_async_context):
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _, first = await async_sign_in(client, "twice@example.com")
        _, second = await async_sign_in(client, "TWICE@example.com")
    assert first == second


@pytest.mark.asyncio
async def test_sync_sign_in_authenticates_the_test_client(authenticated_async_context):
    from src.api.main import app

    with TestClient(app) as client:
        cookie, user_id = sign_in(client, "syncuser@example.com")
        me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["id"] == user_id
    assert cookie
