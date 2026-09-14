"""Which gate guards `POST /api/tokens` — the session, NOT email verification.

The verify-job360 skill used to tell agents that minting an agent token returns
403 `email_not_verified` for a fresh user, and to go `UPDATE users SET
email_verified_at = now()` before touching the MCP surface. It does not:
`routes/tokens.create_token` depends on `require_session_user`, so an unverified
browser session mints fine and only a *token*-authenticated caller is refused
(`test_api_tokens.py::test_a_token_cannot_manage_tokens` pins that half).
`require_verified_user` guards the routes that SPEND a paid LLM call
(`routes/tailor`) — see `test_email_enforcement.py`.

This test exists so the skill can cite it instead of restating it.
"""
import pytest

import src.core.settings as settings_mod
from src.repositories import pgsync


@pytest.mark.asyncio
async def test_an_unverified_session_can_still_mint_a_token(
    authenticated_async_context, monkeypatch
):
    """Email verification is enforced (default ON), and minting is still allowed."""
    monkeypatch.delenv("REQUIRE_EMAIL_VERIFICATION", raising=False)

    # conftest verifies the fixture user by default; un-verify for this test.
    conn = pgsync.connect(str(settings_mod.DB_PATH))
    conn.execute(
        "UPDATE users SET email_verified_at = NULL WHERE id = ?",
        (authenticated_async_context.fixture_user_id,),
    )
    conn.commit()
    conn.close()

    async with authenticated_async_context() as client:
        resp = await client.post("/api/tokens", json={"name": "agent"})

    assert resp.status_code == 201, resp.text
    assert resp.json()["token"].startswith("j360_")
