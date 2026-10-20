"""Bulk-apply wave 2 (Q1, Q2, M1, Q3, F4): the batch queue preflight, the queue summary and
"left today", the closed reason on ``unqueued``, "run 360 batch N" and ``answer_corrected``.

Pure tests need no database; the rest go through the real doors (HTTP and MCP) on the real
Postgres. Helpers are copied, never imported from another test module (schema isolation).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.core import settings
from src.services.applications import queue_preflight
from src.services.profile import assistant_settings as rules
from src.services.profile.models import AssistantSettings, UserPreferences

FORM = "https://careers.northwind.example/apply/7"
PREFLIGHT_KEYS = {
    "kit_missing", "has_cv", "duplicate_flag", "company_left", "avoided", "blocked_count", "queued_days", "hold",
}
SUMMARY_KEYS = {
    "queued", "ready_to_send", "asked", "blocked", "submitted_today", "daily_cap", "daily_left", "removed_today",
}


def _ad(n: int, company: str = "Northwind") -> dict[str, Any]:
    return {
        "title": f"Data Engineer {n}", "company": company, "location": "Remote",
        "apply_url": f"https://northwind.example/careers/{n}",
        "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
    }


def _eff(**kw: Any) -> rules.EffectiveSettings:
    return rules.effective(AssistantSettings(**kw))


def _counts(today: int = 0, company: int = 0) -> rules.SubmitCounts:
    return rules.SubmitCounts(submitted_today=today, applied_since_auto_on=1, company_applied_in_window=company)


# ═══════════════════════════════════════════════════════════════════════════
# Pure
# ═══════════════════════════════════════════════════════════════════════════


def test_daily_left_and_company_left_null_means_no_cap_and_never_negative():
    assert rules.daily_left(_eff(), _counts(today=9)) is None
    assert rules.daily_left(_eff(daily_cap=10), _counts(today=3)) == 7
    assert rules.daily_left(_eff(daily_cap=10), _counts(today=12)) == 0
    assert rules.company_left(_eff(), 5) is None
    assert rules.company_left(_eff(company_cap=3), 1) == 2
    assert rules.company_left(_eff(company_cap=3), 7) == 0


def test_unqueue_payload_is_empty_or_one_closed_reason():
    assert queue_preflight.UNQUEUE_REASONS == (
        "blocked", "duplicate", "avoided_company", "cap_reached", "closed_posting", "user_removed", "other",
    )
    assert queue_preflight.check_unqueue_payload({}) == []
    for reason in queue_preflight.UNQUEUE_REASONS:
        assert queue_preflight.check_unqueue_payload({"reason": reason}) == []
    assert queue_preflight.check_unqueue_payload({"reason": "tired"})
    assert queue_preflight.check_unqueue_payload({"reason": "blocked", "x": 1})
    assert queue_preflight.check_unqueue_payload({"reason": 5})


def test_answer_corrected_is_a_note_event_with_a_label():
    assert "answer_corrected" in settings.APPLICATION_NOTE_EVENT_TYPES
    assert "answer_corrected" not in settings.APPLICATION_STATUS_EVENT_TYPES
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib"
    assert "answer_corrected" in json.loads((root / "event-types.json").read_text())["note"]
    assert "answer_corrected:" in (root / "event-labels.ts").read_text()


def test_recipes_and_instructions_carry_the_new_lines():
    from src.api import mcp_server
    from src.api.routes.recipes import load_recipe

    batch = " ".join(load_recipe("batch").text.split())
    assert "preflight" in batch and "ONCE" in batch and "kit_missing" in batch
    assert "blocked_count` >= 2" in batch and "Never reorder by fit" in batch
    assert "run 360 batch 5" in batch and "daily_left" in batch
    assert "queue_summary" in batch and "removed_today" in batch
    assert len(load_recipe("batch").text.splitlines()) <= 70
    apply_text = " ".join(load_recipe("apply").text.split())
    assert "answer_corrected" in apply_text and "remember this for next time?" in apply_text
    assert "only on a yes" in apply_text and "user_said" in apply_text
    assert len(load_recipe("apply").text.splitlines()) <= 70
    assert 'run 360 batch N' in mcp_server.INSTRUCTIONS
    assert "run 360 <daily|hunt|batch>" in mcp_server.INSTRUCTIONS
    assert len(mcp_server.INSTRUCTIONS) <= 2000


# ═══════════════════════════════════════════════════════════════════════════
# Real-database helpers
# ═══════════════════════════════════════════════════════════════════════════


def _seed_profile(user_id: str, avoid: list[str] | None = None) -> None:
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"], avoid_companies=avoid or []),
        ),
        user_id,
        source_action="cv_upload",
    )


async def _bring(client: AsyncClient, ad: dict[str, Any]) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, **body: Any):
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, **body})


async def _queue(client: AsyncClient, app_id: int) -> None:
    assert (await _event(client, app_id, "queued")).status_code == 201


async def _queued(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/applications", params={"queued": True})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _by_id(body: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {r["id"]: r for r in body["applications"]}


async def _fill(client: AsyncClient, app_id: int) -> None:
    ok = await _event(client, app_id, "form_filled", payload={"form_url": FORM, "fields_count": 1})
    assert ok.status_code == 201, ok.text
    swept = await _event(client, app_id, "form_swept", payload={"fields_checked": 1, "fixed": 0})
    assert swept.status_code == 201, swept.text


async def _mint_token(client: AsyncClient) -> str:
    resp = await client.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _mcp_client(token: str):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


def _payload(result) -> dict:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


# ═══════════════════════════════════════════════════════════════════════════
# Q1: the preflight on every queued row
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_every_queued_row_carries_a_preflight_and_other_lists_do_not(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        a = await _bring(client, _ad(1))
        b = await _bring(client, _ad(2, company="Contoso"))
        await _queue(client, a)
        await _queue(client, b)
        body = await _queued(client)
        rows = _by_id(body)
        assert [r["id"] for r in body["applications"]] == [a, b], "first in first out, never reordered"
        for row in rows.values():
            assert set(row["preflight"]) == PREFLIGHT_KEYS
        p = rows[a]["preflight"]
        assert p["has_cv"] is False and p["avoided"] is False and p["blocked_count"] == 0
        assert p["duplicate_flag"] == "" and p["company_left"] is None and p["hold"] is None
        assert p["queued_days"] == 0
        assert isinstance(p["kit_missing"], list)
        # a plain list has neither
        plain = (await client.get("/api/applications")).json()
        assert plain["queue_summary"] is None
        assert all(r["preflight"] is None for r in plain["applications"])


@pytest.mark.asyncio
async def test_preflight_cv_company_left_avoided_and_duplicate(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id, avoid=["Initech"])
        target = await _bring(client, _ad(1))
        avoided = await _bring(client, _ad(2, company="Initech"))
        sent = await _bring(client, _ad(3))
        await _queue(client, target)
        await _queue(client, avoided)
        saved = await client.post(f"/api/applications/{target}/artifacts", json={"kind": "cv", "text": "My CV"})
        assert saved.status_code == 201, saved.text
        assert (await _event(client, sent, "applied")).status_code == 201
        rows = _by_id(await _queued(client))
        assert rows[target]["preflight"]["has_cv"] is True
        assert rows[target]["preflight"]["duplicate_flag"] == "same_company"
        assert rows[avoided]["preflight"]["avoided"] is True
        assert rows[avoided]["preflight"]["has_cv"] is False
        # a company limit of 3: one other application at Northwind is applied, so 2 are left
        patched = await client.patch(
            "/api/profile", json={"edits": [{"path": "assistant_settings.company_cap", "value": 3}]}
        )
        assert patched.status_code == 200, patched.text
        rows = _by_id(await _queued(client))
        assert rows[target]["preflight"]["company_left"] == 2
        assert rows[avoided]["preflight"]["company_left"] == 3


@pytest.mark.asyncio
async def test_preflight_kit_missing_names_what_memory_lacks_for_the_job_country(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        no_country = await _bring(client, _ad(1))
        gb = await _bring(client, {**_ad(2, company="Contoso"), "location": "London, UK", "country": "GB"})
        await _queue(client, no_country)
        await _queue(client, gb)
        rows = _by_id(await _queued(client))
        assert rows[no_country]["preflight"]["kit_missing"] == ["job_country"]
        gb_missing = rows[gb]["preflight"]["kit_missing"]
        # the seeded profile has no UK right to work or salary: exactly those, from the job's country
        assert gb_missing == [
            "right_to_work.GB.work_authorization", "right_to_work.GB.needs_sponsorship", "salary.GB",
        ]


@pytest.mark.asyncio
async def test_blocked_count_and_hold_and_queued_days(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, _ad(1))
        await _queue(client, app_id)
        blocked = {"reason": "captcha", "step": "submit", "page_host": "jobs.example.com", "detail": "CAPTCHA"}
        for _ in range(2):
            assert (await _event(client, app_id, "blocked", payload=blocked)).status_code == 201
        pre = _by_id(await _queued(client))[app_id]["preflight"]
        assert pre["blocked_count"] == 2
        assert pre["hold"] is None and pre["queued_days"] == 0
        # a kit read is a live hold; a queued event dated three days back reads queued_days 3
        kit = await client.get(f"/api/applications/{app_id}/kit", params={"session": "batch-run"})
        assert kit.status_code == 200, kit.text
        from src.repositories import pgsync

        conn = pgsync.connect(str(settings.DB_PATH))
        try:
            conn.execute(
                "UPDATE application_events SET recorded_at = ?, occurred_at = ? "
                "WHERE application_id = ? AND event_type = 'queued'",
                ((datetime.now(timezone.utc) - timedelta(days=3, hours=1)).isoformat(),) * 2 + (app_id,),
            )
            conn.commit()
        finally:
            conn.close()
        pre = _by_id(await _queued(client))[app_id]["preflight"]
        assert pre["queued_days"] == 3
        assert pre["hold"] is not None and pre["hold"]["session"] == "batch-run"
        # the other worker's recipe rule: blocked_count >= 2 leaves the queue, with a closed reason
        out = await _event(client, app_id, "unqueued", payload={"reason": "blocked"})
        assert out.status_code == 201, out.text
        assert (await _queued(client))["applications"] == []


@pytest.mark.asyncio
async def test_preflight_and_summary_are_per_user(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps
    from src.services.applications import spine

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        a = await _bring(client, _ad(1))
        await _queue(client, a)
        db = await api_deps.get_db()
        other = await spine.list_applications(db, "someone-else", queued=True)
        assert other["applications"] == [] and other["queue_summary"]["queued"] == 0
        mine = await spine.list_applications(db, fixture_user_id, queued=True)
        assert [r["id"] for r in mine["applications"]] == [a] and mine["queue_summary"]["queued"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# Q2: the queue summary and "left today"
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_queue_summary_counts_the_whole_open_queue(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        ids = [await _bring(client, _ad(n, company=f"Company{n}")) for n in range(1, 6)]
        for i in ids:
            await _queue(client, i)
        await _fill(client, ids[0])                                       # ready to send
        ask = await client.post("/api/asks", json={"question": "Notice period?", "application_id": ids[1]})
        assert ask.status_code in (200, 201), ask.text                    # asked
        blocked = {"reason": "captcha", "step": "s", "page_host": "j.example.com", "detail": "x"}
        assert (await _event(client, ids[2], "blocked", payload=blocked)).status_code == 201   # blocked
        assert (await _event(client, ids[3], "unqueued", payload={"reason": "duplicate"})).status_code == 201
        done = await _bring(client, _ad(9, company="Elsewhere"))
        assert (await _event(client, done, "applied")).status_code == 201     # one submit today
        assert (await client.patch(
            "/api/profile", json={"edits": [{"path": "assistant_settings.daily_cap", "value": 10}]}
        )).status_code == 200
        body = await _queued(client)
        s = body["queue_summary"]
        assert set(s) == SUMMARY_KEYS
        assert s["queued"] == 4 and len(body["applications"]) == 4
        assert s["ready_to_send"] == 1 and s["asked"] == 1 and s["blocked"] == 1
        assert s["submitted_today"] == 1 and s["daily_cap"] == 10 and s["daily_left"] == 9
        assert s["removed_today"] == {"duplicate": 1}
        # a page of 1 still summarises the whole queue
        page = (await client.get("/api/applications", params={"queued": True, "limit": 1})).json()
        assert len(page["applications"]) == 1 and page["queue_summary"]["queued"] == 4


@pytest.mark.asyncio
async def test_queue_summary_without_a_cap_says_null(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        s = (await _queued(client))["queue_summary"]
        assert s["queued"] == 0 and s["daily_cap"] is None and s["daily_left"] is None
        assert s["removed_today"] == {}


@pytest.mark.asyncio
async def test_check_submit_returns_daily_left_and_company_left(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        target = await _bring(client, _ad(1))
        other = await _bring(client, _ad(2))
        await _fill(client, target)
        body = (await client.get(f"/api/applications/{target}/submit-check", params={"form_url": FORM})).json()
        assert body["daily_left"] is None and body["company_left"] is None
        assert (await _event(client, other, "applied")).status_code == 201
        patched = await client.patch("/api/profile", json={"edits": [
            {"path": "assistant_settings.daily_cap", "value": 5},
            {"path": "assistant_settings.company_cap", "value": 4},
        ]})
        assert patched.status_code == 200, patched.text
        body = (await client.get(f"/api/applications/{target}/submit-check", params={"form_url": FORM})).json()
        assert body["daily_left"] == 4 and body["company_left"] == 3


# ═══════════════════════════════════════════════════════════════════════════
# M1: the closed reason on unqueued
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_unqueued_takes_an_optional_closed_reason(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        a, b, c = [await _bring(client, _ad(n, company=f"Co{n}")) for n in (1, 2, 3)]
        for i in (a, b, c):
            await _queue(client, i)
        bad = await _event(client, a, "unqueued", payload={"reason": "tired"})
        assert bad.status_code == 422
        extra = await _event(client, a, "unqueued", payload={"reason": "blocked", "note": "x"})
        assert extra.status_code == 422
        assert (await _event(client, a, "unqueued", payload={"reason": "blocked"})).status_code == 201
        assert (await _event(client, b, "unqueued", payload={"reason": "closed_posting"})).status_code == 201
        assert (await _event(client, c, "unqueued", detail="no reason given")).status_code == 201
        s = (await _queued(client))["queue_summary"]
        assert s["removed_today"] == {"blocked": 1, "closed_posting": 1, "other": 1}
        # `queued` still takes no payload
        again = await _event(client, a, "queued", payload={"reason": "blocked"})
        assert again.status_code == 422


@pytest.mark.asyncio
async def test_a_corrected_unqueued_is_not_counted(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        a = await _bring(client, _ad(1))
        await _queue(client, a)
        resp = await _event(client, a, "unqueued", payload={"reason": "duplicate"})
        event_id = resp.json()["event_id"]
        assert (await _queued(client))["queue_summary"]["removed_today"] == {"duplicate": 1}
        fix = await _event(client, a, "unqueued", corrects_event_id=event_id, payload={"reason": "other"})
        assert fix.status_code == 201, fix.text
        s = (await _queued(client))["queue_summary"]
        assert s["removed_today"] == {"other": 1}


# ═══════════════════════════════════════════════════════════════════════════
# F4: answer_corrected
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"key": "notice_period", "was_source": "written"}, 201),
        ({"question": "Why this role?", "was_source": "guessed"}, 201),
        ({"key": "k", "question": "q", "was_source": "written"}, 201),
        ({"was_source": "written"}, 422),
        ({"key": "k"}, 422),
        ({"key": "k", "was_source": "memory"}, 422),
        ({"key": "k", "was_source": "written", "answer": "x"}, 422),
        ({"key": "", "was_source": "written"}, 422),
        ({"key": 5, "was_source": "written"}, 422),
        ({"key": "k" * 301, "was_source": "written"}, 422),
        ({}, 422),
    ],
)
async def test_answer_corrected_payload(authenticated_async_context, fixture_user_id, payload, status):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, _ad(1))
        resp = await _event(client, app_id, "answer_corrected", payload=payload)
        assert resp.status_code == status, resp.text


@pytest.mark.asyncio
async def test_answer_corrected_is_a_note_and_saves_nothing(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, _ad(1))
        before = (await client.get(f"/api/applications/{app_id}")).json()["status"]
        resp = await _event(client, app_id, "answer_corrected", payload={"key": "notice_period", "was_source": "written"})
        assert resp.status_code == 201
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        assert detail["status"] == before
        assert "answer_corrected" in [e["event_type"] for e in detail["events"]]
        from src.services.profile.storage import load_profile

        assert not (load_profile(fixture_user_id).user_info.answers or [])


# ═══════════════════════════════════════════════════════════════════════════
# HTTP and MCP parity
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mcp_matches_http_for_preflight_summary_and_check_submit(
    authenticated_async_context, fixture_user_id
):
    pytest.importorskip("mcp")
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
        a = await _bring(client, _ad(1))
        b = await _bring(client, _ad(2, company="Contoso"))
        await _queue(client, a)
        await _queue(client, b)
        await _fill(client, a)
        http_list = await _queued(client)
        http_gate = (await client.get(f"/api/applications/{a}/submit-check", params={"form_url": FORM})).json()
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            listed = _payload(await mcp.call_tool("list_applications", {"queued": True}))
            assert listed["queue_summary"] == http_list["queue_summary"]
            assert {r["id"]: r["preflight"] for r in listed["applications"]} == {
                r["id"]: r["preflight"] for r in http_list["applications"]
            }
            gate = _payload(await mcp.call_tool("check_submit", {"application_id": a, "form_url": FORM}))
            assert gate["daily_left"] == http_gate["daily_left"] and gate["company_left"] == http_gate["company_left"]
            ok = _payload(await mcp.call_tool("record_event", {
                "application_id": b, "event_type": "unqueued", "payload": {"reason": "user_removed"},
            }))
            assert ok["event_type"] == "unqueued"
            refused = await mcp.call_tool("record_event", {
                "application_id": a, "event_type": "unqueued", "payload": {"reason": "nope"},
            })
            assert refused.is_error
            fixed = _payload(await mcp.call_tool("record_event", {
                "application_id": a, "event_type": "answer_corrected",
                "payload": {"question": "Why us?", "was_source": "guessed"},
            }))
            assert fixed["event_type"] == "answer_corrected"
            summary = _payload(await mcp.call_tool("list_applications", {"queued": True}))["queue_summary"]
            assert summary["removed_today"] == {"user_removed": 1} and summary["queued"] == 1


@pytest.mark.asyncio
async def test_an_earlier_application_found_in_gmail_is_recorded_then_unqueued_as_duplicate(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        a = await _bring(client, _ad(1, company="Contoso"))
        b = await _bring(client, _ad(2, company="Fabrikam"))
        await _queue(client, a)
        await _queue(client, b)
        receipt = await client.post(f"/api/applications/{a}/receipt", json={
            "channel": "company_site", "applied_at": "2026-08-14T09:30:00+00:00",
            "note": "earlier application found in Gmail: no-reply@contoso.example | Contoso: application received",
        })
        assert receipt.status_code == 201, receipt.text
        repeat = await _event(client, a, "applied", source={
            "message_id": "<contoso-0814@mail.example>", "sender": "no-reply@contoso.example",
            "subject": "Contoso: application received", "received_at": "2026-08-14T09:31:00+00:00",
        })
        assert repeat.status_code == 201, repeat.text
        assert repeat.json()["event_type"] == "note"
        assert any("submission confirmation" in w for w in repeat.json()["warnings"])
        closed = await _event(client, a, "unqueued", payload={"reason": "duplicate"})
        assert closed.status_code == 201, closed.text
        detail = (await client.get(f"/api/applications/{a}")).json()
        assert detail["status"] == "applied"
        assert detail["proof"]["level"] == "email"
        applied = [e for e in detail["events"] if e["event_type"] == "applied"]
        assert applied and applied[0]["occurred_at"].startswith("2026-08-14T09:30")
        body = await _queued(client)
        s = body["queue_summary"]
        assert s["queued"] == 1 and [r["id"] for r in body["applications"]] == [b]
        assert s["removed_today"] == {"duplicate": 1}
        assert s["submitted_today"] == 1
        again = await _event(client, a, "unqueued", payload={"reason": "duplicate"})
        assert again.status_code == 409
