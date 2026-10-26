"""The held form survives a deploy (owner, 2026-10-26).

A restart wipes Job360's in-process browser pool. On boot, ``browser_agent.restore`` replays the fill
script of every form whose CAPTCHA hold (``by: browser``) would still have been running, and holds it for
the REMAINING time only, so "Finish it here" keeps working with no click. The browser is faked.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from tests import test_browser_refill as refill_tests
from tests.test_browser_refill import _Ctx, _db, _filled_and_blocked, _job, _open_asks

# The refill tests' fake pool, as ``fake_pool``.
fake_pool = refill_tests.fake_browser


def _backdate(user_id: str, app_id: int, minutes: float) -> None:
    """Move every event and fill step of the application ``minutes`` into the past (the old process's time)."""
    from src.core import settings
    from src.repositories import pgsync

    when = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        for table in ("application_events", "application_fill_steps"):
            conn.execute(
                f"UPDATE {table} SET recorded_at = ? WHERE user_id = ? AND application_id = ?",  # noqa: S608
                (when, user_id, app_id),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def replays(monkeypatch):
    """A fake replay that opens the job's browser (as the real one does) and records which jobs it ran."""
    from src.services.browser_agent import agent, refill

    ran: list[int] = []

    async def fake_replay(pool, user_id, application_id, form_url, steps, *, files, load_state=None):
        ran.append(application_id)
        pool._sessions[agent.browser_key(user_id, application_id)] = agent.Session(context=_Ctx(), page=None)  # noqa: SLF001
        return {"status": "ok", "steps_ok": len(steps), "steps_failed": []}

    monkeypatch.setattr(refill, "replay", fake_replay)
    return ran


async def _app(client, n: int) -> int:
    brought = await client.post("/api/jobs/bring", json={
        "title": f"Engineer {n}", "company": "Acme", "location": "London",
        "description": "Build services. " * 10, "apply_url": "https://jobs.acme.com/apply/7",
    })
    return int(brought.json()["application_id"])


def _finish(asks: list[dict[str, Any]], app_id: int) -> dict[str, Any]:
    (ask,) = [a for a in asks if a["application_id"] == app_id and a["status"] == "open"]
    return ask


@pytest.mark.asyncio
async def test_a_held_captcha_form_is_restored_on_boot_for_the_remaining_time(
    authenticated_async_context, fake_pool, replays
):
    from src.services.browser_agent import agent, restore

    async with authenticated_async_context() as client:
        app_id = await _job(client)
        await _filled_and_blocked(client, app_id)
        user_id = authenticated_async_context.fixture_user_id
        _backdate(user_id, app_id, 20)  # the hold started 20 minutes ago in the old process
        fake_pool._sessions.clear()  # noqa: SLF001 - the deploy restarted the process
        assert _finish(await _open_asks(client), app_id)["finish_here"] is False

        results = await restore.restore_held_forms()
        assert [(r["application_id"], r["result"]) for r in results] == [(app_id, "ok")], results
        assert replays == [app_id]
        ask = _finish(await _open_asks(client), app_id)
        assert ask["finish_here"] is True, "Finish it here works again with no click"
        assert 0 < ask["held_seconds"] <= agent.HANDOFF_HOLD_SECONDS - 20 * 60, "the remaining time, never a fresh 30"
        assert ask["held_seconds"] > agent.HANDOFF_HOLD_SECONDS - 22 * 60


@pytest.mark.asyncio
async def test_a_second_deploy_never_extends_the_hold_past_its_first_end(
    authenticated_async_context, fake_pool, replays
):
    """reviewer-bugs P2 on #1292: the restore's own form_filled must not re-arm the hold's start."""
    from src.services.browser_agent import agent, restore

    async with authenticated_async_context() as client:
        app_id = await _job(client)
        await _filled_and_blocked(client, app_id)
        _backdate(authenticated_async_context.fixture_user_id, app_id, 20)
        fake_pool._sessions.clear()  # noqa: SLF001 - first deploy
        assert [r["result"] for r in await restore.restore_held_forms()] == ["ok"]
        fake_pool._sessions.clear()  # noqa: SLF001 - a second deploy right after
        async with _db() as db:
            (c,) = await restore.candidates(db, datetime.now(timezone.utc))
        left = (c["ends_at"] - datetime.now(timezone.utc)).total_seconds()
        assert left <= agent.HANDOFF_HOLD_SECONDS - 20 * 60, "the first hold's end stands"


@pytest.mark.asyncio
async def test_a_block_older_than_the_hold_window_is_not_restored(authenticated_async_context, fake_pool, replays):
    from src.services.browser_agent import restore

    async with authenticated_async_context() as client:
        app_id = await _job(client)
        await _filled_and_blocked(client, app_id)
        _backdate(authenticated_async_context.fixture_user_id, app_id, 31)
        fake_pool._sessions.clear()  # noqa: SLF001
        assert await restore.restore_held_forms() == []
        assert replays == []
        assert _finish(await _open_asks(client), app_id)["finish_here"] is False


@pytest.mark.asyncio
async def test_a_paused_user_a_login_block_and_a_callers_block_are_not_restored(
    authenticated_async_context, fake_pool, replays
):
    from src.api.auth_deps import CurrentUser
    from src.services.applications import blocked
    from src.services.browser_agent import restore

    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        held = await _job(client)
        await _filled_and_blocked(client, held)
        login, look_alike = await _app(client, 2), await _app(client, 3)
        for app_id in (login, look_alike):
            typed = {"action": "type", "ref": "e1_ab", "application_id": app_id}
            assert (await client.post("/api/browser", json=typed)).status_code == 200
        async with _db() as db:  # a sign-in wall in Job360's own browser: the user signs in, never here
            await blocked.record_browser_block(
                db, CurrentUser(id=user_id, email=""), login, {"reason": "login_needed", "step": "Job360 browser"},
            )
        fake = {"event_type": "blocked", "payload": {"reason": "captcha", "step": "Job360 browser"}}
        assert (await client.post(f"/api/applications/{look_alike}/events", json=fake)).status_code == 201
        fake_pool._sessions.clear()  # noqa: SLF001

        pause = {"edits": [{"path": "assistant_settings.paused_until", "value": "until_resumed"}]}
        assert (await client.patch("/api/profile", json=pause)).status_code == 200
        out = await restore.restore_held_forms()
        assert [(r["application_id"], r["result"]) for r in out] == [(held, "paused")]
        assert replays == [], "a paused assistant's form is never touched"

        resume = {"edits": [{"path": "assistant_settings.paused_until", "value": ""}]}
        assert (await client.patch("/api/profile", json=resume)).status_code == 200
        out = await restore.restore_held_forms()
        assert [(r["application_id"], r["result"]) for r in out] == [(held, "ok")]
        assert replays == [held], "only the browser's own CAPTCHA block is restored"


@pytest.mark.asyncio
async def test_one_failed_restore_does_not_stop_the_others(authenticated_async_context, fake_pool, monkeypatch):
    from src.services.browser_agent import agent, refill, restore

    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        first, second = await _job(client), await _app(client, 2)
        for app_id in (first, second):
            await _filled_and_blocked(client, app_id)
        _backdate(user_id, first, 5)
        _backdate(user_id, second, 10)
        fake_pool._sessions.clear()  # noqa: SLF001

        async def flaky(pool, uid, application_id, form_url, steps, *, files, load_state=None):
            if application_id == first:
                raise RuntimeError("chromium crashed")
            pool._sessions[agent.browser_key(uid, application_id)] = agent.Session(context=_Ctx(), page=None)  # noqa: SLF001
            return {"status": "ok", "steps_ok": len(steps), "steps_failed": []}

        monkeypatch.setattr(refill, "replay", flaky)
        out = await restore.restore_held_forms()
        assert {r["application_id"]: r["result"] for r in out} == {first: "error", second: "ok"}
        asks = await _open_asks(client)
        assert _finish(asks, second)["finish_here"] is True
        assert _finish(asks, first)["finish_here"] is False
