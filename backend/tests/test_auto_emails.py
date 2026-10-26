"""Feedback loop slice 2 - automatic employer emails: the four event types and their payloads, the
``auto_employer_emails`` setting (on = riskier, off = safer), the pure gate ``decide``, the due list and
the violations watch, ``GET /api/applications/{id}/auto-email-check`` + the MCP ``check_auto_email`` tool,
and the doctor ``auto_emails`` block. Helpers are copied, never imported from another test module."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.applications import auto_emails as ae
from src.services.profile import assistant_settings as rules
from src.services.profile.models import AssistantSettings

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
AUTO = "assistant_settings.auto_employer_emails"
_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}
_SENT = {"to": "rita@northwind.example", "text": "Hello Rita, thank you.", "to_kind": "person"}


# ═══════════════════════════════════════════════════════════════════════════
# The pure gate
# ═══════════════════════════════════════════════════════════════════════════


def _cfg(**kw: Any) -> rules.EffectiveSettings:
    return rules.effective(AssistantSettings(**kw))


def _ev(n: int, event_type: str, days_ago: float, **kw: Any) -> ae.AutoEvent:
    return ae.AutoEvent(id=n, event_type=event_type, when=NOW - timedelta(days=days_ago), **kw)


def _facts(status: str, *events: ae.AutoEvent, sent_today: int = 0) -> ae.AutoEmailFacts:
    return ae.AutoEmailFacts(application_id=1, status=status, events=tuple(events), sent_today=sent_today)


def _decide(kind: str, facts: ae.AutoEmailFacts, on: bool = True, **cfg: Any) -> tuple[str, str]:
    verdict = ae.decide(kind, facts, _cfg(auto_employer_emails=on, **cfg), NOW)
    assert verdict.detail and "\n" not in verdict.detail
    return verdict.decision, verdict.reason


APPLIED_8D = _facts("applied", _ev(1, "brought", 9), _ev(2, "applied", 8))
REJECTED = _facts("rejected", _ev(1, "applied", 20), _ev(2, "rejected", 1))
ASKED_8D = _facts("rejected", _ev(1, "applied", 30), _ev(2, "rejected", 10), _ev(3, "feedback_asked", 8))


def test_checkin_sends_when_on_and_asks_when_off():
    assert _decide("checkin", APPLIED_8D) == ("send", "auto_on")
    assert _decide("checkin", APPLIED_8D, on=False) == ("ask", "auto_off")


def test_each_stop_reason_alone():
    assert _decide("checkin", APPLIED_8D, paused_until="until_resumed") == ("stop", "paused")
    assert _decide("nonsense", APPLIED_8D) == ("stop", "unknown_kind")
    sent = _facts("applied", _ev(1, "applied", 8), _ev(2, "checkin_sent", 1))
    assert _decide("checkin", sent) == ("stop", "already_sent")
    skipped = _facts("applied", _ev(1, "applied", 8), _ev(2, "auto_email_skipped", 1, kind="checkin"))
    assert _decide("checkin", skipped) == ("stop", "already_skipped")
    assert _decide("checkin", _facts("applied", _ev(1, "applied", 8), sent_today=settings.AUTO_EMAILS_MAX_PER_DAY)) == (
        "stop", "daily_cap_reached",
    )
    assert _decide("checkin", _facts("applied", _ev(1, "applied", 3))) == ("stop", "not_due")
    assert _decide("checkin", _facts("replied", _ev(1, "applied", 9), _ev(2, "replied", 2))) == (
        "stop", "application_moved_on",
    )
    assert _decide("feedback_request", APPLIED_8D) == ("stop", "not_rejected")
    assert _decide("feedback_followup", REJECTED) == ("stop", "no_feedback_request")
    replied = _facts("rejected", _ev(1, "feedback_asked", 9), _ev(2, "replied", 2, has_source=True))
    assert _decide("feedback_followup", replied) == ("stop", "they_replied")
    assert _decide("feedback_followup", _facts("rejected", _ev(1, "feedback_asked", 2))) == ("stop", "not_due")


def test_first_match_wins_order():
    """paused beats everything; already_sent beats the cap and timing; the cap beats timing."""
    both = _facts("applied", _ev(1, "applied", 1), _ev(2, "checkin_sent", 0), sent_today=99)
    assert _decide("checkin", both, paused_until="until_resumed") == ("stop", "paused")
    assert _decide("nonsense", both, paused_until="until_resumed") == ("stop", "paused")
    assert _decide("checkin", both) == ("stop", "already_sent")
    capped = _facts("applied", _ev(1, "applied", 1), sent_today=99)
    assert _decide("checkin", capped) == ("stop", "daily_cap_reached")  # before not_due
    skipped_and_sent = _facts("applied", _ev(1, "applied", 8), _ev(2, "checkin_sent", 1),
                              _ev(3, "auto_email_skipped", 1, kind="checkin"))
    assert _decide("checkin", skipped_and_sent) == ("stop", "already_sent")


def test_checkin_boundary_and_news_after_applied():
    exactly = _facts("applied", _ev(1, "applied", settings.AUTO_EMAIL_CHECKIN_DAYS))
    assert _decide("checkin", exactly) == ("send", "auto_on")
    almost = _facts("applied", _ev(1, "applied", settings.AUTO_EMAIL_CHECKIN_DAYS - 0.5))
    verdict = ae.decide("checkin", almost, _cfg(auto_employer_emails=True), NOW)
    assert (verdict.decision, verdict.reason) == ("stop", "not_due")
    assert (NOW + timedelta(days=0.5)).date().isoformat() in verdict.detail  # the detail gives the due date
    # only the NEWEST applied counts, and a status event after it means it moved on
    news = _facts("applied", _ev(1, "applied", 20), _ev(2, "interview_requested", 15), _ev(3, "applied", 1))
    assert _decide("checkin", news) == ("stop", "not_due")
    news_after = _facts("applied", _ev(1, "applied", 20), _ev(2, "replied", 15))
    assert _decide("checkin", news_after) == ("stop", "application_moved_on")
    assert _decide("checkin", _facts("considering", _ev(1, "brought", 20))) == ("stop", "not_due")


def test_feedback_request_is_due_at_once_and_ignores_no_feedback_wording():
    assert _decide("feedback_request", REJECTED) == ("send", "auto_on")
    assert _decide("feedback_request", REJECTED, on=False) == ("ask", "auto_off")
    # a later status event that is not a rejection means the newest status is not `rejected`
    reopened = _facts("replied", _ev(1, "rejected", 5), _ev(2, "replied", 1))
    assert _decide("feedback_request", reopened) == ("stop", "not_rejected")
    # notes are not status news: a note after the rejection changes nothing
    noted = _facts("rejected", _ev(1, "rejected", 5), _ev(2, "note", 1))
    assert _decide("feedback_request", noted) == ("send", "auto_on")


def test_followup_waits_the_days_and_stops_when_they_replied():
    assert _decide("feedback_followup", ASKED_8D) == ("send", "auto_on")
    assert _decide("feedback_followup", ASKED_8D, on=False) == ("ask", "auto_off")
    # an event with an email source BEFORE the ask is not an answer to it
    earlier = _facts("rejected", _ev(1, "rejected", 12, has_source=True), _ev(2, "feedback_asked", 8))
    assert _decide("feedback_followup", earlier) == ("send", "auto_on")
    # one follow-up only
    done = _facts("rejected", _ev(1, "feedback_asked", 20), _ev(2, "feedback_followup_sent", 1))
    assert _decide("feedback_followup", done) == ("stop", "already_sent")


def _ev_pos(n: int, event_type: str, days_ago: float, pos: int, **kw: Any) -> ae.AutoEvent:
    return ae.AutoEvent(id=n, event_type=event_type, when=NOW - timedelta(days=days_ago), pos=pos, **kw)


def test_a_corrected_feedback_request_keeps_its_slot_so_the_reply_still_counts():
    # asked (1), their email reply (2), then asked is corrected by a new feedback_asked (3, root 1)
    facts = _facts(
        "rejected", _ev(1, "rejected", 12), _ev_pos(3, "feedback_asked", 8, 1), _ev(2, "note", 7, has_source=True),
    )
    assert _decide("feedback_followup", facts) == ("stop", "they_replied")


def test_retracted_send_still_blocks_a_second_one():
    facts = ae.AutoEmailFacts(
        application_id=1, status="applied", events=(_ev(1, "applied", 9),),
        ever_sent=frozenset({"checkin_sent"}),
    )
    assert _decide("checkin", facts) == ("stop", "already_sent")


def test_followup_needs_the_application_to_still_be_rejected():
    facts = _facts(
        "withdrawn", _ev(1, "rejected", 20), _ev(2, "feedback_asked", 10), _ev(3, "withdrawn", 8),
    )
    assert _decide("feedback_followup", facts) == ("stop", "application_moved_on")


def test_due_since_values():
    assert ae.due_since("checkin", APPLIED_8D) == NOW - timedelta(days=8) + timedelta(days=7)
    assert ae.due_since("feedback_request", REJECTED) == NOW - timedelta(days=1)
    assert ae.due_since("feedback_followup", ASKED_8D) == NOW - timedelta(days=8) + timedelta(days=7)
    assert ae.due_since("feedback_request", APPLIED_8D) is None
    assert ae.due_since("nonsense", APPLIED_8D) is None


# ═══════════════════════════════════════════════════════════════════════════
# The setting: on = riskier, off = safer
# ═══════════════════════════════════════════════════════════════════════════


def test_setting_classification_and_validation():
    assert rules.AUTO_EMPLOYER_EMAILS_PATH == AUTO
    assert AUTO in rules.SETTING_PATHS and AUTO in rules.GATED_PATHS
    assert rules.classify_change(AUTO, False, True) == "riskier"
    assert rules.classify_change(AUTO, True, False) == "safer"
    assert rules.classify_change(AUTO, False, False) == "safer"
    assert rules.classify_change(AUTO, True, True) == "safer"
    assert rules.validate_setting(AUTO, True) is True and rules.validate_setting(AUTO, False) is False
    for bad in ("true", 1, 0, None, []):
        with pytest.raises(Exception) as info:
            rules.validate_setting(AUTO, bad)
        assert AUTO in str(info.value)
    assert rules.effective(AssistantSettings()).auto_employer_emails is False
    assert rules.effective(AssistantSettings(auto_employer_emails=True)).auto_employer_emails is True
    assert rules.effective_path_value(AUTO, None) is False and rules.effective_path_value(AUTO, True) is True


# ═══════════════════════════════════════════════════════════════════════════
# The API
# ═══════════════════════════════════════════════════════════════════════════


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )


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


async def _set_auto(client: AsyncClient, value: bool) -> Any:
    return await client.patch("/api/profile", json={"edits": [{"path": AUTO, "value": value}]})


async def _check(client: AsyncClient, app_id: int, kind: str) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/auto-email-check", params={"kind": kind})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _doctor(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/doctor")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _applied(client: AsyncClient, n: int, days_ago: float) -> int:
    app_id = await _bring(client, n)
    await _event(client, app_id, "applied", occurred_at=_ago(days_ago))
    return app_id


async def _second_user_session_cookie(email: str = "second@example.com") -> str:
    from fastapi.testclient import TestClient

    from src.api.main import app
    from tests._signin import sign_in

    sync_client = TestClient(app)
    cookie, _ = sign_in(sync_client, email)
    return cookie


@pytest.mark.asyncio
async def test_the_setting_defaults_off_and_the_web_turns_it_on_at_once(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        view = (await client.get("/api/assistant-settings")).json()
        assert view["auto_employer_emails"] == {"value": False, "effective": False}
        resp = await _set_auto(client, True)
        assert resp.status_code == 200, resp.text
        assert resp.json()["waiting"] == []
        view = (await client.get("/api/assistant-settings")).json()
        assert view["auto_employer_emails"] == {"value": True, "effective": True}
        assert (await _doctor(client))["auto_emails"]["on"] is True
        bad = await client.patch("/api/profile", json={"edits": [{"path": AUTO, "value": "yes"}]})
        assert bad.status_code == 422 and AUTO in bad.json()["detail"]


@pytest.mark.asyncio
async def test_an_assistant_cannot_turn_it_on_but_can_turn_it_off_at_once(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
    from src.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as agent:
        on = await _set_auto(agent, True)
        assert on.status_code == 200, on.text
        body = on.json()
        assert body["applied"] == [] and [w["path"] for w in body["waiting"]] == [AUTO]
        assert body["waiting"][0]["value"] is True
        assert body["profile"]["assistant_settings"]["auto_employer_emails"]["effective"] is False
    async with authenticated_async_context() as client:
        assert (await _set_auto(client, True)).status_code == 200  # the owner's own click
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as agent:
        off = await _set_auto(agent, False)  # the stop switch
        assert off.status_code == 200, off.text
        assert [a["path"] for a in off.json()["applied"]] == [AUTO] and off.json()["waiting"] == []
        assert off.json()["profile"]["assistant_settings"]["auto_employer_emails"]["effective"] is False


@pytest.mark.asyncio
async def test_check_route_end_to_end_each_kind(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        due = await _applied(client, 1, 8)
        fresh = await _applied(client, 2, 2)
        # off: the assistant may only draft
        got = await _check(client, due, "checkin")
        assert (got["decision"], got["reason"]) == ("ask", "auto_off")
        assert got["daily_max"] == settings.AUTO_EMAILS_MAX_PER_DAY and got["sent_today"] == 0
        expected_due = (datetime.now(timezone.utc) - timedelta(days=8) + timedelta(days=7)).date().isoformat()
        assert got["due_since"].startswith(expected_due)
        assert (await _set_auto(client, True)).status_code == 200
        assert (await _check(client, due, "checkin"))["decision"] == "send"
        too_soon = await _check(client, fresh, "checkin")
        assert (too_soon["decision"], too_soon["reason"]) == ("stop", "not_due")
        assert (await _check(client, due, "feedback_request"))["reason"] == "not_rejected"
        assert (await _check(client, due, "bogus"))["reason"] == "unknown_kind"
        # a rejection makes the feedback request due, an ask makes the follow-up wait
        await _event(client, due, "rejected", occurred_at=_ago(1))
        assert (await _check(client, due, "feedback_request"))["decision"] == "send"
        assert (await _check(client, due, "checkin"))["reason"] == "application_moved_on"
        await _event(client, due, "feedback_asked", payload=_SENT, occurred_at=_ago(8))
        assert (await _check(client, due, "feedback_request"))["reason"] == "already_sent"
        assert (await _check(client, due, "feedback_followup"))["decision"] == "send"
        # they answered with an email after the ask: stop
        await _event(client, due, "note", source={"message_id": "m-1", "sender": "hr@northwind.example"})
        assert (await _check(client, due, "feedback_followup"))["reason"] == "they_replied"


@pytest.mark.asyncio
async def test_sent_counts_today_and_the_cap_stops_it(authenticated_async_context, fixture_user_id, monkeypatch):
    monkeypatch.setattr(settings, "AUTO_EMAILS_MAX_PER_DAY", 2)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _set_auto(client, True)
        a, b, c = [await _applied(client, n, 9) for n in (1, 2, 3)]
        assert (await _check(client, c, "checkin"))["sent_today"] == 0
        for app_id in (a, b):
            await _event(client, app_id, "checkin_sent", payload=_SENT)
        got = await _check(client, c, "checkin")
        assert (got["decision"], got["reason"], got["sent_today"], got["daily_max"]) == (
            "stop", "daily_cap_reached", 2, 2,
        )
        # a retracted send no longer counts toward today's cap
        sent = await _event(client, a, "checkin_sent", payload=_SENT)
        await _event(client, a, "note", corrects_event_id=sent["event_id"])
        assert (await _check(client, c, "checkin"))["sent_today"] == 2
        assert (await _check(client, a, "checkin"))["reason"] == "already_sent"  # the first send still stands


@pytest.mark.asyncio
async def test_retracted_events_are_ignored(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _set_auto(client, True)
        app_id = await _applied(client, 1, 9)
        sent = await _event(client, app_id, "checkin_sent", payload=_SENT)
        assert (await _check(client, app_id, "checkin"))["reason"] == "already_sent"
        await _event(client, app_id, "note", corrects_event_id=sent["event_id"])
        # a retracted send still left the building: never send it twice
        assert (await _check(client, app_id, "checkin"))["reason"] == "already_sent"
        assert (await _doctor(client))["auto_emails"]["last"] == []


@pytest.mark.asyncio
async def test_check_route_is_read_only_scoped_and_404_for_another_users_application(
    authenticated_async_context, fixture_user_id
):
    from src.api.main import app

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _applied(client, 1, 8)
        before = (await client.get(f"/api/applications/{app_id}")).json()["events"]
        for _ in range(3):
            await _check(client, app_id, "checkin")
        assert (await client.get(f"/api/applications/{app_id}")).json()["events"] == before
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
            assert (await anon.get(f"/api/applications/{app_id}/auto-email-check", params={"kind": "checkin"})).status_code == 401
        assert (await client.get("/api/applications/987654321/auto-email-check", params={"kind": "checkin"})).status_code == 404
        client.cookies.set("job360_session", await _second_user_session_cookie())
        foreign = await client.get(f"/api/applications/{app_id}/auto-email-check", params={"kind": "checkin"})
    assert foreign.status_code == 404 and foreign.json()["detail"] == "application not found"


@pytest.mark.asyncio
async def test_simulations_and_other_statuses_are_not_due(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _set_auto(client, True)
        real = await _applied(client, 1, 9)
        fake = await _bring(client, 2)
        await _event(client, fake, "simulation")
        await _event(client, fake, "applied", occurred_at=_ago(9))
        interview = await _applied(client, 3, 9)
        await _event(client, interview, "interview_requested")
        due = (await _doctor(client))["auto_emails"]["due"]
    assert [(d["application_id"], d["kind"]) for d in due] == [(real, "checkin")]


@pytest.mark.asyncio
async def test_due_list_for_a_user(authenticated_async_context, fixture_user_id):
    from src.api.dependencies import get_request_db

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _set_auto(client, True)
        checkin = await _applied(client, 1, 8)
        await _applied(client, 2, 3)  # not due yet
        rejected = await _bring(client, 3)
        await _event(client, rejected, "applied", occurred_at=_ago(30))
        await _event(client, rejected, "rejected", occurred_at=_ago(1))
        asked = await _bring(client, 4)
        await _event(client, asked, "applied", occurred_at=_ago(30))
        await _event(client, asked, "rejected", occurred_at=_ago(12))
        await _event(client, asked, "feedback_asked", payload=_SENT, occurred_at=_ago(8))
        async for db in get_request_db():
            due = await ae.due_for_user(db, fixture_user_id)
    assert sorted((d["application_id"], d["kind"]) for d in due) == sorted(
        [(checkin, "checkin"), (rejected, "feedback_request"), (asked, "feedback_followup")]
    )
    assert all(d["due_since"] for d in due)


@pytest.mark.asyncio
async def test_a_paused_user_has_nothing_due(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _set_auto(client, True)
        await _applied(client, 1, 9)
        assert len((await _doctor(client))["auto_emails"]["due"]) == 1
        assert (await client.patch(
            "/api/profile", json={"edits": [{"path": "assistant_settings.paused_until", "value": "until_resumed"}]}
        )).status_code == 200
        assert (await _doctor(client))["auto_emails"]["due"] == []


# ── violations ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_violations_each_rule_and_a_clean_send_is_not_one(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        clean = await _applied(client, 1, 9)
        await _event(client, clean, "checkin_sent", payload=_SENT)
        assert (await _doctor(client))["auto_emails"]["violations"] == []

        twice = await _applied(client, 2, 9)
        await _event(client, twice, "checkin_sent", payload=_SENT)
        second = await _event(client, twice, "checkin_sent", payload=_SENT)
        early = await _applied(client, 3, 1)
        early_ev = await _event(client, early, "checkin_sent", payload=_SENT)
        never_applied = await _bring(client, 4)
        never_ev = await _event(client, never_applied, "checkin_sent", payload=_SENT)
        no_reject = await _applied(client, 5, 9)
        asked_ev = await _event(client, no_reject, "feedback_asked", payload=_SENT)
        no_ask = await _applied(client, 6, 30)
        await _event(client, no_ask, "rejected", occurred_at=_ago(20))
        noask_ev = await _event(client, no_ask, "feedback_followup_sent", payload=_SENT)
        soon = await _applied(client, 7, 30)
        await _event(client, soon, "rejected", occurred_at=_ago(20))
        await _event(client, soon, "feedback_asked", payload=_SENT, occurred_at=_ago(3))
        soon_ev = await _event(client, soon, "feedback_followup_sent", payload=_SENT)
        ok = await _applied(client, 8, 30)
        await _event(client, ok, "rejected", occurred_at=_ago(20))
        await _event(client, ok, "feedback_asked", payload=_SENT, occurred_at=_ago(9))
        await _event(client, ok, "feedback_followup_sent", payload=_SENT, occurred_at=_ago(1))

        found = (await _doctor(client))["auto_emails"]["violations"]
    got = {(v["event_id"], v["application_id"], v["rule"]) for v in found}
    assert got == {
        (second["event_id"], twice, "sent_twice"),
        (early_ev["event_id"], early, "checkin_too_early"),
        (never_ev["event_id"], never_applied, "checkin_too_early"),
        (asked_ev["event_id"], no_reject, "asked_without_rejection"),
        (noask_ev["event_id"], no_ask, "followup_too_early"),
        (soon_ev["event_id"], soon, "followup_too_early"),
    }


@pytest.mark.asyncio
async def test_violations_follow_the_correction_chain_not_the_id(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        # (b) applied, checkin 8 days later, then applied is corrected: the check-in is still on time
        a = await _bring(client, 1)
        first = await _event(client, a, "applied", occurred_at=_ago(9))
        await _event(client, a, "checkin_sent", payload=_SENT)
        await _event(client, a, "applied", occurred_at=_ago(9), corrects_event_id=first["event_id"])
        # (c) rejected, feedback asked, then rejected is corrected: the ask still follows a rejection
        b = await _bring(client, 2)
        rej = await _event(client, b, "rejected", occurred_at=_ago(5))
        await _event(client, b, "feedback_asked", payload=_SENT)
        await _event(client, b, "rejected", occurred_at=_ago(5), corrects_event_id=rej["event_id"])
        assert (await _doctor(client))["auto_emails"]["violations"] == []
        # (a) the reply after the ask still stops the follow-up when the ask is corrected
        c = await _bring(client, 3)
        await _event(client, c, "rejected", occurred_at=_ago(20))
        ask = await _event(client, c, "feedback_asked", payload=_SENT, occurred_at=_ago(10))
        await _event(
            client, c, "note", source={"message_id": "m-1", "sender": "hr@northwind.example"}, occurred_at=_ago(8),
        )
        await _event(
            client, c, "feedback_asked", payload=_SENT, occurred_at=_ago(10), corrects_event_id=ask["event_id"]
        )
        assert (await _check(client, c, "feedback_followup"))["reason"] == "they_replied"


@pytest.mark.asyncio
async def test_followup_after_withdrawn_is_application_moved_on(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, 1)
        await _event(client, app_id, "rejected", occurred_at=_ago(20))
        await _event(client, app_id, "feedback_asked", payload=_SENT, occurred_at=_ago(10))
        await _event(client, app_id, "withdrawn", occurred_at=_ago(8))
        got = await _check(client, app_id, "feedback_followup")
        assert (got["decision"], got["reason"]) == ("stop", "application_moved_on")


# ── payloads ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_payload_422s_name_the_key(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        for event_type in ("checkin_sent", "feedback_asked", "feedback_followup_sent"):
            for missing in ("to", "text", "to_kind"):
                payload = {k: v for k, v in _SENT.items() if k != missing}
                resp = await _post(client, app_id, event_type, payload=payload)
                assert resp.status_code == 422 and f"payload.{missing}" in resp.json()["detail"], (event_type, missing)
            bad = [
                ({"to": "x" * 321}, "payload.to"), ({"text": "x" * 3001}, "payload.text"),
                ({"thread_id": "x" * 257}, "payload.thread_id"), ({"subject": "x" * 301}, "payload.subject"),
                ({"to_kind": "company"}, "payload.to_kind"), ({"to": 5}, "payload.to"), ({"text": ""}, "payload.text"),
                ({"thread_id": 7}, "payload.thread_id"),
            ]
            for patch, key in bad:
                resp = await _post(client, app_id, event_type, payload={**_SENT, **patch})
                assert resp.status_code == 422 and key in resp.json()["detail"], (event_type, patch)
        full = {**_SENT, "thread_id": "t" * 256, "subject": "s" * 300, "text": "x" * 3000, "to": "y" * 320}
        assert (await _post(client, app_id, "checkin_sent", payload=full)).status_code == 201
        assert (await _post(client, app_id, "feedback_asked", payload={**_SENT, "to_kind": "careers_address"})).status_code == 201
        # auto_email_skipped
        ok = {"kind": "checkin", "reason": "no contact, no careers address"}
        assert (await _post(client, app_id, "auto_email_skipped", payload=ok)).status_code == 201
        for patch, key in (
            ({"kind": "nope"}, "payload.kind"), ({"reason": ""}, "payload.reason"),
            ({"reason": "r" * 201}, "payload.reason"), ({"reason": 3}, "payload.reason"),
        ):
            resp = await _post(client, app_id, "auto_email_skipped", payload={**ok, **patch})
            assert resp.status_code == 422 and key in resp.json()["detail"], patch
        for missing in ("kind", "reason"):
            resp = await _post(client, app_id, "auto_email_skipped", payload={k: v for k, v in ok.items() if k != missing})
            assert resp.status_code == 422 and f"payload.{missing}" in resp.json()["detail"]
        # none of the four moves the status
        assert (await client.get(f"/api/applications/{app_id}")).json()["status"] == "considering"


@pytest.mark.asyncio
async def test_a_recorded_skip_stops_that_kind(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _set_auto(client, True)
        app_id = await _applied(client, 1, 9)
        await _event(client, app_id, "auto_email_skipped", payload={"kind": "checkin", "reason": "no contact"})
        got = await _check(client, app_id, "checkin")
        assert (got["decision"], got["reason"]) == ("stop", "already_skipped")
        assert (await _doctor(client))["auto_emails"]["due"] == []


# ── doctor ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_doctor_block_shape_and_last_sends_show_company_never_the_address(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        block = (await _doctor(client))["auto_emails"]
        assert block["on"] is False and block["sent_today"] == 0 and block["last"] == []
        assert block["due"] == [] and block["violations"] == []
        assert block["daily_max"] == settings.AUTO_EMAILS_MAX_PER_DAY
        assert "stop automatic emails" in block["stop_hint"] and "auto_employer_emails" in block["stop_hint"]
        await _set_auto(client, True)
        app_id = await _applied(client, 1, 9)
        sent = await _event(client, app_id, "checkin_sent", payload=_SENT)
        block = (await _doctor(client))["auto_emails"]
    assert block["on"] is True and block["sent_today"] == 1
    assert len(block["last"]) == 1
    last = block["last"][0]
    assert (last["event_id"], last["application_id"], last["company"], last["kind"]) == (
        sent["event_id"], app_id, "Northwind", "checkin",
    )
    assert last["at"]
    assert "rita@northwind.example" not in str(block)


@pytest.mark.asyncio
async def test_doctor_last_is_capped_at_five_newest_first(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        ids = []
        for n in range(7):
            app_id = await _applied(client, n, 9)
            ids.append((await _event(client, app_id, "checkin_sent", payload=_SENT))["event_id"])
        last = (await _doctor(client))["auto_emails"]["last"]
    assert [row["event_id"] for row in last] == list(reversed(ids))[:5]


@pytest.mark.asyncio
async def test_doctor_survives_a_failure_in_the_block(authenticated_async_context, fixture_user_id, monkeypatch):
    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(ae, "violations", boom)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await client.get("/api/doctor")
        assert resp.status_code == 200
        block = resp.json()["auto_emails"]
    assert block["due"] == [] and block["violations"] == [] and block["last"] == [] and block["sent_today"] == 0
    assert block["stop_hint"]


# ── MCP ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_check_auto_email_parity(authenticated_async_context, fixture_user_id):
    """The tool answers exactly what the route answers, from the same function; a foreign id is a 404."""
    pytest.importorskip("mcp")
    from mcp.server.mcpserver.exceptions import ToolError

    from src.api import mcp_server

    tool = mcp_server.build_server()._tool_manager.get_tool("check_auto_email")
    assert tool is not None
    assert len(tool.description) < 2000
    assert "same thread" in tool.description and "send" in tool.description
    user = mcp_server.CurrentUser(
        id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc",
    )
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _set_auto(client, True)
        app_id = await _applied(client, 1, 9)
        token = mcp_server._current_user.set(user)
        try:
            for kind in ("checkin", "feedback_request", "bogus"):
                assert await tool.fn(application_id=app_id, kind=kind) == await _check(client, app_id, kind)
            with pytest.raises(ToolError) as info:
                await tool.fn(application_id=987654321, kind="checkin")
            assert str(info.value).startswith("404")
        finally:
            mcp_server._current_user.reset(token)


def test_the_config_constants_have_the_owner_values():
    assert settings.AUTO_EMAIL_CHECKIN_DAYS == 7
    assert settings.AUTO_EMAIL_FOLLOWUP_DAYS == 7
    assert settings.AUTO_EMAILS_MAX_PER_DAY == 10
    for name in ("checkin_sent", "feedback_asked", "feedback_followup_sent", "auto_email_skipped"):
        assert name in settings.APPLICATION_NOTE_EVENT_TYPES
        assert name not in settings.APPLICATION_STATUS_EVENT_TYPES
