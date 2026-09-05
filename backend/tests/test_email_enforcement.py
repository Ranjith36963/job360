"""Finding #15 — email verification is enforced on app routes.

`require_verified_user` returns 403 `email_not_verified` for a logged-in but
unverified user; verified users pass. `POST /api/search` is gated with it.
"""
import pytest
from fastapi import HTTPException

import src.core.settings as settings_mod
from src.api.auth_deps import CurrentUser, require_verified_user
from src.repositories import pgsync


@pytest.mark.asyncio
async def test_require_verified_user_passes_when_verified():
    u = CurrentUser(id="x", email="a@b.c", email_verified=True)
    assert await require_verified_user(u) is u


@pytest.mark.asyncio
async def test_require_verified_user_blocks_when_unverified():
    u = CurrentUser(id="x", email="a@b.c", email_verified=False)
    with pytest.raises(HTTPException) as ei:
        await require_verified_user(u)
    assert ei.value.status_code == 403
    assert ei.value.detail == "email_not_verified"


@pytest.mark.asyncio
async def test_a_gated_route_blocks_an_unverified_user(authenticated_async_context):
    """Unverified user → a `require_verified_user` route → 403, before the
    route body runs.

    The route under test was `POST /api/search`; slice 5 (#483) deleted it.
    The tailor is the gate's remaining holder — and the reason the gate exists
    at all is unchanged: it guards the routes that SPEND a paid LLM call.
    """
    uid = authenticated_async_context.fixture_user_id
    # conftest verifies the fixture user by default; un-verify for this test.
    conn = pgsync.connect(str(settings_mod.DB_PATH))
    conn.execute("UPDATE users SET email_verified_at = NULL WHERE id = ?", (uid,))
    conn.commit()
    conn.close()

    async with authenticated_async_context() as client:
        resp = await client.post("/api/tailor/1/generate", json={})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "email_not_verified"
