"""The signed-in LinkedIn hunt and people search in the user's own browser (owner decision 2026-11-03).

Two more kinds in the ONE own-browser queue: ``linkedin_search`` (read the user's alerts, bring each new job) and
``profile_read`` (find people for applications and the network, store them). Each is due at most once an hour,
only while its duty is on and not paused; take / claim expiry / release work like the other kinds, and ``done``
answers the count from what was stored. Real rows through the real doors; value asserts (rule #21). Hosts are
compared exactly. Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.repositories import pgsync
from src.services.browser_agent import routing

HUNT = "assistant_settings.hunt_every_hours"
PEOPLE = "assistant_settings.people_hunt"
PAUSE = "assistant_settings.paused_until"
LINKEDIN = {"kind": "own_browser", "label": "Your own browser: runs when your PC is on", "reason": "linkedin_own_browser"}
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "description": "Build the pipelines.",
}


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
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


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"})


async def _bring(client: AsyncClient, n: int) -> int:
    resp = await client.post(
        "/api/jobs/bring", json={**_AD, "title": f"Engineer {n}", "apply_url": f"https://northwind.example/c/{n}"}
    )
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _kinds(client: AsyncClient) -> dict[str, dict[str, Any]]:
    resp = await client.get("/api/own-browser/queue")
    assert resp.status_code == 200, resp.text
    return {t["kind"]: t for t in resp.json()["tasks"]}


async def _take(agent: AsyncClient, session: str = "pc") -> dict[str, Any]:
    resp = await agent.post("/api/own-browser/take", json={"session": session})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _release(agent: AsyncClient, kind: str, reason: str, session: str = "pc"):
    return await agent.post(
        "/api/own-browser/release", json={"session": session, "task_id": f"duty:{kind}", "reason": reason}
    )


def _rows(user_id: str, prefix: str) -> dict[str, dict[str, Any]]:
    conn = pgsync.connect(str(settings.DB_PATH))
    cur = conn.execute(
        "SELECT duty, done_at, outcome, reason, failures FROM assistant_duty_runs WHERE user_id = ? AND duty LIKE ?",
        (user_id, prefix + "%"),
    )
    out = {str(r[0]): {"done_at": r[1], "outcome": r[2], "reason": r[3], "failures": int(r[4])} for r in cur.fetchall()}
    conn.close()
    return out


def _age(user_id: str, duty: str, minutes: int) -> None:
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute(
        "UPDATE assistant_duty_runs SET done_at = ? WHERE user_id = ? AND duty = ?",
        ((datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(), user_id, duty),
    )
    conn.commit()
    conn.close()


# ── due, and not otherwise ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_each_kind_appears_only_while_its_duty_is_on_and_not_paused(authenticated_async_context, fixture_user_id):
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 0)
        assert set(await _kinds(client)) == set()  # neither duty on: nothing (rule #29)
        await _patch(client, HUNT, 6)
        hunt = (await _kinds(client))["linkedin_search"]
        assert hunt["task_id"] == "duty:linkedin_search" and hunt["claim"] is None and hunt["owner_yes"] is False
        assert hunt["what"] == "Read your LinkedIn job alerts, notifications and saved searches, and bring each new job"
        assert "profile_read" not in await _kinds(client)
        await _patch(client, PEOPLE, True)
        people = (await _kinds(client))["profile_read"]
        assert people["application_ids"] == [app_id] and people["max_people"] == settings.NETWORK_MAX_PER_RUN == 5
        assert people["task_id"] == "duty:profile_read"
        await _patch(client, PAUSE, "until_resumed")
        assert set(await _kinds(client)) == set()  # paused: neither is due
        await _patch(client, PAUSE, "")  # resume
        await _patch(client, HUNT, 0)  # hunt off
        assert set(await _kinds(client)) == {"profile_read"}


# ── take, claim, done ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hunt_is_claimed_then_done_answers_the_jobs_brought_and_waits_its_hour(
    authenticated_async_context, fixture_user_id
):
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        await _patch(client, HUNT, 1)
        async with await _agent(client) as agent:
            got = await _take(agent)
            assert got["status"] == "task" and got["task"]["kind"] == "linkedin_search"
            assert got["task"]["claim"]["session"] == "pc" and got["task"]["claim"]["until"]
            assert 'reason="done"' in got["next"] and "bring_job" in got["next"]
            assert (await _take(agent))["status"] == "already_yours"
            assert (await _take(agent, "laptop"))["status"] == "empty"  # one session per kind
            assert (await _kinds(client))["linkedin_search"]["claim"]["session"] == "pc"
            assert (await _release(agent, "linkedin_search", "done", "laptop")).status_code == 409
            await _bring(agent, 1)
            await _bring(agent, 2)
            await _bring(client, 3)  # the user's own bring on the web is not the session's
            done = await _release(agent, "linkedin_search", "done")
            assert done.status_code == 200, done.text
            body = done.json()
            assert (body["outcome"], body["count"], body["summary"]) == ("done", 2, "done, 2 jobs brought")
            assert (await _release(agent, "linkedin_search", "done")).status_code == 409  # closed once
            assert "linkedin_search" not in await _kinds(client)  # due again in an hour, not now
            slot = _rows(fixture_user_id, "own_browser:")
            assert slot["own_browser:linkedin_search"]["outcome"] == "done"
            assert "own_browser:claim:linkedin_search" not in slot
            _age(fixture_user_id, "own_browser:linkedin_search", 61)
            assert (await _kinds(client))["linkedin_search"]["claim"] is None


@pytest.mark.asyncio
async def test_a_failure_is_recorded_waits_and_comes_back(authenticated_async_context, fixture_user_id):
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        await _patch(client, HUNT, 1)
        async with await _agent(client) as agent:
            assert (await _take(agent))["status"] == "task"
            failed = (await _release(agent, "linkedin_search", "captcha")).json()
            assert (failed["outcome"], failed.get("count"), failed["failures_in_row"]) == ("failed", None, 1)
            slot = _rows(fixture_user_id, "own_browser:linkedin_search")["own_browser:linkedin_search"]
            assert (slot["outcome"], slot["reason"], slot["failures"]) == ("failed", "needs_user", 1)
            assert "linkedin_search" not in await _kinds(client)  # backs off 2 hours, not 1
            _age(fixture_user_id, "own_browser:linkedin_search", 70)
            assert "linkedin_search" not in await _kinds(client)
            _age(fixture_user_id, "own_browser:linkedin_search", 121)
            assert (await _take(agent))["task"]["kind"] == "linkedin_search"
            done = (await _release(agent, "linkedin_search", "done")).json()
            assert (done["count"], done["summary"], done["failures_in_row"]) == (0, "done, 0 jobs brought", 0)
            assert _rows(fixture_user_id, "own_browser:linkedin_search")["own_browser:linkedin_search"]["reason"] == "no_work"


@pytest.mark.asyncio
async def test_an_expired_claim_puts_the_task_back_for_another_session(authenticated_async_context, fixture_user_id):
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        await _patch(client, HUNT, 1)
        async with await _agent(client) as agent:
            assert (await _take(agent))["status"] == "task"
            _age(fixture_user_id, "own_browser:claim:linkedin_search", settings.DUTY_CLAIM_MINUTES + 1)
            assert (await _kinds(client))["linkedin_search"]["claim"] is None  # never lost
            again = await _take(agent, "laptop")
            assert again["status"] == "task" and again["task"]["claim"]["session"] == "laptop"
            assert (await _release(agent, "linkedin_search", "done")).status_code == 409  # pc's claim is gone


@pytest.mark.asyncio
async def test_people_done_answers_the_people_added_and_counts_a_try(authenticated_async_context, fixture_user_id):
    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 0)
        await _patch(client, PEOPLE, True)
        async with await _agent(client) as agent:
            got = await _take(agent)
            assert got["task"]["kind"] == "profile_read" and got["task"]["application_ids"] == [app_id]
            assert "add_contact" in got["next"]
            for n in range(2):
                added = await agent.post(
                    f"/api/applications/{app_id}/contacts",
                    json={"name": f"Priya {n}", "role": "Recruiter", "linkedin_url": f"https://www.linkedin.com/in/p{n}",
                          "found_via": "linkedin"},
                )
                assert added.status_code == 201, added.text
            done = (await _release(agent, "profile_read", "done")).json()
            assert (done["outcome"], done["count"], done["summary"]) == ("done", 2, "done, 2 people added")
        tries = _rows(fixture_user_id, "own_browser:reach:")
        assert tries == {f"own_browser:reach:{app_id}": {**tries[f"own_browser:reach:{app_id}"], "failures": 1}}
        assert "profile_read" not in await _kinds(client)
        # The scheduled run's own people hunt still has this job: the two do not hide each other's work.
        due = (await client.get("/api/schedule/due")).json()["due"]
        assert [d["application_ids"] for d in due if d["duty"] == "reach"] == [[app_id]]


@pytest.mark.asyncio
async def test_done_is_only_for_a_hunt_or_people_task(authenticated_async_context):
    async with authenticated_async_context() as client:
        async with await _agent(client) as agent:
            bad = await agent.post(
                "/api/own-browser/release", json={"session": "pc", "task_id": "contact:1", "reason": "done"}
            )
            assert bad.status_code == 422, bad.text
            assert (await _release(agent, "inbox", "done")).status_code == 422


# ── the MCP door ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_mcp_tool_takes_and_closes_a_hunt(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server

    _seed_profile(fixture_user_id)
    tool = mcp_server.build_server()._tool_manager.get_tool("own_browser_queue")
    user = mcp_server.CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True)
    async with authenticated_async_context() as client:
        await _patch(client, HUNT, 1)
        token = mcp_server._current_user.set(user)
        try:
            listed = await tool.fn(action="list")
            taken = await tool.fn(action="take", session="pc")
            await _bring(client, 5)  # the same actor as the session user above
            closed = await tool.fn(action="release", session="pc", task_id="duty:linkedin_search", reason="done")
        finally:
            mcp_server._current_user.reset(token)
    assert [t["kind"] for t in listed["tasks"]] == ["linkedin_search"]
    assert taken["status"] == "task" and taken["task"]["task_id"] == "duty:linkedin_search"
    assert (closed["outcome"], closed["count"], closed["summary"]) == ("done", 1, "done, 1 job brought")


# ── LinkedIn never goes to Job360's browser ──────────────────────────────────


@pytest.mark.asyncio
async def test_hunt_and_people_tasks_never_route_to_job360s_browser(authenticated_async_context, fixture_user_id):
    from src.services.browser_agent.agent import BrowserError, _refuse_linkedin

    _seed_profile(fixture_user_id)
    async with authenticated_async_context() as client:
        await _bring(client, 0)
        await _patch(client, HUNT, 1)
        await _patch(client, PEOPLE, True)
        kinds = await _kinds(client)
        due = (await client.get("/api/schedule/due")).json()["due"]
    assert set(kinds) == {"linkedin_search", "profile_read"}
    for kind, task in kinds.items():
        assert task["browser"] == LINKEDIN, kind
        assert urlsplit(task["url"]).hostname == "www.linkedin.com"
        with pytest.raises(BrowserError):
            _refuse_linkedin(task["url"])
        # Even a public job page is the own browser once it is a signed-in LinkedIn task (never a guest read).
        route = routing.browser_for(url="https://www.linkedin.com/jobs/view/100", queue_kind=kind)
        assert route == routing.Route("own_browser", "linkedin_own_browser")
        assert kind in routing.QUEUE_KINDS
    # The scheduled cloud run is never handed these kinds: whats_due names only its own duties.
    assert {d["duty"] for d in due} <= set(settings.SCHEDULE_DUTIES)
    assert not {d["duty"] for d in due} & {"linkedin_search", "profile_read"}
