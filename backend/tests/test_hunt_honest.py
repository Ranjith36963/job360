"""The hunt reports what really happened (evidence: the owner's 2026-11-18 hunt run, reports/hunt-run-2026-11-18.md).

The 11-18 run (Sonnet, recipe hunt.md after #1628 and #1639) spent 42 of 52 minutes waiting on the kit, then sent
a mark_done report that Job360 accepted although it was not true:

* ``read_in_full`` 221 = ``prefilter_passed`` 221, while about 35 ads were unreadable (403/404/410) or judged on a
  500-character snippet - the free-text ``unread_reason`` let it pass. Now the kit funnel carries ``unreadable`` and
  ``snippet_only`` and ``read_in_full + unreadable + snippet_only`` must equal ``prefilter_passed``.
* ``skipped`` was an excuse ("not run: WebSearch/TinyFish/Apify not called this run"). Now a skip is
  ``{code, detail}`` with a closed code; free text alone, or "not run / not called / no time", is refused.
* ``possible_duplicate`` named 10 ids on a generic title in other cities (1034), and was read past. Now it fires
  only when the role words AND the place match, with at most 5 ids.
* the profile had no target titles and the run invented 23. Now whats_due's hunt item says ``needs``.
Real routes and the MCP tools on the real Postgres; the website's own calls are unchanged.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.profile import schedule

# ── the 2026-11-18 mark_done report, exactly as the run sent it (section 2 of the evidence) ──────────────────
REPORT_1118: dict[str, Any] = {
    "kit": {"new": 221, "prefilter_passed": 221, "read_in_full": 221, "results": 283,
            "unread_reason": "about 35 ads stayed truncated or unreadable (403 on whatjobs/adzuna/naukri); judged on "
                             "the kit's snippet or scored null"},
    "sources": {
        "browser": {"skipped": "not run: no job board needed a browser; arbeitnow/adzuna/naukri pages returned 403 "
                               "to plain fetch and were not retried with a browser"},
        "connectors": {"ads": 31, "brought": 0, "calls": 4},
        "kit": {"ads": 283, "brought": 6, "calls": 1},
        "mail": {"ads": 16, "brought": 0, "calls": 8},
        "web": {"skipped": "not run: WebSearch/TinyFish/Apify not called this run; kit and connectors used instead"},
    },
}
_RAN = {"calls": 3, "ads": 40, "brought": 1}
HONEST: dict[str, Any] = {
    "sources": {"mail": _RAN, "kit": {"calls": 1, "ads": 283, "brought": 6}, "connectors": _RAN, "web": _RAN,
                "browser": {"skipped": {"code": "login_wall", "detail": "arbeitnow.co.uk asked for a sign-in"}}},
    "kit": {"results": 283, "new": 221, "prefilter_passed": 221, "read_in_full": 180, "unreadable": 29,
            "snippet_only": 12, "done": {"results": 283, "full": 240, "snippet": 34, "empty": 9}},
}


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "scheduler"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _done(agent: AsyncClient, report: dict[str, Any], outcome: str = "done"):
    return await agent.post("/api/schedule/done", json={"duty": "hunt", "outcome": outcome, "report": report})


def _refused(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "hunt_report_refused"]


# ── 1 + 2. the 11-18 report replayed ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_1118_hunt_report_is_refused_with_what_to_fix(authenticated_async_context, caplog):
    caplog.set_level(logging.INFO)
    async with authenticated_async_context() as client, await _agent(client) as agent:
        resp = await _done(agent, REPORT_1118)
        due = (await client.get("/api/schedule/due")).json()
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "unreadable" in detail and "snippet_only" in detail
    assert "sources web, browser" in detail and "code" in detail and "tool_error" in detail
    for caller_text in ("arbeitnow", "WebSearch", "whatjobs", "not called"):
        assert caller_text not in detail, "the 422 never echoes the caller's text"
    assert due["last_done_at"]["hunt"] is None, "nothing was stamped"
    refused = _refused(caplog)
    assert len(refused) == 1 and refused[0].codes == "skip_free_text,kit_unaccounted"
    assert "arbeitnow" not in str(refused[0].__dict__) and "WebSearch" not in str(refused[0].__dict__)


@pytest.mark.asyncio
async def test_unreadable_ads_counted_on_top_of_a_full_read_count_are_refused(authenticated_async_context, caplog):
    """The same run, honest about its 35 unreadable ads but still claiming 221 read in full: 221 + 35 != 221."""
    caplog.set_level(logging.INFO)
    lying = {**HONEST, "kit": {**HONEST["kit"], "read_in_full": 221, "unreadable": 35, "snippet_only": 0}}
    async with authenticated_async_context() as client, await _agent(client) as agent:
        resp = await _done(agent, lying)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "221 + 35 + 0 = 256" in detail and "prefilter_passed is 221" in detail
    assert _refused(caplog)[0].codes == "kit_unread"


@pytest.mark.asyncio
async def test_an_honest_report_is_accepted_and_logs_counts_and_skip_codes_only(authenticated_async_context, caplog):
    caplog.set_level(logging.INFO)
    async with authenticated_async_context() as client, await _agent(client) as agent:
        resp = await _done(agent, HONEST, outcome="partial")
    assert resp.status_code == 200, resp.text
    ok = [r for r in caplog.records if r.getMessage() == "hunt_report_ok"]
    assert len(ok) == 1
    assert (ok[0].kit_read_in_full, ok[0].kit_unreadable, ok[0].kit_snippet_only) == (180, 29, 12)
    assert ok[0].skips == "browser:login_wall"
    assert "arbeitnow" not in str(ok[0].__dict__), "a skip's detail is never logged"


@pytest.mark.parametrize("code", ["tool_missing", "tool_error", "no_results", "paid_needs_yes", "login_wall"])
@pytest.mark.asyncio
async def test_every_closed_skip_code_with_a_detail_is_accepted(authenticated_async_context, code):
    report = {**HONEST, "sources": {**HONEST["sources"], "web": {"skipped": {"code": code, "detail": "HTTP 503"}}}}
    async with authenticated_async_context() as client, await _agent(client) as agent:
        resp = await _done(agent, report)
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize(("skipped", "code"), [
    ("tool_missing", "skip_free_text"),
    ({"code": "not_needed", "detail": "the kit was enough"}, "skip_code"),
    ({"code": "tool_error", "detail": " "}, "skip_detail"),
    ({"code": "tool_error"}, "skip_detail"),
    ({"code": "tool_missing", "detail": "not run: WebSearch/TinyFish/Apify not called this run"}, "skip_excuse"),
    ({"code": "no_results", "detail": "No time left after the kit"}, "skip_excuse"),
    ({"code": "tool_error", "detail": "Didn't run it"}, "skip_excuse"),
    ({"code": "tool_missing", "detail": "out of time"}, "skip_excuse"),
])
@pytest.mark.asyncio
async def test_a_skip_that_is_free_text_unknown_empty_or_an_excuse_is_refused_naming_the_source(
    authenticated_async_context, caplog, skipped, code,
):
    caplog.set_level(logging.INFO)
    report = {**HONEST, "sources": {**HONEST["sources"], "web": {"skipped": skipped}}}
    async with authenticated_async_context() as client, await _agent(client) as agent:
        resp = await _done(agent, report)
    assert resp.status_code == 422, resp.text
    assert "web" in resp.json()["detail"]
    assert _refused(caplog)[0].codes == code


@pytest.mark.parametrize("kit", [
    {"results": 283, "new": 221, "prefilter_passed": 221, "read_in_full": 221},
    {"results": 283, "new": 221, "prefilter_passed": 221, "read_in_full": 221, "unreadable": 0},
])
@pytest.mark.asyncio
async def test_a_kit_funnel_without_both_honest_counts_is_refused(authenticated_async_context, caplog, kit):
    caplog.set_level(logging.INFO)
    async with authenticated_async_context() as client, await _agent(client) as agent:
        resp = await _done(agent, {**HONEST, "kit": kit})
    assert resp.status_code == 422, resp.text
    assert "kit.unreadable" in resp.json()["detail"] and "kit.snippet_only" in resp.json()["detail"]
    assert _refused(caplog)[0].codes == "kit_unaccounted"


@pytest.mark.asyncio
async def test_the_mcp_mark_done_refuses_the_1118_report(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    tools = m.build_server()._tool_manager
    token = m._current_user.set(
        CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc")
    )
    try:
        async with authenticated_async_context():
            with pytest.raises(Exception, match="snippet_only"):
                await tools.get_tool("mark_done").fn(duty="hunt", report=REPORT_1118)
            assert (await tools.get_tool("mark_done").fn(duty="hunt", report=HONEST))["duty"] == "hunt"
    finally:
        m._current_user.reset(token)


def test_the_mcp_mark_done_text_names_the_new_shape():
    from src.api import mcp_server as m

    text = m.build_server()._tool_manager.get_tool("mark_done").description
    for word in ("unreadable", "snippet_only", "tool_missing", "tool_error", "no_results", "paid_needs_yes",
                 "login_wall"):
        assert word in text, word


# ── 3. a quieter possible_duplicate ──────────────────────────────────────────────────────────────────────────


def test_same_place_matches_the_city_or_the_whole_place_only() -> None:
    from src.services.applications.reposts import same_place

    assert same_place("London", "London, UK") and same_place("Pune, Maharashtra, India", "Pune")
    assert same_place("", "")
    assert not same_place("Pune, India", "Bengaluru, India"), "a shared country is not the same city"
    assert not same_place("Pune, India", "London, UK")
    assert not same_place("Remote", "Remote, US"), "remote alone names no place"
    assert not same_place("London", "")


_AIML = "Build agentic systems with LLMs, retrieval and evaluation. Python. " * 4
# The 1034 case: a generic title the user already had open under other employers in other cities.
_OTHER_CITIES = ["London, UK", "Bengaluru, India", "Hyderabad, India", "Remote, US", "Berlin, Germany",
                 "Dublin, Ireland", "Toronto, Canada", "Singapore", "Chennai, India", "Austin, TX"]


def _payload(result) -> dict:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def _mcp_client(token: str):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test",
                              headers={"Authorization": f"Bearer {token}"})
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


@pytest.mark.asyncio
async def test_the_1034_generic_title_in_another_city_no_longer_warns(authenticated_async_context, caplog):
    from src.api.mcp_server import mcp_runtime

    caplog.set_level(logging.INFO)
    async with authenticated_async_context() as client:
        for i, place in enumerate(_OTHER_CITIES):
            seeded = await client.post("/api/jobs/bring", json={
                "title": "AI/ML Engineer", "company": f"Employer {i}", "location": place,
                "apply_url": f"https://careers.employer{i}.example/jobs/{i}", "description": _AIML,
            })
            assert seeded.status_code == 200, seeded.text
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
    async with mcp_runtime(), _mcp_client(token) as mcp:
        reply = _payload(await mcp.call_tool("bring_job", {
            "title": "AI/ML Engineer (Agentic Systems)", "company": "Intangles", "location": "Pune, India",
            "apply_url": "https://careers.intangles.example/jobs/1034", "description": _AIML,
        }))
    assert [w["code"] for w in reply["warnings"]] == [], reply["warnings"]
    assert not [r for r in caplog.records if r.getMessage() == "job_bring_possible_duplicate"]


@pytest.mark.asyncio
async def test_same_role_in_the_same_city_warns_with_at_most_five_ids(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    ids: list[int] = []
    async with authenticated_async_context() as client:
        for i in range(7):
            seeded = await client.post("/api/jobs/bring", json={
                "title": "AI/ML Engineer", "company": f"Agency {i}", "location": "Pune, Maharashtra",
                "apply_url": f"https://careers.agency{i}.example/jobs/{i}", "description": _AIML,
            })
            ids.append(seeded.json()["application_id"])
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
    async with mcp_runtime(), _mcp_client(token) as mcp:
        reply = _payload(await mcp.call_tool("bring_job", {
            "title": "AI/ML Engineer (Agentic Systems)", "company": "Intangles", "location": "Pune, India",
            "apply_url": "https://careers.intangles.example/jobs/1034", "description": _AIML,
        }))
    dup = [w for w in reply["warnings"] if w["code"] == "possible_duplicate"]
    assert len(dup) == 1 and dup[0]["application_ids"] == ids[:5], "oldest first, capped at 5"


# ── 4. no target titles: the hunt asks first ────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 11, 18, 9, 13, tzinfo=timezone.utc)


def _facts(**kw: Any) -> schedule.DueFacts:
    base: dict[str, Any] = {
        "hunt_every_hours": 1, "inbox_mode": "", "check_every": "", "paused": False, "apply_on_own": False,
        "queued": 0, "daily_left": None, "weekly_left": None, "inside_hours": True, "stamps": {},
    }
    base.update(kw)
    return schedule.DueFacts(**base)


def test_a_due_hunt_without_target_titles_says_it_needs_them():
    bare = schedule.due_now(_facts(titles_missing=True), _NOW)
    assert bare["due"] == [{"duty": "hunt", "recipe": "hunt", "reason": "never_done",
                            "needs": ["preferences.target_job_titles"]}]
    assert schedule.due_now(_facts(), _NOW)["due"] == [{"duty": "hunt", "recipe": "hunt", "reason": "never_done"}]


@pytest.mark.asyncio
async def test_whats_due_reads_the_users_target_titles(authenticated_async_context, fixture_user_id, caplog):
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    caplog.set_level(logging.INFO)
    async with authenticated_async_context() as client:
        save_profile(UserProfile(cv_data=CVData(raw_text="Python.", name="Ada")), fixture_user_id,
                     source_action="cv_upload")
        on = await client.patch("/api/profile", json={"edits": [
            {"path": "assistant_settings.hunt_every_hours", "value": 1}]})
        assert on.status_code == 200, on.text
        empty = (await client.get("/api/schedule/due")).json()
        titled = await client.patch("/api/profile", json={"edits": [
            {"path": "preferences.target_job_titles", "value": ["Data Engineer"]}]})
        assert titled.status_code == 200, titled.text
        full = (await client.get("/api/schedule/due")).json()
    hunt = [d for d in empty["due"] if d["duty"] == "hunt"]
    assert hunt and hunt[0]["needs"] == ["preferences.target_job_titles"]
    assert [d.get("needs") for d in full["due"] if d["duty"] == "hunt"] == [None]
    reads = [r for r in caplog.records if r.getMessage() == "schedule_due_read"]
    assert reads[0].hunt_needs == "preferences.target_job_titles" and reads[-1].hunt_needs == "none"


# ── 5. the recipe says it ────────────────────────────────────────────────────────────────────────────────────


def test_the_hunt_recipe_works_while_the_kit_runs_and_reports_honestly():
    from src.api.routes.recipes import load_recipe

    text = load_recipe("hunt").text
    flat = " ".join(text.split())
    assert len(text.splitlines()) <= 70
    assert "--max-minutes 20" in flat and "--max-minutes 25" not in flat and "--max-minutes 40" not in flat
    assert "split -l 40" in flat and "in waves" in flat and "8 helpers" in flat
    assert "unreadable" in flat and "snippet_only" in flat
    assert "read_in_full + unreadable + snippet_only = prefilter_passed" in flat
    for code in ("tool_missing", "tool_error", "no_results", "paid_needs_yes", "login_wall"):
        assert code in flat, code
    assert "Job360 `browser`, then TinyFish, then Apify" in flat
    assert "DON'T BRING is final" in flat
    assert "possible_duplicate" in flat and "get_application" in flat
    assert "follow each job link" in flat and "every `search_brief.rows` row" in flat
    assert "preferences.target_job_titles" in flat and "needs" in flat and "never invent titles" in flat
