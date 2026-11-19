"""PC run pc-1905 (2026-11-18, 19:05 UTC) ended 185 seconds in with ~45 tasks waiting.

Stored transcript (session cse_01KTmoHAD5eRaYU8yJo2xi8g): take gave ``duty:linkedin_search``; its release answered
``"next": "Keep going: 50 tasks still wait; call take again."`` and the run took again. Take then gave Revolut 787
(an application); the kit's ``submit_preview`` said stop (below_score_line), the run recorded ``hold_released``
{reason: stopped} and that reply carried no next step, so the run went straight to the summary. A person or a duty
is closed with ``own_browser_queue release`` (which says keep going); an application is closed with ``record_event
hold_released``, which said nothing. Its reply now says keep going too, but only to the session that took from the
PC queue, and take hands out a DIFFERENT application while others wait.

Real rows through the real routes; value asserts (rule #21). Helpers are copied, never imported.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.auth_deps import CurrentUser
from src.api.routes import browser as browser_routes
from src.services.applications import blocked as blocked_service
from src.services.profile.models import CVData, UserPreferences, UserProfile


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )


async def _bring(client: AsyncClient, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "London", "country": "GB",
        "remote": False, "apply_url": f"https://northwind{n}.example/careers/{n}", "description": "Build it. " * 8,
    })
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _browser_block(ctx: Any, app_id: int) -> None:
    user = CurrentUser(id=ctx.fixture_user_id, email="u@example.com")
    async with browser_routes._db() as db:  # noqa: SLF001
        await blocked_service.record_browser_block(
            db, user, app_id, {"reason": "captcha", "step": "Job360 browser", "page_host": "careers.example"}
        )


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"})


async def _take(agent: AsyncClient, session: str) -> dict[str, Any]:
    resp = await agent.post("/api/own-browser/take", json={"session": session})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _kit_then_let_go(agent: AsyncClient, app_id: int, session: str) -> dict[str, Any]:
    """The run's whole visit to one application: the compact kit (the hold), then ``hold_released`` stopped."""
    kit = await agent.get(f"/api/applications/{app_id}/kit", params={"session": session, "compact": "true"})
    assert kit.status_code == 200, kit.text
    resp = await agent.post(
        f"/api/applications/{app_id}/events",
        json={"event_type": "hold_released", "payload": {"reason": "stopped"}, "detail": "check_submit said stop"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_an_application_let_go_in_a_pc_run_says_take_again_and_take_gives_the_next_one(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    monkeypatch.setattr(blocked_service, "browser_hold_live", lambda *_: False)  # Job360's browser let go
    _seed(fixture_user_id)
    async with authenticated_async_context() as client:
        apps = [await _bring(client, n) for n in (1, 2, 3)]
        for app_id in apps:
            await _browser_block(authenticated_async_context, app_id)
        async with await _agent(client) as agent:
            first = await _take(agent, "pc-1905")
            assert first["status"] == "task" and first["task"]["kind"] == "application", first
            taken = int(first["task"]["application_id"])
            body = await _kit_then_let_go(agent, taken, "pc-1905")
            nxt = int(body["next_task"]["task"]["application_id"])  # pc-2306: the reply takes the next one
            assert body["next"] == (f"Do this task now: application:{nxt} (application): start it with get_application_kit "
                                    "(its next). 40 of the run's 40 minutes are left: never stop early.")
            again = await _take(agent, "pc-1905")
            assert again["status"] == "task", again
            assert int(again["task"]["application_id"]) in set(apps) - {taken}


@pytest.mark.asyncio
async def test_the_last_application_let_go_says_nothing_is_left(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    monkeypatch.setattr(blocked_service, "browser_hold_live", lambda *_: False)
    _seed(fixture_user_id)
    async with authenticated_async_context() as client:
        only = await _bring(client, 1)
        await _browser_block(authenticated_async_context, only)
        async with await _agent(client) as agent:
            got = await _take(agent, "pc-1905")
            assert int(got["task"]["application_id"]) == only
            body = await _kit_then_let_go(agent, only, "pc-1905")
            assert body["next"] == "Queue done: write the summary."
            assert (await _take(agent, "pc-1905"))["status"] in ("empty", "wait")


@pytest.mark.asyncio
async def test_a_session_that_never_took_from_the_pc_queue_gets_no_take_again(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    """A cloud apply run never takes PC tasks (the recipe's own rule): its hold_released must not send it there."""
    monkeypatch.setattr(blocked_service, "browser_hold_live", lambda *_: False)
    _seed(fixture_user_id)
    async with authenticated_async_context() as client:
        apps = [await _bring(client, n) for n in (1, 2)]
        for app_id in apps:
            await _browser_block(authenticated_async_context, app_id)
        async with await _agent(client) as agent:
            await _take(agent, "pc-1905")  # the PC run is live, under ANOTHER label
            body = await _kit_then_let_go(agent, apps[1], "batch-apply")
            assert body["next"] is None
