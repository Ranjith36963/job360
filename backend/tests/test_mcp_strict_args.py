"""Unknown MCP arguments are refused loudly, and doctor shows a stale tool list.

2026-10-15 (live): an assistant called ``update_job`` with ``apply_url`` while
its claude.ai connector served an OLD cached tool list. The SDK's argument
model ignored the key, so the link silently vanished - no error, no field in
the reply - and the assistant believed it was saved. Two fixes, pinned here:

* every tool refuses an argument it does not take, naming it, listing what it
  does take and pointing at doctor - and nothing is written;
* doctor reports ``tools`` ({name: [params]}) and ``tools_version`` (12 hex),
  and ``tools_stale`` when the caller passes the version it saw before.

Helpers are copied, never imported from another test module (schema isolation).
"""
from __future__ import annotations

import json
import re
from typing import Any

import pytest

pytest.importorskip("mcp")

JOB = {
    "title": "Senior Python Engineer",
    "company": "Acme Ltd",
    "location": "London",
    "description": "Build FastAPI services with Postgres. " * 8,
    "apply_url": "https://acme.example/jobs/1",
}


async def _mint_token(authenticated_async_context, name: str = "agent") -> str:
    async with authenticated_async_context() as client:
        resp = await client.post("/api/tokens", json={"name": name})
        assert resp.status_code == 201, resp.text
        return resp.json()["token"]


def _mcp_client(token: str):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


def _payload(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def _sql(query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    from src.core import settings
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        cur = conn.execute(query, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
        return [tuple(r) for r in rows]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Unknown arguments
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_update_job_with_an_unknown_argument_is_refused_and_writes_nothing(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            brought = _payload(await mcp.call_tool("bring_job", JOB))
            app_id = brought["application_id"]
            before_events = _sql("SELECT COUNT(*) FROM application_events WHERE application_id = ?", (app_id,))
            before_row = _sql("SELECT job_country FROM applications WHERE id = ?", (app_id,))

            # A known argument next to a misspelt one: the whole call is refused.
            refused = await mcp.call_tool(
                "update_job",
                {"application_id": app_id, "country": "DE", "apply_urll": "https://acme.example/careers/9"},
            )
            assert refused.is_error
            text = refused.content[0].text
            assert "unknown argument(s): apply_urll." in text
            assert (
                "This tool takes: application_id, country, remote, found_on, posted_at, "
                "employer_published_at, apply_url." in text
            )
            assert "your tool list may be out of date: call doctor" in text
            assert "Nothing was done" in text

            # No side effect: the country did not move, no event was appended, the link is unchanged.
            assert _sql("SELECT job_country FROM applications WHERE id = ?", (app_id,)) == before_row
            assert before_row == [(None,)]
            assert (
                _sql("SELECT COUNT(*) FROM application_events WHERE application_id = ?", (app_id,)) == before_events
            )
            job = _payload(await mcp.call_tool("get_job", {"job_id": brought["job_id"]}))
            assert job["apply_url"] == JOB["apply_url"]
            assert job["country"] is None

            # The same call without the typo works - the refusal was about the key, nothing else.
            fixed = _payload(await mcp.call_tool("update_job", {"application_id": app_id, "country": "DE"}))
            assert fixed["country"] == "DE"


@pytest.mark.asyncio
async def test_every_unknown_key_is_named_over_the_wire(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            refused = await mcp.call_tool("list_receipts", {"limit": 5, "zzz": 1, "page": 2})
    assert refused.is_error
    text = refused.content[0].text
    assert "unknown argument(s): zzz, page." in text
    assert "This tool takes: job_id, limit." in text


@pytest.mark.asyncio
async def test_every_tool_advertises_additional_properties_false_and_refuses_extras_in_process():
    from mcp.server.mcpserver.exceptions import ToolError

    from src.api.mcp_server import build_server

    server = build_server()
    listed = await server.list_tools()
    assert len(listed) > 20
    for tool in listed:
        assert tool.input_schema.get("additionalProperties") is False, tool.name
    # In-process callers go through the same door as the wire (no user is needed to be refused).
    with pytest.raises(ToolError) as caught:
        await server.call_tool("get_job", {"job_id": 1, "bogus": True})
    assert "unknown argument(s): bogus. This tool takes: job_id." in str(caught.value)


def test_unknown_arguments_message_bounds_caller_text():
    from src.api.mcp_server import unknown_arguments_message

    schema = {"properties": {"a": {}, "b": {}}}
    assert unknown_arguments_message(schema, {"a": 1}) == ""
    assert unknown_arguments_message(schema, {}) == ""
    keys = {f"k{i}": 1 for i in range(12)} | {"x" * 200: 1}
    msg = unknown_arguments_message(schema, keys)
    assert "(+3 more)" in msg and "x" * 61 not in msg
    assert "This tool takes: a, b." in msg
    assert "This tool takes: no arguments." in unknown_arguments_message({"properties": {}}, {"q": 1})


# ═══════════════════════════════════════════════════════════════════════════
# doctor: tools, tools_version, tools_stale
# ═══════════════════════════════════════════════════════════════════════════


def test_tools_version_is_12_hex_stable_and_moves_with_the_tool_set():
    from src.api.mcp_server import tools_version

    base = {"update_job": ["application_id", "country", "apply_url"], "doctor": ["client_tools_version"]}
    v1 = tools_version(base)
    assert re.fullmatch(r"[0-9a-f]{12}", v1)
    # Stable: same set in another order gives the same version.
    assert tools_version({"doctor": ["client_tools_version"], "update_job": ["apply_url", "country", "application_id"]}) == v1
    # A parameter removed (the stale-cache case), a tool added, a tool renamed: each moves it.
    assert tools_version({**base, "update_job": ["application_id", "country"]}) != v1
    assert tools_version({**base, "new_tool": []}) != v1
    assert tools_version({"update_job2": base["update_job"], "doctor": base["doctor"]}) != v1


@pytest.mark.asyncio
async def test_doctor_reports_the_served_tools_and_staleness(authenticated_async_context):
    from src.api.mcp_server import TOOLS_STALE_HINT, mcp_runtime, tools_version

    token = await _mint_token(authenticated_async_context)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            first = _payload(await mcp.call_tool("doctor", {}))
            second = _payload(await mcp.call_tool("doctor", {}))
            served = {t.name: list(t.input_schema.get("properties", {})) for t in (await mcp.list_tools()).tools}
            version = first["tools_version"]
            match = _payload(await mcp.call_tool("doctor", {"client_tools_version": version}))
            mismatch = _payload(await mcp.call_tool("doctor", {"client_tools_version": "000000000000"}))
            # The client's own list over the wire: current version, but add_contact lacks `email`.
            cached = {**served, "add_contact": [p for p in served["add_contact"] if p != "email"]}
            caught = _payload(await mcp.call_tool("doctor", {"client_tools_version": version, "client_tools": cached}))
            fresh = _payload(await mcp.call_tool("doctor", {"client_tools": served}))

    assert re.fullmatch(r"[0-9a-f]{12}", version)
    assert second["tools_version"] == version
    assert "apply_url" in first["tools"]["update_job"]
    assert first["tools"]["doctor"] == ["client_tools_version", "client_tools_rev", "client_tools"]
    # The map is exactly what tools/list advertises, and the version is its hash.
    assert first["tools"] == served
    assert version == tools_version(served)
    assert first["tools_stale"] is None
    assert match["tools_stale"] is False
    assert mismatch["tools_stale"] is True
    assert first["stale_hint"] == TOOLS_STALE_HINT
    assert "reconnect the Job360 connector" in mismatch["stale_hint"]
    assert caught["tools_stale"] is True and caught["tools_missing"] == {"add_contact": ["email"]}
    assert fresh["tools_stale"] is False and fresh["tools_missing"] == {}


@pytest.mark.asyncio
async def test_doctor_route_carries_the_same_tool_block(authenticated_async_context):
    async with authenticated_async_context() as client:
        plain = (await client.get("/api/doctor")).json()
        version = plain["tools_version"]
        same = (await client.get("/api/doctor", params={"client_tools_version": version})).json()
        other = (await client.get("/api/doctor", params={"client_tools_version": "abcdefabcdef"})).json()
    assert re.fullmatch(r"[0-9a-f]{12}", version)
    assert "apply_url" in plain["tools"]["update_job"]
    assert plain["tools_stale"] is None and same["tools_stale"] is False and other["tools_stale"] is True


def test_recipes_tell_the_assistant_what_a_refused_argument_means():
    from pathlib import Path

    line = (
        "If a Job360 tool ignores or refuses an argument you expected, call doctor: a parameter it lists "
        "that your tool list lacks means your connector is stale; tell the user to reconnect Job360."
    )
    recipes = Path(__file__).resolve().parents[1] / "src" / "recipes"
    for name in ("setup.md", "rules.md"):
        assert line in (recipes / name).read_text(encoding="utf-8"), name


def test_stale_hint_is_client_specific():
    from src.api.mcp_server import TOOLS_STALE_HINT, stale_hint_for

    assert stale_hint_for("") == TOOLS_STALE_HINT
    assert stale_hint_for("cursor") == TOOLS_STALE_HINT
    assert "quit it fully" in stale_hint_for("claude-ai")
    assert stale_hint_for("claude-ai").startswith(TOOLS_STALE_HINT)
