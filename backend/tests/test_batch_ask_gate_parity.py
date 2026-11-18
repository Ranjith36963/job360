"""2026-11-18 (batch run cse_01KTmoHDgw2hAU2TB48JuafJ, 20:55-20:57; ``failures_in_row.batch`` 4): the queue
called 57 rows "needs nothing" (``preflight.waiting_on_owner`` false) though 125 (ElevenLabs) and 136 (Coinbase)
each had an open question ask, raised AFTER a fill that a newer CV had made stale. ``check_submit`` and the kit's
``submit_preview`` counted that ask (``question_open``); ``queue_preflight._owner_waiting_apps`` did not (#1660).
The run stopped with ``mark_done(batch, partial, needs_user)`` and got 409 "60 queued jobs still need no owner
input; next: application 125 (ElevenLabs)", every hour since 16:30.

Rule: a row waits on the owner exactly when ``check_submit`` would see an open ask (``kit.open_ask_kinds_for_apps``,
one truth for both). A refill row with NO open ask is still the run's work, and the 409 names its step.
Helpers are copied, never imported across test modules (schema isolation)."""
from __future__ import annotations

import contextlib
import logging
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.profile.models import CVData, UserPreferences, UserProfile


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Python.", name="Ada"),
                             preferences=UserPreferences(target_job_titles=["Data Engineer"])),
                 user_id, source_action="cv_upload")


async def _job(client: AsyncClient, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "London, United Kingdom",
        "queue": True, "apply_url": f"https://northwind.example/careers/{n}", "description": "Build it. " * 8,
    })
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> None:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})
    assert resp.status_code == 201, resp.text


async def _cv(client: AsyncClient, app_id: int, text: str = "Ada, engineer.") -> None:
    saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": text})
    assert saved.status_code in (200, 201), saved.text


async def _stale_fill(client: AsyncClient, app_id: int) -> None:
    """Application 125's shape: CV v1, its fill (Oct 25), then a newer CV (Nov 10): "refill the form"."""
    await _cv(client, app_id)
    await _event(client, app_id, "form_filled", {
        "form_url": f"https://northwind.example/careers/{app_id}/apply", "fields_count": 1})
    await _cv(client, app_id, "Ada, engineer. Tailored again.")


async def _ask(client: AsyncClient, app_id: int, question: str = "Approve the 4 essay drafts?") -> None:
    ask = await client.post("/api/asks", json={"question": question, "application_id": app_id})
    assert ask.status_code in (200, 201), ask.text


async def _queue(user_id: str) -> dict[int, dict[str, Any]]:
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    token = m._current_user.set(
        CurrentUser(id=user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc"))
    try:
        out = await m.build_server()._tool_manager.get_tool("list_applications").fn(queued=True, compact=True)
    finally:
        m._current_user.reset(token)
    return {r["id"]: r for r in out["applications"]}


async def _gate_asks(user_id: str, app_id: int) -> tuple[str, ...]:
    from src.api.dependencies import get_request_db
    from src.services.applications import kit, spine

    async with contextlib.asynccontextmanager(get_request_db)() as db:
        row = await spine.get_owned_application(db, user_id, app_id)
        return (await kit.gate_facts(db, user_id, row, email_verified=True)).open_asks


async def _stop(client: AsyncClient):
    return await client.post("/api/schedule/done", json={"duty": "batch", "outcome": "partial", "reason": "needs_user"})


@pytest.mark.asyncio
async def test_an_ask_raised_after_a_stale_fill_waits_on_the_owner_like_check_submit(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _job(client, 1)
        await _stale_fill(client, app_id)
        await _ask(client, app_id)  # ask 653, 16:32: raised after the stale fill
        assert "question" in await _gate_asks(fixture_user_id, app_id)  # check_submit: question_open
        rows = await _queue(fixture_user_id)
        assert rows[app_id]["preflight"]["waiting_on_owner"] is True
        assert [w for w in rows[app_id]["waiting_on"] if w.endswith(":you")]  # the flag and waiting_on agree
        done = await _stop(client)  # the run's "partial, needs_user" is the truth: accepted
    assert done.status_code == 200, done.text


@pytest.mark.asyncio
async def test_queue_waiting_matches_the_gate_for_every_ask_shape(authenticated_async_context, fixture_user_id):
    from src.services.applications import asks

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        ids = [await _job(client, n) for n in range(1, 5)]
        before, after, lesson, plain = ids
        await _ask(client, before, "Notice period?")
        await _stale_fill(client, before)  # the ask is older than the stale fill
        await _stale_fill(client, after)
        await _ask(client, after)  # the ask is newer than the stale fill (125, 136, 122)
        await _cv(client, lesson)
        from src.api import dependencies as api_deps

        db = await api_deps.get_db()
        await asks.create_ask(db, fixture_user_id, lesson, "Keep this lesson?", "", "claude-code", questions=[
            {"key": "doctor_repeat_7", "question": "Keep this lesson?", "answer": "Keep", "source": "guess"}])
        await _cv(client, plain)
        rows = await _queue(fixture_user_id)
        for app_id in ids:
            gate = bool(await _gate_asks(fixture_user_id, app_id))
            assert rows[app_id]["preflight"]["waiting_on_owner"] is gate, (app_id, gate)
    assert [rows[i]["preflight"]["waiting_on_owner"] for i in ids] == [True, True, False, False]


@pytest.mark.asyncio
async def test_a_refill_row_without_an_ask_is_still_work_and_the_409_names_its_step(
    authenticated_async_context, fixture_user_id
):
    from src.utils.logger import get_audit_logger

    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        refill, fresh, asked = await _job(client, 1), await _job(client, 2), await _job(client, 3)
        await _stale_fill(client, refill)
        await _cv(client, fresh)
        await _stale_fill(client, asked)
        await _ask(client, asked)
        audit = get_audit_logger()
        level = audit.level
        audit.addHandler(handler)
        audit.setLevel(logging.INFO)
        try:
            refused = await _stop(client)
        finally:
            audit.removeHandler(handler)
            audit.setLevel(level)
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert f"{refill} (refill the form)" in detail and f"{fresh} (fill the form)" in detail
    assert f"Ids: {refill}, {fresh}." in detail
    assert f"{asked} " not in detail and f"{asked}." not in detail and f"{asked}," not in detail
    logged = [r for r in records if r.getMessage() == "batch_stop_refused"]
    assert len(logged) == 1
    assert logged[0].ids == f"{refill}:refill,{fresh}:fill"
    assert logged[0].reason == "needs_no_owner_input"
