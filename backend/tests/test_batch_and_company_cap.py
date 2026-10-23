"""Owner ask 2026-10-18: "apply more, ~10 a day".

Part A - the per-company limit in the submit gate (``assistant_settings.company_cap`` +
``company_cap_days``, rule 4a of ``may_submit``, ``company_cap_reached``).
Part B - the batch queue (``queued`` / ``unqueued`` note events, ``list_applications
queued=true`` and the "batch" recipe).

Pure tests need no database; the rest go through the real doors on the real
Postgres. Helpers are copied, never imported from another test module (schema isolation).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.profile import assistant_settings as rules
from src.services.profile.edits import ProfileEditError, validate_edit
from src.services.profile.models import AssistantSettings, UserPreferences

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
FORM = "https://careers.northwind.example/apply/7"
CAP = "assistant_settings.company_cap"
DAYS = "assistant_settings.company_cap_days"
DAILY = "assistant_settings.daily_cap"
SCORE = "assistant_settings.apply_min_score"
ABOVE = "assistant_settings.above_line"
BELOW = "assistant_settings.below_line"
PAUSE = "assistant_settings.paused_until"


def _ad(n: int, company: str = "Northwind") -> dict[str, Any]:
    return {
        "title": f"Data Engineer {n}",
        "company": company,
        "location": "Remote",
        "apply_url": f"https://northwind.example/careers/{n}",
        "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
    }


# ═══════════════════════════════════════════════════════════════════════════
# A. The pure gate: rule 4a
# ═══════════════════════════════════════════════════════════════════════════


def _eff(**kw: Any) -> rules.EffectiveSettings:
    kw.setdefault("apply_min_score", 50)
    kw.setdefault("above_line", "auto")
    kw.setdefault("below_line", "auto")
    kw.setdefault("submit_mode", "auto_when_sure")
    return rules.effective(AssistantSettings(**kw))


def _counts(today: int = 0, company: int = 0) -> rules.SubmitCounts:
    return rules.SubmitCounts(submitted_today=today, applied_since_auto_on=1, company_applied_in_window=company)


def _facts(**kw: Any) -> rules.SubmitFacts:
    base: dict[str, Any] = {
        "status": "considering", "has_receipt": False, "submit_override": None, "cv_seen": True,
        "form_filled": True, "form_swept": True, "fit_score": 80, "form_host": "careers.northwind.example",
    }
    base.update(kw)
    return rules.SubmitFacts(**base)


def test_company_cap_is_a_reason_right_after_the_daily_cap():
    reasons = list(rules.REASONS)
    assert reasons.index("company_cap_reached") == reasons.index("daily_cap_reached") + 1


def test_company_cap_stops_at_n_and_not_at_n_minus_one():
    cfg = _eff(company_cap=3)
    stop = rules.may_submit(cfg, _facts(), FORM, _counts(company=3), now=NOW)
    assert (stop.decision, stop.reason) == ("stop", "company_cap_reached")
    assert "3 applications per company in 180 days" in stop.detail
    assert rules.may_submit(cfg, _facts(), FORM, _counts(company=4), now=NOW).reason == "company_cap_reached"
    ok = rules.may_submit(cfg, _facts(), FORM, _counts(company=2), now=NOW)
    assert (ok.decision, ok.reason) == ("submit", "auto_when_sure")


def test_the_stop_names_the_users_own_window():
    cfg = _eff(company_cap=1, company_cap_days=30)
    assert "1 applications per company in 30 days" in rules.may_submit(
        cfg, _facts(), FORM, _counts(company=1), now=NOW
    ).detail


def test_no_company_cap_never_stops_however_many_were_sent():
    cfg = _eff()
    assert cfg.company_cap is None
    assert rules.may_submit(cfg, _facts(), FORM, _counts(company=10_000), now=NOW).reason == "auto_when_sure"


def test_a_stored_yes_never_clears_the_company_cap():
    cfg = _eff(company_cap=1)
    verdict = rules.may_submit(cfg, _facts(approved=True), FORM, _counts(company=1), now=NOW)
    assert (verdict.decision, verdict.reason) == ("stop", "company_cap_reached")


def test_paused_applied_declined_and_the_daily_cap_all_win_before_the_company_cap():
    capped = dict(company_cap=1, daily_cap=1)
    counts = _counts(today=1, company=1)
    assert rules.may_submit(_eff(**capped), _facts(), FORM, counts, now=NOW).reason == "daily_cap_reached"
    assert rules.may_submit(
        _eff(**capped, paused_until="until_resumed"), _facts(), FORM, counts, now=NOW
    ).reason == "paused"
    assert rules.may_submit(_eff(**capped), _facts(has_receipt=True), FORM, counts, now=NOW).reason == "already_applied"
    assert rules.may_submit(_eff(**capped), _facts(declined=True), FORM, counts, now=NOW).reason == "user_declined"
    # ... and it wins over every later rule (the form is not even filled yet)
    assert rules.may_submit(
        _eff(company_cap=1), _facts(form_filled=False), FORM, _counts(company=1), now=NOW
    ).reason == "company_cap_reached"


# ═══════════════════════════════════════════════════════════════════════════
# A. Validation, effective values, risk
# ═══════════════════════════════════════════════════════════════════════════


def test_the_two_new_paths_are_gated_setting_paths():
    for path in (CAP, DAYS):
        assert path in rules.SETTING_PATHS and path in rules.GATED_PATHS


@pytest.mark.parametrize(
    ("path", "value", "ok"),
    [
        (CAP, 1, True), (CAP, 50, True), (CAP, 0, False), (CAP, 51, False), (CAP, -1, False),
        (CAP, True, False), (CAP, "3", False), (CAP, 2.5, False),
        (DAYS, 1, True), (DAYS, 365, True), (DAYS, 0, True), (DAYS, -1, False), (DAYS, 366, False), (DAYS, True, False),
        (DAYS, "30", False),
    ],
)
def test_validate_setting_bounds(path, value, ok):
    if ok:
        assert validate_edit(path, value) == value
        return
    with pytest.raises(ProfileEditError) as info:
        validate_edit(path, value)
    assert info.value.status_code == 422
    assert info.value.detail.startswith(path)  # names the PATH


def test_the_cap_ceiling_is_an_operator_setting(monkeypatch):
    monkeypatch.setattr(settings, "ASSISTANT_COMPANY_CAP_MAX", 5)
    assert validate_edit(CAP, 5) == 5
    with pytest.raises(ProfileEditError):
        validate_edit(CAP, 6)


def test_defaults_are_silent_and_the_window_default_fills_in():
    eff = rules.effective(AssistantSettings())
    assert eff.company_cap is None
    assert eff.company_cap_days == settings.ASSISTANT_COMPANY_CAP_DEFAULT_DAYS == 180
    assert rules.effective(AssistantSettings(company_cap=2, company_cap_days=30)).company_cap_days == 30
    assert rules.effective_path_value(CAP, None) is None
    assert rules.effective_path_value(CAP, 4) == 4
    assert rules.effective_path_value(DAYS, None) == 180
    assert rules.effective_path_value(DAYS, 30) == 30


@pytest.mark.parametrize(
    ("path", "before", "after", "expected"),
    [
        (CAP, 3, 5, "riskier"),
        (CAP, 3, None, "riskier"),
        (CAP, None, 3, "safer"),
        (CAP, 3, 2, "safer"),
        (CAP, 3, 3, "safer"),
        (CAP, None, None, "safer"),
        (DAYS, 180, 30, "riskier"),
        (DAYS, 30, 180, "safer"),
        (DAYS, 90, 90, "safer"),
    ],
)
def test_classify_change(path, before, after, expected):
    assert rules.classify_change(path, before, after) == expected


def test_the_read_model_shows_both_beside_the_daily_cap():
    from src.services.profile.models import UserProfile

    profile = UserProfile(assistant_settings=AssistantSettings(daily_cap=5, company_cap=2))
    view = rules.build_settings_view(profile, [], (None, 0), NOW)
    assert view["daily_cap"] == {"value": 5, "effective": 5}
    assert view["company_cap"] == {"value": 2, "effective": 2}
    assert view["company_cap_days"] == {"value": None, "effective": 180}


# ═══════════════════════════════════════════════════════════════════════════
# Real-database helpers
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
    return await client.post(
        f"/api/applications/{app_id}/events", json={"event_type": event_type, **body}
    )


async def _applied_at(client: AsyncClient, app_id: int, when: datetime) -> None:
    resp = await _event(client, app_id, "applied", occurred_at=when.isoformat())
    assert resp.status_code == 201, resp.text


async def _fill(client: AsyncClient, app_id: int) -> None:
    assert (await _event(client, app_id, "form_filled", payload={"form_url": FORM, "fields_count": 1})).status_code == 201
    assert (await _event(client, app_id, "form_swept", payload={"fields_checked": 1, "fixed": 0})).status_code == 201


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
# A. load_submit_counts against the real database
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_load_submit_counts_counts_other_applications_at_the_same_company(
    authenticated_async_context, fixture_user_id
):
    from src.api import dependencies as api_deps

    now = datetime.now(timezone.utc)
    async with authenticated_async_context() as client:
        target = await _bring(client, _ad(1))
        recent_a = await _bring(client, _ad(2))
        recent_b = await _bring(client, _ad(3))
        old = await _bring(client, _ad(4))
        other_company = await _bring(client, _ad(5, company="Southwind"))
        simulated = await _bring(client, _ad(6))
        await _applied_at(client, recent_a, now - timedelta(days=10))
        await _applied_at(client, recent_b, now - timedelta(days=170))
        await _applied_at(client, old, now - timedelta(days=200))
        await _applied_at(client, other_company, now - timedelta(days=1))
        assert (await _event(client, simulated, "simulation")).status_code == 201
        await _applied_at(client, simulated, now - timedelta(days=2))
        # this application's own applied event never counts against itself
        await _applied_at(client, target, now - timedelta(days=3))
        db = await api_deps.get_db()

        counts = await rules.load_submit_counts(db, fixture_user_id, now, target, window_days=180)
        # a, b inside 180 days; old outside; Southwind another company; simulation ignored; self ignored
        assert counts.company_applied_in_window == 2
        assert (await rules.load_submit_counts(db, fixture_user_id, now, target, window_days=100)).company_applied_in_window == 1
        assert (await rules.load_submit_counts(db, fixture_user_id, now, target, window_days=5)).company_applied_in_window == 0
        # default window when none is passed
        assert (await rules.load_submit_counts(db, fixture_user_id, now, target)).company_applied_in_window == 2
        # no application given (morning_check): nothing is counted
        assert (await rules.load_submit_counts(db, fixture_user_id, now)).company_applied_in_window == 0
        # a retracted simulation mark counts the application again
        # (covered by the practice_info shape; here: the company of the OTHER application sees the target)
        assert (await rules.load_submit_counts(db, fixture_user_id, now, recent_a, window_days=180)).company_applied_in_window == 2 + 1 - 1


@pytest.mark.asyncio
async def test_a_corrected_applied_date_or_a_late_simulation_mark_is_handled(
    authenticated_async_context, fixture_user_id
):
    """CodeRabbit 2026-10-18 (safer than bug review P1): a correction may move an ``applied``
    date INTO the window but never out, so re-dating cannot give a cap slot back; the
    application still counts once. Security review 2026-10-18: a ``simulation`` mark
    recorded AFTER the ``applied`` cannot re-label a real application."""
    from src.api import dependencies as api_deps

    now = datetime.now(timezone.utc)
    async with authenticated_async_context() as client:
        target = await _bring(client, _ad(1))
        moved = await _bring(client, _ad(2))
        relabelled = await _bring(client, _ad(3))
        first = await _event(client, moved, "applied", occurred_at=(now - timedelta(days=10)).isoformat())
        assert first.status_code == 201, first.text
        await _applied_at(client, relabelled, now - timedelta(days=5))
        db = await api_deps.get_db()
        assert (await rules.load_submit_counts(db, fixture_user_id, now, target, window_days=180)).company_applied_in_window == 2
        fix = await _event(
            client, moved, "applied", occurred_at=(now - timedelta(days=300)).isoformat(),
            corrects_event_id=first.json()["event_id"],
        )
        assert fix.status_code == 201, fix.text
        assert (await _event(client, relabelled, "simulation")).status_code == 201
        counts = await rules.load_submit_counts(db, fixture_user_id, now, target, window_days=180)
        # moved: re-dated to 300 days ago, still counted once; relabelled: the late mark is ignored
        assert counts.company_applied_in_window == 2
        # A correction INTO the window counts too: an old applied re-dated to yesterday.
        old = await _bring(client, _ad(4))
        aged = await _event(client, old, "applied", occurred_at=(now - timedelta(days=400)).isoformat())
        assert (await rules.load_submit_counts(db, fixture_user_id, now, target, window_days=180)).company_applied_in_window == 2
        back = await _event(
            client, old, "applied", occurred_at=(now - timedelta(days=1)).isoformat(),
            corrects_event_id=aged.json()["event_id"],
        )
        assert back.status_code == 201, back.text
        assert (await rules.load_submit_counts(db, fixture_user_id, now, target, window_days=180)).company_applied_in_window == 3
        # A NOTE that retracts an applied is not a re-dating: an older applied of that application
        # stays out of the window (CodeRabbit 2026-10-19).
        retracted = await _bring(client, _ad(5))
        recent = await _event(client, retracted, "applied", occurred_at=(now - timedelta(days=2)).isoformat())
        assert recent.status_code == 201, recent.text
        undo = await _event(client, retracted, "note", detail="not sent", corrects_event_id=recent.json()["event_id"])
        assert undo.status_code == 201, undo.text
        old_one = await _event(client, retracted, "applied", occurred_at=(now - timedelta(days=500)).isoformat())
        assert old_one.status_code == 201, old_one.text
        assert (await rules.load_submit_counts(db, fixture_user_id, now, target, window_days=180)).company_applied_in_window == 3


@pytest.mark.asyncio
async def test_a_queue_event_may_only_correct_a_queue_event(authenticated_async_context, fixture_user_id):
    """Security review 2026-10-18: ``queued`` / ``unqueued`` cannot retract a kit or note event."""
    async with authenticated_async_context() as client:
        a = await _bring(client, _ad(1))
        note = await _event(client, a, "note", detail="keep me")
        assert note.status_code == 201
        refused = await _event(client, a, "unqueued", corrects_event_id=note.json()["event_id"])
        assert refused.status_code == 422 and "may only correct" in refused.text
        queued = await _queue(client, a)
        assert queued.status_code == 201
        ok = await _event(client, a, "unqueued", corrects_event_id=queued.json()["event_id"])
        assert ok.status_code == 201, ok.text


@pytest.mark.asyncio
async def test_a_blank_or_unknown_company_counts_zero(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps
    from src.repositories import pgsync

    now = datetime.now(timezone.utc)
    async with authenticated_async_context() as client:
        target = await _bring(client, _ad(1))
        other = await _bring(client, _ad(2))
        await _applied_at(client, other, now - timedelta(days=1))
        db = await api_deps.get_db()
        assert (await rules.load_submit_counts(db, fixture_user_id, now, target)).company_applied_in_window == 1
        for blank in ("", "unknown"):
            conn = pgsync.connect(str(settings.DB_PATH))
            conn.execute("UPDATE jobs SET normalized_company = ?", (blank,))
            conn.commit()
            conn.close()
            counts = await rules.load_submit_counts(db, fixture_user_id, now, target)
            assert counts.company_applied_in_window == 0, blank


@pytest.mark.asyncio
async def test_another_users_applications_are_never_counted(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps

    now = datetime.now(timezone.utc)
    async with authenticated_async_context() as client:
        target = await _bring(client, _ad(1))
        db = await api_deps.get_db()
        assert (await rules.load_submit_counts(db, "someone-else", now, target)).company_applied_in_window == 0


# ═══════════════════════════════════════════════════════════════════════════
# A. check_submit end to end (value presence, hard rule 21)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_check_submit_route_answers_company_cap_reached(authenticated_async_context, fixture_user_id):
    now = datetime.now(timezone.utc)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        target = await _bring(client, _ad(1))
        first = await _bring(client, _ad(2))
        second = await _bring(client, _ad(3))
        await _fill(client, target)
        # the user's own web click: a limit of 2 in 90 days (none -> a number is the safer direction)
        patched = await _patch(client, _e(CAP, 2), _e(DAYS, 90))
        assert patched.status_code == 200, patched.text
        assert [a["path"] for a in patched.json()["applied"]] == [CAP, DAYS]
        await _applied_at(client, first, now - timedelta(days=5))
        resp = await client.get(f"/api/applications/{target}/submit-check", params={"form_url": FORM})
        assert resp.json()["reason"] != "company_cap_reached", "1 of 2 used"
        await _applied_at(client, second, now - timedelta(days=95))  # outside the 90-day window
        resp = await client.get(f"/api/applications/{target}/submit-check", params={"form_url": FORM})
        assert resp.json()["reason"] != "company_cap_reached", "the old one is outside the window"
        third = await _bring(client, _ad(4))
        await _applied_at(client, third, now - timedelta(days=1))
        resp = await client.get(f"/api/applications/{target}/submit-check", params={"form_url": FORM})
        body = resp.json()
        assert (body["decision"], body["reason"]) == ("stop", "company_cap_reached")
        assert "2 applications per company in 90 days" in body["detail"]
        # the settings read model shows both beside daily_cap
        view = (await client.get("/api/assistant-settings")).json()
        assert view["company_cap"] == {"value": 2, "effective": 2}
        assert view["company_cap_days"] == {"value": 90, "effective": 90}
        # the morning check shows them too
        morning = (await client.get("/api/morning-check")).json()["state"]
        assert (morning["company_cap"], morning["company_cap_days"]) == (2, 90)


@pytest.mark.asyncio
async def test_a_riskier_company_cap_change_from_a_token_waits_for_the_user(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await _patch(client, _e(CAP, 2), _e(DAYS, 90))).status_code == 200
        token = await _mint_token(client)
    from src.api.main import app

    bearer = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    async with bearer:
        raised = (await _patch(bearer, _e(CAP, 10))).json()
        assert [w["path"] for w in raised["waiting"]] == [CAP] and raised["applied"] == []
        shorter = (await _patch(bearer, _e(DAYS, 30))).json()
        assert [w["path"] for w in shorter["waiting"]] == [DAYS] and shorter["applied"] == []
        lowered = (await _patch(bearer, _e(CAP, 1), _e(DAYS, 365))).json()
        assert [a["path"] for a in lowered["applied"]] == [CAP, DAYS] and lowered["waiting"] == []


@pytest.mark.asyncio
async def test_check_submit_mcp_tool_answers_company_cap_reached(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    from src.api.mcp_server import mcp_runtime

    now = datetime.now(timezone.utc)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client)
        target = await _bring(client, _ad(1))
        other = await _bring(client, _ad(2))
        await _fill(client, target)
        await _applied_at(client, other, now - timedelta(days=1))
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            # none -> a number is safer: the assistant may set it itself
            result = _payload(await mcp.call_tool("update_profile", {"edits": [_e(CAP, 1)]}))
            assert [a["path"] for a in result["applied"]] == [CAP]
            settings_view = _payload(await mcp.call_tool("get_profile", {}))["settings"]
            assert settings_view["company_cap"] == {"value": 1, "effective": 1}
            assert settings_view["company_cap_days"] == {"value": None, "effective": 180}
            verdict = _payload(await mcp.call_tool("check_submit", {"application_id": target, "form_url": FORM}))
            assert (verdict["decision"], verdict["reason"]) == ("stop", "company_cap_reached")


# ═══════════════════════════════════════════════════════════════════════════
# B. The batch queue: events
# ═══════════════════════════════════════════════════════════════════════════


async def _queue(client: AsyncClient, app_id: int):
    return await _event(client, app_id, "queued")


async def _rows(client: AsyncClient, **params: Any) -> dict[str, Any]:
    resp = await client.get("/api/applications", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _ids(body: dict[str, Any]) -> list[int]:
    return [r["id"] for r in body["applications"]]


def test_queue_events_are_note_events_and_never_move_the_status():
    for event_type in ("queued", "unqueued"):
        assert event_type in settings.APPLICATION_NOTE_EVENT_TYPES
        assert event_type not in settings.APPLICATION_STATUS_EVENT_TYPES


@pytest.mark.asyncio
async def test_queued_and_unqueued_checks(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, _ad(1))
        # unqueued when nothing is queued
        resp = await _event(client, app_id, "unqueued")
        assert resp.status_code == 409 and resp.json()["detail"] == "not queued"
        # a payload is refused
        resp = await _event(client, app_id, "queued", payload={"why": "x"})
        assert resp.status_code == 422
        resp = await _event(client, app_id, "unqueued", payload={"why": "x"})
        assert resp.status_code == 422
        # queue it (detail is optional free text), and the status stays considering
        resp = await _event(client, app_id, "queued", detail="batch one")
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "considering"
        # already queued
        resp = await _queue(client, app_id)
        assert resp.status_code == 409 and resp.json()["detail"] == "already queued"
        # unqueue, then it can be queued again
        assert (await _event(client, app_id, "unqueued", detail="user changed their mind")).status_code == 201
        assert (await _event(client, app_id, "unqueued")).status_code == 409
        assert (await _queue(client, app_id)).status_code == 201


@pytest.mark.asyncio
async def test_an_application_that_is_not_open_cannot_be_queued(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        withdrawn = await _bring(client, _ad(1))
        assert (await _event(client, withdrawn, "withdrawn")).status_code == 201
        resp = await _queue(client, withdrawn)
        assert resp.status_code == 409 and resp.json()["detail"] == "not open"
        sent = await _bring(client, _ad(2))
        receipt = await client.post(f"/api/applications/{sent}/receipt", json={"channel": "company_site"})
        assert receipt.status_code == 201, receipt.text
        resp = await _queue(client, sent)
        assert resp.status_code == 409 and resp.json()["detail"] == "not open"
        # someone else's / missing application is a 404, never a queue entry
        assert (await _queue(client, 999_999)).status_code == 404


@pytest.mark.asyncio
async def test_queue_events_are_audited_without_free_text(authenticated_async_context, fixture_user_id, caplog):
    import logging

    logger = logging.getLogger("job360.audit")
    records: list[dict[str, Any]] = []

    class _Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(dict(record.__dict__))

    handler = _Grab()
    old = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        async with authenticated_async_context() as client:
            app_id = await _bring(client, _ad(1, company="Zorblax Secret Corp"))
            assert (await _event(client, app_id, "queued", detail="private words")).status_code == 201
            assert (await _queue(client, app_id)).status_code == 409
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)
    ok = [r for r in records if r.get("event") == "queue_event"]
    refused = [r for r in records if r.get("event") == "queue_event_refused"]
    assert len(ok) == 1 and ok[0]["application_id"] == app_id and ok[0]["event_type"] == "queued"
    assert len(refused) == 1 and refused[0]["reason"] == "already_queued" and refused[0]["status"] == 409
    flat = json.dumps([{k: str(v) for k, v in r.items()} for r in ok + refused])
    assert "private words" not in flat and "Zorblax" not in flat


# ═══════════════════════════════════════════════════════════════════════════
# B. The batch queue: list_applications queued=true
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_queue_is_first_in_first_out_with_positions(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        a = await _bring(client, _ad(1))
        b = await _bring(client, _ad(2))
        c = await _bring(client, _ad(3))
        d = await _bring(client, _ad(4))  # never queued
        for app_id in (c, a, b):
            assert (await _queue(client, app_id)).status_code == 201
        queue = await _rows(client, queued="true")
        assert _ids(queue) == [c, a, b] and queue["total"] == 3
        assert [r["queue_position"] for r in queue["applications"]] == [1, 2, 3]
        assert all(r["queued_at"] for r in queue["applications"])
        # the plain list carries the same two fields on every row, null when not queued
        everything = {r["id"]: r for r in (await _rows(client))["applications"]}
        assert [everything[i]["queue_position"] for i in (c, a, b)] == [1, 2, 3]
        assert everything[d]["queue_position"] is None and everything[d]["queued_at"] is None
        # a page of the queue keeps the position in the WHOLE queue
        page = await _rows(client, queued="true", limit=1, offset=1)
        assert _ids(page) == [a] and page["total"] == 3 and page["applications"][0]["queue_position"] == 2
        # a status filter intersects with the queue
        assert _ids(await _rows(client, queued="true", status="applied")) == []
        # re-queueing after an unqueue goes to the back
        assert (await _event(client, c, "unqueued")).status_code == 201
        assert (await _queue(client, c)).status_code == 201
        assert _ids(await _rows(client, queued="true")) == [a, b, c]


@pytest.mark.asyncio
async def test_unqueued_and_closed_applications_drop_out_and_positions_close_up(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        a = await _bring(client, _ad(1))
        b = await _bring(client, _ad(2))
        c = await _bring(client, _ad(3))
        d = await _bring(client, _ad(4))
        for app_id in (a, b, c, d):
            assert (await _queue(client, app_id)).status_code == 201
        assert (await _event(client, b, "unqueued", detail="blocked: login wall")).status_code == 201
        # applied: the status moves off considering, so it is out of the queue without any event
        assert (await client.post(f"/api/applications/{a}/receipt", json={"channel": "company_site"})).status_code == 201
        # withdrawn likewise
        assert (await _event(client, d, "withdrawn")).status_code == 201
        queue = await _rows(client, queued="true")
        assert _ids(queue) == [c] and queue["applications"][0]["queue_position"] == 1 and queue["total"] == 1
        rows = {r["id"]: r for r in (await _rows(client))["applications"]}
        for gone in (a, b, d):
            assert rows[gone]["queue_position"] is None and rows[gone]["queued_at"] is None, gone


@pytest.mark.asyncio
async def test_a_corrected_queued_event_is_not_in_the_queue(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        a = await _bring(client, _ad(1))
        b = await _bring(client, _ad(2))
        queued_a = await _queue(client, a)
        assert queued_a.status_code == 201
        assert (await _queue(client, b)).status_code == 201
        assert _ids(await _rows(client, queued="true")) == [a, b]
        # a correcting event supersedes the one it names
        fix = await _event(
            client, a, "note", detail="queued the wrong job", corrects_event_id=queued_a.json()["event_id"]
        )
        assert fix.status_code == 201, fix.text
        queue = await _rows(client, queued="true")
        assert _ids(queue) == [b] and queue["applications"][0]["queue_position"] == 1
        # the retraction is of the event, so the application can be queued again
        assert (await _queue(client, a)).status_code == 201
        assert _ids(await _rows(client, queued="true")) == [b, a]


@pytest.mark.asyncio
async def test_the_queue_is_per_user(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps
    from src.services.applications import spine

    async with authenticated_async_context() as client:
        a = await _bring(client, _ad(1))
        assert (await _queue(client, a)).status_code == 201
        db = await api_deps.get_db()
        assert set(await spine.queued_at_by_app(db, fixture_user_id)) == {a}
        assert await spine.queued_at_by_app(db, "someone-else") == {}
        assert await spine.queued_at_by_app(db, fixture_user_id, []) == {}
        assert set(await spine.queued_at_by_app(db, fixture_user_id, [a, 999_999])) == {a}


@pytest.mark.asyncio
async def test_mcp_queue_round_trip(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        token = await _mint_token(client)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            ids = [_payload(await mcp.call_tool("bring_job", {**_ad(n)}))["application_id"] for n in (1, 2, 3)]
            for app_id in (ids[2], ids[0]):
                _payload(await mcp.call_tool("record_event", {"application_id": app_id, "event_type": "queued"}))
            refused = await mcp.call_tool("record_event", {"application_id": ids[0], "event_type": "queued"})
            assert refused.is_error and "already queued" in refused.content[0].text
            listed = _payload(await mcp.call_tool("list_applications", {"queued": True}))
            assert [r["id"] for r in listed["applications"]] == [ids[2], ids[0]]
            assert [r["queue_position"] for r in listed["applications"]] == [1, 2]
            everything = _payload(await mcp.call_tool("list_applications", {}))
            assert {r["id"]: r["queue_position"] for r in everything["applications"]}[ids[1]] is None


# ═══════════════════════════════════════════════════════════════════════════
# B. The recipe
# ═══════════════════════════════════════════════════════════════════════════


def test_the_batch_recipe_is_listed_short_and_names_the_gate():
    from src.api.routes.recipes import RECIPE_NAMES, load_recipe

    assert "batch" in RECIPE_NAMES
    assert RECIPE_NAMES.index("batch") == RECIPE_NAMES.index("apply") + 1
    recipe = load_recipe("batch")
    assert recipe.title.startswith("360-batch")
    lines = recipe.text.splitlines()
    assert len(lines) <= 70
    assert "Print this checklist and tick each step:" in recipe.text
    text = " ".join(recipe.text.lower().split())
    assert "check_submit" in text and "company_cap_reached" in text and "daily_cap_reached" in text
    assert "queued=true" in text and "never solve a captcha" in text and "never store or type a password" in text
    assert 'get_recipe("apply")' in recipe.text


def test_run_360_batch_is_in_the_mcp_instructions():
    from src.api import mcp_server

    assert "run 360 <daily|hunt|batch>" in mcp_server.instructions()
