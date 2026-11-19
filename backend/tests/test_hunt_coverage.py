"""The hunt covers every search-plan row (owner decision 2026-11-18, coverage audit of the 14:15, 16:13 and 17:11 runs).

The owner's plan has 30 rows (GB, IN, seven regions and ANY; remote, on-site and contract each). The three runs searched
about 80% GB/IN on-site, 15% remote and 3% anything else, and brought nothing outside GB/IN; their reports named
sources only, so mark_done accepted them. Now:

* every plan row has a stable id (``GB:onsite``, ``EUROPE:remote``, ``ANY:contract``) in get_profile's
  ``search_brief.plan`` and in whats_due's hunt item ``plan_rows``;
* mark_done's hunt ``report.coverage`` needs one entry per row (searched with the kit AND at least one other query, or
  skipped with a closed code), the plan rebuilt server-side from the profile; else 422 ``place_missing`` naming rows;
* the effort split sums to exactly 100 (the owner's plan said 107);
* an on-site row abroad keeps every ad whatever it says about sponsorship (the brief no longer says "only when the
  ad sponsors").
Helpers are copied, never imported from another test module (schema isolation).
"""
from __future__ import annotations

import copy
import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.profile.models import CVData, UserPreferences, UserProfile
from src.services.profile.search_brief import build_search_brief, plan_row_ids

_REGIONS = ("NORTH_AMERICA", "EUROPE", "SOUTH_AMERICA", "ASIA", "AFRICA", "OCEANIA", "MIDDLE_EAST")


def _rec(country: str, onsite: str = "yes", remote: str = "highest", on: str = "highest",
         contract: str = "highest", needs: bool = True) -> dict[str, Any]:
    return {"country": country, "needs_sponsorship": needs, "remote_ok": True, "onsite": onsite, "contract_ok": True,
            "remote_priority": remote, "onsite_priority": on, "contract_priority": contract}


# The owner's right_to_work levels as get_profile read them on 2026-11-18 (no personal facts).
OWNER_RTW: dict[str, Any] = {"countries": [
    {**_rec("GB", needs=False), "work_authorization": "visa"},
    {**_rec("IN", needs=False), "work_authorization": "citizen"},
    _rec("ANY", onsite="only_if_sponsored", contract="medium"),
    _rec("NORTH_AMERICA"), _rec("EUROPE"), _rec("SOUTH_AMERICA", on="medium", contract="medium"), _rec("ASIA"),
    _rec("AFRICA", on="medium", contract="low"), _rec("OCEANIA", contract="medium"), _rec("MIDDLE_EAST"),
]}
ALL_ROWS = {f"{c}:{k}" for c in ("GB", "IN", "ANY", *_REGIONS) for k in ("remote", "onsite", "contract")}

_SOURCES = {"mail": {"calls": 2, "ads": 9, "brought": 1}, "kit": {"calls": 1, "ads": 496, "brought": 3},
            "connectors": {"calls": 6, "ads": 40, "brought": 1}, "web": {"calls": 8, "ads": 30, "brought": 0},
            "browser": {"skipped": {"code": "tool_missing", "detail": "no browser tool in this scheduled cloud run"}}}
_KIT = {"results": 496, "new": 370, "prefilter_passed": 370, "read_in_full": 370, "unreadable": 0, "snippet_only": 0,
        "done": {"results": 496, "full": 400, "snippet": 90, "empty": 6}}
# The 14:15 / 16:13 / 17:11 reports as they were accepted: every source counted, no row named.
REPLAY_RUN: dict[str, Any] = {"sources": _SOURCES, "kit": _KIT}


def _searched(row: str) -> dict[str, Any]:
    return {"row": row, "titles": 23, "sources": {"kit": 1, "connectors": 2, "web": 1, "browser": 0},
            "ads": 12, "brought": 0}


def _full_report(rows: list[str]) -> dict[str, Any]:
    return {**copy.deepcopy(REPLAY_RUN), "coverage": [_searched(r) for r in rows]}


# ── pure: row ids and the effort split ───────────────────────────────────────


def test_the_owners_plan_has_thirty_row_ids_and_its_effort_sums_to_exactly_100():
    plan = build_search_brief(OWNER_RTW, "GB")["plan"]
    assert {p["row"] for p in plan} == ALL_ROWS and len(plan) == 30
    assert all(p["row"] == f"{p['country']}:{p['kind']}" for p in plan)
    assert sum(p["effort_pct"] for p in plan) == 100, "round() gave this plan 107"
    assert plan_row_ids({"plan": plan}) == [p["row"] for p in plan], "plan order, every row has effort"
    by_row = {p["row"]: p["effort_pct"] for p in plan}
    assert by_row["GB:onsite"] >= by_row["AFRICA:onsite"] >= by_row["AFRICA:contract"] >= 1


@pytest.mark.parametrize("weights", [[1], [1, 1, 1], [4, 3, 2, 1], [2] * 7, [4] * 24 + [2] * 5 + [1], [1] * 150])
def test_the_effort_split_always_sums_to_100(weights):
    from src.services.profile import job_priority

    levels = {4: "highest", 3: "high", 2: "medium", 1: "low"}
    plan = job_priority.effort_split([{"priority": levels[w]} for w in weights])
    assert sum(p["effort_pct"] for p in plan) == 100
    assert all(p["effort_pct"] >= 0 for p in plan)


def test_on_site_abroad_keeps_every_ad_whatever_it_says_about_sponsorship():
    rows = {r["country"]: r for r in build_search_brief(OWNER_RTW, "GB")["rows"]}
    for country in ("ANY", "EUROPE"):
        onsite = rows[country]["counts_if"][1]
        assert "keep the ad whatever it says about sponsorship and record what it says" in onsite
        assert "only when the ad says it sponsors" not in onsite and "refuses sponsorship is out" not in onsite


# ── real routes on the real Postgres ─────────────────────────────────────────


def _seed(user_id: str, rtw: dict[str, Any]) -> None:
    from src.services.profile.storage import save_profile, save_user_info

    profile = UserProfile(
        cv_data=CVData(raw_text="Python AI engineer.", name="Ada Lovelace", skills=["Python"]),
        preferences=UserPreferences(target_job_titles=["AI Engineer"]),
    )
    save_profile(profile, user_id, source_action="cv_upload")
    profile.user_info.right_to_work = rtw
    profile.user_info.contact = {"residence_country": "GB"}
    assert save_user_info(user_id, profile.user_info, source_action="test_seed")


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "scheduler"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _done(agent: AsyncClient, report: dict[str, Any], outcome: str = "done"):
    return await agent.post("/api/schedule/done", json={"duty": "hunt", "outcome": outcome, "report": report})


@pytest.mark.asyncio
async def test_whats_due_names_the_same_plan_rows_as_get_profile(authenticated_async_context, fixture_user_id):
    _seed(fixture_user_id, OWNER_RTW)
    async with authenticated_async_context() as client, await _agent(client) as agent:
        on = await client.patch("/api/profile", json={"edits": [{"path": "assistant_settings.hunt_every_hours",
                                                                 "value": 1}]})
        assert on.status_code == 200, on.text
        due = (await agent.get("/api/schedule/due")).json()
    (hunt,) = [d for d in due["due"] if d["duty"] == "hunt"]
    plan = build_search_brief(OWNER_RTW, "GB")["plan"]
    assert hunt["plan_rows"] == [p["row"] for p in plan] and set(hunt["plan_rows"]) == ALL_ROWS


@pytest.mark.asyncio
async def test_the_replayed_gb_in_only_report_is_refused_naming_every_missing_row(
    authenticated_async_context, fixture_user_id, caplog,
):
    caplog.set_level(logging.INFO)
    _seed(fixture_user_id, OWNER_RTW)
    async with authenticated_async_context() as client, await _agent(client) as agent:
        bare = await _done(agent, REPLAY_RUN)
        gb_in = _full_report(["GB:onsite", "IN:onsite", "GB:remote", "IN:remote"])
        short = await _done(agent, gb_in, outcome="partial")
        due = (await client.get("/api/schedule/due")).json()
    assert bare.status_code == 422, bare.text
    assert "EUROPE:onsite" in bare.json()["detail"]
    assert short.status_code == 422, short.text
    named = short.json()["detail"].split("no entry for plan rows ", 1)[1].split(": search each one", 1)[0]
    assert set(named.split(", ")) == ALL_ROWS - {"GB:onsite", "IN:onsite", "GB:remote", "IN:remote"}
    assert due["last_done_at"]["hunt"] is None, "nothing was stamped"
    refused = [r for r in caplog.records if r.getMessage() == "hunt_report_refused"]
    assert [r.codes for r in refused] == ["place_missing", "place_missing"]
    assert refused[-1].plan_rows == "30" and refused[-1].rows_covered == "4"


@pytest.mark.asyncio
async def test_a_row_searched_by_the_kit_alone_or_skipped_with_an_excuse_is_refused(
    authenticated_async_context, fixture_user_id,
):
    _seed(fixture_user_id, OWNER_RTW)
    rows = sorted(ALL_ROWS)
    report = _full_report(rows)
    report["coverage"][0]["sources"] = {"kit": 1, "connectors": 0, "web": 0, "browser": 0}
    report["coverage"][1] = {"row": rows[1], "skipped": {"code": "no_results", "detail": "not run: no time"}}
    report["coverage"][2] = {"row": rows[2], "titles": 0, "sources": {"kit": 1}, "ads": 0, "brought": 0}
    async with authenticated_async_context() as client, await _agent(client) as agent:
        resp = await _done(agent, report)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert f"coverage rows {rows[0]} ran the kit only" in detail
    assert f"coverage rows {rows[1]}: \"not run\"" in detail
    assert f"coverage rows {rows[2]} need titles" in detail
    assert "not run: no time" not in detail, "never the caller's text"


@pytest.mark.asyncio
async def test_every_row_covered_or_honestly_skipped_is_recorded_and_logged_as_counts(
    authenticated_async_context, fixture_user_id, caplog,
):
    caplog.set_level(logging.INFO)
    _seed(fixture_user_id, OWNER_RTW)
    rows = sorted(ALL_ROWS)
    report = _full_report(rows)
    report["coverage"][0] = {"row": rows[0], "skipped": {"code": "login_wall", "detail": "Upwork asked to sign in"}}
    report["coverage"][1]["titles"] = ["AI Engineer", "Machine Learning Engineer"]
    report["coverage"].append(_searched("MARS:onsite"))  # a row not in the plan is ignored, never echoed
    async with authenticated_async_context() as client, await _agent(client) as agent:
        resp = await _done(agent, report)
    assert resp.status_code == 200, resp.text
    (ok,) = [r for r in caplog.records if r.getMessage() == "hunt_report_ok"]
    assert (ok.plan_rows, ok.rows_covered, ok.rows_skipped) == (30, 30, 1)
    assert "MARS" not in str(ok.__dict__) and "Upwork" not in str(ok.__dict__)


@pytest.mark.asyncio
async def test_a_user_with_no_plan_needs_no_coverage_and_the_website_never_does(
    authenticated_async_context, fixture_user_id,
):
    async with authenticated_async_context() as client:
        async with await _agent(client) as agent:
            assert (await _done(agent, REPLAY_RUN)).status_code == 200, "no profile: no plan rows"
        _seed(fixture_user_id, OWNER_RTW)
        assert (await client.post("/api/schedule/done", json={"duty": "hunt"})).status_code == 200


def test_the_hunt_recipe_reports_every_plan_row():
    from src.api.routes.recipes import load_recipe

    text = load_recipe("hunt").text
    assert len(text.splitlines()) <= 70
    for must in ("`plan_rows`", "coverage: [", "EVERY `plan` row is searched every due run", "`place_missing`",
                 "the coverage table (= `report.coverage`)"):
        assert must in text, must
