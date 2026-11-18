"""The PC run reaches every queued application, not the same few every hour (owner via Kits, 2026-11-18).

Production, 2026-11-18 (stored events, runs pc-1307 .. pc-1810): the hourly own-browser run took Revolut 787 in
pc-1307, pc-1506, pc-1607 and pc-1706 and WSP 946 in pc-1407, pc-1607 and pc-1706. Each run read the kit and let
go with ``hold_released`` {reason: stopped}: 787 because its fit (40) is under the user's line (60) and the user
drops those jobs ("Kit submit_preview: stop, below_score_line (60)"). The queue's application tasks came in a fixed
order (lowest application id first) behind a 60-minute release cooldown, the same as the hourly cadence, and the
queue is cut at ``QUEUE_MAX``: the batch queue counted 265 own-browser rows while the PC queue listed 43
applications, so the rest (Easy Apply 411, 418, 420 ...) were never reached.

Real rows through the real routes; value asserts (rule #21). Helpers are copied, never imported.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.auth_deps import CurrentUser
from src.api.routes import browser as browser_routes
from src.api.routes import own_browser as own_browser_routes
from src.services.applications import blocked as blocked_service
from src.services.applications import own_browser
from src.services.profile.models import CVData, UserPreferences, UserProfile


async def _seed(client: AsyncClient, user_id: str, **settings: Any) -> None:
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )
    if settings:
        edits = [{"path": f"assistant_settings.{k}", "value": v} for k, v in settings.items()]
        resp = await client.patch("/api/profile", json={"edits": edits})
        assert resp.status_code == 200, resp.text


async def _bring(client: AsyncClient, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "London", "country": "GB",
        "remote": False, "apply_url": f"https://northwind{n}.example/careers/{n}", "description": "Build it. " * 8,
    })
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _fit(client: AsyncClient, app_id: int, score: int) -> None:
    resp = await client.put(
        f"/api/applications/{app_id}/fit", json={"score": score, "verdict": "my read", "score_source": "full_ad"}
    )
    assert resp.status_code == 200, resp.text


async def _browser_block(ctx: Any, app_id: int, reason: str = "captcha") -> None:
    user = CurrentUser(id=ctx.fixture_user_id, email="u@example.com")
    async with browser_routes._db() as db:  # noqa: SLF001
        await blocked_service.record_browser_block(
            db, user, app_id, {"reason": reason, "step": "Job360 browser", "page_host": "careers.example"}
        )


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"})


def _clock(monkeypatch: pytest.MonkeyPatch, at: datetime) -> None:
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return at if tz is not None else at.replace(tzinfo=None)

    monkeypatch.setattr(own_browser_routes, "datetime", _Frozen)


async def _take(agent: AsyncClient, session: str) -> dict[str, Any]:
    resp = await agent.post("/api/own-browser/take", json={"session": session})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _try_and_let_go(agent: AsyncClient, app_id: int, session: str) -> None:
    """One PC run's whole visit: read the kit (the hold), then let go without progress, as pc-1706 did."""
    kit = await agent.get(f"/api/applications/{app_id}/kit", params={"session": session})
    assert kit.status_code == 200, kit.text
    resp = await agent.post(
        f"/api/applications/{app_id}/events",
        json={"event_type": "hold_released", "payload": {"reason": "stopped"}, "detail": f"{session}: not worked"},
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_a_job_under_the_users_line_is_never_a_pc_task(authenticated_async_context, fixture_user_id,
                                                               monkeypatch):
    """Revolut 787: fit 40, line 60, below_line skip. The kit always stops it (below_score_line), so handing it
    to the PC run only spends a visit every hour."""
    monkeypatch.setattr(blocked_service, "browser_hold_live", lambda *_: False)  # Job360's browser let go
    async with authenticated_async_context() as client:
        await _seed(client, fixture_user_id, apply_min_score=60, below_line="skip")
        under, over = await _bring(client, 1), await _bring(client, 2)
        await _fit(client, under, 40)
        await _fit(client, over, 75)
        await _browser_block(authenticated_async_context, under)
        await _browser_block(authenticated_async_context, over)
        queue = (await client.get("/api/own-browser/queue")).json()
        assert [t["task_id"] for t in queue["tasks"]] == [f"application:{over}"]
        async with await _agent(client) as agent:
            got = await _take(agent, "pc-1706")
            assert got["status"] == "task" and got["task"]["application_id"] == over


@pytest.mark.asyncio
async def test_the_users_other_line_switches_keep_a_low_fit_job(authenticated_async_context, fixture_user_id,
                                                                monkeypatch):
    """Only the user's own drop rule leaves a job out (rule #29): ``below_line`` ask keeps it a PC task."""
    monkeypatch.setattr(blocked_service, "browser_hold_live", lambda *_: False)
    async with authenticated_async_context() as client:
        await _seed(client, fixture_user_id, apply_min_score=60, below_line="ask")
        under = await _bring(client, 1)
        await _fit(client, under, 40)
        await _browser_block(authenticated_async_context, under)
        queue = (await client.get("/api/own-browser/queue")).json()
        assert [t["task_id"] for t in queue["tasks"]] == [f"application:{under}"]


@pytest.mark.asyncio
async def test_an_hourly_run_reaches_a_job_it_never_tried_before_the_one_it_let_go(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    """The queue is cut at QUEUE_MAX (2 here). Runs at 16:07 and 17:10 each tried the first job and let it go; the
    next hour the cooldown is over. The third job was never shown and never taken: it must come first now."""
    monkeypatch.setattr(blocked_service, "browser_hold_live", lambda *_: False)
    monkeypatch.setattr(own_browser, "QUEUE_MAX", 2)
    async with authenticated_async_context() as client:
        await _seed(client, fixture_user_id)
        first, second, never = await _bring(client, 1), await _bring(client, 2), await _bring(client, 3)
        for app_id in (first, second, never):
            await _browser_block(authenticated_async_context, app_id)
        async with await _agent(client) as agent:
            got = await _take(agent, "pc-1607")
            assert got["task"]["application_id"] == first
            await _try_and_let_go(agent, first, "pc-1607")
            got = await _take(agent, "pc-1607")
            assert got["task"]["application_id"] == second
            await _try_and_let_go(agent, second, "pc-1607")
            # The next hourly run: both cooldowns are over.
            _clock(monkeypatch, datetime.now(timezone.utc) + timedelta(minutes=61))
            queue = (await agent.get("/api/own-browser/queue", params={"session": "pc-1710"})).json()
            assert [t["application_id"] for t in queue["tasks"]] == [never, first]
            got = await _take(agent, "pc-1710")
            assert got["status"] == "task" and got["task"]["application_id"] == never
