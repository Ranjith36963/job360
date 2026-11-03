"""Learned per-host blocking (owner 2026-11-02, two browsers): two CAPTCHAs or bot checks that Job360's own
browser met on one form host, for two different jobs of ONE user, send that user's next job on that exact host to
their own browser from the start, with the reason on the chip. A later confirmation seen by Job360's browser on
that host clears it. Per user, never global. Real doors, real Postgres; helpers copied (schema isolation)."""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from src.api.auth_deps import CurrentUser
from src.api.routes import bring as bring_routes
from src.api.routes import browser as browser_routes
from src.services.applications import blocked as blocked_service
from src.services.applications import queue_preflight
from src.services.browser_agent import learned, route_book, routing

HOST = "jobs.acme-careers.com"
REASON = "Job360's browser was blocked twice on jobs.acme-careers.com: your own browser runs it"


def _ad(slug: str, url: str) -> dict[str, Any]:
    return {
        "title": f"Engineer {slug}", "company": f"Co {slug}", "location": "Remote", "apply_url": url,
        "description": "Build the pipelines. Python, dbt. Fully remote.",
    }


async def _job(client: AsyncClient, slug: str, url: str, *, queue: bool = True) -> int:
    resp = await client.post("/api/jobs/bring", json=_ad(slug, url))
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    if queue:
        queued = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "queued"})
        assert queued.status_code == 201, queued.text
    return app_id


async def _block(user: CurrentUser, app_id: int, reason: str = "captcha", host: str = HOST) -> None:
    async with browser_routes._db() as db:  # noqa: SLF001
        await blocked_service.record_browser_block(
            db, user, app_id, {"reason": reason, "step": "Job360 browser", "page_host": host},
        )


async def _browsers(client: AsyncClient) -> dict[int, dict[str, str]]:
    resp = await client.get("/api/applications", params={"queued": True})
    assert resp.status_code == 200, resp.text
    return {int(r["id"]): r["browser"] for r in resp.json()["applications"]}


async def _own_queue(client: AsyncClient) -> dict[int, dict[str, Any]]:
    resp = await client.get("/api/own-browser/queue")
    assert resp.status_code == 200, resp.text
    return {int(t["application_id"]): t for t in resp.json()["tasks"] if t["kind"] == "application"}


def _me(ctx: Any) -> CurrentUser:
    return CurrentUser(id=ctx.fixture_user_id, email="test@example.com")


@pytest.mark.asyncio
async def test_two_captchas_on_one_host_send_the_next_job_there_to_the_own_browser(authenticated_async_context):
    ctx = authenticated_async_context
    async with ctx() as client:
        me = _me(ctx)
        first = await _job(client, "a", f"https://{HOST}/apply/1", queue=False)
        second = await _job(client, "b", f"https://{HOST}/apply/2", queue=False)
        nxt = await _job(client, "c", f"https://{HOST}/apply/3")
        other = await _job(client, "d", "https://boards.greenhouse.io/acme/jobs/9")

        await _block(me, first)  # one CAPTCHA: still Job360's browser
        assert (await _browsers(client))[nxt] == {"kind": "job360", "label": routing.LABELS["job360"],
                                                  "reason": "default"}
        assert nxt not in await _own_queue(client)
        # The same job blocked again is still one job, not two.
        cleared = await client.post(f"/api/applications/{first}/events",
                                    json={"event_type": "unblocked", "payload": {"resolution": "user_did_it"}})
        assert cleared.status_code == 201, cleared.text
        await _block(me, first, "bot_check")
        assert (await _browsers(client))[nxt]["kind"] == "job360"

        await _block(me, second, "bot_check")  # a second job on the same host: learned
        chips = await _browsers(client)
        assert chips[nxt] == {"kind": "own_browser", "label": REASON, "reason": "learned_bot_check"}
        assert chips[other]["kind"] == "job360"
        task = (await _own_queue(client))[nxt]
        assert task["why_own_browser"] == "Job360's browser was blocked twice on this site (a CAPTCHA or bot check)."
        assert task["browser"]["reason"] == "learned_bot_check"
        async with browser_routes._db() as db:  # noqa: SLF001
            fillable = {r["id"] for r in await queue_preflight.fillable_rows(db, me.id)}
        assert nxt not in fillable and other in fillable


@pytest.mark.asyncio
async def test_a_sign_in_wall_is_not_learned_but_still_moves_that_job(authenticated_async_context):
    ctx = authenticated_async_context
    async with ctx() as client:
        me = _me(ctx)
        a = await _job(client, "a", f"https://{HOST}/apply/1")
        b = await _job(client, "b", f"https://{HOST}/apply/2")
        nxt = await _job(client, "c", f"https://{HOST}/apply/3")
        await _block(me, a, "login_needed")
        await _block(me, b, "login_needed")
        chips = await _browsers(client)
        assert chips[a]["reason"] == chips[b]["reason"] == "job360_login_required"
        assert chips[nxt]["kind"] == "job360"


@pytest.mark.asyncio
async def test_another_users_blocks_never_route_this_user(authenticated_async_context):
    ctx = authenticated_async_context
    async with ctx() as client:
        mine = await _job(client, "mine", f"https://{HOST}/apply/3")
        other = CurrentUser(id=str(uuid.uuid4()), email="other@example.com")
        async with browser_routes._db() as db:  # noqa: SLF001
            await db._db.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)", (other.id, other.email, "h"),
            )
            ids = [
                (await bring_routes.bring_job(bring_routes.BringJobRequest(**_ad(s, f"https://{HOST}/apply/{s}")),
                                              db, other)).application_id
                for s in ("x", "y")
            ]
        for app_id in ids:
            await _block(other, int(app_id))
        async with browser_routes._db() as db:  # noqa: SLF001
            assert await learned.blocked_hosts(db, other.id) == frozenset({HOST})
            assert await learned.blocked_hosts(db, ctx.fixture_user_id) == frozenset()
        assert (await _browsers(client))[mine]["kind"] == "job360"


@pytest.mark.asyncio
async def test_a_job360_browser_success_on_the_host_clears_it(authenticated_async_context):
    ctx = authenticated_async_context
    async with ctx() as client:
        me = _me(ctx)
        a = await _job(client, "a", f"https://{HOST}/apply/1", queue=False)
        b = await _job(client, "b", f"https://{HOST}/apply/2", queue=False)
        nxt = await _job(client, "c", f"https://{HOST}/apply/3")
        await _block(me, a)
        await _block(me, b)
        assert (await _browsers(client))[nxt]["reason"] == "learned_bot_check"
        async with browser_routes._db() as db:  # noqa: SLF001
            # A success elsewhere, or a mere read, does not clear it.
            assert await route_book.record(db, me.id, "https://boards.greenhouse.io/x/jobs/1", "fill", "job360",
                                           "confirmed")
            assert await route_book.record(db, me.id, f"https://{HOST}/apply/9", "read", "job360", "confirmed")
        assert (await _browsers(client))[nxt]["reason"] == "learned_bot_check"
        async with browser_routes._db() as db:  # noqa: SLF001
            assert await route_book.record(db, me.id, f"https://{HOST}/thanks", "fill", "job360", "confirmed")
        assert (await _browsers(client))[nxt] == {"kind": "job360", "label": routing.LABELS["job360"],
                                                  "reason": "default"}
        assert nxt not in await _own_queue(client)


def test_hosts_are_compared_exactly_never_by_substring():
    hosts = frozenset({HOST})
    assert learned.route_for(f"https://{HOST}/apply/1", hosts) == learned.LEARNED_ROUTE
    assert learned.route_for(f"{HOST}/apply/1", hosts) == learned.LEARNED_ROUTE
    for url in (f"https://{HOST}.evil.example/apply", f"https://evil-{HOST}/apply", f"https://www.{HOST}/x",
                "https://acme-careers.com/x", f"https://evil.example/?next={HOST}", ""):
        assert learned.route_for(url, hosts) is None, url
    assert {learned.host_of(f"https://{HOST}/a"), learned.host_of(HOST)} == {HOST}
    # Strict rules still win: a LinkedIn link stays the user's choice, never a learned block.
    route = routing.browser_for(url="https://www.linkedin.com/jobs/view/1", route=learned.LEARNED_ROUTE,
                                queue_kind="application")
    assert route.reason == "linkedin_own_browser"
    assert "learned_bot_check" in routing.ROUTE_REASONS and "learned_bot_check" in routing.ROUTE_LABELS
