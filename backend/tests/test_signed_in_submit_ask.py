"""Owner decision 2026-11-03 - "ask before a submit through my own signed-in account" is the user's setting.

``assistant_settings.ask_before_signed_in_submit`` is on by default: ``check_submit`` answers ``ask_always_site``
for a submit in the user's own browser (a fill / sweep recorded with ``browser`` own_browser or manual), in
Job360's browser with a saved sign-in for the form's site, and - the fallback when the route is unknown - on
Indeed / LinkedIn. Job360's browser with no saved sign-in on a company site is never affected. Only the user's
own web click turns it off; an assistant's off waits for the user's confirm (riskier), its on applies at once.
With it off every other rule still asks (practice run, new site, CV not seen). The LinkedIn send default is 15
a day. All through the real doors (web session, bearer token, MCP tools). Helpers are copied, never imported
from another test module.
"""
from __future__ import annotations

import contextlib
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.profile.models import UserPreferences

SIGNED_IN = "assistant_settings.ask_before_signed_in_submit"
SUBMIT = "assistant_settings.submit_mode"
SCORE = "assistant_settings.apply_min_score"
ABOVE = "assistant_settings.above_line"
BELOW = "assistant_settings.below_line"
FORM = "https://careers.northwind.example/apply/7"
WORKDAY = "https://acme.wd3.myworkdayjobs.com/en-US/careers/job/Data-Engineer_R1/apply"
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


async def _record(client: AsyncClient, app_id: int, url: str, browser: str | None = None) -> None:
    """Fill and sweep on ``url``'s site (rules 4b, 4c, 5e) in ``browser`` (None = not said)."""
    said = {"browser": browser} if browser else {}
    for event, payload in (("form_filled", {"form_url": url, "fields_count": 1, **said}),
                           ("form_swept", {"fields_checked": 1, "fixed": 0, **said})):
        resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event, "payload": payload})
        assert resp.status_code == 201, resp.text


async def _gate(client: AsyncClient, app_id: int, url: str) -> tuple[str, str]:
    body = (await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": url})).json()
    return body["decision"], body["reason"]


async def _check(client: AsyncClient, app_id: int, url: str, browser: str | None = None) -> tuple[str, str]:
    await _record(client, app_id, url, browser)
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


async def _company_ready(client: AsyncClient) -> int:
    """Auto mode, the practice run done on FORM's company site: a second job there with everything else OK."""
    await _patch(client, SUBMIT, "auto_when_sure")
    first = await _bring(client, 1)
    await _ready(client, first)
    await _check(client, first, FORM)
    await _apply(client, first)
    second = await _bring(client, 2)
    await _ready(client, second)
    return second


class _Pool:
    """Job360's browser has a page open for these keys (``which_browser`` accepts ``browser: job360`` only then)."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        self._sessions: dict[str, Any] = {}  # no page under a hand-off hold

    def user_keys(self, user_id: str) -> list[str]:
        return [k for k in self.keys if k.split("#job:")[0] == user_id]


async def _job360_fill(client: AsyncClient, user_id: str, app_id: int, url: str = FORM) -> None:
    """A fill on FORM in Job360's browser while it has this job's page open, then a sweep that names no browser."""
    from src.services.browser_agent import agent

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "BROWSER_AGENT_ENABLED", True)
        mp.setattr(agent, "get_pool", lambda: _Pool([agent.browser_key(user_id, app_id)]))
        fill = {"form_url": url, "fields_count": 1, "browser": "job360"}
        resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "form_filled", "payload": fill})
        assert resp.status_code == 201, resp.text
    swept = {"event_type": "form_swept", "payload": {"fields_checked": 1, "fixed": 0}}
    assert (await client.post(f"/api/applications/{app_id}/events", json=swept)).status_code == 201
    events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
    newest_fill = max((e for e in events if e["event_type"] == "form_filled"), key=lambda e: e["id"])
    assert newest_fill["payload"]["browser"] == "job360"


async def _save_sign_in(monkeypatch: pytest.MonkeyPatch, user_id: str, host: str) -> None:
    """The user pressed "Keep me signed in" on ``host`` in Job360's browser."""
    from cryptography.fernet import Fernet

    from src.api import dependencies as api_deps
    from src.services.browser_agent import sign_ins

    monkeypatch.setenv("BROWSER_SIGN_IN_KEY", Fernet.generate_key().decode())
    state = {"cookies": [{"name": "sid", "value": "v", "domain": "." + host, "path": "/"}], "origins": []}
    assert await sign_ins.save(await api_deps.get_db(), user_id, host, state) == 1


def _agent_again(agent: AsyncClient) -> AsyncClient:
    """A fresh client with the same assistant token (an AsyncClient is closed after one ``async with``)."""
    return _agent(str(agent.headers["Authorization"]).removeprefix("Bearer "))


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
        assert (await _view(client))[SIGNED_IN.split(".")[1]] == {"value": True, "effective": True}
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
async def test_an_unknown_route_on_linkedin_follows_the_switch(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _linkedin_ready(client)
        assert await _check(client, app_id, LINKEDIN) == ("ask", "ask_always_site")
        await _patch(client, SIGNED_IN, False)  # the user's own click applies at once
        view = await _view(client)
        assert view["ask_before_signed_in_submit"] == {"value": False, "effective": False}
        assert view["waiting"] == []
        assert await _check(client, app_id, LINKEDIN) == ("submit", "auto_when_sure")
        # turning it back on is the safe direction: LinkedIn asks again
        await _patch(client, SIGNED_IN, True)
        assert await _check(client, app_id, LINKEDIN) == ("ask", "ask_always_site")


@pytest.mark.asyncio
async def test_with_it_off_the_practice_run_new_site_and_cv_not_seen_still_ask(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _patch(client, SIGNED_IN, False)
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
            held = await _patch(agent, SIGNED_IN, False)
        assert [(w["path"], w["value"]) for w in held["waiting"]] == [(SIGNED_IN, False)]
        tools = m.build_server()._tool_manager
        token = m._current_user.set(CurrentUser(
            id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc",
        ))
        try:
            via_mcp = await tools.get_tool("update_profile").fn(edits=[{"path": SIGNED_IN, "value": False}])
        finally:
            m._current_user.reset(token)
        assert via_mcp["applied"] == [] and [w["path"] for w in via_mcp["waiting"]] == [SIGNED_IN]
        view = await _view(client)
        assert view["ask_before_signed_in_submit"] == {"value": True, "effective": True}, "not applied"
        assert {w["path"] for w in view["waiting"]} == {SIGNED_IN}
        assert await _check(client, app_id, LINKEDIN) == ("ask", "ask_always_site")
        # the user's confirm on the website applies it
        request_id = view["waiting"][0]["id"]
        confirmed = await client.post(f"/api/assistant-settings/requests/{request_id}/confirm")
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["ask_before_signed_in_submit"] == {"value": False, "effective": False}
        assert await _check(client, app_id, LINKEDIN) == ("submit", "auto_when_sure")
        # an assistant turning it back ON is safer: applied at once
        async with _agent(minted.json()["token"]) as agent:
            on = await _patch(agent, SIGNED_IN, True)
        assert on["waiting"] == []
        assert (await _view(client))["ask_before_signed_in_submit"] == {"value": True, "effective": True}
        assert await _check(client, app_id, LINKEDIN) == ("ask", "ask_always_site")


@pytest.mark.asyncio
async def test_the_linkedin_daily_send_default_is_15(authenticated_async_context, fixture_user_id):
    assert settings.LINKEDIN_SENDS_PER_DAY_DEFAULT == 15
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await _view(client))["linkedin_sends_per_day"] == {"value": None, "effective": 15}


@pytest.mark.asyncio
async def test_the_users_own_browser_asks_while_on_and_submits_when_off(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _company_ready(client)
        assert await _check(client, app_id, FORM) == ("submit", "auto_when_sure"), "a company site, route not said"
        assert await _check(client, app_id, FORM, "own_browser") == ("ask", "ask_always_site")
        assert await _check(client, app_id, FORM, "manual") == ("ask", "ask_always_site")
        await _patch(client, SIGNED_IN, False)
        assert await _check(client, app_id, FORM, "own_browser") == ("submit", "auto_when_sure")


@pytest.mark.asyncio
async def test_job360s_browser_with_no_saved_sign_in_is_never_affected(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _company_ready(client)
        await _save_sign_in(monkeypatch, fixture_user_id, "acme.wd5.myworkdayjobs.com")  # another site's sign-in
        await _job360_fill(client, fixture_user_id, app_id)
        assert await _gate(client, app_id, FORM) == ("submit", "auto_when_sure")
        await _patch(client, SIGNED_IN, False)
        assert await _gate(client, app_id, FORM) == ("submit", "auto_when_sure")


@pytest.mark.asyncio
async def test_job360s_browser_with_a_saved_sign_in_follows_the_switch(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _company_ready(client)
        await _save_sign_in(monkeypatch, fixture_user_id, "careers.northwind.example")
        await _job360_fill(client, fixture_user_id, app_id)
        assert await _gate(client, app_id, FORM) == ("ask", "ask_always_site")
        await _patch(client, SIGNED_IN, False)
        assert await _gate(client, app_id, FORM) == ("submit", "auto_when_sure")


# ── The assistant's route is the server's fact (independent check of #1497) ──────────────────────────


async def _workday_ready(client: AsyncClient) -> tuple[int, AsyncClient]:
    """A Workday job (not Indeed / LinkedIn) with everything else OK, and an assistant's own client."""
    app_id = await _company_ready(client)
    minted = await client.post("/api/tokens", json={"name": "claude-code"})
    assert minted.status_code == 201, minted.text
    return app_id, _agent(minted.json()["token"])


async def _assistant_gate(agent: AsyncClient, app_id: int, url: str = WORKDAY) -> tuple[str, str]:
    body = (await agent.get(f"/api/applications/{app_id}/submit-check", params={"form_url": url})).json()
    return body["decision"], body["reason"]


@contextlib.contextmanager
def _job360_pages(user_id: str, *app_ids: int):
    """Job360's browser has these applications' pages open (BROWSER_AGENT_ENABLED on)."""
    from src.services.browser_agent import agent

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "BROWSER_AGENT_ENABLED", True)
        mp.setattr(agent, "get_pool", lambda: _Pool([agent.browser_key(user_id, a) for a in app_ids]))
        yield


@pytest.mark.asyncio
async def test_a_an_assistant_that_names_no_browser_still_asks(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id, agent = await _workday_ready(client)
        await _record(client, app_id, WORKDAY)  # no browser label on the fill or the sweep
        async with agent:
            assert await _assistant_gate(agent, app_id) == ("ask", "ask_always_site")
            await _patch(client, SIGNED_IN, False)
            assert await _assistant_gate(agent, app_id) == ("submit", "auto_when_sure")


@pytest.mark.asyncio
async def test_b_a_job360_label_while_another_jobs_page_is_open_still_asks(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id, agent = await _workday_ready(client)
        other = await _bring(client, 9)
        with _job360_pages(fixture_user_id, other):
            await _record(client, app_id, WORKDAY, "job360")  # accepted: Job360's browser has SOME page open
            async with agent:
                assert await _assistant_gate(agent, app_id) == ("ask", "ask_always_site")
                await _patch(client, SIGNED_IN, False)
                assert await _assistant_gate(agent, app_id) == ("submit", "auto_when_sure")


@pytest.mark.asyncio
async def test_e_an_own_browser_fill_followed_by_an_unlabelled_fill_still_asks(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id, agent = await _workday_ready(client)
        fill = {"form_url": WORKDAY, "fields_count": 1, "browser": "own_browser"}
        sweep = {"fields_checked": 1, "fixed": 0}
        unlabelled = {"form_url": WORKDAY, "fields_count": 1}
        for event, payload in (("form_filled", fill), ("form_swept", sweep), ("form_filled", unlabelled),
                               ("form_swept", sweep)):
            resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event, "payload": payload})
            assert resp.status_code == 201, resp.text
        assert await _gate(client, app_id, WORKDAY) == ("ask", "ask_always_site"), "the web door reads every label"
        async with agent:
            assert await _assistant_gate(agent, app_id) == ("ask", "ask_always_site")
            await _patch(client, SIGNED_IN, False)
            assert await _assistant_gate(agent, app_id) == ("submit", "auto_when_sure")
            assert await _gate(client, app_id, WORKDAY) == ("submit", "auto_when_sure")


@pytest.mark.asyncio
async def test_job360s_own_click_on_its_open_page_with_no_saved_sign_in_is_unaffected(
    authenticated_async_context, fixture_user_id
):
    from src.api.auth_deps import CurrentUser
    from src.api.routes import browser as browser_routes

    user = CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token",
                       actor_name="claude-code")
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id, agent = await _workday_ready(client)
        await _job360_fill(client, fixture_user_id, app_id, WORKDAY)
        gate = browser_routes._gate_and_files(user, app_id, "")[0]
        for on in (True, False):
            await _patch(client, SIGNED_IN, on)
            click = await gate.check(WORKDAY)  # Job360's browser's own submit click (SERVER_SAYS job360)
            assert (click["decision"], click["reason"]) == ("submit", "auto_when_sure")
        # the same assistant check from outside Job360's click path, with Job360's browser not holding the page
        await _patch(client, SIGNED_IN, True)
        async with agent:
            assert await _assistant_gate(agent, app_id) == ("ask", "ask_always_site")
