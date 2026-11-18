"""2026-11-18: the two bulk-apply runs after #1634 submitted 0. From their transcripts:

A. the run could not tell which queued rows had an open ask, read kits to probe for asks (holding jobs) and
   gave up on ~44 rows: the compact rows carry ``preflight.waiting_on_owner``, batch.md never named it;
B. it paged all ~971 applications to find the rows the user pressed Send on: the queued reply now carries
   ``queue_summary.submit_now`` (every open application whose yes holds, on the queue or not);
C. ``mark_done`` 409'd for a queued row with no CV and the batch run wrote that CV: CVs are the CV run's job,
   so a CV-less row is skipped (``no_cv``) and never refuses the batch's stop;
D. working application 127 it opened application 130's URL: ``browser`` open checks the URL belongs to the
   application named (another application's link is refused, another host is logged and warned).
Helpers are local to this module.
"""
from __future__ import annotations

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


async def _job(client: AsyncClient, n: int, *, queue: bool = True) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "Remote", "queue": queue,
        "apply_url": f"https://northwind.example/careers/{n}", "description": "Build the pipelines. " * 8,
    })
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]):
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})


async def _cv(client: AsyncClient, app_id: int) -> None:
    saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "Ada, engineer."})
    assert saved.status_code in (200, 201), saved.text


async def _sent(client: AsyncClient, app_id: int) -> None:
    """CV, a filled form and the user's Send: the row reads ``submit_now``."""
    await _cv(client, app_id)
    fill = await _event(client, app_id, "form_filled", {
        "form_url": f"https://northwind.example/careers/{app_id}/apply", "fields_count": 1})
    assert fill.status_code == 201, fill.text
    yes = await client.post(f"/api/applications/{app_id}/send/approve",
                            params={"form_filled_event_id": fill.json()["event_id"]})
    assert yes.status_code == 201, yes.text


def _tool(name: str):
    from src.api import mcp_server as m

    return m.build_server()._tool_manager.get_tool(name)


async def _as_agent(fixture_user_id: str, coro_fn):
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    token = m._current_user.set(
        CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc")
    )
    try:
        return await coro_fn()
    finally:
        m._current_user.reset(token)


def _batch() -> str:
    from src.api.routes.recipes import load_recipe

    return " ".join(load_recipe("batch").text.split())


# ── A. open asks are visible on the compact queue rows, and batch.md says to skip them ───────────────


@pytest.mark.asyncio
async def test_compact_queue_rows_carry_the_open_ask_flag(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        asked, free = await _job(client, 1), await _job(client, 2)
        ask = await client.post("/api/asks", json={"question": "Notice period?", "application_id": asked})
        assert ask.status_code in (200, 201), ask.text
        out = await _as_agent(fixture_user_id, lambda: _tool("list_applications").fn(queued=True, compact=True))
    rows = {r["id"]: r for r in out["applications"]}
    assert rows[asked]["preflight"]["waiting_on_owner"] is True
    assert rows[free]["preflight"]["waiting_on_owner"] is False


def test_batch_recipe_skips_open_ask_rows_by_the_flag_and_never_probes_kits():
    batch = _batch()
    assert "waiting_on_owner" in batch
    assert "never read a kit to look for asks" in batch


# ── B. the rows the user pressed Send on come straight from the queued reply ──────────────────────────


@pytest.mark.asyncio
async def test_the_queued_reply_names_every_sent_row_even_off_the_queue(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        on_queue, off_queue, plain = await _job(client, 1), await _job(client, 2, queue=False), await _job(client, 3)
        await _sent(client, on_queue)
        await _sent(client, off_queue)  # Send queues it ...
        left = await _event(client, off_queue, "unqueued", {"reason": "other"})  # ... and something took it off
        assert left.status_code == 201, left.text
        out = await _as_agent(fixture_user_id, lambda: _tool("list_applications").fn(queued=True, compact=True))
    assert out["queue_summary"]["submit_now"] == sorted([on_queue, off_queue])
    assert plain not in out["queue_summary"]["submit_now"]
    assert off_queue not in {r["id"] for r in out["applications"]}


def test_batch_recipe_reads_sent_rows_from_the_queue_and_never_pages_the_whole_list():
    batch = _batch()
    assert "`queue_summary.submit_now`" in batch
    assert "without `queued`" not in batch
    assert "never page `list_applications` without `queued=true`" in batch


# ── C. a queued row with no CV is the CV run's: skipped (no_cv), never a 409 on the batch's stop ───────


async def _stop(client: AsyncClient):
    return await client.post("/api/schedule/done", json={"duty": "batch", "outcome": "partial", "reason": "needs_user"})


@pytest.mark.asyncio
async def test_a_queued_row_without_a_cv_never_refuses_the_batch_stop(authenticated_async_context, fixture_user_id):
    # 2026-11-18 run: mark_done 409'd for application 1033 (no CV), and the batch run wrote that CV itself.
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        await _job(client, 1)  # no CV, no ask, Job360's browser: the CV run's work
        done = await _stop(client)
        assert done.status_code == 200, done.text
        assert done.json()["outcome"] == "partial"


@pytest.mark.asyncio
async def test_a_queued_row_with_its_cv_still_refuses_the_stop(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        no_cv, ready = await _job(client, 1), await _job(client, 2)
        await _cv(client, ready)
        refused = await _stop(client)
        assert refused.status_code == 409, refused.text
        detail = refused.json()["detail"]
        assert "1 queued jobs still need no owner input" in detail and f"next: application {ready}" in detail
        assert f"Ids: {ready}." in detail and f"application {no_cv} " not in detail


@pytest.mark.asyncio
async def test_the_cv_run_still_sees_the_cv_less_row(authenticated_async_context, fixture_user_id):
    import contextlib

    from src.api.dependencies import get_request_db
    from src.services.applications import queue_preflight

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _job(client, 1)
        async with contextlib.asynccontextmanager(get_request_db)() as db:
            assert app_id in {r["id"] for r in await queue_preflight.cv_rows(db, fixture_user_id)}
            assert app_id in {r["id"] for r in await queue_preflight.fillable_rows(db, fixture_user_id)}
            assert app_id not in {r["id"] for r in await queue_preflight.fillable_rows(
                db, fixture_user_id, with_cv=True)}


def test_batch_recipe_skips_cv_less_rows_and_writes_no_cv():
    batch = _batch()
    assert "`no_cv`" in batch
    assert "write and save a tailored CV" not in batch
    assert "CV if missing" not in batch
    assert "never write, save or review a CV" in batch


# ── D. a browser open checks the URL belongs to the application it names ───────────────────────────────


def _fake_run(monkeypatch) -> list[str]:
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    opened: list[str] = []

    async def fake_run(p, user_id, action, **kw):
        opened.append(kw["url"])
        return {"status": "ok", "url": kw["url"], "transport": {"status": 200, "headers": {}, "final_host": ""}}

    monkeypatch.setattr(agent, "run", fake_run)
    return opened


@pytest.mark.asyncio
async def test_opening_another_applications_link_is_refused(authenticated_async_context, monkeypatch, caplog):
    opened = _fake_run(monkeypatch)
    async with authenticated_async_context() as client:
        mine, other = await _job(client, 127), await _job(client, 130)
        with caplog.at_level(logging.INFO):
            out = (await client.post("/api/browser", json={
                "action": "open", "url": "https://northwind.example/careers/130", "application_id": mine})).json()
        assert out["status"] == "refused" and out["reason"] == "browser_url_mismatch", out
        assert str(other) in out["detail"] and "https://northwind.example/careers/127" in out["detail"]
        assert opened == []  # nothing was opened
        logged = [r for r in caplog.records if getattr(r, "event", "") == "browser_url_mismatch"]
        assert logged and logged[0].application_id == mine and logged[0].other_application_id == other
        own = (await client.post("/api/browser", json={
            "action": "open", "url": "https://northwind.example/careers/127", "application_id": mine})).json()
        assert own["status"] == "ok" and "url_warning" not in own
        assert opened == ["https://northwind.example/careers/127"]


@pytest.mark.asyncio
async def test_opening_a_link_on_another_host_is_warned_and_logged(authenticated_async_context, monkeypatch, caplog):
    opened = _fake_run(monkeypatch)
    async with authenticated_async_context() as client:
        mine = await _job(client, 127)
        with caplog.at_level(logging.INFO):
            out = (await client.post("/api/browser", json={
                "action": "open", "url": "https://boards.example.org/apply/9", "application_id": mine})).json()
    assert out["status"] == "ok" and opened == ["https://boards.example.org/apply/9"]  # an ATS may live elsewhere
    assert "url_warning" in out and str(mine) in out["url_warning"]
    logged = [r for r in caplog.records if getattr(r, "event", "") == "browser_url_mismatch"]
    assert logged and logged[0].reason == "other_host" and logged[0].application_id == mine


# ── Recipe: the PC list is pasted, and batch runs no CV reviewer of any kind ─────────────────────────────


def test_batch_self_check_requires_the_pasted_pc_list_and_no_cv_reviewer():
    batch = _batch()
    self_check = batch[batch.index("6. **Self-check:**"):batch.index("7. **Summary**")]
    assert "Ready for your PC" in self_check and "jq output pasted" in self_check
    assert "no CV reviewer run" in self_check
    assert "never run a CV reviewer" in batch
