"""The batch queue says which browser runs each job (owner, 2026-10-26): every queued row of
``GET /applications?queued=true`` carries ``browser`` ``{kind, label, reason}`` and ``queue_summary`` carries
``by_browser`` over the WHOLE queue. Real doors, real Postgres. Helpers are copied (schema isolation)."""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.api.auth_deps import CurrentUser
from src.api.routes import browser as browser_routes
from src.services.applications import blocked as blocked_service
from src.services.browser_agent import routing


def _ad(slug: str, url: str) -> dict[str, Any]:
    return {
        "title": f"Engineer {slug}", "company": f"Co {slug}", "location": "Remote", "apply_url": url,
        "description": "Build the pipelines. Python, dbt. Fully remote.",
    }


async def _queued_job(client: AsyncClient, slug: str, url: str) -> int:
    resp = await client.post("/api/jobs/bring", json=_ad(slug, url))
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    queued = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "queued"})
    assert queued.status_code == 201, queued.text
    return app_id


async def _list(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/applications", params={"queued": True})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_queued_rows_carry_the_browser_and_the_summary_counts_them(authenticated_async_context):
    async with authenticated_async_context() as client:
        gh = await _queued_job(client, "gh", "https://boards.greenhouse.io/acme/jobs/1")
        li = await _queued_job(client, "li", "https://www.linkedin.com/jobs/view/123456789/")
        body = await _list(client)
        rows = {r["id"]: r for r in body["applications"]}
        assert rows[gh]["browser"] == {
            "kind": "job360", "label": routing.LABELS["job360"], "reason": "default",
        }
        assert rows[li]["browser"] == {
            "kind": "own_browser", "label": routing.ROUTE_LABELS["linkedin_own_browser"],
            "reason": "linkedin_own_browser",
        }
        assert body["queue_summary"]["by_browser"] == {"job360": 1, "own_browser": 1, "human": 0}
        # a page of 1 still counts the whole queue
        page = (await client.get("/api/applications", params={"queued": True, "limit": 1})).json()
        assert len(page["applications"]) == 1
        assert page["queue_summary"]["by_browser"] == {"job360": 1, "own_browser": 1, "human": 0}
        # outside the queue view no row carries a browser
        plain = (await client.get("/api/applications")).json()
        assert all(r["browser"] is None for r in plain["applications"])


@pytest.mark.asyncio
async def test_a_job_blocked_by_job360s_browser_moves_to_the_own_browser(authenticated_async_context):
    async with authenticated_async_context() as client:
        stuck = await _queued_job(client, "stuck", "https://boards.greenhouse.io/acme/jobs/2")
        fine = await _queued_job(client, "fine", "https://boards.greenhouse.io/acme/jobs/3")
        user = CurrentUser(id=authenticated_async_context.fixture_user_id, email="u@example.com")
        async with browser_routes._db() as db:  # noqa: SLF001
            await blocked_service.record_browser_block(
                db, user, stuck, {"reason": "login_needed", "step": "Job360 browser", "page_host": "acme.example"}
            )
        body = await _list(client)
        rows = {r["id"]: r for r in body["applications"]}
        assert rows[stuck]["browser"]["kind"] == "own_browser"
        assert rows[stuck]["browser"]["reason"] == "job360_login_required"
        assert rows[fine]["browser"]["kind"] == "job360"
        assert body["queue_summary"]["by_browser"] == {"job360": 1, "own_browser": 1, "human": 0}


@pytest.mark.asyncio
async def test_browser_counts_never_include_another_users_queue(authenticated_async_context):
    from src.api import dependencies as api_deps
    from src.services.applications import spine

    async with authenticated_async_context() as client:
        await _queued_job(client, "mine", "https://boards.greenhouse.io/acme/jobs/4")
        db = await api_deps.get_db()
        other = await spine.list_applications(db, "someone-else", queued=True)
        assert other["queue_summary"]["by_browser"] == {"job360": 0, "own_browser": 0, "human": 0}
        mine = await spine.list_applications(db, authenticated_async_context.fixture_user_id, queued=True)
        assert mine["queue_summary"]["by_browser"] == {"job360": 1, "own_browser": 0, "human": 0}
