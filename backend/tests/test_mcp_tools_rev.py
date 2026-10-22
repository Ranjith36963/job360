"""WS9b - the tool-list version is told to the agent everywhere it looks.

``tools_version`` (hash of tool + parameter names) rides in the instructions,
and it and ``tools_rev`` (an integer DERIVED from that hash, never hand-bumped)
ride on ``doctor`` and on every ``get_recipe`` result, so a client holding a
cached tool list can see it is stale. Real Postgres via ``authenticated_async_context``.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


def _user(fixture_user_id: str):
    from src.api import mcp_server

    return mcp_server.CurrentUser(
        id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc",
    )


def _params(server) -> dict[str, list[str]]:
    return {t.name: sorted(t.parameters.get("properties") or {}) for t in server._tool_manager.list_tools()}


def test_instructions_carry_current_tools_version_not_rev():
    from src.api import mcp_server as m

    served = m.tools_version(m.served_tool_parameters())
    assert f"tools_version={served}" in m.instructions()
    assert "rev=" not in m.instructions()
    assert m.build_server().instructions == m.instructions()


def test_tools_rev_is_derived_from_tools_version():
    from src.api import mcp_server as m

    rev = m.tools_rev()
    assert isinstance(rev, int) and not isinstance(rev, bool) and 1 <= rev <= 999_999
    # 17 was the last hand-pinned rev; the offset keeps today's hash on it. This constant moves only
    # when the tool surface (tool or parameter names) moves - then update it to the new derived value.
    if m.tools_version(m.served_tool_parameters()) == "5e9dd224ad5e":
        assert rev == 17


def test_different_parameter_map_gives_different_rev():
    from src.api import mcp_server as m

    served = m.served_tool_parameters()
    changed = {**served, "doctor": [*served["doctor"], "new_param"]}
    v_served, v_changed = m.tools_version(served), m.tools_version(changed)
    assert v_served != v_changed
    derived = [(int(v, 16) + m._TOOLS_REV_OFFSET) % 999_999 + 1 for v in (v_served, v_changed)]
    assert derived[0] == m.tools_rev() and derived[0] != derived[1]


def test_parameters_do_not_depend_on_instructions_text():
    from src.api import mcp_server as m

    assert _params(m.build_server(instructions_text="")) == _params(m.build_server())


@pytest.mark.asyncio
async def test_get_recipe_list_and_each_recipe_carry_same_version(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m
    from src.api.routes.recipes import RECIPE_NAMES

    assert len(RECIPE_NAMES) == 14  # slice 4 added "debrief"; the batch queue added "batch"; bulk-apply F1 added "ats"
    tools = m.build_server()._tool_manager
    expected = m.tools_version(m.served_tool_parameters())
    token = m._current_user.set(_user(fixture_user_id))
    try:
        async with authenticated_async_context():
            doc = await tools.get_tool("doctor").fn()
            for name in ("", *RECIPE_NAMES):
                payload = await tools.get_tool("get_recipe").fn(name=name)
                assert payload["tools_version"] == expected == doc["tools_version"], name
                assert payload["tools_rev"] == m.tools_rev() == doc["tools_rev"], name
    finally:
        m._current_user.reset(token)


@pytest.mark.asyncio
async def test_doctor_stale_by_rev(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m

    tools = m.build_server()._tool_manager
    rev = m.tools_rev()
    token = m._current_user.set(_user(fixture_user_id))
    try:
        async with authenticated_async_context():
            doctor = tools.get_tool("doctor").fn
            assert (await doctor())["tools_stale"] is None
            assert (await doctor(client_tools_rev=rev - 1))["tools_stale"] is True
            served = m.tools_version(m.served_tool_parameters())
            current = await doctor(client_tools_version=served, client_tools_rev=rev)
            assert current["tools_stale"] is False
            assert current["tools_rev"] == rev
            # right rev but an old hash is still stale
            old_hash = await doctor(client_tools_version="000000000000", client_tools_rev=rev)
            assert old_hash["tools_stale"] is True
    finally:
        m._current_user.reset(token)


@pytest.mark.asyncio
async def test_rest_recipe_and_doctor_agree_with_mcp(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m
    from src.api.main import app

    tools = m.build_server()._tool_manager
    rev = m.tools_rev()
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
                rest_doc = (await agent.get("/api/doctor", params={"client_tools_rev": rev - 1})).json()
            via_mcp = await tools.get_tool("get_recipe").fn(name="rules")
    finally:
        m._current_user.reset(token)
    assert rest["tools_version"] == via_mcp["tools_version"] == m.tools_version(m.served_tool_parameters())
    assert rest["tools_rev"] == via_mcp["tools_rev"] == rev
    assert rest_doc["tools_rev"] == rev and rest_doc["tools_stale"] is True
