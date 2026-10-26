"""linkedin.com is the user's own browser only (owner decision 2026-10-26): LinkedIn sends and Easy Apply route
there from the start, Job360's browser refuses linkedin.com, Easy Apply waits in the own-browser queue at most
10 a day, and every other link stays on Job360's browser. Value asserts (rule #21): real rows through the real
doors; hosts compared exactly, never as substrings."""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.routes import browser as browser_routes
from src.services.applications import own_browser, spine
from src.services.browser_agent import routing

LINKEDIN = {"kind": "own_browser", "label": "Your own browser: runs when your PC is on", "reason": "linkedin_own_browser"}
JOB360 = {"kind": "job360", "label": "I'll use Job360's browser", "reason": "default"}
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "description": "Build the pipelines.",
}


# ── the pure rule ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "want"),
    [
        ("https://www.linkedin.com/jobs/view/100", True),
        ("https://linkedin.com/in/ada", True),
        ("https://uk.linkedin.com/jobs/view/1", True),
        ("HTTPS://WWW.LINKEDIN.COM./jobs", True),
        ("www.linkedin.com/jobs/view/2", True),
        ("https://linkedin.com.evil.example/jobs", False),
        ("https://notlinkedin.com/jobs", False),
        ("https://evil.example/?next=https://www.linkedin.com/jobs", False),
        ("https://www.linkedin.com@evil.example/jobs", False),
        ("https://boards.greenhouse.io/acme/jobs/1", False),
        ("", False),
        ("http://[::1", False),
    ],
)
def test_is_linkedin_compares_the_exact_hostname(url, want):
    assert routing.is_linkedin(url) is want


def test_linkedin_routes_to_the_own_browser_and_nothing_else_moves():
    route = routing.browser_for(url="https://www.linkedin.com/jobs/view/100")
    assert route == routing.Route("own_browser", "linkedin_own_browser") == routing.browser_for(linkedin=True)
    assert route.reason in routing.ROUTE_REASONS
    assert routing.label(route) == LINKEDIN
    # Not "turn your PC on": nothing failed, it is the user's choice.
    assert "turn your PC on" not in routing.LINKEDIN_LABEL
    # A LinkedIn task does not wait for a failure first, whatever Job360's browser reported.
    assert routing.browser_for(linkedin=True, job360_failed="captcha") == route
    # A password or a code is still the user's alone, on LinkedIn too.
    assert routing.browser_for(linkedin=True, page_stop="password") == routing.Route("human", "password")
    # Every other link: Job360's browser first, exactly as before.
    assert routing.browser_for(url="https://boards.greenhouse.io/acme/jobs/1") == routing.Route("job360", "default")
    assert routing.browser_for(url="https://linkedin.com.evil.example/x") == routing.Route("job360", "default")


@pytest.mark.parametrize(
    ("kind", "url", "want"),
    [
        ("question", "https://www.linkedin.com/jobs/view/1", ("own_browser", "linkedin_own_browser")),
        ("", "https://www.linkedin.com/jobs/view/1", ("own_browser", "linkedin_own_browser")),
        ("login", "https://www.linkedin.com/jobs/view/1", ("human", "password")),
        ("captcha", "https://www.linkedin.com/jobs/view/1", ("human", "only_you")),
        ("question", "https://careers.example/apply/1", ("job360", "default")),
    ],
)
def test_ask_route_on_an_easy_apply_application(kind, url, want):
    route = routing.ask_route(kind, url=url)
    assert (route.kind, route.reason) == want


def test_the_linkedin_label_names_no_assistant():
    import re

    assert not re.search(r"claude|chatgpt|gemini|atlas|chrome", routing.LINKEDIN_LABEL, re.I)


@pytest.mark.asyncio
async def test_job360s_browser_refuses_to_open_linkedin():
    """Checked before the screen, the pool or any page is touched: no browser is needed to prove it."""
    from src.services.browser_agent.agent import BrowserError, _act, _refuse_linkedin

    with pytest.raises(BrowserError) as exc:
        await _act(
            None, None, "open", url="https://www.linkedin.com/in/ada", ref="", text="", on=False,  # type: ignore[arg-type]
            doc_kind="", gate=None, files=None,
        )
    assert (exc.value.status, exc.value.reason) == ("refused", "linkedin_own_browser")
    assert "own browser" in exc.value.detail
    _refuse_linkedin("https://boards.greenhouse.io/acme/jobs/1")  # any other site: no refusal


# ── the real doors ───────────────────────────────────────────────────────────


async def _bring(client: AsyncClient, n: int, url: str) -> int:
    resp = await client.post("/api/jobs/bring", json={**_AD, "title": f"Engineer {n}", "apply_url": url})
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any] | None = None) -> None:
    body: dict[str, Any] = {"event_type": event_type}
    if payload is not None:
        body["payload"] = payload
    resp = await client.post(f"/api/applications/{app_id}/events", json=body)
    assert resp.status_code == 201, resp.text


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"})


async def _take(agent: AsyncClient, session: str = "desk") -> dict[str, Any]:
    resp = await agent.post("/api/own-browser/take", json={"session": session})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_a_linkedin_send_is_the_own_browser_from_the_start(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 0, "https://careers.example/c/0")
        added = await client.post(
            f"/api/applications/{app_id}/contacts",
            json={"name": "Priya", "role": "Recruiter", "linkedin_url": "https://www.linkedin.com/in/priya"},
        )
        cid = int(added.json()["contact"]["id"])
        msg = await client.post(
            f"/api/applications/{app_id}/artifacts",
            json={"kind": "outreach", "text": "Hi", "contact_id": cid, "channel": "linkedin"},
        )
        assert msg.status_code == 201, msg.text
        person = (await client.get(f"/api/people?contact_id={cid}")).json()["person"]
        assert person["send_browser"] == LINKEDIN  # before any click, before any Job360 attempt
        mid = int(person["outreach"]["messages"][-1]["id"])
        clicked = await client.post(f"/api/contacts/{cid}/own-browser-send", json={"message_id": mid, "decision": "approve"})
        assert clicked.status_code == 200, clicked.text
        task = (await client.get("/api/own-browser/queue")).json()["tasks"][0]
        assert task["kind"] == "linkedin_outreach" and task["browser"] == LINKEDIN


@pytest.mark.asyncio
async def test_easy_apply_waits_in_the_own_browser_queue_and_other_jobs_do_not(authenticated_async_context):
    async with authenticated_async_context() as client:
        easy = await _bring(client, 0, "https://www.linkedin.com/jobs/view/4001")
        company = await _bring(client, 1, "https://careers.example/c/1")
        unqueued = await _bring(client, 2, "https://www.linkedin.com/jobs/view/4002")
        for app_id in (easy, company):
            await _event(client, app_id, "queued")
        queue = (await client.get("/api/own-browser/queue")).json()
        assert [t["task_id"] for t in queue["tasks"]] == [f"application:{easy}"]  # not the company form, not unqueued
        assert queue["tasks"][0]["browser"] == LINKEDIN
        assert queue["tasks"][0]["why_own_browser"] == own_browser.WHY_WORDS["linkedin_own_browser"]
        assert unqueued not in [t["application_id"] for t in queue["tasks"]]
        # The ask on that job and its ready card carry the same chip; the company form stays Job360's browser.
        for app_id in (easy, company):
            await _event(client, app_id, "form_filled", {"form_url": "https://careers.example/apply", "fields_count": 1})
        cards = {c["application_id"]: c["browser"] for c in (await client.get("/api/ready-to-send")).json()["items"]}
        assert cards == {easy: LINKEDIN, company: JOB360}
        asked = await client.post("/api/asks", json={"application_id": easy, "question": "Notice period?"})
        assert asked.status_code == 201, asked.text
        ask = next(a for a in (await client.get("/api/asks")).json()["asks"] if a["application_id"] == easy)
        assert ask["browser"] == LINKEDIN
        async with await _agent(client) as agent:
            got = await _take(agent)
            assert got["status"] == "task" and got["task"]["task_id"] == f"application:{easy}"


@pytest.mark.asyncio
async def test_easy_apply_stops_at_ten_a_day_and_other_applications_still_go(authenticated_async_context):
    user_id = authenticated_async_context.fixture_user_id
    async with authenticated_async_context() as client:
        done = [await _bring(client, n, f"https://www.linkedin.com/jobs/view/{n}") for n in range(10)]
        # Five recorded, five clicked but never recorded: all ten count, each application once.
        async with browser_routes._db() as db:  # noqa: SLF001
            for n, app_id in enumerate(done):
                for event_type in (("submit_attempted", "applied") if n < 5 else ("submit_attempted",)):
                    await spine.append_event(
                        db, user_id=user_id, application_id=app_id, event_type=event_type, payload={},
                        occurred_at=spine.parse_occurred_at(None), recorded_by="web",
                    )
            await db._db.commit()  # noqa: SLF001
        easy = await _bring(client, 20, "https://www.linkedin.com/jobs/view/20")
        await _event(client, easy, "queued")
        async with browser_routes._db() as db:  # noqa: SLF001
            from datetime import datetime, timezone

            assert await own_browser.easy_apply_today(db, user_id, datetime.now(timezone.utc)) == 10
        async with await _agent(client) as agent:
            got = await _take(agent)
            assert got["status"] == "daily_limit" and got["task"] is None
            # A company form Job360's browser failed on is not Easy Apply: it is still handed out.
            from src.api.auth_deps import CurrentUser
            from src.services.applications import blocked as blocked_service

            other = await _bring(client, 21, "https://careers.example/c/21")
            async with browser_routes._db() as db:  # noqa: SLF001
                await blocked_service.record_browser_block(
                    db, CurrentUser(id=user_id, email="u@example.com"), other,
                    {"reason": "login_needed", "step": "Job360 browser", "page_host": "careers.example"},
                )
            got = await _take(agent)
            assert got["status"] == "task" and got["task"]["application_id"] == other


@pytest.mark.asyncio
async def test_a_submit_on_another_site_never_counts_toward_easy_apply(authenticated_async_context):
    user_id = authenticated_async_context.fixture_user_id
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 0, "https://careers.example/c/0")
        async with browser_routes._db() as db:  # noqa: SLF001
            await spine.append_event(
                db, user_id=user_id, application_id=app_id, event_type="submit_attempted", payload={},
                occurred_at=spine.parse_occurred_at(None), recorded_by="web",
            )
            await db._db.commit()  # noqa: SLF001
            from datetime import datetime, timezone

            assert await own_browser.easy_apply_today(db, user_id, datetime.now(timezone.utc)) == 0


def test_the_recipes_send_linkedin_to_the_own_browser_and_company_forms_to_job360():
    from src.api.routes.recipes import load_recipe

    def flat(name: str) -> str:
        return " ".join(load_recipe(name).text.lower().split())

    hunt, apply, batch = flat("hunt"), flat("apply"), flat("batch")
    assert "only in the user's own browser (never job360's; owner 2026-10-26)" in hunt
    assert "at most 5 searches a day, read each result slowly" in hunt
    assert "a scheduled cloud run skips linkedin" in hunt
    assert "easy apply runs only in the user's own browser" in apply and "same `check_submit` rule" in apply
    assert "`update_job` `apply_url` = that form, fill it in job360's browser" in apply
    assert "it waits in the own-browser queue (10 a day), stop" in apply
    assert "linkedin.com (easy apply) runs only in the user's own browser" in batch
