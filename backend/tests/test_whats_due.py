"""Owner 2026-10-23 - "what is due now?" (``whats_due`` / ``mark_done``, migration 0082).

The user's own scheduled task asks Job360 which duties are due; Job360 runs nothing. Pure tests
pin the due rules (elapsed time, never fixed slots, so late runs heal; the batch only when the user
lets the assistant apply on its own); API tests go through the real doors and pin the security
lines: the user comes from the session/token, a mark is a closed value, the read writes nothing,
and both are rate limited. Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.profile import schedule
from src.services.profile.models import UserPreferences

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
HUNT = "assistant_settings.hunt_every_hours"
SUBMIT = "assistant_settings.submit_mode"
INBOX = "preferences.daily_check"
EVERY = "preferences.check_every"
PAUSE = "assistant_settings.paused_until"


def _facts(**kw: Any) -> schedule.DueFacts:
    base: dict[str, Any] = {
        "hunt_every_hours": None, "inbox_mode": "", "check_every": "", "paused": False, "apply_on_own": False,
        "queued": 0, "daily_left": None, "weekly_left": None, "inside_hours": True, "stamps": {},
    }
    base.update(kw)
    return schedule.DueFacts(**base)


def _duties(answer: dict[str, Any]) -> list[str]:
    return [d["duty"] for d in answer["due"]]


# ── Pure rules ───────────────────────────────────────────────────────────────


def test_nothing_set_means_nothing_due_and_no_next_time():
    """Rule #29: an empty shelf stays silent - no duty is invented for the user."""
    answer = schedule.due_now(_facts(), NOW)
    assert answer["nothing_due"] is True and answer["due"] == []
    assert answer["next_due_at"] == {}
    assert answer["expected_every_hours"] == settings.SCHEDULE_TICK_HOURS
    assert answer["batch_hold"] == "not_allowed"


def test_a_hunt_never_done_is_due_now_then_counts_from_its_last_run():
    first = schedule.due_now(_facts(hunt_every_hours=6), NOW)
    assert first["due"] == [{"duty": "hunt", "recipe": "hunt", "reason": "never_done"}]
    done = NOW - timedelta(hours=5)
    later = schedule.due_now(_facts(hunt_every_hours=6, stamps={"hunt": done}), NOW)
    assert later["nothing_due"] is True
    assert later["next_due_at"]["hunt"] == (done + timedelta(hours=6)).isoformat(timespec="seconds")
    elapsed = schedule.due_now(_facts(hunt_every_hours=6, stamps={"hunt": NOW - timedelta(hours=6)}), NOW)
    assert elapsed["due"][0]["reason"] == "interval_elapsed"


def test_a_late_or_skipped_run_heals_itself_never_fixed_slots():
    """Three days of missed ticks: still due exactly once, not three times, and counted from now on."""
    answer = schedule.due_now(_facts(hunt_every_hours=6, stamps={"hunt": NOW - timedelta(days=3)}), NOW)
    assert _duties(answer) == ["hunt"]


@pytest.mark.parametrize(
    ("mode", "every", "hours"),
    [("auto", "3h", 3), ("scheduled", "6h", 6), ("auto", "", 24), ("ask", "1h", None),
     ("paused", "1h", None), ("declined", "1h", None), ("", "1h", None)],
)
def test_the_inbox_runs_on_a_schedule_only_in_auto_mode(mode, every, hours):
    assert schedule.inbox_every_hours(mode, every) == hours
    answer = schedule.due_now(_facts(inbox_mode=mode, check_every=every), NOW)
    assert _duties(answer) == (["inbox"] if hours else [])
    if hours:
        assert answer["due"][0]["recipe"] == "daily"


@pytest.mark.parametrize(
    ("kw", "hold"),
    [
        ({"paused": True}, "paused"),
        ({"apply_on_own": False}, "not_allowed"),
        ({"queued": 0}, "queue_empty"),
        ({"daily_left": 0}, "daily_cap_reached"),
        ({"weekly_left": 0}, "weekly_cap_reached"),
        ({"inside_hours": False}, "outside_hours"),
    ],
)
def test_the_batch_is_held_for_each_reason_alone(kw, hold):
    ready = {"apply_on_own": True, "queued": 3, "daily_left": 5, "weekly_left": 9, "inside_hours": True}
    assert schedule.due_now(_facts(**ready), NOW)["due"] == [
        {"duty": "batch", "recipe": "batch", "reason": "queue_waiting"}
    ]
    answer = schedule.due_now(_facts(**{**ready, **kw}), NOW)
    assert answer["batch_hold"] == hold and "batch" not in _duties(answer)
    assert hold in schedule.BATCH_HOLDS


def test_a_pause_stops_only_the_batch_not_the_hunt_or_inbox():
    answer = schedule.due_now(
        _facts(paused=True, hunt_every_hours=6, inbox_mode="auto", check_every="3h", apply_on_own=True, queued=2), NOW
    )
    assert _duties(answer) == ["inbox", "hunt"]


def test_the_run_order_is_inbox_then_hunt_then_batch():
    answer = schedule.due_now(
        _facts(hunt_every_hours=1, inbox_mode="auto", check_every="1h", apply_on_own=True, queued=1), NOW
    )
    assert _duties(answer) == ["inbox", "hunt", "batch"]


def test_the_answer_holds_closed_values_only_never_instructions():
    answer = schedule.due_now(_facts(hunt_every_hours=6, inbox_mode="auto", apply_on_own=True, queued=1), NOW)
    assert set(answer) == {
        "due", "nothing_due", "next_due_at", "batch_hold", "last_done_at", "last_tick_at", "expected_every_hours",
        "checked_at",
    }
    for item in answer["due"]:
        assert item["duty"] in settings.SCHEDULE_DUTIES
        assert item["recipe"] == settings.SCHEDULE_DUTY_RECIPES[item["duty"]]
        assert item["reason"] in ("never_done", "interval_elapsed", "queue_waiting")


# ── Through the real doors ───────────────────────────────────────────────────


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


async def _patch(client: AsyncClient, path: str, value: Any) -> None:
    resp = await client.patch("/api/profile", json={"edits": [{"path": path, "value": value}]})
    assert resp.status_code == 200, resp.text


async def _due(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/schedule/due")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _row_count() -> int:
    from src.repositories import pgsync

    with pgsync.connect(str(settings.DB_PATH)) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM assistant_duty_runs").fetchone()[0])


@pytest.mark.asyncio
async def test_hunt_due_then_marked_done_then_not_due(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await _due(client))["nothing_due"] is True
        await _patch(client, HUNT, 6)
        answer = await _due(client)
        assert [d["duty"] for d in answer["due"]] == ["hunt"]
        before = _row_count()
        await _due(client)
        assert _row_count() == before  # the read writes nothing
        done = await client.post("/api/schedule/done", json={"duty": "hunt"})
        assert done.status_code == 200, done.text
        assert done.json()["duty"] == "hunt" and done.json()["last_tick_at"] == done.json()["done_at"]
        after = await _due(client)
        assert after["nothing_due"] is True
        assert after["last_done_at"]["hunt"] is not None and after["last_tick_at"] is not None
        assert after["next_due_at"]["hunt"] is not None


@pytest.mark.parametrize("bad", [{"duty": "submit"}, {"duty": ""}, {"duty": "hunt", "user_id": "x"}, {}])
@pytest.mark.asyncio
async def test_mark_done_takes_a_closed_duty_and_nothing_else(authenticated_async_context, fixture_user_id, bad):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await client.post("/api/schedule/done", json=bad)
        assert resp.status_code == 422, resp.text
    assert _row_count() == 0


@pytest.mark.asyncio
async def test_mark_inbox_checked_also_stamps_the_inbox_duty(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(client, INBOX, "auto")
        await _patch(client, EVERY, "6h")
        assert [d["duty"] for d in (await _due(client))["due"]] == ["inbox"]
        app = await client.post("/api/jobs/bring", json={
            "title": "Data Engineer", "company": "Northwind", "location": "Remote",
            "apply_url": "https://northwind.example/careers/7",
            "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
        })
        assert app.status_code == 200, app.text
        app_id = app.json()["application_id"]
        resp = await client.post("/api/applications/inbox-checked", json={"application_ids": [app_id]})
        assert resp.status_code == 200, resp.text
        answer = await _due(client)
        assert answer["nothing_due"] is True and answer["last_done_at"]["inbox"] is not None


@pytest.mark.asyncio
async def test_the_batch_is_due_only_when_the_user_lets_the_assistant_apply(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app = await client.post("/api/jobs/bring", json={
            "title": "Data Engineer", "company": "Northwind", "location": "Remote", "queue": True,
            "apply_url": "https://northwind.example/careers/7",
            "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
        })
        assert app.status_code == 200, app.text
        held = await _due(client)
        assert held["batch_hold"] == "not_allowed" and held["nothing_due"] is True
        await _patch(client, SUBMIT, "auto_when_sure")  # the web user's own click
        assert [d["duty"] for d in (await _due(client))["due"]] == ["batch"]
        await _patch(client, PAUSE, "until_resumed")
        assert (await _due(client))["batch_hold"] == "paused"


@pytest.mark.asyncio
async def test_a_token_sees_and_marks_only_its_own_user(authenticated_async_context, fixture_user_id):
    """Rules #12/#25: the user comes from the token; one user's marks never touch another's."""
    from src.api.main import app

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(client, HUNT, 6)
        token = (await client.post("/api/tokens", json={"name": "scheduler"})).json()["token"]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
        ) as agent:
            assert [d["duty"] for d in (await _due(agent))["due"]] == ["hunt"]
            assert (await agent.post("/api/schedule/done", json={"duty": "hunt"})).status_code == 200
        assert (await _due(client))["nothing_due"] is True
    from src.repositories import pgsync

    with pgsync.connect(str(settings.DB_PATH)) as conn:
        users = {r[0] for r in conn.execute("SELECT user_id FROM assistant_duty_runs").fetchall()}
    assert users == {fixture_user_id}


@pytest.mark.asyncio
async def test_anonymous_callers_are_refused(authenticated_async_context):
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/schedule/due")).status_code == 401
        assert (await anon.post("/api/schedule/done", json={"duty": "hunt"})).status_code == 401


@pytest.mark.asyncio
async def test_reads_and_marks_are_rate_limited(authenticated_async_context, fixture_user_id, monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULE_READS_MAX_PER_HOUR", 2)
    monkeypatch.setattr(settings, "SCHEDULE_MARKS_MAX_PER_HOUR", 1)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await client.get("/api/schedule/due")).status_code == 200
        assert (await client.get("/api/schedule/due")).status_code == 200
        assert (await client.get("/api/schedule/due")).status_code == 429
        assert (await client.post("/api/schedule/done", json={"duty": "inbox"})).status_code == 200
        assert (await client.post("/api/schedule/done", json={"duty": "inbox"})).status_code == 429


@pytest.mark.asyncio
async def test_the_mcp_tools_answer_through_the_same_routes(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    tools = m.build_server()._tool_manager
    token = m._current_user.set(
        CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc")
    )
    try:
        async with authenticated_async_context() as client:
            _seed_profile(fixture_user_id)
            await _patch(client, HUNT, 12)
            due = await tools.get_tool("whats_due").fn()
            assert [d["duty"] for d in due["due"]] == ["hunt"]
            marked = await tools.get_tool("mark_done").fn(duty="hunt")
            assert marked["duty"] == "hunt"
            assert (await tools.get_tool("whats_due").fn())["nothing_due"] is True
            with pytest.raises(Exception):  # noqa: B017 - a closed set: anything else is refused
                await tools.get_tool("mark_done").fn(duty="apply")
    finally:
        m._current_user.reset(token)
