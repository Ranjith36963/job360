"""Outreach pack #5/#6 (2026-10-20): meeting / call / intro and referral entries on the
contact ledger, ``referred_by_contact_id`` on the receipt, ``application.referred_by`` in the
kit, and ``stats(view="referrals")``. Helpers are COPIED from test_outreach_people.py, never
imported (a cross-module fixture import breaks per-test schema isolation).
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from migrations import runner
from src.repositories import pg
from src.repositories.database import JobDatabase

STEM = "0072_contact_interactions_referrals"
STEM73 = "0073_receipt_referred_by"

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}


def _soon() -> str:
    return (date.today() + timedelta(days=7)).isoformat()


def _ad(n: int) -> dict[str, Any]:
    return {
        **_AD, "title": f"Data Engineer {n}", "company": f"Northwind {n}",
        "apply_url": f"https://northwind.example/careers/{100 + n}",
        "description": f"Build the pipelines number {n}. Python, dbt, Snowflake. Fully remote.",
    }


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _add_person(client: AsyncClient, **body: Any):
    payload = {"name": "Priya Shah", "role": "Talent Partner", "company": "Northwind", **body}
    return await client.post("/api/contacts", json=payload)


async def _linked(client: AsyncClient, app_id: int, name: str = "Priya Shah", email: str = "priya@x.example") -> int:
    added = await client.post(f"/api/applications/{app_id}/contacts", json={"name": name, "email": email})
    assert added.status_code == 201, added.text
    return int(added.json()["contact"]["id"])


async def _mint_token(client: AsyncClient, name: str = "agent") -> str:
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


def _mcp_payload(result) -> dict:
    import json

    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def _mcp_error_text(result) -> str:
    assert result.is_error, "expected a tool error"
    return result.content[0].text


async def _second_user_session_cookie(email: str = "second-referral@example.com") -> str:
    from fastapi.testclient import TestClient

    from src.api.main import app
    from tests._signin import sign_in

    sync_client = TestClient(app)
    cookie, _ = sign_in(sync_client, email)
    sync_client.close()
    return cookie


def _session_client(cookie: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie}
    )


# ═══════════════════════════════════════════════════════════════════════════
# Ledger entries
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_meeting_on_a_linked_contact_writes_ledger_and_timeline_and_never_moves_status(
    authenticated_async_context,
):
    soon = _soon()
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        cid = await _linked(client, app_id)
        resp = await client.post(
            f"/api/contacts/{cid}/outreach",
            json={
                "entry": "meeting", "channel": "video", "text": "Coffee chat about the data team",
                "occurred_at": "2026-10-14T15:00:00+00:00", "follow_up_on": soon,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert isinstance(body["event_id"], int)
        assert body["follow_up_on"] == soon
        assert body["outreach"]["follow_up_on"] == soon

        detail = (await client.get(f"/api/applications/{app_id}")).json()
        events = [e for e in detail["events"] if e["event_type"] == "contact_meeting"]
        assert len(events) == 1
        assert events[0]["payload"]["contact_id"] == cid
        assert events[0]["payload"]["channel"] == "video"
        assert events[0]["payload"]["contact_name"] == "Priya Shah"
        assert detail["status"] == "considering"
        assert detail["follow_up_on"] == soon
        contact = next(c for c in detail["contacts"] if c["id"] == cid)
        first = contact["outreach"]["interactions"][0]
        assert first["entry"] == "meeting"
        assert first["text"] == "Coffee chat about the data team"
        assert first["follow_up_on"] == soon
        assert contact["outreach"]["last_sent"] is None


@pytest.mark.asyncio
async def test_call_on_a_cold_contact_keeps_its_follow_up_on_the_entry(authenticated_async_context):
    soon = _soon()
    async with authenticated_async_context() as client:
        added = await _add_person(client, email="cold-call@x.example")
        cid = added.json()["contact"]["id"]
        resp = await client.post(
            f"/api/contacts/{cid}/outreach", json={"entry": "call", "channel": "call", "follow_up_on": soon}
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["event_id"] is None
        assert resp.json()["follow_up_on"] == soon

        person = (await client.get(f"/api/people?contact_id={cid}")).json()["person"]
        assert person["outreach"]["interactions"][0]["follow_up_on"] == soon

        cold_sent = await client.post(
            f"/api/contacts/{cid}/outreach", json={"entry": "sent", "channel": "email", "follow_up_on": soon}
        )
        assert cold_sent.status_code == 422, cold_sent.text
        assert "cold contacts have no follow-up date" in cold_sent.text


@pytest.mark.asyncio
async def test_entry_and_channel_are_closed_sets(authenticated_async_context):
    async with authenticated_async_context() as client:
        added = await _add_person(client, email="sets@x.example")
        cid = added.json()["contact"]["id"]
        url = f"/api/contacts/{cid}/outreach"

        bad_entry = await client.post(url, json={"entry": "lunch", "channel": "call"})
        assert bad_entry.status_code == 422
        assert "'referral_given'" in bad_entry.text

        bad_channel = await client.post(url, json={"entry": "meeting", "channel": "sms"})
        assert bad_channel.status_code == 422
        assert "CONTACT_LEDGER_CHANNELS" in bad_channel.text

        assert (await client.post(url, json={"entry": "meeting", "channel": "linkedin"})).status_code == 201
        assert (await client.post(url, json={"entry": "intro", "channel": "email"})).status_code == 201

        send_set = await client.post(url, json={"entry": "sent", "channel": "video"})
        assert send_set.status_code == 422
        assert "OUTREACH_CHANNELS" in send_set.text

        with_source = await client.post(
            url, json={"entry": "meeting", "channel": "in_person", "source": {"message_id": "<m1@x>"}}
        )
        assert with_source.status_code == 422
        assert "only meaningful on 'sent'/'reply'" in with_source.text


@pytest.mark.asyncio
async def test_referral_given_needs_a_linked_contact_but_asked_does_not(authenticated_async_context):
    async with authenticated_async_context() as client:
        added = await _add_person(client, email="cold-ref@x.example")
        cid = added.json()["contact"]["id"]
        given = await client.post(f"/api/contacts/{cid}/outreach", json={"entry": "referral_given", "channel": "email"})
        assert given.status_code == 422
        assert "referral_given needs a contact linked to the job" in given.text
        asked = await client.post(
            f"/api/contacts/{cid}/outreach", json={"entry": "referral_asked", "channel": "linkedin"}
        )
        assert asked.status_code == 201, asked.text
        assert asked.json()["event_id"] is None


@pytest.mark.asyncio
async def test_interactions_spend_their_own_budget_never_the_outreach_one(authenticated_async_context, monkeypatch):
    from src.api.main import app
    from src.core import settings

    monkeypatch.setattr(settings, "OUTREACH_MAX_PER_HOUR", 1)
    monkeypatch.setattr(settings, "CONTACT_INTERACTIONS_MAX_PER_HOUR", 1)
    monkeypatch.setattr(settings, "OUTREACH_ENTRIES_PER_CONTACT_MAX", 1)
    async with authenticated_async_context() as client:
        added = await _add_person(client, email="budget-int@x.example")
        cid = added.json()["contact"]["id"]
        token = await _mint_token(client)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
        ) as agent:
            url = f"/api/contacts/{cid}/outreach"
            assert (await agent.post(url, json={"entry": "sent", "channel": "email"})).status_code == 201
            assert (await agent.post(url, json={"entry": "meeting", "channel": "video"})).status_code == 201
            second_meeting = await agent.post(url, json={"entry": "meeting", "channel": "video"})
            assert second_meeting.status_code == 429
            assert "interaction rate limit exceeded" in second_meeting.text
            second_sent = await agent.post(url, json={"entry": "sent", "channel": "email"})
            assert second_sent.status_code == 429
            assert "outreach" in second_sent.text

        monkeypatch.setattr(settings, "CONTACT_INTERACTIONS_PER_CONTACT_MAX", 1)
        other = await _add_person(client, email="budget-cap@x.example", name="Sam Cole")
        oid = other.json()["contact"]["id"]
        assert (
            await client.post(f"/api/contacts/{oid}/outreach", json={"entry": "call", "channel": "call"})
        ).status_code == 201
        capped = await client.post(f"/api/contacts/{oid}/outreach", json={"entry": "call", "channel": "call"})
        assert capped.status_code == 429
        assert "CONTACT_INTERACTIONS_PER_CONTACT_MAX" in capped.text


@pytest.mark.asyncio
async def test_record_event_doors_for_interactions_and_referrals(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        linked_cid = await _linked(client, app_id)
        cold = await _add_person(client, email="cold-door@x.example", name="Sam Cole")
        cold_cid = cold.json()["contact"]["id"]

        no_contact = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "referral_given"})
        assert no_contact.status_code == 422
        assert "need contact_id" in no_contact.text
        with_payload = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "contact_call", "contact_id": linked_cid, "channel": "call", "payload": {"x": 1}},
        )
        assert with_payload.status_code == 422
        assert "not supported for outreach" in with_payload.text
        token = await _mint_token(client)

    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            call = _mcp_payload(
                await mcp.call_tool(
                    "record_event",
                    {
                        "event_type": "contact_call", "application_id": app_id, "contact_id": linked_cid,
                        "channel": "call", "detail": "Phone chat with the hiring manager",
                    },
                )
            )
            assert isinstance(call["event_id"], int)
            asked = _mcp_payload(
                await mcp.call_tool(
                    "record_event", {"event_type": "referral_asked", "contact_id": cold_cid, "channel": "linkedin"}
                )
            )
            assert asked["event_id"] is None
            missing = await mcp.call_tool("record_event", {"event_type": "contact_meeting", "application_id": app_id})
            assert "need contact_id" in _mcp_error_text(missing)
            listed = _mcp_payload(await mcp.call_tool("list_people", {"contact_id": linked_cid}))
            first = listed["person"]["outreach"]["interactions"][0]
            assert first["entry"] == "call"
            assert first["text"] == "Phone chat with the hiring manager"


# ═══════════════════════════════════════════════════════════════════════════
# Receipt, kit, stats, export
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_record_application_stores_the_referrer_on_the_receipt(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        app_a = await _bring(client, _ad(1))
        cid = await _linked(client, app_a)
        app_b = await _bring(client, _ad(2))
        b_cid = await _linked(client, app_b, name="Sam Cole", email="sam@x.example")
        app_b2 = await _bring(client, _ad(3))
        b2_cid = await _linked(client, app_b2, name="Lee Park", email="lee@x.example")

        ok = await client.post(
            f"/api/applications/{app_a}/receipt", json={"channel": "referral", "referred_by_contact_id": cid}
        )
        assert ok.status_code == 201, ok.text
        assert ok.json()["referred_by_contact_id"] == cid
        # No receipt_id in the legacy path: the application's receipt list carries it.
        receipt_id = ok.json()["receipt_id"]
        got = await client.get(f"/api/receipts/{receipt_id}")
        assert got.status_code == 200, got.text
        assert got.json()["referred_by_contact_id"] == cid

        before = (await client.get("/api/receipts")).json()
        wrong_job = await client.post(
            f"/api/applications/{app_b2}/receipt", json={"channel": "referral", "referred_by_contact_id": b_cid}
        )
        assert wrong_job.status_code == 422, wrong_job.text
        assert "must be one of your contacts linked to this application" in wrong_job.text

        cookie = await _second_user_session_cookie()
        async with _session_client(cookie) as other:
            theirs = await other.post("/api/contacts", json={"name": "Foreign Person", "company": "Northwind", "email": "f@y.example"})
            foreign_id = theirs.json()["contact"]["id"]
        foreign = await client.post(
            f"/api/applications/{app_b2}/receipt", json={"channel": "referral", "referred_by_contact_id": foreign_id}
        )
        assert foreign.status_code == 422, foreign.text
        assert "must be one of your contacts linked to this application" in foreign.text
        assert (await client.get("/api/receipts")).json() == before  # the refusals wrote nothing
        token = await _mint_token(client)

    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            reply = _mcp_payload(
                await mcp.call_tool(
                    "record_application", {"application_id": app_b2, "referred_by_contact_id": b2_cid}
                )
            )
            assert reply["referred_by_contact_id"] == b2_cid


@pytest.mark.asyncio
async def test_kit_names_the_referrer_by_current_name_and_forgets_a_retracted_one(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        cid = await _linked(client, app_id)
        patched = await client.patch(f"/api/contacts/{cid}", json={"name": "Priya S. Shah"})
        assert patched.status_code == 200, patched.text
        given = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "referral_given", "contact_id": cid, "channel": "email"},
        )
        assert given.status_code == 201, given.text
        eid = given.json()["event_id"]

        kit = await client.get(f"/api/applications/{app_id}/kit")
        assert kit.status_code == 200, kit.text
        assert kit.json()["application"]["referred_by"] == {"contact_id": cid, "name": "Priya S. Shah"}

        retract = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "note", "detail": "Referral fell through", "corrects_event_id": eid},
        )
        assert retract.status_code == 201, retract.text
        kit = await client.get(f"/api/applications/{app_id}/kit")
        assert kit.json()["application"]["referred_by"] is None


@pytest.mark.asyncio
async def test_stats_referrals_view_splits_referred_from_not(authenticated_async_context):
    async with authenticated_async_context() as client:
        async def event(app_id: int, **body: Any) -> dict[str, Any]:
            resp = await client.post(f"/api/applications/{app_id}/events", json=body)
            assert resp.status_code == 201, resp.text
            return resp.json()

        async def receipt(app_id: int, **body: Any) -> None:
            resp = await client.post(f"/api/applications/{app_id}/receipt", json={"channel": "referral", **body})
            assert resp.status_code == 201, resp.text

        a = await _bring(client, _ad(1))
        a_cid = await _linked(client, a)
        await event(a, event_type="referral_given", contact_id=a_cid, channel="email")
        await receipt(a)
        await event(a, event_type="replied")

        b = await _bring(client, _ad(2))
        b_cid = await _linked(client, b, name="Sam Cole", email="sam@x.example")
        await receipt(b, referred_by_contact_id=b_cid)

        c = await _bring(client, _ad(3))
        await receipt(c)

        d = await _bring(client, _ad(4))
        d_cid = await _linked(client, d, name="Lee Park", email="lee@x.example")
        await event(d, event_type="referral_asked", contact_id=d_cid, channel="linkedin")

        e = await _bring(client, _ad(5))
        e_cid = await _linked(client, e, name="Ann Fox", email="ann@x.example")
        given = await event(e, event_type="referral_given", contact_id=e_cid, channel="email")
        await event(e, event_type="note", detail="Referral fell through", corrects_event_id=given["event_id"])
        await receipt(e)

        resp = await client.get("/api/applications/stats?view=referrals")
        assert resp.status_code == 200, resp.text
        refs = resp.json()["referrals"]
        assert refs["n"] == 5
        assert refs["referred"]["applied"] == 2
        assert refs["referred"]["replied"] == 1
        assert refs["referred"]["reply_rate"] == 0.5
        assert refs["referred"]["small_sample"] is True
        assert refs["not_referred"]["applied"] == 2
        assert refs["not_referred"]["replied"] == 0
        assert refs["not_referred"]["reply_rate"] == 0.0
        assert refs["referral_asked"] == 1
        assert "never rank" in refs["note"].lower()

        sites = await client.get("/api/applications/stats?view=sites")
        assert sites.status_code == 200, sites.text
        assert sites.json()["referrals"] is None


@pytest.mark.asyncio
async def test_export_without_text_strips_interaction_notes(authenticated_async_context):
    async with authenticated_async_context() as client:
        cold = await _add_person(client, email="cold-export-int@x.example")
        cid = cold.json()["contact"]["id"]
        posted = await client.post(
            f"/api/contacts/{cid}/outreach",
            json={"entry": "meeting", "channel": "in_person", "text": "Private notes"},
        )
        assert posted.status_code == 201, posted.text
        export = await client.get("/api/applications/export?include_text=false")
        assert export.status_code == 200, export.text
        contact = next(c for c in export.json()["unlinked_contacts"] if c["id"] == cid)
        interaction = contact["outreach"]["interactions"][0]
        assert interaction["entry"] == "meeting"
        assert interaction["text"] == ""
        assert "Private notes" not in str(export.json())


# ═══════════════════════════════════════════════════════════════════════════
# Migration and settings
# ═══════════════════════════════════════════════════════════════════════════


async def _has_column(path: str, table: str, column: str) -> bool:
    async with pg.connect(path) as conn:
        cur = await conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = ? AND column_name = ?",
            (table, column),
        )
        return await cur.fetchone() is not None


@pytest.mark.asyncio
async def test_migration_0073_up_down(tmp_path):
    path = str(tmp_path / f"m73_{uuid.uuid4().hex[:8]}.db")
    db = JobDatabase(path)
    await db.init_db()
    await db.close()
    await runner.up(path, target=STEM73)
    assert await _has_column(path, "contact_outreach", "follow_up_on")
    assert await _has_column(path, "application_receipts", "referred_by_contact_id")

    assert await runner.down(path) == STEM73
    assert await _has_column(path, "contact_outreach", "follow_up_on")
    assert not await _has_column(path, "application_receipts", "referred_by_contact_id")

    await runner.up(path, target=STEM73)
    assert await _has_column(path, "application_receipts", "referred_by_contact_id")


def test_new_event_types_are_note_family():
    from src.core import settings

    new = {"contact_meeting", "contact_call", "contact_intro", "referral_asked", "referral_given"}
    for name in new:
        assert name in settings.APPLICATION_NOTE_EVENT_TYPES
        assert name not in settings.APPLICATION_STATUS_EVENT_TYPES
        assert name not in settings.FEEDBACK_FUNNEL_RANK
    assert set(settings.CONTACT_LEDGER_EVENT_TYPES.values()) - {"outreach_sent", "outreach_replied"} == new
    assert settings.CONTACT_LEDGER_CHANNELS == settings.OUTREACH_CHANNELS + settings.CONTACT_INTERACTION_CHANNELS
    assert settings.OUTREACH_CHANNELS == ("linkedin", "email", "other")
