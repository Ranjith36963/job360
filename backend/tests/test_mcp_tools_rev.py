"""WS9b - the tool-list version is told to the agent everywhere it looks.

``tools_version`` (hash of tool + parameter names) and ``tools_rev`` (a
hand-bumped integer, like career-ops' VERSION file) ride in INSTRUCTIONS, on
``doctor`` and on every ``get_recipe`` result, so a client holding a cached
tool list can see it is stale. Real Postgres via ``authenticated_async_context``.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


def _user(fixture_user_id: str):
    from src.api import mcp_server

    return mcp_server.CurrentUser(
        id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc",
    )


def test_instructions_carry_current_tools_version_and_rev():
    from src.api import mcp_server as m

    served = m.tools_version(m.served_tool_parameters())
    assert f"tools_version={served}" in m.INSTRUCTIONS
    assert f"rev={m.TOOLS_REV}" in m.INSTRUCTIONS
    assert m.build_server().instructions == m.INSTRUCTIONS


def test_tools_rev_is_int_and_matches_hash_pin():
    from src.api import mcp_server as m

    assert isinstance(m.TOOLS_REV, int) and not isinstance(m.TOOLS_REV, bool) and m.TOOLS_REV >= 1
    assert m.tools_version(m.served_tool_parameters()) == m.TOOLS_REV_FOR_HASH, (
        "a tool or parameter changed: bump TOOLS_REV and set TOOLS_REV_FOR_HASH to the new tools_version"
    )


@pytest.mark.asyncio
async def test_get_recipe_list_and_each_recipe_carry_same_version(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m
    from src.api.routes.recipes import RECIPE_NAMES

    assert len(RECIPE_NAMES) == 12  # slice 4 added "debrief"
    tools = m.build_server()._tool_manager
    expected = m.tools_version(m.served_tool_parameters())
    token = m._current_user.set(_user(fixture_user_id))
    try:
        async with authenticated_async_context():
            doc = await tools.get_tool("doctor").fn()
            for name in ("", *RECIPE_NAMES):
                payload = await tools.get_tool("get_recipe").fn(name=name)
                assert payload["tools_version"] == expected == doc["tools_version"], name
                assert payload["tools_rev"] == m.TOOLS_REV == doc["tools_rev"], name
    finally:
        m._current_user.reset(token)


@pytest.mark.asyncio
async def test_doctor_stale_by_rev(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m

    tools = m.build_server()._tool_manager
    token = m._current_user.set(_user(fixture_user_id))
    try:
        async with authenticated_async_context():
            doctor = tools.get_tool("doctor").fn
            assert (await doctor())["tools_stale"] is None
            assert (await doctor(client_tools_rev=m.TOOLS_REV - 1))["tools_stale"] is True
            current = await doctor(client_tools_version=m.TOOLS_REV_FOR_HASH, client_tools_rev=m.TOOLS_REV)
            assert current["tools_stale"] is False
            assert current["tools_rev"] == m.TOOLS_REV
            # right rev but an old hash is still stale
            old_hash = await doctor(client_tools_version="000000000000", client_tools_rev=m.TOOLS_REV)
            assert old_hash["tools_stale"] is True
    finally:
        m._current_user.reset(token)


@pytest.mark.asyncio
async def test_rest_recipe_and_doctor_agree_with_mcp(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m
    from src.api.main import app

    tools = m.build_server()._tool_manager
    token = m._current_user.set(_user(fixture_user_id))
    try:
        async with authenticated_async_context() as client:
            minted = await client.post("/api/tokens", json={"name": "cc"})
            bearer = minted.json()["token"]
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test",
                headers={"Authorization": f"Bearer {bearer}"},
            ) as agent:
                rest = (await agent.get("/api/recipes/rules")).json()
                rest_doc = (await agent.get("/api/doctor", params={"client_tools_rev": m.TOOLS_REV - 1})).json()
            via_mcp = await tools.get_tool("get_recipe").fn(name="rules")
    finally:
        m._current_user.reset(token)
    assert rest["tools_version"] == via_mcp["tools_version"] == m.TOOLS_REV_FOR_HASH
    assert rest["tools_rev"] == via_mcp["tools_rev"] == m.TOOLS_REV
    assert rest_doc["tools_rev"] == m.TOOLS_REV and rest_doc["tools_stale"] is True
