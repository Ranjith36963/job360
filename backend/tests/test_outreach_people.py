"""Outreach tracking (owner decisions, 2026-09-25): a contact can be linked
to a job or to none (cold networking); Job360 remembers every message
version, who/when/channel, sent, reply, and which job (or none). Helpers are
COPIED from test_slice4_contacts.py, never imported (a cross-module fixture
import breaks per-test schema isolation).
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _add_person(client: AsyncClient, **body: Any):
    payload = {"name": "Priya Shah", "role": "Talent Partner", **body}
    return await client.post("/api/contacts", json=payload)


# ═══════════════════════════════════════════════════════════════════════════
# Cold contacts — a person with no job
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cold_contact_stored_with_null_job_and_appears_in_list_people(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await _add_person(client, email="priya@northwind.example")
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["contact"]["application_id"] is None

        people = await client.get("/api/people")
        assert people.status_code == 200, people.text
        rows = people.json()["people"]
        assert any(p["email"] == "priya@northwind.example" for p in rows)


@pytest.mark.asyncio
async def test_duplicate_cold_email_is_already_existed(authenticated_async_context):
    async with authenticated_async_context() as client:
        first = await _add_person(client, email="priya@northwind.example")
        assert first.status_code == 201, first.text
        again = await _add_person(client, email="  Priya@Northwind.example ", name="P. Shah")
        assert again.status_code == 200, again.text
        assert again.json()["already_existed"] is True
        assert again.json()["contact"]["id"] == first.json()["contact"]["id"]


# ═══════════════════════════════════════════════════════════════════════════
# Message versions
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_two_message_versions_both_appear_in_get_application_and_list_people(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        added = await client.post(
            f"/api/applications/{app_id}/contacts",
            json={"name": "Priya Shah", "role": "Recruiter", "email": "priya@northwind.example"},
        )
        contact_id = added.json()["contact"]["id"]

        v1 = await client.post(
            f"/api/applications/{app_id}/artifacts",
            json={"kind": "outreach", "text": "Hi Priya, v1", "contact_id": contact_id, "channel": "linkedin"},
        )
        assert v1.status_code == 201, v1.text
        assert v1.json()["event_id"] is None  # a version is not news
        v2 = await client.post(
            f"/api/applications/{app_id}/artifacts",
            json={"kind": "outreach", "text": "Hi Priya, v2", "contact_id": contact_id, "channel": "linkedin"},
        )
        assert v2.status_code == 201, v2.text
        assert v1.json()["version_no"] == 1
        assert v2.json()["version_no"] == 2

        detail = await client.get(f"/api/applications/{app_id}")
        contact = next(c for c in detail.json()["contacts"] if c["id"] == contact_id)
        texts = [m["text"] for m in contact["outreach"]["messages"]]
        assert texts == ["Hi Priya, v1", "Hi Priya, v2"]

        person = await client.get(f"/api/people?contact_id={contact_id}")
        assert person.status_code == 200, person.text
        person_texts = [m["text"] for m in person.json()["person"]["outreach"]["messages"]]
        assert person_texts == ["Hi Priya, v1", "Hi Priya, v2"]


@pytest.mark.asyncio
async def test_message_requires_outreach_kind(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        added = await client.post(
            f"/api/applications/{app_id}/contacts", json={"name": "Priya Shah", "email": "priya@x.example"}
        )
        contact_id = added.json()["contact"]["id"]
        resp = await client.post(
            f"/api/applications/{app_id}/artifacts",
            json={"kind": "cv", "text": "x", "contact_id": contact_id, "channel": "linkedin"},
        )
        assert resp.status_code == 422, resp.text


# ═══════════════════════════════════════════════════════════════════════════
# Sent / reply on a linked contact
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_outreach_sent_writes_ledger_and_timeline_without_moving_status(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        added = await client.post(
            f"/api/applications/{app_id}/contacts", json={"name": "Priya Shah", "email": "priya@x.example"}
        )
        contact_id = added.json()["contact"]["id"]

        sent = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "outreach_sent", "contact_id": contact_id, "channel": "linkedin"},
        )
        assert sent.status_code == 201, sent.text
        body = sent.json()
        assert body["status"] == "considering"  # note-family event never moves status
        assert isinstance(body["event_id"], int)

        detail = await client.get(f"/api/applications/{app_id}")
        events = [e for e in detail.json()["events"] if e["event_type"] == "outreach_sent"]
        assert len(events) == 1
        assert events[0]["payload"]["contact_id"] == contact_id
        assert events[0]["payload"]["channel"] == "linkedin"
        contact = next(c for c in detail.json()["contacts"] if c["id"] == contact_id)
        assert contact["outreach"]["last_sent"]["channel"] == "linkedin"


@pytest.mark.asyncio
async def test_outreach_replied_at_interview_scheduled_leaves_status_and_stats_alone(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await client.post(
            f"/api/applications/{app_id}/events", json={"event_type": "applied"}
        )
        sched = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "interview_scheduled", "scheduled_at": "2027-01-01T10:00:00+00:00"},
        )
        assert sched.status_code == 201, sched.text
        added = await client.post(
            f"/api/applications/{app_id}/contacts", json={"name": "Priya Shah", "email": "priya@x.example"}
        )
        contact_id = added.json()["contact"]["id"]

        replied = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "outreach_replied", "contact_id": contact_id, "channel": "linkedin"},
        )
        assert replied.status_code == 201, replied.text
        assert replied.json()["status"] == "interview_scheduled"

        stats = await client.get("/api/applications/stats")
        assert stats.status_code == 200, stats.text
        assert stats.json()["overall"]["replied"] == 0


@pytest.mark.asyncio
async def test_follow_up_on_linked_send_makes_it_due(authenticated_async_context):
    from datetime import date, timedelta

    yesterday = (date.today() - timedelta(days=1)).isoformat()

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        added = await client.post(
            f"/api/applications/{app_id}/contacts", json={"name": "Priya Shah", "email": "priya@x.example"}
        )
        contact_id = added.json()["contact"]["id"]
        resp = await client.post(
            f"/api/applications/{app_id}/events",
            json={
                "event_type": "outreach_sent", "contact_id": contact_id, "channel": "email",
                "follow_up_on": yesterday,
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["follow_up_on"] == yesterday

        listed = await client.get("/api/applications?due=true")
        assert listed.status_code == 200, listed.text
        assert any(a["id"] == app_id and a["follow_up_due"] for a in listed.json()["applications"])


@pytest.mark.asyncio
async def test_follow_up_on_cold_contact_is_422(authenticated_async_context):
    async with authenticated_async_context() as client:
        added = await _add_person(client, email="cold@x.example")
        contact_id = added.json()["contact"]["id"]
        resp = await client.post(
            f"/api/contacts/{contact_id}/outreach",
            json={"entry": "sent", "channel": "email", "follow_up_on": "2030-01-01"},
        )
        assert resp.status_code == 422, resp.text
        assert "follow-up" in resp.text.lower()


# ═══════════════════════════════════════════════════════════════════════════
# Ownership / mismatch / idempotency
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_foreign_contact_id_is_404_on_every_door(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        r1 = await client.post(
            f"/api/applications/{app_id}/artifacts",
            json={"kind": "outreach", "text": "hi", "contact_id": 987654321, "channel": "email"},
        )
        assert r1.status_code == 404, r1.text
        r2 = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "outreach_sent", "contact_id": 987654321, "channel": "email"},
        )
        assert r2.status_code == 404, r2.text
        r3 = await client.post(
            "/api/contacts/987654321/outreach", json={"entry": "sent", "channel": "email"}
        )
        assert r3.status_code == 404, r3.text
        r4 = await client.patch("/api/contacts/987654321", json={"name": "x"})
        assert r4.status_code == 404, r4.text
        r5 = await client.get("/api/people?contact_id=987654321")
        assert r5.status_code == 404, r5.text


@pytest.mark.asyncio
async def test_mismatched_application_id_is_422(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id_a = await _bring(client)
        app_id_b = await _bring(
            client,
            ad={**_AD, "title": "Platform Engineer", "apply_url": "https://northwind.example/careers/99"},
        )
        added = await client.post(
            f"/api/applications/{app_id_a}/contacts", json={"name": "Priya Shah", "email": "priya@x.example"}
        )
        contact_id = added.json()["contact"]["id"]
        resp = await client.post(
            f"/api/applications/{app_id_b}/events",
            json={"event_type": "outreach_sent", "contact_id": contact_id, "channel": "email"},
        )
        assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_bad_channel_and_oversized_text_and_version_cap_name_the_setting(authenticated_async_context, monkeypatch):
    from src.core import settings

    async with authenticated_async_context() as client:
        added = await _add_person(client, email="x@x.example")
        contact_id = added.json()["contact"]["id"]

        bad_channel = await client.post(
            f"/api/contacts/{contact_id}/outreach", json={"entry": "message", "channel": "carrier-pigeon", "text": "hi"}
        )
        assert bad_channel.status_code == 422, bad_channel.text
        assert "OUTREACH_CHANNELS" in bad_channel.text

        monkeypatch.setattr(settings, "OUTREACH_MESSAGE_MAX_CHARS", 5)
        too_long = await client.post(
            f"/api/contacts/{contact_id}/outreach", json={"entry": "message", "channel": "email", "text": "way too long"}
        )
        assert too_long.status_code == 422, too_long.text
        assert "OUTREACH_MESSAGE_MAX_CHARS" in too_long.text

        monkeypatch.setattr(settings, "OUTREACH_VERSIONS_PER_CONTACT_MAX", 1)
        monkeypatch.setattr(settings, "OUTREACH_MESSAGE_MAX_CHARS", 5000)
        ok = await client.post(
            f"/api/contacts/{contact_id}/outreach", json={"entry": "message", "channel": "email", "text": "hi"}
        )
        assert ok.status_code == 201, ok.text
        capped = await client.post(
            f"/api/contacts/{contact_id}/outreach", json={"entry": "message", "channel": "email", "text": "hi again"}
        )
        assert capped.status_code == 429, capped.text
        assert "OUTREACH_VERSIONS_PER_CONTACT_MAX" in capped.text


@pytest.mark.asyncio
async def test_same_source_twice_is_one_row(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        added = await client.post(
            f"/api/applications/{app_id}/contacts", json={"name": "Priya Shah", "email": "priya@x.example"}
        )
        contact_id = added.json()["contact"]["id"]
        source = {"kind": "email", "message_id": "<msg-1@example.com>", "sender": "priya@x.example", "subject": "Re: role"}
        first = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "outreach_replied", "contact_id": contact_id, "channel": "email", "source": source},
        )
        assert first.status_code == 201, first.text
        again = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "outreach_replied", "contact_id": contact_id, "channel": "email", "source": source},
        )
        assert again.status_code in (200, 201), again.text
        assert again.json()["already_existed"] is True

        detail = await client.get(f"/api/applications/{app_id}")
        contact = next(c for c in detail.json()["contacts"] if c["id"] == contact_id)
        assert len(contact["outreach"]["replies"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
# Contact edits — append-only, old values kept
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_patch_role_twice_keeps_history_and_never_touches_base_row(authenticated_async_context):
    async with authenticated_async_context() as client:
        added = await _add_person(client, email="priya@x.example", role="Recruiter")
        contact_id = added.json()["contact"]["id"]

        first = await client.patch(f"/api/contacts/{contact_id}", json={"role": "Senior Recruiter"})
        assert first.status_code == 200, first.text
        assert first.json()["role"] == "Senior Recruiter"

        second = await client.patch(f"/api/contacts/{contact_id}", json={"role": "Talent Lead"})
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["role"] == "Talent Lead"
        role_history = [h["value"] for h in body["edit_history"]["role"]]
        assert role_history == ["Recruiter", "Senior Recruiter", "Talent Lead"]
        for h in body["edit_history"]["role"]:
            assert h["recorded_by"]
            assert h["recorded_at"]


async def _second_user_session_cookie(email: str = "second-outreach@example.com") -> str:
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.core import settings
    from src.repositories import pgsync

    sync_client = TestClient(app)
    r = sync_client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"})
    assert r.status_code == 201, r.text
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
    conn.commit()
    conn.close()
    lr = sync_client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    assert lr.status_code == 200, lr.text
    cookie = sync_client.cookies.get("job360_session")
    assert cookie, "failed to capture second user's session cookie"
    sync_client.close()
    return cookie


def _session_client(cookie: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
    )


@pytest.mark.asyncio
async def test_edit_of_foreign_contact_is_404(authenticated_async_context):
    async with authenticated_async_context() as client:
        added = await _add_person(client, email="priya@x.example")
        contact_id = added.json()["contact"]["id"]

    cookie = await _second_user_session_cookie()
    async with _session_client(cookie) as other:
        resp = await other.patch(f"/api/contacts/{contact_id}", json={"role": "x"})
        assert resp.status_code == 404, resp.text


# ═══════════════════════════════════════════════════════════════════════════
# Append-only guard, MCP parity, VISION pin
# ═══════════════════════════════════════════════════════════════════════════


def test_contact_outreach_and_contact_edits_are_append_only():
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src"
    offenders = []
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if re.search(r"(UPDATE|DELETE\s+FROM)\s+contact_outreach", text, re.IGNORECASE):
            offenders.append(str(py))
        if re.search(r"(UPDATE|DELETE\s+FROM)\s+contact_edits", text, re.IGNORECASE):
            offenders.append(str(py))
    assert offenders == [], f"contact_outreach/contact_edits must be append-only: {offenders}"


def test_outreach_replied_is_a_note_type_never_a_status_type():
    from src.core import settings

    assert "outreach_replied" in settings.APPLICATION_NOTE_EVENT_TYPES
    assert "outreach_replied" not in settings.APPLICATION_STATUS_EVENT_TYPES


# ═══════════════════════════════════════════════════════════════════════════
# Coordinator review, 2026-09-26 — 6 real bugs found in ebb71f6
# ═══════════════════════════════════════════════════════════════════════════


async def _mint_token(client: AsyncClient, name: str = "agent") -> str:
    resp = await client.post("/api/tokens", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _mcp_client(token: str):
    """Official MCP client wired straight into the FastAPI app (in-process) —
    copied from test_mcp_server.py, never imported (fixture isolation)."""
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


def _mcp_payload(result) -> dict:
    import json

    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def _mcp_error_text(result) -> str:
    assert result.is_error, "expected a tool error"
    return result.content[0].text


# ── Bug 1 [P1] — cold contacts could never get sent/reply over MCP ─────────


@pytest.mark.asyncio
async def test_bug1_cold_contact_record_event_outreach_sent_over_http(authenticated_async_context):
    """The HTTP-level mechanism record_event's MCP tool now uses for a cold
    contact: POST /api/contacts/{id}/outreach directly (no application)."""
    async with authenticated_async_context() as client:
        added = await _add_person(client, email="cold@x.example")
        contact_id = added.json()["contact"]["id"]
        resp = await client.post(
            f"/api/contacts/{contact_id}/outreach",
            json={"entry": "sent", "channel": "linkedin"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["event_id"] is None  # cold — no job timeline to write to

        people = await client.get("/api/people")
        person = next(p for p in people.json()["people"] if contact_id in p["contact_ids"])
        assert person["last_sent"]["channel"] == "linkedin"


@pytest.mark.asyncio
async def test_bug1_mcp_record_event_stores_sent_for_a_cold_contact(authenticated_async_context):
    """MCP parity — the actual tool, not just the underlying route."""
    pytest.importorskip("mcp")
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        added = await _add_person(client, email="cold-mcp@x.example")
        contact_id = added.json()["contact"]["id"]
        token = await _mint_token(client)

    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            result = await mcp.call_tool(
                "record_event",
                {"event_type": "outreach_sent", "contact_id": contact_id, "channel": "linkedin"},
            )
            body = _mcp_payload(result)
            assert body["event_id"] is None
            assert body["already_existed"] is False

            listed = await mcp.call_tool("list_people", {"contact_id": contact_id})
            person = _mcp_payload(listed)["person"]
            assert person["outreach"]["last_sent"]["channel"] == "linkedin"


@pytest.mark.asyncio
async def test_bug1_cold_reply_with_source_twice_is_one_row(authenticated_async_context):
    async with authenticated_async_context() as client:
        added = await _add_person(client, email="cold-reply@x.example")
        contact_id = added.json()["contact"]["id"]
        source = {"kind": "email", "message_id": "<cold-reply-1@example.com>", "sender": "cold-reply@x.example"}
        first = await client.post(
            f"/api/contacts/{contact_id}/outreach",
            json={"entry": "reply", "channel": "email", "source": source},
        )
        assert first.status_code == 201, first.text
        again = await client.post(
            f"/api/contacts/{contact_id}/outreach",
            json={"entry": "reply", "channel": "email", "source": source},
        )
        assert again.status_code == 200, again.text
        assert again.json()["already_existed"] is True

        person = await client.get(f"/api/people?contact_id={contact_id}")
        assert len(person.json()["person"]["outreach"]["replies"]) == 1


@pytest.mark.asyncio
async def test_bug1_mcp_save_artifact_cold_wrong_kind_is_422(authenticated_async_context):
    pytest.importorskip("mcp")
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        added = await _add_person(client, email="cold-kind@x.example")
        contact_id = added.json()["contact"]["id"]
        token = await _mint_token(client)

    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            result = await mcp.call_tool(
                "save_artifact", {"kind": "cv", "text": "x", "contact_id": contact_id, "channel": "email"}
            )
            assert "422" in _mcp_error_text(result)


@pytest.mark.asyncio
async def test_bug1_mcp_tools_list_is_still_19(authenticated_async_context):
    pytest.importorskip("mcp")
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        token = await _mint_token(client)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            listed = await mcp.list_tools()
            assert len(listed.tools) == 19


# ── Bug 2 [P1] — list_people / add_contact ignored contact edits ───────────


@pytest.mark.asyncio
async def test_bug2_edited_email_is_found_and_old_one_is_not(authenticated_async_context):
    async with authenticated_async_context() as client:
        added = await _add_person(client, email="typo@x.example", role="Recruiter")
        contact_id = added.json()["contact"]["id"]
        patched = await client.patch(f"/api/contacts/{contact_id}", json={"email": "fixed@x.example"})
        assert patched.status_code == 200, patched.text

        found = await client.get("/api/people?email=fixed@x.example")
        assert found.status_code == 200, found.text
        people = found.json()["people"]
        assert any(contact_id in p["contact_ids"] for p in people)
        match = next(p for p in people if contact_id in p["contact_ids"])
        assert match["email"] == "fixed@x.example"
        assert match["role"] == "Recruiter"

        gone = await client.get("/api/people?email=typo@x.example")
        assert gone.json()["people"] == []


@pytest.mark.asyncio
async def test_bug2_add_contact_new_email_is_already_existed_old_email_is_new_row(authenticated_async_context):
    async with authenticated_async_context() as client:
        added = await _add_person(client, email="original@x.example")
        contact_id = added.json()["contact"]["id"]
        await client.patch(f"/api/contacts/{contact_id}", json={"email": "changed@x.example"})

        same_as_current = await _add_person(client, email="changed@x.example", name="Someone Else")
        assert same_as_current.status_code == 200, same_as_current.text
        assert same_as_current.json()["already_existed"] is True
        assert same_as_current.json()["contact"]["id"] == contact_id

        same_as_old = await _add_person(client, email="original@x.example", name="Fresh Person")
        assert same_as_old.status_code == 201, same_as_old.text
        assert same_as_old.json()["contact"]["id"] != contact_id


@pytest.mark.asyncio
async def test_bug2_colliding_cold_edit_is_409(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _add_person(client, email="a@x.example")
        b = await _add_person(client, email="b@x.example")
        b_id = b.json()["contact"]["id"]
        resp = await client.patch(f"/api/contacts/{b_id}", json={"email": "a@x.example"})
        assert resp.status_code == 409, resp.text
        assert a.json()["contact"]["id"] != b_id


# ── Bug 3 [P2] — concurrent message saves must never 500 ───────────────────


@pytest.mark.asyncio
async def test_bug3_concurrent_message_version_race_retries_instead_of_500(authenticated_async_context, monkeypatch):
    from src.repositories.database import JobDatabase
    from src.services.applications import contacts as contacts_service

    async with authenticated_async_context() as client:
        added = await _add_person(client, email="race@x.example")
        contact_id = added.json()["contact"]["id"]

    real_count = contacts_service._message_version_count
    calls = {"n": 0}

    async def racy_count(db: JobDatabase, cid: int) -> int:
        # Simulate another writer landing version 1 between this call's
        # count and its INSERT: the FIRST count call sees 0 (about to try
        # version_no=1), but a rival row for version_no=1 already exists by
        # the time the INSERT runs, so it must collide and retry.
        n = await real_count(db, cid)
        calls["n"] += 1
        if calls["n"] == 1 and cid == contact_id:
            async with db._db.transaction():
                await db._db.execute(
                    "INSERT INTO contact_outreach "
                    "(user_id, contact_id, entry, channel, text, version_no, occurred_at, recorded_at, "
                    " recorded_by, source_message_id) VALUES "
                    "((SELECT user_id FROM application_contacts WHERE id = ?), ?, 'message', 'email', "
                    " 'racer', 1, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 'web', '')",
                    (cid, cid),
                )
        return n

    monkeypatch.setattr(contacts_service, "_message_version_count", racy_count)

    async with authenticated_async_context() as client:
        resp = await client.post(
            f"/api/contacts/{contact_id}/outreach",
            json={"entry": "message", "channel": "email", "text": "mine"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["outreach"]["version_no"] == 2


# ── Bug 4 [P2] — record_outreach spent the rate slot before validating ─────


@pytest.mark.asyncio
async def test_bug4_bad_input_never_spends_the_rate_limit(authenticated_async_context, monkeypatch):
    from src.api.main import app
    from src.core import settings

    monkeypatch.setattr(settings, "OUTREACH_MAX_PER_HOUR", 1)
    async with authenticated_async_context() as client:
        added = await _add_person(client, email="budget@x.example")
        contact_id = added.json()["contact"]["id"]
        token = await _mint_token(client)

    # A bearer/token actor (not "web") is what actually spends the outreach
    # budget — a web session is exempt (owner decision), so this test must
    # use a token to exercise the limiter at all.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as agent:
        bad = await agent.post(
            f"/api/contacts/{contact_id}/outreach",
            json={"entry": "sent", "channel": "email", "occurred_at": "not-a-date"},
        )
        assert bad.status_code == 422, bad.text
        good = await agent.post(
            f"/api/contacts/{contact_id}/outreach", json={"entry": "sent", "channel": "email"}
        )
        assert good.status_code == 201, good.text


# ── Bug 5 [P2] — list_people read the OLDEST LIST_PEOPLE_MAX rows ──────────


@pytest.mark.asyncio
async def test_bug5_email_lookup_of_the_newest_still_found_when_over_the_cap(
    authenticated_async_context, monkeypatch
):
    from src.core import settings

    monkeypatch.setattr(settings, "LIST_PEOPLE_MAX", 2)
    async with authenticated_async_context() as client:
        await _add_person(client, email="one@x.example", name="One")
        await _add_person(client, email="two@x.example", name="Two")
        await _add_person(client, email="three@x.example", name="Three")

        found = await client.get("/api/people?email=three@x.example")
        assert found.status_code == 200, found.text
        assert any(p["email"] == "three@x.example" for p in found.json()["people"])

        unfiltered = await client.get("/api/people")
        assert unfiltered.status_code == 200, unfiltered.text
        assert unfiltered.json()["truncated"] is True
        assert len(unfiltered.json()["people"]) == 2


# ── Bug 6 [P2] — export_history unlinked_contacts: unbounded/untruncated ───


@pytest.mark.asyncio
async def test_bug6_include_text_false_strips_outreach_text_everywhere(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        linked = await client.post(
            f"/api/applications/{app_id}/contacts", json={"name": "Linked", "email": "linked@x.example"}
        )
        linked_id = linked.json()["contact"]["id"]
        await client.post(
            f"/api/applications/{app_id}/artifacts",
            json={"kind": "outreach", "text": "secret linked text", "contact_id": linked_id, "channel": "email"},
        )
        cold = await _add_person(client, email="cold-export@x.example")
        cold_id = cold.json()["contact"]["id"]
        await client.post(
            f"/api/contacts/{cold_id}/outreach",
            json={"entry": "message", "channel": "email", "text": "secret cold text"},
        )

        export = await client.get("/api/applications/export?include_text=false")
        assert export.status_code == 200, export.text
        body = export.json()
        blob = str(body)
        assert "secret linked text" not in blob
        assert "secret cold text" not in blob


@pytest.mark.asyncio
async def test_bug6_unlinked_contacts_only_on_the_first_page(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _add_person(client, email="page-one@x.example")
        app_id = await _bring(client)

        first = await client.get("/api/applications/export?include_text=true")
        assert first.status_code == 200, first.text
        first_body = first.json()
        assert len(first_body["unlinked_contacts"]) == 1

        # A `since` cursor is a follow-up call — unlinked_contacts must not
        # repeat on every page.
        since = first_body["applications"][0]["updated_at"] if first_body["applications"] else "2099-01-01"
        second = await client.get(f"/api/applications/export?since={since}")
        assert second.status_code == 200, second.text
        assert second.json()["unlinked_contacts"] == []
        assert app_id  # keep the linter/application reference honest


@pytest.mark.asyncio
async def test_bug6_unlinked_contacts_truncate_over_budget(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "EXPORT_HISTORY_MAX_BYTES", 400)
    async with authenticated_async_context() as client:
        await _add_person(client, email="big-one@x.example", notes="x" * 300)
        await _add_person(client, email="big-two@x.example", notes="y" * 300)

        export = await client.get("/api/applications/export?include_text=true")
        assert export.status_code == 200, export.text
        assert export.json()["unlinked_contacts_truncated"] is True
