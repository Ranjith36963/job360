"""Feedback loop slice 1 - "record what happened": hired / offer_declined / post_mortem, outcome
payload shapes, the `pasted` source kind, automatic ghosting at GHOSTED_AFTER_DAYS, lesson removal.
Helpers are copied, never imported from another test module (schema isolation)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.core import settings
from src.services.applications.status import replay_status

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}


async def _bring(client: AsyncClient, n: int = 0) -> int:
    ad = {**_AD, "title": f"Data Engineer {n}", "apply_url": f"https://northwind.example/careers/{n}"}
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _post(client: AsyncClient, app_id: int, event_type: str, **extra: Any) -> Any:
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, **extra})


async def _event(client: AsyncClient, app_id: int, event_type: str, **extra: Any) -> dict[str, Any]:
    resp = await _post(client, app_id, event_type, **extra)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _detail(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


async def _doctor(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/doctor")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── A1 statuses ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hired_is_a_rung_above_offer_and_closes_the_application(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "applied")
        await _event(client, app_id, "offer")
        res = await _event(client, app_id, "hired")
        assert res["status"] == "hired"
        detail = await _detail(client, app_id)
        assert detail["status"] == "hired"
        assert detail["next_step"]["code"] == "lesson", detail["next_step"]  # closed, lesson not yet flagged
        # a late `offer` record never slides it back
        assert (await _event(client, app_id, "offer"))["status"] == "hired"
        # silence never overrides hired
        assert (await _event(client, app_id, "ghosted"))["status"] == "hired"
        assert replay_status(detail["events"]) == "hired"
    assert settings.APPLICATION_FOLLOW_UP_CLOSED_STATUSES[-2:] == ("hired", "offer_declined")


@pytest.mark.asyncio
async def test_offer_declined_is_terminal_and_projects_to_rejected_stage(authenticated_async_context):
    from src.services.applications.status import STATUS_TO_STAGE, TERMINAL_STATUSES

    assert STATUS_TO_STAGE["hired"] == "offer" and STATUS_TO_STAGE["offer_declined"] == "rejected"
    assert "offer_declined" in TERMINAL_STATUSES
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "applied")
        await _event(client, app_id, "offer", occurred_at=_ago(3))
        res = await _event(client, app_id, "offer_declined", occurred_at=_ago(1))
        assert res["status"] == "offer_declined"
        assert (await _event(client, app_id, "ghosted"))["status"] == "offer_declined"
        detail = await _detail(client, app_id)
        assert detail["next_step"]["code"] == "lesson"


# ── A2 payloads ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_good_outcome_payload_is_stored_verbatim_with_unicode(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "applied")
        words = "Merci pour votre candidature — nous avons choisi un autre profil. 谢谢 ☕"
        res = await _event(
            client, app_id, "rejected",
            payload={
                "verbatim_feedback": words, "reasons": ["  too junior ", "", "no visa"], "stage": "after interview 2",
                "reply_type": "rejection", "evidence": ["we have chosen another candidate"], "confidence": "high",
                "custom_key": {"kept": 1},
            },
        )
        events = (await _detail(client, app_id))["events"]
        stored = next(e for e in events if e["id"] == res["event_id"])["payload"]
    assert stored["verbatim_feedback"] == words
    assert stored["reasons"] == ["too junior", "no visa"]
    assert stored["stage"] == "after interview 2"
    assert stored["reply_type"] == "rejection"
    assert stored["evidence"] == ["we have chosen another candidate"]
    assert stored["confidence"] == "high"
    assert stored["custom_key"] == {"kept": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type,payload,fragment",
    [
        ("rejected", {"verbatim_feedback": "x" * 2001}, "verbatim_feedback"),
        ("rejected", {"verbatim_feedback": 5}, "verbatim_feedback"),
        ("hired", {"reasons": "not a list"}, "reasons"),
        ("rejected", {"reasons": [1, 2]}, "reasons"),
        ("rejected", {"reasons": [f"r{i}" for i in range(11)]}, "reasons"),
        ("withdrawn", {"reasons": ["y" * 121]}, "reasons"),
        ("offer_declined", {"stage": "s" * 61}, "stage"),
        ("ghosted", {"stage": ["screen"]}, "stage"),
        ("replied", {"reply_type": "t" * 41}, "reply_type"),
        ("interview_requested", {"evidence": ["e"] * 6}, "evidence"),
        ("offer", {"evidence": ["e" * 301]}, "evidence"),
        ("rejected", {"confidence": "certain"}, "confidence"),
        ("post_mortem", {"what_was_sent": ["a"] * 11}, "what_was_sent"),
        ("post_mortem", {"what_to_change": ["a" * 301]}, "what_to_change"),
        ("post_mortem", {"proposed_lessons": ["a"] * 6}, "proposed_lessons"),
        ("post_mortem", {"outcome_event_id": "7"}, "outcome_event_id"),
        ("post_mortem", {"outcome_event_id": 99999999}, "outcome_event_id"),
    ],
)
async def test_bad_outcome_payload_is_422_naming_the_key(authenticated_async_context, event_type, payload, fragment):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _post(client, app_id, event_type, payload=payload)
        assert resp.status_code == 422, resp.text
        assert fragment in resp.json()["detail"]
        assert (await _detail(client, app_id))["status"] == "considering"


@pytest.mark.asyncio
async def test_post_mortem_links_an_outcome_event_on_the_same_application(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        other = await _bring(client, 2)
        await _event(client, app_id, "applied")
        outcome = await _event(client, app_id, "rejected")
        foreign = await _event(client, other, "applied")
        bad = await _post(client, app_id, "post_mortem", payload={"outcome_event_id": foreign["event_id"]})
        assert bad.status_code == 422 and "outcome_event_id" in bad.json()["detail"]
        pm = await _event(
            client, app_id, "post_mortem", detail="a guess, not a cause",
            payload={
                "outcome_event_id": outcome["event_id"], "what_was_sent": ["CV v2"],
                "what_to_change": ["lead with Python"], "proposed_lessons": ["quantify results"],
            },
        )
        events = (await _detail(client, app_id))["events"]
    stored = next(e for e in events if e["id"] == pm["event_id"])
    assert stored["event_type"] == "post_mortem"
    assert stored["payload"]["outcome_event_id"] == outcome["event_id"]
    assert stored["payload"]["proposed_lessons"] == ["quantify results"]
    assert pm["status"] == "rejected"  # a note never moves the status


# ── A3 pasted source ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pasted_source_dedupes_on_its_own_message_id(authenticated_async_context):
    src = {"kind": "pasted", "message_id": "pasted:hr@northwind.example:Your application:2026-10-15",
           "sender": "hr@northwind.example", "subject": "Your application"}
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "applied")
        first = await _event(client, app_id, "rejected", source=src)
        second = await _event(client, app_id, "rejected", source=src)
        bad = await _post(client, app_id, "note", source={**src, "kind": "sms", "message_id": "m2"})
        events = (await _detail(client, app_id))["events"]
    assert first["already_existed"] is False
    assert second["already_existed"] is True and second["event_id"] == first["event_id"]
    assert bad.status_code == 422 and "kind" in bad.json()["detail"]
    kept = [e for e in events if e["event_type"] == "rejected"]
    assert len(kept) == 1 and kept[0]["source"]["kind"] == "pasted"


# ── A4 automatic ghosting ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_doctor_ghosts_only_silent_applied_applications(authenticated_async_context):
    assert settings.GHOSTED_AFTER_DAYS == 45
    async with authenticated_async_context() as client:
        young = await _bring(client, 1)
        old = await _bring(client, 2)
        replied = await _bring(client, 3)
        simulated = await _bring(client, 4)
        offered = await _bring(client, 5)
        considering = await _bring(client, 6)
        await _event(client, young, "applied", occurred_at=_ago(44))
        await _event(client, old, "applied", occurred_at=_ago(46))
        await _event(client, replied, "applied", occurred_at=_ago(60))
        await _event(client, replied, "replied", occurred_at=_ago(10))
        await _event(client, simulated, "simulation")
        await _event(client, simulated, "applied", occurred_at=_ago(60))
        await _event(client, offered, "applied", occurred_at=_ago(70))
        await _event(client, offered, "offer", occurred_at=_ago(60))

        doc = await _doctor(client)
        assert doc["ghosted_now"] == [old]
        again = await _doctor(client)
        assert again["ghosted_now"] == []

        assert (await _detail(client, old))["status"] == "ghosted"
        for app_id, status in (
            (young, "applied"), (replied, "replied"), (simulated, "applied"),
            (offered, "offer"), (considering, "considering"),
        ):
            assert (await _detail(client, app_id))["status"] == status
        ghost = next(e for e in (await _detail(client, old))["events"] if e["event_type"] == "ghosted")
    assert ghost["recorded_by"] == "system"
    assert ghost["detail"] == "No answer 45 days after applying — marked automatically"
    assert ghost["payload"]["auto"] is True and ghost["payload"]["days"] == 45


@pytest.mark.asyncio
async def test_reply_after_auto_ghost_moves_it_off_ghosted(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "applied", occurred_at=_ago(50))
        assert (await _doctor(client))["ghosted_now"] == [app_id]
        res = await _event(client, app_id, "replied")
        assert res["status"] == "replied"
        detail = await _detail(client, app_id)
        assert detail["status"] == "replied"
        assert replay_status(detail["events"]) == "replied"
        # a replied application is never swept again
        assert (await _doctor(client))["ghosted_now"] == []


@pytest.mark.asyncio
async def test_stats_never_sweeps_so_doctor_reports_every_new_ghost(authenticated_async_context):
    """Only doctor sweeps: a stats read first must not swallow the id doctor reports in ghosted_now."""
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "applied", occurred_at=_ago(46))
        resp = await client.get("/api/applications/stats")
        assert resp.status_code == 200, resp.text
        assert (await _detail(client, app_id))["status"] == "applied"
        assert (await _doctor(client))["ghosted_now"] == [app_id]


@pytest.mark.asyncio
async def test_mcp_doctor_reports_ghosted_now_like_the_route(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    from src.api import mcp_server

    tools = mcp_server.build_server()._tool_manager
    user = mcp_server.CurrentUser(
        id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc",
    )
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _event(client, app_id, "applied", occurred_at=_ago(47))
        ctx = mcp_server._current_user.set(user)
        try:
            via_tool = await tools.get_tool("doctor").fn()
        finally:
            mcp_server._current_user.reset(ctx)
        route = await _doctor(client)
    assert via_tool["ghosted_now"] == [app_id]
    assert route["ghosted_now"] == []
    skip = {"ghosted_now", "auth"}  # auth differs by door (token vs session cookie)
    assert {k: v for k, v in via_tool.items() if k not in skip} == {k: v for k, v in route.items() if k not in skip}


# ── A5 lesson removal ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_correcting_note_removes_a_lesson_from_get_profile(authenticated_async_context, fixture_user_id):
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace")),
        fixture_user_id, source_action="cv_upload",
    )
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        keep = await _event(client, app_id, "lesson", detail="Keep this one.")
        gone = await _event(client, app_id, "lesson", detail="Drop this one.")
        before = (await client.get("/api/profile")).json()["lessons"]
        assert {r["detail"] for r in before} == {"Keep this one.", "Drop this one."}
        await _event(client, app_id, "note", detail="removed: owner disagrees", corrects_event_id=gone["event_id"])
        after = (await client.get("/api/profile")).json()["lessons"]
        listed = (await client.get("/api/applications/lessons")).json()
    assert [r["detail"] for r in after] == ["Keep this one."]
    assert [r["event_id"] for r in after] == [keep["event_id"]]
    assert [r["detail"] for r in listed["lessons"]] == ["Keep this one."]
