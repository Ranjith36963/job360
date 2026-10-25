"""Bulk apply 2026-10-18, wave 2: R1 counted auto grant, C1 pace (min gap + apply hours),
C2 weekly cap, F2 multi-page forms (``form_pages_incomplete``).

Pure tests need no database; the rest go through the real doors on the real Postgres.
Helpers are copied, never imported from another test module (schema isolation).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.core import settings
from src.services.applications import kit as kit_service
from src.services.profile import assistant_settings as rules
from src.services.profile.edits import ProfileEditError, validate_edit
from src.services.profile.models import AssistantSettings, UserPreferences

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)  # a Monday, noon UTC
FORM = "https://careers.northwind.example/apply/7"
WEEKLY = "assistant_settings.weekly_cap"
GAP = "assistant_settings.min_gap_minutes"
HOURS = "assistant_settings.apply_hours"
GRANT_N = "assistant_settings.auto_grant_count"
GRANT_UNTIL = "assistant_settings.auto_grant_until"
SUBMIT = "assistant_settings.submit_mode"


def _ad(n: int, company: str = "Northwind") -> dict[str, Any]:
    return {
        "title": f"Data Engineer {n}", "company": company, "location": "Remote",
        "apply_url": f"https://northwind.example/careers/{n}",
        "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
    }


def _eff(**kw: Any) -> rules.EffectiveSettings:
    kw.setdefault("apply_min_score", 50)
    kw.setdefault("above_line", "auto")
    kw.setdefault("below_line", "auto")
    return rules.effective(AssistantSettings(**kw))


def _counts(**kw: Any) -> rules.SubmitCounts:
    kw.setdefault("submitted_today", 0)
    kw.setdefault("applied_since_auto_on", 1)
    return rules.SubmitCounts(**kw)


def _facts(**kw: Any) -> rules.SubmitFacts:
    base: dict[str, Any] = {
        "status": "considering", "has_receipt": False, "submit_override": None, "cv_seen": True,
        "form_filled": True, "form_swept": True, "fit_score": 80, "form_host": "careers.northwind.example",
    }
    base.update(kw)
    return rules.SubmitFacts(**base)


def _gate(cfg: rules.EffectiveSettings, facts: rules.SubmitFacts | None = None, counts: Any = None,
          now: datetime = NOW) -> rules.SubmitDecision:
    return rules.may_submit(cfg, facts or _facts(), FORM, counts or _counts(), now=now)


def _until(hours: float = 2) -> str:
    return (NOW + timedelta(hours=hours)).isoformat()


# ═══════════════════════════════════════════════════════════════════════════
# R1. Counted auto grant (pure)
# ═══════════════════════════════════════════════════════════════════════════


def test_a_grant_makes_confirm_mode_auto_until_the_count_is_used():
    cfg = _eff(submit_mode="confirm", auto_grant_count=3, auto_grant_until=_until())
    assert _gate(cfg).reason == "auto_when_sure"  # nothing used yet
    assert _gate(cfg, counts=_counts(applied_since_grant=2)).reason == "auto_when_sure"
    back = _gate(cfg, counts=_counts(applied_since_grant=3))
    assert (back.decision, back.reason) == ("ask", "submit_mode_confirm"), "count used: the user's own mode is back"


def test_a_grant_ends_at_its_time_and_needs_both_settings():
    ended = _eff(submit_mode="confirm", auto_grant_count=3, auto_grant_until=_until(-1))
    assert _gate(ended).reason == "submit_mode_confirm"
    assert _gate(_eff(submit_mode="confirm", auto_grant_count=3)).reason == "submit_mode_confirm"
    assert _gate(_eff(submit_mode="confirm", auto_grant_until=_until())).reason == "submit_mode_confirm"


def test_a_grant_keeps_the_practice_run_and_every_stop_and_the_job_override():
    cfg = _eff(submit_mode="confirm", auto_grant_count=5, auto_grant_until=_until())
    practice = _gate(cfg, counts=_counts(applied_since_auto_on=0))
    assert (practice.decision, practice.reason) == ("ask", "practice_run")
    assert _gate(_eff(submit_mode="confirm", auto_grant_count=5, auto_grant_until=_until(), paused_until="until_resumed")
                 ).reason == "paused"
    assert _gate(cfg, _facts(company_avoided=True)).reason == "company_avoided"
    override = _gate(cfg, _facts(submit_override="confirm"))
    assert (override.decision, override.reason) == ("ask", "job_override_confirm"), "the user's per-job confirm wins"
    new_site = _gate(cfg, _facts(new_site=True))
    assert new_site.reason == "new_site_first_time"


def test_a_grant_is_never_cleared_into_a_yes_for_a_stop():
    cfg = _eff(submit_mode="confirm", auto_grant_count=5, auto_grant_until=_until(), daily_cap=1)
    stop = _gate(cfg, _facts(approved=True), _counts(submitted_today=1))
    assert (stop.decision, stop.reason) == ("stop", "daily_cap_reached")


def test_grant_settings_validate_and_classify():
    assert validate_edit(GRANT_N, 10) == 10
    for bad in (0, 101, True, "5", 2.5):
        with pytest.raises(ProfileEditError):
            validate_edit(GRANT_N, bad)
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    assert validate_edit(GRANT_UNTIL, future).startswith(future[:16])
    for bad in (
        "", "until_resumed", "tonight", "2020-01-01T00:00:00+00:00", 5,
        (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        (datetime.now(timezone.utc) + timedelta(hours=3)).replace(tzinfo=None).isoformat(),
    ):
        with pytest.raises(ProfileEditError):
            validate_edit(GRANT_UNTIL, bad)
    cc = rules.classify_change
    assert cc(GRANT_N, None, 10) == "riskier" and cc(GRANT_N, 10, 20) == "riskier"
    assert cc(GRANT_N, 10, 5) == "safer" and cc(GRANT_N, 10, None) == "safer"
    later, earlier = _until(5), _until(1)
    assert cc(GRANT_UNTIL, "", later) == "riskier" and cc(GRANT_UNTIL, earlier, later) == "riskier"
    assert cc(GRANT_UNTIL, later, earlier) == "safer" and cc(GRANT_UNTIL, later, "") == "safer"
    # an expired stored end reads as none, so a new grant after it is riskier
    expired = rules.effective_path_value(GRANT_UNTIL, _until(-2), NOW)
    assert expired == "" and cc(GRANT_UNTIL, expired, later) == "riskier"


# ═══════════════════════════════════════════════════════════════════════════
# C2. Weekly cap (pure)
# ═══════════════════════════════════════════════════════════════════════════


def test_weekly_cap_stops_at_n_and_a_yes_does_not_clear_it():
    cfg = _eff(submit_mode="auto_when_sure", weekly_cap=5)
    assert _gate(cfg, counts=_counts(submitted_this_week=4)).decision == "submit"
    stop = _gate(cfg, _facts(approved=True), _counts(submitted_this_week=5))
    assert (stop.decision, stop.reason) == ("stop", "weekly_cap_reached")
    assert _gate(_eff(submit_mode="auto_when_sure"), counts=_counts(submitted_this_week=500)).decision == "submit"


def test_weekly_cap_is_a_reason_and_comes_before_every_ask_and_the_pace_stops():
    reasons = list(rules.REASONS)
    for name in ("weekly_cap_reached", "pace_wait", "outside_hours", "form_pages_incomplete"):
        assert name in reasons
    assert reasons.index("weekly_cap_reached") == reasons.index("company_cap_reached") + 1
    assert reasons.index("pace_wait") < reasons.index("duplicate_job")


def test_weekly_cap_validates_and_classifies_like_daily_cap():
    assert validate_edit(WEEKLY, 20) == 20
    for bad in (0, settings.ASSISTANT_WEEKLY_CAP_MAX + 1, "x", True):
        with pytest.raises(ProfileEditError):
            validate_edit(WEEKLY, bad)
    cc = rules.classify_change
    assert cc(WEEKLY, None, 10) == "safer" and cc(WEEKLY, 10, 5) == "safer"
    assert cc(WEEKLY, 10, 20) == "riskier" and cc(WEEKLY, 10, None) == "riskier"


# ═══════════════════════════════════════════════════════════════════════════
# C1. Pace: min gap and apply hours (pure)
# ═══════════════════════════════════════════════════════════════════════════


def test_pace_wait_names_the_exact_next_time_in_the_users_zone():
    cfg = _eff(submit_mode="auto_when_sure", min_gap_minutes=30)
    last = NOW - timedelta(minutes=10)
    stop = _gate(cfg, counts=_counts(last_applied_at=last, user_tz="Europe/London"))
    assert (stop.decision, stop.reason) == ("stop", "pace_wait")
    assert "2026-10-19T13:20+01:00" in stop.detail  # last 11:50 UTC + 30 min = 12:20 UTC = 13:20 in London
    ok = _gate(cfg, counts=_counts(last_applied_at=NOW - timedelta(minutes=30)))
    assert ok.decision == "submit", "exactly the gap later is allowed"
    assert _gate(cfg, counts=_counts()).decision == "submit", "no earlier application: no wait"
    assert _gate(_eff(submit_mode="auto_when_sure", min_gap_minutes=0),
                 counts=_counts(last_applied_at=NOW)).decision == "submit"


def test_a_yes_never_clears_pace_wait_or_outside_hours():
    cfg = _eff(submit_mode="confirm", min_gap_minutes=60)
    stop = _gate(cfg, _facts(approved=True), _counts(last_applied_at=NOW - timedelta(minutes=1)))
    assert (stop.decision, stop.reason) == ("stop", "pace_wait")
    hours = _eff(apply_hours={"from": "13:00", "to": "17:00"})
    out = _gate(hours, _facts(approved=True))
    assert (out.decision, out.reason) == ("stop", "outside_hours")


def test_apply_hours_are_read_in_the_users_timezone_and_name_the_next_opening():
    cfg = _eff(submit_mode="auto_when_sure", apply_hours={"from": "09:00", "to": "17:00"})
    # 12:00 UTC is 13:00 in London (BST): inside
    assert _gate(cfg, counts=_counts(user_tz="Europe/London")).decision == "submit"
    # 12:00 UTC is 05:00 in Los Angeles (PDT): outside, opens at 09:00 local the same day
    stop = _gate(cfg, counts=_counts(user_tz="America/Los_Angeles"))
    assert (stop.decision, stop.reason) == ("stop", "outside_hours")
    assert "2026-10-19T09:00-07:00" in stop.detail
    late = _gate(cfg, counts=_counts(user_tz="UTC"), now=NOW.replace(hour=18))
    assert late.reason == "outside_hours" and "2026-10-20T09:00+00:00" in late.detail
    assert _gate(cfg, counts=_counts(user_tz="Not/AZone")).decision == "submit", "a bad zone reads as UTC"


def test_an_overnight_window_and_the_boundaries():
    cfg = _eff(submit_mode="auto_when_sure", apply_hours={"from": "22:00", "to": "06:00"})
    assert _gate(cfg, now=NOW.replace(hour=23)).decision == "submit"
    assert _gate(cfg, now=NOW.replace(hour=5, minute=59)).decision == "submit"
    assert _gate(cfg, now=NOW.replace(hour=6)).reason == "outside_hours"
    assert _gate(cfg, now=NOW.replace(hour=21, minute=59)).reason == "outside_hours"
    assert _gate(cfg, now=NOW.replace(hour=22)).decision == "submit"


def test_the_gap_wait_is_moved_to_the_next_opening_when_it_would_fall_outside_the_hours():
    cfg = _eff(submit_mode="auto_when_sure", min_gap_minutes=240, apply_hours={"from": "09:00", "to": "17:00"})
    now = NOW.replace(hour=15)  # inside the hours; the gap ends 19:00, after closing
    stop = _gate(cfg, counts=_counts(last_applied_at=now - timedelta(minutes=5)), now=now)
    assert stop.reason == "pace_wait" and "2026-10-20T09:00+00:00" in stop.detail


def test_pace_settings_validate_and_classify():
    assert validate_edit(GAP, 0) == 0 and validate_edit(GAP, 240) == 240
    assert validate_edit(GAP, 1.5) == 1.5, "whole seconds are allowed (owner ask 2026-10-18)"
    for bad in (-1, 241, "5", True, 1.001):
        with pytest.raises(ProfileEditError):
            validate_edit(GAP, bad)
    assert validate_edit(HOURS, {"from": "09:00", "to": "17:30"}) == {"from": "09:00", "to": "17:30"}
    for bad in (
        {"from": "09:00"}, {"from": "9:00", "to": "17:00"}, {"from": "09:00", "to": "24:00"},
        {"from": "09:00", "to": "09:00"}, {"from": "09:00", "to": "17:00", "tz": "UTC"}, "09:00-17:00", [],
    ):
        with pytest.raises(ProfileEditError):
            validate_edit(HOURS, bad)
    cc = rules.classify_change
    assert cc(GAP, 30, 60) == "safer" and cc(GAP, None, 30) == "safer"
    assert cc(GAP, 30, 10) == "riskier" and cc(GAP, 30, None) == "riskier" and cc(GAP, 30, 0) == "riskier"
    wide, narrow = {"from": "08:00", "to": "20:00"}, {"from": "09:00", "to": "17:00"}
    assert cc(HOURS, wide, narrow) == "safer" and cc(HOURS, None, narrow) == "safer"
    assert cc(HOURS, narrow, wide) == "riskier" and cc(HOURS, narrow, None) == "riskier"
    assert cc(HOURS, narrow, {"from": "10:00", "to": "18:00"}) == "riskier", "moved: new minutes are allowed"
    assert cc(HOURS, narrow, narrow) == "safer"


def test_the_defaults_change_nothing():
    cfg = rules.effective(AssistantSettings(submit_mode="auto_when_sure", apply_min_score=50, above_line="auto",
                                            below_line="auto"))
    assert (cfg.weekly_cap, cfg.min_gap_minutes, cfg.apply_hours, cfg.auto_grant_count) == (None,) * 4
    assert _gate(cfg, counts=_counts(last_applied_at=NOW, submitted_this_week=999)).decision == "submit"


# ═══════════════════════════════════════════════════════════════════════════
# F2. Multi-page forms (payload checks and the per-page facts)
# ═══════════════════════════════════════════════════════════════════════════


def _ev(event_id: int, event_type: str, **payload: Any) -> dict[str, Any]:
    return {"id": event_id, "event_type": event_type, "payload": payload}


def _paged(events_oldest_first: list[dict[str, Any]], filled: int | None = None) -> tuple[bool, bool, bool]:
    return kit_service._paged_shortfalls(list(reversed(events_oldest_first)), filled)


def test_every_page_checked_and_swept_is_complete():
    events = [
        _ev(1, "form_checked", questions_count=4, page=1, pages_total=2),
        _ev(2, "form_swept", fields_checked=4, page=1, pages_total=2),
        _ev(3, "form_checked", questions_count=3, page=2, pages_total=2),
        _ev(4, "form_swept", fields_checked=3, page=2, pages_total=2),
    ]
    assert _paged(events, filled=7) == (False, False, False)


def test_a_page_with_no_newer_sweep_is_incomplete_even_when_the_last_page_was_swept():
    events = [
        _ev(1, "form_checked", questions_count=4, page=1, pages_total=3),
        _ev(2, "form_checked", questions_count=3, page=2, pages_total=3),
        _ev(3, "form_swept", fields_checked=3, page=2, pages_total=3),
        _ev(4, "form_checked", questions_count=2, page=3, pages_total=3),
        _ev(5, "form_swept", fields_checked=2, page=3, pages_total=3),
    ]
    assert _paged(events)[2] is True, "page 1 was never swept"
    # page 2 checked again after its sweep: open again
    again = events[:3] + [_ev(6, "form_checked", questions_count=5, page=2, pages_total=3)]
    assert _paged(again + [events[3], events[4]])[2] is True


def test_a_page_that_was_never_reached_is_incomplete():
    events = [_ev(1, "form_checked", questions_count=4, page=1, pages_total=3),
              _ev(2, "form_swept", fields_checked=4, page=1, pages_total=3)]
    assert _paged(events)[2] is True, "pages 2 and 3 have no sweep"


def test_the_count_is_judged_per_page_and_unreadable_over_every_page():
    short = [
        _ev(1, "form_checked", questions_count=4, page=1, pages_total=2),
        _ev(2, "form_swept", fields_checked=2, page=1, pages_total=2),
        _ev(3, "form_checked", questions_count=2, page=2, pages_total=2),
        _ev(4, "form_swept", fields_checked=9, page=2, pages_total=2),
    ]
    assert _paged(short) == (True, False, False), "page 2's surplus cannot hide page 1's shortfall"
    unreadable = [
        _ev(1, "form_checked", questions_count=1, page=1, pages_total=2),
        _ev(2, "form_swept", fields_checked=1, unreadable=["Phone"], page=1, pages_total=2),
        _ev(3, "form_checked", questions_count=1, page=2, pages_total=2),
        _ev(4, "form_swept", fields_checked=1, page=2, pages_total=2),
    ]
    assert _paged(unreadable) == (False, True, False)
    assert _paged(unreadable[:2] + unreadable[2:], filled=5)[0] is True, "all pages together read fewer than form_filled"


def test_page_and_pages_total_go_together_and_are_bounded():
    ok = {"fields_checked": 3, "fixed": 0, "page": 2, "pages_total": 3}
    assert kit_service.payload_problems("form_swept", ok) == []
    checked = {"questions_count": 3, "answered": 3, "missing": 0, "page": 1, "pages_total": 2}
    assert kit_service.payload_problems("form_checked", checked) == []
    for bad in (
        {"page": 1}, {"pages_total": 2}, {"page": 3, "pages_total": 2}, {"page": 0, "pages_total": 2},
        {"page": 1, "pages_total": kit_service.FORM_PAGES_MAX + 1}, {"page": True, "pages_total": 2},
        {"page": "1", "pages_total": 2},
    ):
        assert kit_service.payload_problems("form_swept", {"fields_checked": 3, "fixed": 0, **bad}), bad
        assert kit_service.payload_problems("form_checked", {"questions_count": 3, "answered": 3, "missing": 0, **bad}), bad
    assert kit_service.payload_problems("form_swept", {"fields_checked": 3, "fixed": 0}) == []


def test_form_pages_incomplete_is_a_stop_a_yes_never_clears_and_default_false():
    cfg = _eff(submit_mode="auto_when_sure")
    stop = _gate(cfg, _facts(form_pages_incomplete=True, approved=True))
    assert (stop.decision, stop.reason) == ("stop", "form_pages_incomplete")
    assert _gate(cfg).decision == "submit"
    cfg_confirm = _eff(submit_mode="confirm")
    assert _gate(cfg_confirm, _facts(form_pages_incomplete=True)).decision == "stop", "before the asks"


# ═══════════════════════════════════════════════════════════════════════════
# Real-database tests
# ═══════════════════════════════════════════════════════════════════════════


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )


def _e(path: str, value: Any) -> dict[str, Any]:
    return {"path": path, "value": value}


async def _patch(client: AsyncClient, *edits_: dict[str, Any]):
    return await client.patch("/api/profile", json={"edits": list(edits_)})


async def _bring(client: AsyncClient, ad: dict[str, Any]) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, **body: Any):
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, **body})


async def _applied(client: AsyncClient, app_id: int) -> None:
    resp = await _event(client, app_id, "applied")
    assert resp.status_code == 201, resp.text


async def _check(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_pace_wait_and_weekly_cap_through_the_real_check_submit(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        target = await _bring(client, _ad(1))
        other = await _bring(client, _ad(2, company="Southwind"))
        assert (await _patch(client, _e(GAP, 90), _e(WEEKLY, 1))).status_code == 200
        before = await _check(client, target)
        assert before["reason"] not in ("pace_wait", "weekly_cap_reached")
        await _applied(client, other)
        body = await _check(client, target)
        # both stops are true now; the weekly cap is checked first
        assert (body["decision"], body["reason"]) == ("stop", "weekly_cap_reached")
        assert (await _patch(client, _e(WEEKLY, 5))).status_code == 200
        body = await _check(client, target)
        assert (body["decision"], body["reason"]) == ("stop", "pace_wait")
        assert "90 minutes" in body["detail"] and "(your time)" in body["detail"]
        view = (await client.get("/api/assistant-settings")).json()
        assert view["weekly_cap"] == {"value": 5, "effective": 5}
        assert view["min_gap_minutes"] == {"value": 90, "effective": 90}
        assert view["apply_hours"] == {"value": None, "effective": None}
        assert (await _patch(client, _e(GAP, None))).status_code == 200
        assert (await _check(client, target))["reason"] != "pace_wait"


@pytest.mark.asyncio
async def test_a_riskier_grant_or_pace_change_from_a_token_waits_for_the_user(
    authenticated_async_context, fixture_user_id
):
    from httpx import ASGITransport

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await _patch(
            client, _e(GAP, 60), _e(HOURS, {"from": "09:00", "to": "17:00"}), _e(WEEKLY, 5)
        )).status_code == 200
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
    from src.api.main import app

    bearer = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    async with bearer:
        until = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        grant = (await _patch(bearer, _e(GRANT_N, 10), _e(GRANT_UNTIL, until))).json()
        assert {w["path"] for w in grant["waiting"]} == {GRANT_N, GRANT_UNTIL} and grant["applied"] == []
        riskier = (await _patch(
            bearer, _e(GAP, 10), _e(HOURS, {"from": "06:00", "to": "23:00"}), _e(WEEKLY, 50)
        )).json()
        assert {w["path"] for w in riskier["waiting"]} == {GAP, HOURS, WEEKLY} and riskier["applied"] == []
        removed = (await _patch(bearer, _e(GAP, None), _e(HOURS, None))).json()
        assert {w["path"] for w in removed["waiting"]} == {GAP, HOURS}
        safer = (await _patch(
            bearer, _e(GAP, 120), _e(HOURS, {"from": "10:00", "to": "12:00"}), _e(WEEKLY, 2)
        )).json()
        assert [a["path"] for a in safer["applied"]] == [GAP, HOURS, WEEKLY] and safer["waiting"] == []


@pytest.mark.asyncio
async def test_grant_info_counts_from_the_grant_and_a_shorter_end_never_restarts_it(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        first = await _bring(client, _ad(1))
        second = await _bring(client, _ad(2, company="Southwind"))
        third = await _bring(client, _ad(3, company="Eastwind"))
        await _applied(client, first)  # no grant history yet: it counts (the safe side)
        assert rules.grant_info(fixture_user_id)[1] == 1, "no grant yet: every applied counts (the safe side)"
        end = datetime.now(timezone.utc) + timedelta(hours=4)
        assert (await _patch(client, _e(GRANT_N, 2), _e(GRANT_UNTIL, end.isoformat()))).status_code == 200
        began, used = rules.grant_info(fixture_user_id)
        assert began is not None and used == 0
        await _applied(client, second)
        assert rules.grant_info(fixture_user_id)[1] == 1
        # a SHORTER end (a safer change) must not reset the count
        assert (await _patch(client, _e(GRANT_UNTIL, (end - timedelta(hours=1)).isoformat()))).status_code == 200
        assert rules.grant_info(fixture_user_id)[1] == 1
        # a LATER end is a new grant: the count starts over
        assert (await _patch(client, _e(GRANT_UNTIL, (end + timedelta(hours=1)).isoformat()))).status_code == 200
        assert rules.grant_info(fixture_user_id)[1] == 0
        await _applied(client, third)
        assert rules.grant_info(fixture_user_id)[1] == 1
        # the first grant also started the practice run
        since, applied_since = rules.practice_info(fixture_user_id)
        assert since is not None and applied_since == 2  # second and third; the first was before the grant


@pytest.mark.asyncio
async def test_a_grant_resolves_the_mode_through_the_real_gate(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        target = await _bring(client, _ad(1))
        other = await _bring(client, _ad(2, company="Southwind"))
        assert (await _event(client, target, "form_filled", payload={"form_url": FORM, "fields_count": 1})).status_code == 201
        assert (await _event(client, target, "form_swept", payload={"fields_checked": 1, "fixed": 0})).status_code == 201
        assert (await _patch(client, _e("assistant_settings.apply_min_score", 0))).status_code == 200
        fit = {"score": 80, "verdict": "ok", "score_source": "full_ad"}
        assert (await client.put(f"/api/applications/{target}/fit", json=fit)).status_code == 200
        body = await _check(client, target)
        assert body["reason"] != "practice_run"
        # Slice 30: a grant covers only what is in the queue when the user confirms it.
        assert (await _event(client, target, "queued")).status_code == 201
        until = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        assert (await _patch(
            client, _e("assistant_settings.above_line", "auto"), _e("assistant_settings.below_line", "auto"),
            _e(GRANT_N, 1), _e(GRANT_UNTIL, until),
        )).status_code == 200
        view = (await client.get("/api/assistant-settings")).json()
        assert view["auto_grant_count"] == {"value": 1, "effective": 1}
        assert view["practice_run"]["needed"] is True
        body = await _check(client, target)
        assert body["reason"] in ("practice_run", "cv_not_seen", "new_site_first_time"), body
        assert body["decision"] == "ask", "auto resolved: the next ask is an auto-mode ask, not submit_mode_confirm"
        await _applied(client, other)  # the one granted application is used
        body = await _check(client, target)
        assert body["reason"] == "submit_mode_confirm", body


@pytest.mark.asyncio
async def test_a_two_page_form_stops_until_every_page_is_swept(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client, _ad(1))

        async def rec(event_type: str, **payload: Any) -> None:
            resp = await _event(client, app_id, event_type, payload=payload)
            assert resp.status_code == 201, resp.text

        await rec("form_checked", questions_count=2, answered=2, missing=0, page=1, pages_total=2)
        await rec("form_filled", form_url=FORM, fields_count=3)
        await rec("form_swept", fields_checked=2, fixed=0, page=1, pages_total=2)
        body = await _check(client, app_id)
        assert (body["decision"], body["reason"]) == ("fill", "form_pages_incomplete")
        await rec("form_checked", questions_count=1, answered=1, missing=0, page=2, pages_total=2)
        body = await _check(client, app_id)
        assert body["reason"] == "form_pages_incomplete", "page 2 is checked but not swept"
        await rec("form_swept", fields_checked=1, fixed=0, page=2, pages_total=2)
        assert (await _check(client, app_id))["reason"] not in (
            "form_pages_incomplete", "form_count_mismatch", "form_not_swept", "unreadable_fields"
        )
        # a bad pair is refused with the usual 422
        bad = await _event(client, app_id, "form_swept", payload={"fields_checked": 1, "fixed": 0, "page": 3, "pages_total": 2})
        assert bad.status_code == 422 and "page cannot be more than pages_total" in bad.text


# ═══════════════════════════════════════════════════════════════════════════
# Bug review 2026-10-18 regressions
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_simulation_mark_after_a_real_submit_never_frees_a_grant_slot(
    authenticated_async_context, fixture_user_id
):
    from src.repositories import pgsync

    def applied_count() -> int:
        with pgsync.connect(str(settings.DB_PATH)) as conn:
            return rules._applied_after(conn, fixture_user_id, None)

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        real = await _bring(client, _ad(1))
        practice = await _bring(client, _ad(2, company="Southwind"))
        await _applied(client, real)
        assert applied_count() == 1
        # a mark recorded AFTER the real submit cannot re-label it
        assert (await _event(client, real, "simulation")).status_code == 201
        assert applied_count() == 1
        # a mark recorded BEFORE the submit is a practice run: not counted
        assert (await _event(client, practice, "simulation")).status_code == 201
        await _applied(client, practice)
        assert applied_count() == 1


def test_mixed_paged_and_unpaged_sweeps_keep_the_unreadable_and_count_stops():
    from src.services.applications import kit

    def ev(i, t, **p):
        return {"id": i, "event_type": t, "payload": p}

    newest_first = [
        ev(6, "form_swept", fields_checked=3, fixed=0, unreadable=["Salary"]),
        ev(4, "form_swept", fields_checked=2, fixed=0, page=2, pages_total=2),
        ev(3, "form_checked", questions_count=2, page=2, pages_total=2),
        ev(2, "form_swept", fields_checked=1, fixed=0, page=1, pages_total=2),
        ev(1, "form_checked", questions_count=1, page=1, pages_total=2),
    ]
    short, unreadable, incomplete = kit._paged_shortfalls(newest_first, 3)
    assert (short, unreadable, incomplete) == (False, False, False)  # the paged view alone misses it


@pytest.mark.asyncio
async def test_sweep_shortfalls_sees_an_unpaged_unreadable_after_paged_pages(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps
    from src.services.applications import kit

    async with authenticated_async_context() as client:
        resp = await client.post("/api/jobs/bring", json={
            "title": "Mixed Pages Engineer", "company": "Northwind", "url": "https://careers.northwind.example/j/mixed",
            "description": "We need an engineer. " * 20,
        })
        assert resp.status_code in (200, 201), resp.text
        app_id = resp.json()["application_id"]

        async def post(t, **p):
            r = await client.post(f"/api/applications/{app_id}/events", json={"event_type": t, "payload": p})
            assert r.status_code == 201, r.text

        await post("form_checked", questions_count=1, answered=1, missing=0, page=1, pages_total=2)
        await post("form_swept", fields_checked=1, fixed=0, page=1, pages_total=2)
        await post("form_checked", questions_count=2, answered=2, missing=0, page=2, pages_total=2)
        await post("form_swept", fields_checked=2, fixed=0, page=2, pages_total=2)
        await post("form_filled", form_url="https://careers.northwind.example/apply", fields_count=3)
        await post("form_swept", fields_checked=3, fixed=0, unreadable=["Salary"])
        db = await api_deps.get_db()
        _short, unreadable, _incomplete = await kit._sweep_shortfalls(db, fixture_user_id, app_id)
        assert unreadable is True


@pytest.mark.asyncio
async def test_the_grant_and_pace_counters_ignore_relabelling(authenticated_async_context, fixture_user_id):
    """Security review 2026-10-18: a simulation mark or a correcting note must never give a grant
    slot back or move the pace anchor; both read every applied the server recorded."""
    from src.api import dependencies as api_deps

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        end = datetime.now(timezone.utc) + timedelta(hours=4)
        assert (await _patch(client, _e(GRANT_N, 3), _e(GRANT_UNTIL, end.isoformat()))).status_code == 200
        app_id = await _bring(client, _ad(1))
        sent = await _event(client, app_id, "applied")
        assert sent.status_code == 201, sent.text
        db = await api_deps.get_db()
        used, anchor = rules.grant_info(fixture_user_id)[1], await rules.last_applied_at(db, fixture_user_id)
        assert used == 1 and anchor is not None
        assert (await _event(client, app_id, "simulation")).status_code == 201
        await _event(client, app_id, "note", detail="not real", corrects_event_id=sent.json()["event_id"])
        assert rules.grant_info(fixture_user_id)[1] == used
        assert await rules.last_applied_at(db, fixture_user_id) == anchor


def test_a_second_form_filled_reopens_every_page_swept_before_it():
    """CodeRabbit 2026-10-18: pages 1-2 swept, the form filled AGAIN, only page 2 re-swept: page 1
    still describes the old answers. The first form_filled (once, after the last page) reopens nothing."""
    from src.services.applications import kit

    def ev(i, t, **p):
        return {"id": i, "event_type": t, "payload": p}

    walk = [
        ev(1, "form_checked", questions_count=1, page=1, pages_total=2),
        ev(2, "form_swept", fields_checked=1, fixed=0, page=1, pages_total=2),
        ev(3, "form_checked", questions_count=1, page=2, pages_total=2),
        ev(4, "form_swept", fields_checked=1, fixed=0, page=2, pages_total=2),
    ]
    newest_first = list(reversed(walk))
    assert kit._paged_shortfalls(newest_first, 2) == (False, False, False)  # first fill at id 5
    resweep = [ev(7, "form_swept", fields_checked=1, fixed=0, page=2, pages_total=2), *newest_first]
    assert kit._paged_shortfalls(resweep, 2, refill_id=6)[2] is True  # refill at id 6: page 1 is stale
    both = [ev(8, "form_swept", fields_checked=1, fixed=0, page=1, pages_total=2), *resweep]
    assert kit._paged_shortfalls(both, 2, refill_id=6) == (False, False, False)


def test_a_smaller_pages_total_cannot_shrink_the_form():
    from src.services.applications import kit

    def ev(i, t, **p):
        return {"id": i, "event_type": t, "payload": p}

    newest_first = [
        ev(3, "form_swept", fields_checked=9, fixed=0, page=1, pages_total=1),
        ev(2, "form_swept", fields_checked=3, fixed=0, page=1, pages_total=3),
        ev(1, "form_checked", questions_count=3, page=1, pages_total=3),
    ]
    assert kit._paged_shortfalls(newest_first, None)[2] is True  # pages 2 and 3 never swept


@pytest.mark.asyncio
async def test_a_token_cannot_move_the_users_timezone(authenticated_async_context, fixture_user_id):
    from httpx import ASGITransport, AsyncClient

    from src.api.main import app

    async with authenticated_async_context() as client:
        tok = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        own = await client.put("/api/auth/me/timezone", json={"timezone": "Europe/London"})
        assert own.status_code == 200, own.text
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {tok}"}
    ) as bot:
        moved = await bot.put("/api/auth/me/timezone", json={"timezone": "Asia/Bangkok"})
        assert moved.status_code in (401, 403), moved.text
