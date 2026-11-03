"""Owner decision 2026-11-03 - "Indeed and LinkedIn always ask" is the user's own setting.

``assistant_settings.always_ask_job_boards`` is on by default, so ``check_submit`` keeps answering
``ask_always_site`` for Indeed / LinkedIn style sites. Only the user's own web click turns it off; an
assistant's off waits for the user's confirm (riskier), its on applies at once (safer). With it off, every
other rule still asks (practice run, new site, CV not seen). The LinkedIn send default is 15 a day.
All through the real doors (web session, bearer token, MCP tools). Helpers are copied, never imported
from another test module.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.profile.models import UserPreferences

BOARDS = "assistant_settings.always_ask_job_boards"
SUBMIT = "assistant_settings.submit_mode"
SCORE = "assistant_settings.apply_min_score"
ABOVE = "assistant_settings.above_line"
BELOW = "assistant_settings.below_line"
FORM = "https://careers.northwind.example/apply/7"
LINKEDIN = "https://www.linkedin.com/jobs/view/4100000001/apply"


def _ad(n: int) -> dict[str, Any]:
    return {
        "title": f"Data Engineer {n}", "company": f"Company{n}", "location": "Remote",
        "apply_url": f"https://company{n}.example/careers/{n}",
        "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
    }


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )


async def _patch(client: AsyncClient, path: str, value: Any) -> dict[str, Any]:
    resp = await client.patch("/api/profile", json={"edits": [{"path": path, "value": value}]})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _view(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/assistant-settings")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _bring(client: AsyncClient, n: int) -> int:
    resp = await client.post("/api/jobs/bring", json=_ad(n))
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _ready(client: AsyncClient, app_id: int, *, seen: bool = True) -> None:
    """A CV saved (and checked by the user when ``seen``), a fit above the user's line, both switches auto."""
    saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "Ada - engineer."})
    assert saved.status_code == 201, saved.text
    if seen:
        marked = await client.post(f"/api/applications/{app_id}/cv-seen", json={})
        assert marked.status_code == 201, marked.text
    for path, value in ((SCORE, 50), (ABOVE, "auto"), (BELOW, "auto")):
        await _patch(client, path, value)
    fit = await client.put(f"/api/applications/{app_id}/fit", json={"score": 80, "verdict": "ok", "score_source": "full_ad"})
    assert fit.status_code == 200, fit.text


async def _check(client: AsyncClient, app_id: int, url: str) -> tuple[str, str]:
    """Fill and sweep on ``url``'s site (rules 4b, 4c, 5e), then ask the gate."""
    for event, payload in (("form_filled", {"form_url": url, "fields_count": 1}),
                           ("form_swept", {"fields_checked": 1, "fixed": 0})):
        resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event, "payload": payload})
        assert resp.status_code == 201, resp.text
    body = (await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": url})).json()
    return body["decision"], body["reason"]


async def _apply(client: AsyncClient, app_id: int) -> None:
    resp = await client.post(f"/api/applications/{app_id}/receipt", json={"channel": "company_site"})
    assert resp.status_code == 201, resp.text


async def _linkedin_ready(client: AsyncClient) -> int:
    """Auto mode, the practice run done and LinkedIn already applied to once: a second LinkedIn job with
    everything else OK. Returns that second application."""
    await _patch(client, SUBMIT, "auto_when_sure")
    first = await _bring(client, 1)
    await _ready(client, first)
    await _check(client, first, LINKEDIN)
    await _apply(client, first)
    second = await _bring(client, 2)
    await _ready(client, second)
    return second


def _agent(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_on_by_default_linkedin_still_asks_through_the_web_and_the_mcp_door(
    authenticated_async_context, fixture_user_id
):
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await _view(client))[BOARDS.split(".")[1]] == {"value": True, "effective": True}
        app_id = await _linkedin_ready(client)
        assert await _check(client, app_id, LINKEDIN) == ("ask", "ask_always_site")
        tools = m.build_server()._tool_manager
        token = m._current_user.set(CurrentUser(
            id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc",
        ))
        try:
            via_mcp = await tools.get_tool("check_submit").fn(application_id=app_id, form_url=LINKEDIN, session="s1")
        finally:
            m._current_user.reset(token)
        assert (via_mcp["decision"], via_mcp["reason"]) == ("ask", "ask_always_site")


@pytest.mark.asyncio
async def test_a_web_user_turning_it_off_lets_linkedin_auto_submit(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _linkedin_ready(client)
        assert await _check(client, app_id, LINKEDIN) == ("ask", "ask_always_site")
        await _patch(client, BOARDS, False)  # the user's own click applies at once
        view = await _view(client)
        assert view["always_ask_job_boards"] == {"value": False, "effective": False}
        assert view["waiting"] == []
        assert await _check(client, app_id, LINKEDIN) == ("submit", "auto_when_sure")
        # turning it back on is the safe direction: LinkedIn asks again
        await _patch(client, BOARDS, True)
        assert await _check(client, app_id, LINKEDIN) == ("ask", "ask_always_site")


@pytest.mark.asyncio
async def test_with_it_off_the_practice_run_new_site_and_cv_not_seen_still_ask(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(client, BOARDS, False)
        await _patch(client, SUBMIT, "auto_when_sure")
        on_linkedin = await _bring(client, 1)
        await _ready(client, on_linkedin)
        assert await _check(client, on_linkedin, LINKEDIN) == ("ask", "practice_run")
        # the practice run is spent on a company site, so LinkedIn is still a new site
        company = await _bring(client, 2)
        await _ready(client, company)
        assert await _check(client, company, FORM) == ("ask", "practice_run")
        await _apply(client, company)
        assert await _check(client, on_linkedin, LINKEDIN) == ("ask", "new_site_first_time")
        unseen = await _bring(client, 3)
        await _ready(client, unseen, seen=False)
        assert await _check(client, unseen, LINKEDIN) == ("ask", "cv_not_seen")


@pytest.mark.asyncio
async def test_an_assistants_off_is_held_for_the_users_confirm_and_its_on_applies(
    authenticated_async_context, fixture_user_id
):
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _linkedin_ready(client)
        minted = await client.post("/api/tokens", json={"name": "claude-code"})
        assert minted.status_code == 201, minted.text
        async with _agent(minted.json()["token"]) as agent:
            held = await _patch(agent, BOARDS, False)
        assert [(w["path"], w["value"]) for w in held["waiting"]] == [(BOARDS, False)]
        tools = m.build_server()._tool_manager
        token = m._current_user.set(CurrentUser(
            id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc",
        ))
        try:
            via_mcp = await tools.get_tool("update_profile").fn(edits=[{"path": BOARDS, "value": False}])
        finally:
            m._current_user.reset(token)
        assert via_mcp["applied"] == [] and [w["path"] for w in via_mcp["waiting"]] == [BOARDS]
        view = await _view(client)
        assert view["always_ask_job_boards"] == {"value": True, "effective": True}, "not applied"
        assert {w["path"] for w in view["waiting"]} == {BOARDS}
        assert await _check(client, app_id, LINKEDIN) == ("ask", "ask_always_site")
        # the user's confirm on the website applies it
        request_id = view["waiting"][0]["id"]
        confirmed = await client.post(f"/api/assistant-settings/requests/{request_id}/confirm")
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["always_ask_job_boards"] == {"value": False, "effective": False}
        assert await _check(client, app_id, LINKEDIN) == ("submit", "auto_when_sure")
        # an assistant turning it back ON is safer: applied at once
        async with _agent(minted.json()["token"]) as agent:
            on = await _patch(agent, BOARDS, True)
        assert on["waiting"] == []
        assert (await _view(client))["always_ask_job_boards"] == {"value": True, "effective": True}
        assert await _check(client, app_id, LINKEDIN) == ("ask", "ask_always_site")


@pytest.mark.asyncio
async def test_the_linkedin_daily_send_default_is_15(authenticated_async_context, fixture_user_id):
    assert settings.LINKEDIN_SENDS_PER_DAY_DEFAULT == 15
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await _view(client))["linkedin_sends_per_day"] == {"value": None, "effective": 15}
