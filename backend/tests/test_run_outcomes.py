"""Owner 2026-10-25 - how a scheduled run ended: mark_done outcome + reason, back-off, problem cards.

A failed run adds one to the duty's failures in a row and the duty waits 2h, 4h, 8h... (at most 24h)
instead of retrying every hour; done or partial resets it. After 3 failures in a row Home and Needs you
show ONE plain card with what to do. Closed sets only; the user comes from the session / token.
Helpers are defined here, never imported from another test module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.profile import schedule
from src.services.profile.models import CVData, UserPreferences, UserProfile

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
HUNT = "assistant_settings.hunt_every_hours"


def _facts(**kw: Any) -> schedule.DueFacts:
    base: dict[str, Any] = {
        "hunt_every_hours": None, "inbox_mode": "", "check_every": "", "paused": False, "apply_on_own": False,
        "queued": 0, "daily_left": None, "weekly_left": None, "inside_hours": True, "stamps": {},
    }
    base.update(kw)
    return schedule.DueFacts(**base)


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Python.", name="Ada"),
                             preferences=UserPreferences(target_job_titles=["Data Engineer"])),
                 user_id, source_action="cv_upload")


async def _mark(client: AsyncClient, **body: Any):
    return await client.post("/api/schedule/done", json={"duty": "hunt", **body})


# ── Pure rules ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("n", "hours"), [(1, 2), (2, 4), (3, 8), (4, 16), (5, 24), (9, 24)])
def test_backoff_doubles_and_stops_at_a_day(n, hours):
    assert schedule.backoff_hours(n) == hours


def test_a_failed_hunt_waits_its_backoff_not_the_interval():
    last = NOW - timedelta(hours=3)
    waiting = schedule.due_now(_facts(hunt_every_hours=1, stamps={"hunt": last}, failures={"hunt": 2}), NOW)
    assert waiting["nothing_due"] is True  # 2 failures = 4h, only 3h passed (the 1h interval would be due)
    assert waiting["next_due_at"]["hunt"] == (last + timedelta(hours=4)).isoformat(timespec="seconds")
    assert waiting["failures_in_row"]["hunt"] == 2
    later = schedule.due_now(_facts(hunt_every_hours=1, stamps={"hunt": NOW - timedelta(hours=5)},
                                    failures={"hunt": 2}), NOW)
    assert [d["duty"] for d in later["due"]] == ["hunt"]


def test_a_failed_batch_waits_too():
    ready = {"apply_on_own": True, "queued": 2, "daily_left": 3, "weekly_left": 5}
    held = schedule.due_now(_facts(**ready, stamps={"batch": NOW - timedelta(minutes=30)}, failures={"batch": 1}), NOW)
    assert "batch" not in [d["duty"] for d in held["due"]]
    assert [d["duty"] for d in schedule.due_now(_facts(**ready), NOW)["due"]] == ["batch"]


def test_a_card_only_after_three_failures_in_plain_words():
    assert schedule.problems({"hunt": {"failures": 2, "reason": "sign_in_needed", "done_at": "t"}}) == []
    [card] = schedule.problems({"hunt": {"failures": 3, "reason": "sign_in_needed", "done_at": "t"}})
    assert card["title"] == "Hunt failed 3 times in a row: a connected app needs you to sign in again."
    assert "sign in again" in card["what_to_do"]
    [odd] = schedule.problems({"batch": {"failures": 4, "reason": "weird", "done_at": "t"}})
    assert odd["reason"] == "error" and odd["label"] == "Apply"
    for reason in schedule.RUN_REASONS:
        assert reason in schedule.REASON_WORDS


# ── Through the real doors ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_three_failures_open_one_card_and_a_done_run_clears_it(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        for n in (1, 2, 3):
            out = await _mark(client, outcome="failed", reason="sign_in_needed")
            assert out.status_code == 200, out.text
            assert out.json()["failures_in_row"] == n
        [card] = (await client.get("/api/schedule/problems")).json()["problems"]
        assert card["duty"] == "hunt" and card["failures_in_row"] == 3 and "sign in again" in card["what_to_do"]
        await client.patch("/api/profile", json={"edits": [{"path": HUNT, "value": 1}]})
        due = (await client.get("/api/schedule/due")).json()
        assert due["failures_in_row"]["hunt"] == 3 and due["nothing_due"] is True  # waits 8h, not 1h
        assert (await _mark(client, outcome="partial", reason="rate_limited")).json()["failures_in_row"] == 0
        assert (await client.get("/api/schedule/problems")).json()["problems"] == []


@pytest.mark.asyncio
async def test_outcome_and_reason_are_closed_values(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        for bad in ({"outcome": "failed"}, {"outcome": "crashed", "reason": "error"},
                    {"outcome": "failed", "reason": "the site said <script>"}):
            assert (await _mark(client, **bad)).status_code == 422, bad
        ok = await _mark(client)
        assert ok.status_code == 200 and ok.json()["outcome"] == "done"


@pytest.mark.asyncio
async def test_the_mcp_tool_passes_outcome_and_reason(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    tools = m.build_server()._tool_manager
    token = m._current_user.set(
        CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc")
    )
    try:
        async with authenticated_async_context() as client:
            _seed(fixture_user_id)
            out = await tools.get_tool("mark_done").fn(duty="inbox", outcome="failed", reason="tool_unavailable")
            assert out["outcome"] == "failed" and out["failures_in_row"] == 1
            with pytest.raises(Exception):  # noqa: B017 - a reason outside the closed list is refused
                await tools.get_tool("mark_done").fn(duty="inbox", outcome="failed", reason="anything")
            assert (await client.get("/api/schedule/due")).json()["failures_in_row"]["inbox"] == 1
    finally:
        m._current_user.reset(token)


# ── Owner 2026-10-30: needs_user does not end a batch run while a queued job needs no owner input ──────────


async def _queued_job(client: AsyncClient, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "Remote", "queue": True,
        "apply_url": f"https://northwind.example/careers/{n}", "description": "Build the pipelines. " * 8,
    })
    assert resp.status_code == 200 and resp.json()["queued"] is True, resp.text
    return int(resp.json()["application_id"])


async def _stop_batch(client: AsyncClient, **body: Any):
    return await client.post("/api/schedule/done", json={
        "duty": "batch", "outcome": "partial", "reason": "needs_user", **body})


@pytest.mark.asyncio
async def test_needs_user_is_refused_while_a_queued_job_needs_no_owner_input(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        first, second = await _queued_job(client, 1), await _queued_job(client, 2)
        refused = await _stop_batch(client)
        assert refused.status_code == 409, refused.text
        detail = refused.json()["detail"]
        assert "2 queued jobs still need no owner input" in detail
        assert f"next: application {first} (Northwind 1)" in detail and str(second) in detail
        # a form filled on the first row moves "next" to the second
        filled = await client.post(f"/api/applications/{first}/events", json={
            "event_type": "form_filled", "payload": {"form_url": "https://northwind.example/careers/1/apply",
                                                     "fields_count": 3}})
        assert filled.status_code == 201, filled.text
        again = await _stop_batch(client)
        assert again.status_code == 409 and f"next: application {second} (Northwind 2)" in again.json()["detail"]
        assert "1 queued jobs" in again.json()["detail"]
        # an open ask on the second row: now every queued job waits on the owner or is filled
        ask = await client.post("/api/asks", json={"question": "Notice period?", "application_id": second})
        assert ask.status_code in (200, 201), ask.text
        ok = await _stop_batch(client)
        assert ok.status_code == 200, ok.text
        assert ok.json()["outcome"] == "partial"


@pytest.mark.asyncio
async def test_a_blocked_job_no_longer_blocks_the_needs_user_stop(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _queued_job(client, 3)
        assert (await _stop_batch(client)).status_code == 409
        stuck = await client.post(f"/api/applications/{app_id}/events", json={
            "event_type": "blocked", "payload": {"reason": "captcha", "page_host": "jobs.northwind.example"}})
        assert stuck.status_code == 201, stuck.text
        assert (await _stop_batch(client)).status_code == 200


@pytest.mark.asyncio
async def test_partial_with_error_or_no_work_is_the_same_early_stop(authenticated_async_context, fixture_user_id):
    # 2026-10-30 17:42 run: refused needs_user, the run ended with partial/error after one job.
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _queued_job(client, 5)
        for reason in ("error", "no_work", None):
            resp = await _stop_batch(client, reason=reason)
            assert resp.status_code == 409, (reason, resp.text)
            assert f"next: application {app_id} (Northwind 5)" in resp.json()["detail"]
            assert "not a reason to stop" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_only_batch_partial_without_a_blocker_is_ever_refused(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        await _queued_job(client, 4)
        assert (await _stop_batch(client)).status_code == 409
        for body in (
            {"duty": "batch", "outcome": "done"},
            {"duty": "batch", "outcome": "partial", "reason": "rate_limited"},
            {"duty": "batch", "outcome": "failed", "reason": "needs_user"},
            {"duty": "batch", "outcome": "partial", "reason": "network_blocked"},
            {"duty": "hunt", "outcome": "partial", "reason": "needs_user"},
            {"duty": "inbox", "outcome": "partial", "reason": "needs_user"},
        ):
            resp = await client.post("/api/schedule/done", json=body)
            assert resp.status_code == 200, (body, resp.text)
            assert resp.json()["duty"] == body["duty"] and resp.json()["outcome"] == body["outcome"]
