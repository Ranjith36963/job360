"""Refusals name the fix: closed `reason` set in the schema, did-you-mean on unknown arguments.

Prod 2026-10-30/31: 78 of 389 tool calls were shape errors (free-text mark_done reason, ask_user with
`message`, check_form with `session`). Strictness stays: unknown keys are refused, never renamed.
"""
from __future__ import annotations

import json
from typing import Any

import pytest


# Local copies (helpers are never imported from another test module).
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

REASONS = [
    "no_work", "tool_unavailable", "sign_in_needed", "paid_plan_needed", "network_blocked", "rate_limited",
    "needs_user", "error",
]


def _schema(listed, name):
    return next(t for t in listed if t.name == name).input_schema


@pytest.mark.asyncio
async def test_mark_done_schema_exposes_the_closed_reason_set_and_note():
    from src.api.mcp_server import build_server

    props = _schema(await build_server().list_tools(), "mark_done")["properties"]
    assert "note" in props and props["note"].get("maxLength") == 300
    blob = str(props["reason"])
    assert "enum" in blob and all(r in blob for r in REASONS)
    assert "description" in props["reason"]


@pytest.mark.asyncio
async def test_mark_done_free_text_reason_is_refused_by_the_schema():
    from mcp.server.mcpserver.exceptions import ToolError

    from src.api.mcp_server import build_server

    with pytest.raises(ToolError) as caught:
        await build_server().call_tool(
            "mark_done", {"duty": "hunt", "outcome": "done", "reason": "kit ~600 ads seen, 3 brought"}
        )
    assert "no_work" in str(caught.value) and "needs_user" in str(caught.value)


@pytest.mark.asyncio
async def test_mark_done_accepts_needs_user_with_a_note(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            out = _payload(await mcp.call_tool(
                "mark_done",
                {"duty": "hunt", "outcome": "failed", "reason": "needs_user", "note": "CAPTCHA on the board"},
            ))
    assert out["duty"] == "hunt" and out["outcome"] == "failed"


@pytest.mark.asyncio
async def test_ask_user_with_message_is_refused_and_names_question():
    from mcp.server.mcpserver.exceptions import ToolError

    from src.api.mcp_server import build_server

    with pytest.raises(ToolError) as caught:
        await build_server().call_tool("ask_user", {"message": "Please solve the CAPTCHA"})
    text = str(caught.value)
    assert "Nothing was done" in text and "unknown argument(s): message." in text
    assert "Did you mean `message` -> `question`?" in text
    assert "ask_user(question=" in text


@pytest.mark.asyncio
async def test_check_form_with_session_is_refused_naming_the_valid_arguments():
    from mcp.server.mcpserver.exceptions import ToolError

    from src.api.mcp_server import build_server

    with pytest.raises(ToolError) as caught:
        await build_server().call_tool("check_form", {"application_id": 1, "questions": [], "session": "s"})
    text = str(caught.value)
    assert "unknown argument(s): session." in text
    assert "This tool takes: application_id, questions, form_url." in text
    assert "check_form(application_id=" in text


def test_did_you_mean_suggests_but_unknown_keys_stay_refused():
    from src.api.mcp_server import unknown_arguments_message

    schema = {"properties": {"question": {}, "context": {}}}
    assert "Did you mean `questoin` -> `question`?" in unknown_arguments_message(schema, {"questoin": 1}, "x")
    assert "Did you mean" not in unknown_arguments_message(schema, {"zzzzzz": 1}, "x")
    assert unknown_arguments_message(schema, {"question": 1}, "x") == ""


@pytest.mark.asyncio
async def test_whats_due_no_longer_claims_to_be_read_only():
    from src.api.mcp_server import build_server

    tool = next(t for t in await build_server().list_tools() if t.name == "whats_due")
    desc = tool.description or ""
    assert "Read-only" not in desc and "read-only" not in desc
    assert "CLAIMS" in desc and "30" in desc
