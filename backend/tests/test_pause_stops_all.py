"""Owner decision 2026-10-26: "Pause stops everything."

While ``assistant_settings.paused_until`` holds, every MCP tool that starts or advances work answers one
stable refusal (``status: paused``) and writes nothing; reads, doctor, get_recipe and update_profile keep
working; ``whats_due`` says paused with nothing due. The gate is on the MCP door only: the user's own web
clicks still work. After a resume everything works again. Helpers are copied, never imported.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

import src.core.settings as settings_mod
from src.repositories import pgsync

PAUSE = "assistant_settings.paused_until"
HUNT = "assistant_settings.hunt_every_hours"
JOB = {
    "title": "Senior Python Engineer",
    "company": "Acme Ltd",
    "location": "London",
    "description": "Build FastAPI services with Postgres. " * 8,
    "apply_url": "https://acme.example/jobs/1",
}
TABLES = (
    "jobs", "applications", "application_events", "application_artifacts", "application_receipts",
    "application_contacts", "application_asks", "inbox_items",
)


def _refused_calls(app_id: int, job_id: int) -> dict[str, dict[str, Any]]:
    """One call per refused tool, with arguments that would otherwise write or start work."""
    return {
        "bring_job": {**JOB, "title": "Another role"},
        "ask_user": {"question": "Which CV?", "application_id": app_id},
        "match_email": {"sender": "recruiter@acme.example"},
        "mark_inbox_checked": {"application_ids": [app_id]},
        "record_events": {"events": [{"application_id": app_id, "event_type": "note", "detail": "x"}]},
        "record_event": {"event_type": "submit_attempted", "application_id": app_id},
        "save_artifact": {"kind": "cv", "text": "A CV.", "application_id": app_id},
        "save_fit": {"application_id": app_id, "verdict": "Good fit"},
        "add_contact": {"application_id": app_id, "name": "Rita Recruiter", "email": "rita@acme.example"},
        "update_contact": {"contact_id": 1, "notes": "x"},
        "update_job": {"application_id": app_id, "country": "GB"},
        "check_outreach": {"contact_id": 1, "message_id": 1},
        "check_auto_email": {"application_id": app_id, "kind": "checkin"},
        "check_form": {"application_id": app_id, "questions": [{"label": "Name"}]},
        "browser": {"action": "status"},
        "own_browser_queue": {"action": "take", "session": "s1"},
        "get_application_kit": {"application_id": app_id},
    }


def _counts() -> dict[str, int]:
    conn = pgsync.connect(str(settings_mod.DB_PATH))
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}  # noqa: S608
    finally:
        conn.close()


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"])),
        user_id,
        source_action="cv_upload",
    )


async def _patch(client, path: str, value: Any) -> None:
    resp = await client.patch("/api/profile", json={"edits": [{"path": path, "value": value}]})
    assert resp.status_code == 200, resp.text


async def _mint_token(authenticated_async_context) -> str:
    async with authenticated_async_context() as client:
        resp = await client.post("/api/tokens", json={"name": "agent"})
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


def _payload(result) -> dict:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def _is_paused_refusal(result) -> bool:
    if result.is_error:
        return False
    body = json.loads(result.content[0].text)
    return body.get("status") == "paused"


# ── Pure ────────────────────────────────────────────────────────────────────


def test_pause_state_reads_until_resumed_a_future_time_and_an_expired_one():
    from src.services.profile.assistant_settings import pause_state
    from src.services.profile.models import AssistantSettings

    now = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
    later = (now + timedelta(hours=3)).isoformat()
    earlier = (now - timedelta(hours=3)).isoformat()
    assert pause_state(AssistantSettings(paused_until="until_resumed"), now) == (True, "until_resumed")
    assert pause_state(AssistantSettings(paused_until=later), now) == (True, later)
    assert pause_state(AssistantSettings(paused_until=earlier), now) == (False, "")
    assert pause_state(AssistantSettings(), now) == (False, "")


def test_every_refused_tool_is_a_served_tool_and_reads_are_never_refused():
    from src.api import mcp_server

    served = {t.name for t in mcp_server.build_server()._tool_manager.list_tools()}  # noqa: SLF001
    assert mcp_server.PAUSE_REFUSED_TOOLS <= served
    for read in ("get_profile", "get_job", "get_application", "list_applications", "list_receipts", "get_receipt",
                 "whats_new", "whats_due", "doctor", "get_recipe", "stats", "list_people", "export_history",
                 "report_capabilities", "report_missing_tool", "check_submit", "update_profile", "answer_ask"):
        assert not mcp_server.pause_blocks(read, {}), read
    assert mcp_server.pause_blocks("own_browser_queue", {})  # list
    assert not mcp_server.pause_blocks("own_browser_queue", {"action": "release"})
    assert mcp_server.pause_blocks("record_event", {"event_type": "outreach_sent"})
    assert not mcp_server.pause_blocks("record_event", {"event_type": "note"})
    # A submit made before the pause can still be recorded; check_submit stops any new one.
    assert not mcp_server.pause_blocks("record_application", {})
    assert not mcp_server.pause_blocks("record_event", {"event_type": "applied"})


# ── Through the real doors ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pause_stops_the_assistant_not_the_user_and_resume_restores_it(
    authenticated_async_context, fixture_user_id
):
    from src.api.mcp_server import PAUSED_MESSAGE, mcp_runtime

    _seed_profile(fixture_user_id)
    token = await _mint_token(authenticated_async_context)
    async with authenticated_async_context() as client:
        await _patch(client, HUNT, 6)
        brought = await client.post("/api/jobs/bring", json=JOB)
        assert brought.status_code == 200, brought.text
        app_id, job_id = brought.json()["application_id"], brought.json()["job"]["id"]
        await _patch(client, PAUSE, "until_resumed")

    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            before = _counts()
            for name, args in _refused_calls(app_id, job_id).items():
                body = _payload(await mcp.call_tool(name, args))
                assert body == {"status": "paused", "paused_until": "until_resumed", "message": PAUSED_MESSAGE}, name
            assert _counts() == before, "a refused tool wrote something"

            due = _payload(await mcp.call_tool("whats_due", {"duties": ["hunt"]}))
            assert list(due)[:3] == ["status", "paused_until", "message"]
            assert due["paused"] is True and due["due"] == [] and due["nothing_due"] is True

            daily = _payload(await mcp.call_tool("get_recipe", {"name": "daily"}))
            assert list(daily)[0] == "status" and daily["status"] == "paused" and daily["text"]
            assert _payload(await mcp.call_tool("get_recipe", {"name": "rules"})).get("status") != "paused"

            assert _payload(await mcp.call_tool("get_profile", {}))
            listed = _payload(await mcp.call_tool("list_applications", {}))
            assert [a["id"] for a in listed["applications"]] == [app_id]
            assert _payload(await mcp.call_tool("get_application", {"application_id": app_id}))
            assert _payload(await mcp.call_tool("doctor", {}))["paused"] is True
            assert _payload(await mcp.call_tool("whats_new", {})).get("status") != "paused"
            assert _payload(await mcp.call_tool("stats", {})).get("status") != "paused"
            assert _payload(await mcp.call_tool("list_people", {})).get("status") != "paused"
            check = _payload(await mcp.call_tool("check_submit", {"application_id": app_id}))
            assert (check["decision"], check["reason"]) == ("stop", "paused")  # unchanged gate

            note = await mcp.call_tool("record_event", {"event_type": "note", "application_id": app_id,
                                                        "detail": "Stopped: the user paused me."})
            assert _payload(note)["event_id"]  # winding down is still recorded
            assert not _is_paused_refusal(
                await mcp.call_tool("own_browser_queue", {"action": "release", "session": "s1",
                                                          "task_id": "x", "reason": "other"})
            )

    # The user is not paused: their own web clicks still work.
    async with authenticated_async_context() as client:
        art = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "My CV."})
        assert art.status_code == 201, art.text
        other = await client.post("/api/jobs/bring", json={**JOB, "title": "Web role"})
        assert other.status_code == 200, other.text
        ev = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "fit_judged",
                                                                           "detail": "My own view"})
        assert ev.status_code == 201, ev.text
        await _patch(client, PAUSE, "")  # resume

    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            again = _payload(await mcp.call_tool("bring_job", {**JOB, "title": "After resume"}))
            assert again["application_id"] and again.get("status") != "paused"
            saved = _payload(await mcp.call_tool(
                "save_artifact", {"kind": "cv", "text": "A CV.", "application_id": app_id}
            ))
            assert saved.get("status") != "paused"
            due = _payload(await mcp.call_tool("whats_due", {}))
            assert "paused" not in due and [d["duty"] for d in due["due"]] == ["hunt"]
            assert _payload(await mcp.call_tool("get_recipe", {"name": "daily"})).get("status") != "paused"
