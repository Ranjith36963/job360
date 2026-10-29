"""S9 (owner 2026-10-29): the assistant's work log is hidden from the user's timeline and Activity BY TYPE.

``process`` is true on every event whose type is a process type (process_log, kit_read, ask_withdrawn) and on
a note a ``process_log`` moved into the log. The read is all that changes: no row is updated or deleted.
An assistant may move only its OWN plain note; the user's web session may move any plain note; nothing
moves an ``applied`` or any other status, gate or kit event. Real doors, values asserted (rule #21).
Helpers are defined here, never imported (per-test schema isolation).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.repositories import pgsync

_AD = {
    "title": "Data Engineer", "company": "Larkspur", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://larkspur.example/careers/7", "description": "Pipelines and Python.",
}


def _sql(statement: str, params: tuple = ()) -> list[Any]:
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        cur = conn.execute(statement, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
    return rows


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, **body: Any) -> Any:
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, **body})


async def _events(client: AsyncClient, app_id: int) -> dict[int, dict[str, Any]]:
    resp = await client.get(f"/api/applications/{app_id}")
    assert resp.status_code == 200, resp.text
    return {int(e["id"]): e for e in resp.json()["events"]}


def _agent(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


def _event_rows(app_id: int) -> list[tuple[Any, ...]]:
    rows = _sql("SELECT id, event_type, detail, corrects_event_id FROM application_events "
                "WHERE application_id = ? ORDER BY id", (app_id,))
    return [(r["id"], r["event_type"], r["detail"], r["corrects_event_id"]) for r in rows]


def test_the_process_types_are_the_owners_list():
    assert settings.APPLICATION_PROCESS_EVENT_TYPES == ("process_log", "kit_read", "ask_withdrawn")
    assert "process_log" in settings.APPLICATION_NOTE_EVENT_TYPES
    assert "process_log" not in settings.APPLICATION_STATUS_EVENT_TYPES


@pytest.mark.asyncio
async def test_process_types_are_flagged_on_get_application_and_whats_new(authenticated_async_context):
    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        app_id = await _bring(client)
        note = await _event(client, app_id, "note", detail="Recruiter said the team is hiring two.")
        log = await _event(client, app_id, "process_log", detail="Doctor self-review: nothing to fix.")
        assert note.status_code == 201 and log.status_code == 201, (note.text, log.text)
        now = datetime.now(timezone.utc).isoformat()
        _sql("INSERT INTO application_events (user_id, application_id, event_type, detail, payload, occurred_at, "
             "recorded_at, recorded_by) VALUES (?, ?, 'kit_read', '', '{}', ?, ?, 'token:agent')",
             (user_id, app_id, now, now))
        by_type = {e["event_type"]: e["process"] for e in (await _events(client, app_id)).values()}
        assert by_type == {"brought": False, "note": False, "process_log": True, "kit_read": True}
        news = (await client.get("/api/whats-new")).json()["events"]
        assert {e["event_type"]: e["process"] for e in news if e["application_id"] == app_id} == by_type


@pytest.mark.asyncio
async def test_an_assistant_moves_its_own_note_and_the_status_replay_is_unchanged(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        assert (await _event(client, app_id, "applied")).status_code == 201
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        async with _agent(token) as agent:
            own = (await _event(agent, app_id, "note", detail="No person found; tried the team page.")).json()
            moved = await _event(agent, app_id, "process_log", detail="Reach run log.",
                                 corrects_event_id=own["event_id"])
            assert moved.status_code == 201, moved.text
            assert moved.json()["status"] == "applied"
            again = await _event(agent, app_id, "process_log", corrects_event_id=own["event_id"])
            assert again.status_code == 409, again.text
        events = await _events(client, app_id)
        assert events[own["event_id"]]["process"] is True and events[own["event_id"]]["superseded"] is True
        assert events[moved.json()["event_id"]]["process"] is True
        assert [e["process"] for e in events.values() if e["event_type"] in ("brought", "applied")] == [False, False]
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        assert detail["status"] == "applied"
        # Append-only: the moved note is still stored word for word, and nothing was rewritten.
        rows = _event_rows(app_id)
        assert (own["event_id"], "note", "No person found; tried the team page.", None) in rows


@pytest.mark.asyncio
async def test_an_assistant_may_not_move_the_users_note_or_an_applied(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        web_note = (await _event(client, app_id, "note", detail="I spoke to Priya at the meetup.")).json()
        applied = (await _event(client, app_id, "applied")).json()
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        before = _event_rows(app_id)
        async with _agent(token) as agent:
            theirs = await _event(agent, app_id, "process_log", corrects_event_id=web_note["event_id"])
            assert theirs.status_code == 403, theirs.text
            assert theirs.json()["detail"] == "an assistant may move only its own notes into the work log"
            status = await _event(agent, app_id, "process_log", corrects_event_id=applied["event_id"])
            assert status.status_code == 422, status.text
            assert status.json()["detail"] == "a process_log may move only a note into the work log"
        assert _event_rows(app_id) == before
        events = await _events(client, app_id)
        assert events[web_note["event_id"]]["process"] is False
        assert events[applied["event_id"]]["superseded"] is False


@pytest.mark.asyncio
async def test_the_users_session_may_move_a_note_but_never_an_applied(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        async with _agent(token) as agent:
            agent_note = (await _event(agent, app_id, "note", detail="Checked the form twice.")).json()
        applied = (await _event(client, app_id, "applied")).json()
        moved = await _event(client, app_id, "process_log", corrects_event_id=agent_note["event_id"])
        assert moved.status_code == 201, moved.text
        refused = await _event(client, app_id, "process_log", corrects_event_id=applied["event_id"])
        assert refused.status_code == 422, refused.text
        events = await _events(client, app_id)
        assert events[agent_note["event_id"]]["process"] is True
        assert events[applied["event_id"]]["superseded"] is False
        assert (await client.get(f"/api/applications/{app_id}")).json()["status"] == "applied"


@pytest.mark.asyncio
async def test_a_process_log_takes_no_payload_and_moves_no_note_with_a_payload(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        with_payload = await _event(client, app_id, "process_log", payload={"run": 1})
        assert with_payload.status_code == 422 and with_payload.json()["detail"] == "process_log takes no payload"
        chase_on = (datetime.now(timezone.utc) + timedelta(days=7)).date().isoformat()
        dated = await _event(client, app_id, "note", detail="Chase next week.", follow_up_on=chase_on)
        assert dated.status_code == 201, dated.text
        dated = dated.json()
        refused = await _event(client, app_id, "process_log", corrects_event_id=dated["event_id"])
        assert refused.status_code == 422, refused.text
        assert refused.json()["detail"] == "a note with a payload, a source or a correction stays on the record"
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        assert detail["follow_up_on"] == chase_on


@pytest.mark.asyncio
async def test_old_read_the_form_notes_move_to_the_work_log_once(authenticated_async_context):
    """Prod events 866 and 1239: the tidy's own instruction was written as a `note` the user read."""
    from src.services.applications import asks

    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        app_id = await _bring(client)
        now = datetime.now(timezone.utc).isoformat()
        _sql("INSERT INTO application_events (user_id, application_id, event_type, detail, payload, occurred_at, "
             "recorded_at, recorded_by) VALUES (?, ?, 'note', ?, '{}', ?, ?, 'system')",
             (user_id, app_id, asks.READ_FORM_NOTE, now, now))
        users_own = await _event(client, app_id, "note", detail=asks.READ_FORM_NOTE)
        assert users_own.status_code == 201, users_own.text
        await client.get("/api/asks")
        await client.get("/api/asks")  # a second read moves nothing more
        events = await _events(client, app_id)
        logs = [e for e in events.values() if e["event_type"] == "process_log"]
        assert [(e["detail"], e["recorded_by"]) for e in logs] == [(asks.MOVED_TO_LOG_DETAIL, "system")]
        old_note = events[logs[0]["corrects_event_id"]]
        assert (old_note["recorded_by"], old_note["process"]) == ("system", True)
        # Matched by our own actor and constant: the user's identical words stay on the timeline.
        assert events[users_own.json()["event_id"]]["process"] is False
