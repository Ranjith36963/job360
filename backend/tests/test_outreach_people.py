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
