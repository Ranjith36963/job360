"""2026-11-18 (12:41 bulk apply run, 0 submitted, "48 ready to send"): ~46 queued Job360-browser rows read
"refill the form" (a newer CV was saved after their fill) and needed nothing from the user, but each still
had the open ask of its OLD fill, so ``preflight.waiting_on_owner`` was true and batch.md (#1646) skipped
them before opening anything; ``mark_done`` accepted the stop because ``fillable_rows`` counted any filled
form as done. Application 361 was one of them, so its missing job country was never set either.

A. ``waiting_on_owner`` follows the blockers' rule: an ask of a fill that a newer CV made stale is not the
   user's to answer (the form is being redone), so a refill row reads false, like its ``waiting_on``;
B. ``mark_done(batch)`` counts a refill row as work left (409 names it);
D. ``mark_done(batch)`` names the claimed queued rows still missing a job country (``country_missing``).
"""
from __future__ import annotations

import contextlib
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.profile.models import CVData, UserPreferences, UserProfile


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Python.", name="Ada"),
                             preferences=UserPreferences(target_job_titles=["Data Engineer"])),
                 user_id, source_action="cv_upload")


async def _job(client: AsyncClient, n: int, location: str = "London, United Kingdom") -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": location, "queue": True,
        "apply_url": f"https://northwind.example/careers/{n}", "description": "Build the pipelines. " * 8,
    })
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> None:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})
    assert resp.status_code == 201, resp.text


async def _cv(client: AsyncClient, app_id: int, text: str = "Ada, engineer.") -> None:
    saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": text})
    assert saved.status_code in (200, 201), saved.text


async def _filled_and_asked(client: AsyncClient, app_id: int) -> None:
    """CV, a filled form, then that fill's ask to the user (the consent question of 1018/1024/1025)."""
    await _cv(client, app_id)
    await _event(client, app_id, "form_filled", {
        "form_url": f"https://northwind.example/careers/{app_id}/apply", "fields_count": 1})
    ask = await client.post("/api/asks", json={"question": "Privacy notice consent?", "application_id": app_id})
    assert ask.status_code in (200, 201), ask.text


async def _refill_row(client: AsyncClient, app_id: int) -> None:
    """...and then a newer CV: the fill is stale, the next step is the assistant's "refill the form"."""
    await _filled_and_asked(client, app_id)
    await _cv(client, app_id, "Ada, engineer. Tailored again.")


def _tool(name: str):
    from src.api import mcp_server as m

    return m.build_server()._tool_manager.get_tool(name)


async def _as_agent(fixture_user_id: str, name: str, **kw: Any) -> Any:
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    token = m._current_user.set(
        CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc")
    )
    try:
        return await _tool(name).fn(**kw)
    finally:
        m._current_user.reset(token)


async def _queue(fixture_user_id: str) -> dict[int, dict[str, Any]]:
    out = await _as_agent(fixture_user_id, "list_applications", queued=True, compact=True)
    return {r["id"]: r for r in out["applications"]}


async def _stop(client: AsyncClient):
    return await client.post("/api/schedule/done", json={"duty": "batch", "outcome": "partial", "reason": "needs_user"})


# ── A. a refill row's old ask does not make it wait on the user ──────────────────────────────────────


@pytest.mark.asyncio
async def test_a_refill_row_with_its_old_fills_ask_needs_nothing_from_the_user(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        refill, current = await _job(client, 1), await _job(client, 2)
        await _refill_row(client, refill)
        await _filled_and_asked(client, current)  # the ask of the form being sent now: the user's
        rows = await _queue(fixture_user_id)
    assert not [w for w in rows[refill]["waiting_on"] if w.endswith(":you")]
    assert rows[refill]["preflight"]["waiting_on_owner"] is False
    assert rows[current]["preflight"]["waiting_on_owner"] is True


@pytest.mark.asyncio
async def test_an_ask_from_before_the_stale_fill_still_waits_on_the_user(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _job(client, 1)
        ask = await client.post("/api/asks", json={"question": "Notice period?", "application_id": app_id})
        assert ask.status_code in (200, 201), ask.text
        await _cv(client, app_id)
        await _event(client, app_id, "form_filled", {
            "form_url": f"https://northwind.example/careers/{app_id}/apply", "fields_count": 1})
        await _cv(client, app_id, "Ada, engineer. Tailored again.")  # the fill is stale; the ask is older
        rows = await _queue(fixture_user_id)
    assert rows[app_id]["preflight"]["waiting_on_owner"] is True
    assert [w for w in rows[app_id]["waiting_on"] if w.endswith(":you")]  # the flag and waiting_on agree


# ── B. the batch stop counts a refill row as work left ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_refill_row_refuses_the_batch_stop(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        refill, current = await _job(client, 1), await _job(client, 2)
        await _refill_row(client, refill)
        await _filled_and_asked(client, current)
        refused = await _stop(client)
        assert refused.status_code == 409, refused.text
        detail = refused.json()["detail"]
        assert f"next: application {refill}" in detail and f"Ids: {refill}." in detail


@pytest.mark.asyncio
async def test_fillable_rows_keeps_refill_rows_and_drops_current_fills(authenticated_async_context, fixture_user_id):
    from src.api.dependencies import get_request_db
    from src.services.applications import queue_preflight

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        refill, current = await _job(client, 1), await _job(client, 2)
        await _refill_row(client, refill)
        await _filled_and_asked(client, current)
        async with contextlib.asynccontextmanager(get_request_db)() as db:
            ids = {r["id"] for r in await queue_preflight.fillable_rows(db, fixture_user_id, with_cv=True)}
            order, waiting = await queue_preflight.work_order(db, fixture_user_id, [current, refill])
    assert ids == {refill}
    assert waiting == {current} and order == [refill, current]


# ── D. mark_done names the queued rows still missing their job country ─────────────────────────────


@pytest.mark.asyncio
async def test_the_batch_stop_names_rows_missing_their_job_country(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        known, unknown = await _job(client, 1), await _job(client, 2, location="Remote")
        await _as_agent(fixture_user_id, "update_job", application_id=known, country="GB")
        rows = await _queue(fixture_user_id)
        assert rows[known]["country"] == "GB" and rows[unknown]["country"] is None
        done = await _stop(client)  # neither has a CV: both are the CV run's, the stop is accepted
        assert done.status_code == 200, done.text
        body = done.json()
    assert body["country_missing"] == [unknown]
    assert f"{unknown}" in body["note"] and "country" in body["note"]


def test_batch_recipe_works_refill_rows_and_sets_every_missing_country():
    from src.api.routes.recipes import load_recipe

    batch = " ".join(load_recipe("batch").text.split())
    assert "refill the form" in batch
    assert "`country_missing`" in batch
