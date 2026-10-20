"""A retried write never writes a second row (gap roadmap slice 25, top-10 gap 6).

save_artifact, record_event, bring_job, add_contact and ask_user take an optional
`client_key` on the route AND the MCP tool (M5), through the slice-10 store
(services/idempotency, migration 0068). The same key + same body returns the
FIRST reply with `already_existed: true` and writes nothing; the same key with
another body is a 409; no key = the old behavior. Rule #21: real values only.
"""
from __future__ import annotations

from typing import Any, Callable

import pytest

from src.services import idempotency
from src.services.applications import spine
from tests.test_mcp_server import _error_text, _mcp_client, _mint_token, _payload
from tests.test_record_application_idempotency import _bring, _sql

AD2 = {"title": "Platform Engineer", "company": "Contoso", "location": "Leeds",
       "description": "Kubernetes and Terraform. " * 6, "apply_url": "https://contoso.example/jobs/42"}


def _count(sql: str, *params: Any) -> int:
    return int(_sql(sql, params)[0][0])


def _keys(tool: str) -> int:
    return _count("SELECT COUNT(*) FROM idempotency_keys WHERE tool = ?", tool)


async def _cold_contact(client) -> int:
    resp = await client.post("/api/contacts", json={"name": "Sam Lee"})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["contact_id"])


# One row per tool door: (tool, route path, route body, MCP tool, MCP args, the reply's id field, row count).
# Each `body` is the FIRST write; `other` is a different body under the same key.
def _cases(app_id: int, contact_id: int) -> list[dict[str, Any]]:
    return [
        {
            "tool": "save_artifact", "path": f"/api/applications/{app_id}/artifacts",
            "body": {"kind": "answers", "text": "Why us: pipelines."}, "other": {"kind": "answers", "text": "v2"},
            "mcp": {"kind": "answers", "text": "Why us: pipelines.", "application_id": app_id},
            "id": "artifact_id",
            "rows": lambda: _count("SELECT COUNT(*) FROM application_artifacts WHERE application_id = ?", app_id),
        },
        {
            "tool": "save_artifact", "path": f"/api/contacts/{contact_id}/outreach",
            "body": {"entry": "message", "channel": "email", "text": "Hi Sam"},
            "other": {"entry": "message", "channel": "email", "text": "Hello Sam"},
            "mcp": {"kind": "outreach", "contact_id": contact_id, "channel": "email", "text": "Hi Sam"},
            "id": None,
            "rows": lambda: _count(
                "SELECT COUNT(*) FROM contact_outreach WHERE contact_id = ? AND entry = 'message'", contact_id
            ),
        },
        {
            "tool": "record_event", "path": f"/api/applications/{app_id}/events",
            "body": {"event_type": "note", "detail": "Called the recruiter"},
            "other": {"event_type": "note", "detail": "Emailed the recruiter"},
            "mcp": {"event_type": "note", "detail": "Called the recruiter", "application_id": app_id},
            "id": "event_id",
            "rows": lambda: _count(
                "SELECT COUNT(*) FROM application_events WHERE application_id = ? AND event_type = 'note'", app_id
            ),
        },
        {
            "tool": "add_contact", "path": f"/api/applications/{app_id}/contacts",
            "body": {"name": "Priya Shah", "role": "Recruiter"}, "other": {"name": "Priya Shah", "role": "HR"},
            "mcp": {"name": "Priya Shah", "role": "Recruiter", "application_id": app_id},
            "id": "contact_id",
            "rows": lambda: _count("SELECT COUNT(*) FROM application_contacts WHERE application_id = ?", app_id),
        },
        {
            "tool": "ask_user", "path": "/api/asks",
            "body": {"question": "Which start date?", "application_id": app_id},
            "other": {"question": "Which salary?", "application_id": app_id},
            "mcp": {"question": "Which start date?", "application_id": app_id},
            "id": "id",
            "rows": lambda: _count("SELECT COUNT(*) FROM application_asks WHERE application_id = ?", app_id),
        },
    ]


def _first_id(case: dict[str, Any], reply: dict[str, Any]) -> int:
    return int(reply["outreach"]["id"] if case["id"] is None else reply[case["id"]])


CASE_NAMES = ["save_artifact", "save_artifact_outreach", "record_event", "add_contact", "ask_user"]


@pytest.mark.asyncio
@pytest.mark.parametrize("index", range(5), ids=CASE_NAMES)
async def test_route_repeat_writes_one_row_and_a_new_body_is_409(authenticated_async_context, index):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        case = _cases(app_id, await _cold_contact(client))[index]
        rows: Callable[[], int] = case["rows"]
        before = rows()
        first = await client.post(case["path"], json={**case["body"], "client_key": "k-1"})
        again = await client.post(case["path"], json={**case["body"], "client_key": "k-1"})
        clash = await client.post(case["path"], json={**case["other"], "client_key": "k-1"})
        after_keyed = rows()
        plain = [await client.post(case["path"], json=case["body"]) for _ in range(2)]
    assert first.status_code == 201, first.text
    assert again.status_code in (200, 201), again.text
    assert first.json()["already_existed"] is False
    assert again.json()["already_existed"] is True
    assert _first_id(case, again.json()) == _first_id(case, first.json())
    assert after_keyed == before + 1
    assert clash.status_code == 409, clash.text
    assert clash.json()["detail"].startswith("idempotency_key_reused: this client_key was already used")
    assert _keys(case["tool"]) == 1
    # No key: exactly the old behavior - two calls, two rows, two different ids.
    assert [r.status_code for r in plain] == [201, 201]
    assert rows() == after_keyed + 2
    assert _first_id(case, plain[0].json()) != _first_id(case, plain[1].json())


@pytest.mark.asyncio
@pytest.mark.parametrize("index", range(5), ids=CASE_NAMES)
async def test_mcp_tool_shares_the_route_key_and_refuses_a_new_body(authenticated_async_context, index):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        case = _cases(app_id, await _cold_contact(client))[index]
        route = (await client.post(case["path"], json={**case["body"], "client_key": "shared"})).json()
    rows: Callable[[], int] = case["rows"]
    after_route = rows()
    token = await _mint_token(authenticated_async_context)
    other_args = {**case["mcp"], **{k: v for k, v in case["other"].items() if k in case["mcp"]}}
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            again = _payload(await mcp.call_tool(case["tool"], {**case["mcp"], "client_key": "shared"}))
            clash = _error_text(await mcp.call_tool(case["tool"], {**other_args, "client_key": "shared"}))
            fresh = _payload(await mcp.call_tool(case["tool"], {**case["mcp"], "client_key": "mcp-2"}))
            replay = _payload(await mcp.call_tool(case["tool"], {**case["mcp"], "client_key": "mcp-2"}))
            plain = [_payload(await mcp.call_tool(case["tool"], case["mcp"])) for _ in range(2)]
    route_id = _first_id(case, route)
    again_id = int(again["artifact_id"] if case["id"] is None else again[case["id"]])
    assert route["already_existed"] is False
    assert again["already_existed"] is True
    assert again_id == route_id
    assert "409: idempotency_key_reused: this client_key was already used" in clash
    assert fresh["already_existed"] is False
    assert replay["already_existed"] is True
    assert replay == {**fresh, "already_existed": True}
    # Keyed: route + MCP repeat + MCP fresh = 2 rows; no key: 2 more, as before.
    assert rows() == after_route + 1 + 2
    assert "already_existed" not in plain[0] or plain[0]["already_existed"] is False
    assert _keys(case["tool"]) == 2


@pytest.mark.asyncio
async def test_bring_job_replays_the_first_reply_by_route_and_mcp(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    def apps() -> int:
        return _count("SELECT COUNT(*) FROM applications WHERE job_title = ?", AD2["title"])

    async with authenticated_async_context() as client:
        first = await client.post("/api/jobs/bring", json={**AD2, "client_key": "b-1"})
        again = await client.post("/api/jobs/bring", json={**AD2, "client_key": "b-1"})
        clash = await client.post("/api/jobs/bring", json={**AD2, "location": "York", "client_key": "b-1"})
        plain = await client.post("/api/jobs/bring", json=AD2)
    token = await _mint_token(authenticated_async_context)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            via_mcp = _payload(await mcp.call_tool("bring_job", {**AD2, "client_key": "b-1"}))
            mcp_clash = _error_text(await mcp.call_tool("bring_job", {**AD2, "title": "SRE", "client_key": "b-1"}))
    one, two = first.json(), again.json()
    assert first.status_code == 200 and again.status_code == 200, (first.text, again.text)
    assert (one["existing"], one["already_existed"]) == (False, False)
    # The FIRST reply, replayed: existing stays false (it was a new job then).
    assert (two["existing"], two["already_existed"]) == (False, True)
    assert two["application_id"] == one["application_id"]
    assert clash.status_code == 409, clash.text
    assert clash.json()["detail"].startswith("idempotency_key_reused: this client_key was already used")
    # No key: the old re-bring - same application, existing=true.
    assert plain.status_code == 200
    assert (plain.json()["existing"], plain.json()["already_existed"]) == (True, False)
    assert via_mcp["already_existed"] is True and via_mcp["existing"] is False
    assert via_mcp["application_id"] == one["application_id"]
    assert "409: idempotency_key_reused: this client_key was already used" in mcp_clash
    assert apps() == 1
    assert _keys("bring_job") == 1


@pytest.mark.asyncio
async def test_the_two_contact_doors_share_one_key(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        linked = await client.post(
            f"/api/applications/{app_id}/contacts", json={"name": "Ana Ruiz", "client_key": "c-1"}
        )
        via_people = await client.post(
            "/api/contacts", json={"name": "Ana Ruiz", "application_id": app_id, "client_key": "c-1"}
        )
    assert linked.status_code == 201, linked.text
    assert via_people.status_code == 200, via_people.text
    assert via_people.json()["already_existed"] is True
    assert via_people.json()["contact_id"] == linked.json()["contact_id"]
    assert _count("SELECT COUNT(*) FROM application_contacts WHERE application_id = ?", app_id) == 1


@pytest.mark.asyncio
async def test_a_failure_inside_the_write_stores_no_key_and_no_row(authenticated_async_context, monkeypatch):
    """The artifact INSERT, its event and the key row are ONE transaction."""

    async def _boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("injected after the artifact INSERT")

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        path, body = f"/api/applications/{app_id}/artifacts", {"kind": "answers", "text": "A", "client_key": "x"}
        monkeypatch.setattr(spine, "append_event", _boom)
        with pytest.raises(RuntimeError, match="injected"):
            await client.post(path, json=body)
        assert _count("SELECT COUNT(*) FROM application_artifacts WHERE application_id = ?", app_id) == 0
        assert _keys("save_artifact") == 0
        monkeypatch.undo()
        retry = await client.post(path, json=body)
    assert retry.status_code == 201, retry.text
    assert retry.json()["already_existed"] is False
    assert _count("SELECT COUNT(*) FROM application_artifacts WHERE application_id = ?", app_id) == 1


@pytest.mark.asyncio
async def test_a_racing_retry_rolls_back_whole_and_returns_the_winner(authenticated_async_context, monkeypatch):
    real_lookup = idempotency.lookup
    calls = {"n": 0}

    async def _first_miss(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return None if calls["n"] == 1 else await real_lookup(*args, **kwargs)

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        path, body = f"/api/applications/{app_id}/events", {"event_type": "note", "detail": "d", "client_key": "r"}
        won = (await client.post(path, json=body)).json()
        monkeypatch.setattr(idempotency, "lookup", _first_miss)
        lost = await client.post(path, json=body)
    assert lost.status_code == 201, lost.text
    assert lost.json()["already_existed"] is True
    assert lost.json()["event_id"] == won["event_id"]
    assert calls["n"] == 2
    assert _count(
        "SELECT COUNT(*) FROM application_events WHERE application_id = ? AND event_type = 'note'", app_id
    ) == 1


@pytest.mark.asyncio
async def test_a_bad_client_key_is_422_and_writes_nothing(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.post("/api/asks", json={"question": "Q?", "client_key": "x" * 129})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "client_key must be 1-128 printable characters"
    assert _count("SELECT COUNT(*) FROM application_asks") == 0
