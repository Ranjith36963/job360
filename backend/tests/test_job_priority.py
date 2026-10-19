"""JOB PRIORITY (owner decision 2026-10-19): the user's own order of search effort per place.

The owner's words: visa-sponsored on-site abroad and remote anywhere are the highest
priority; India and UK on-site come next. Job360 never ranks a job (VISION rule 4) -
it only stores the level per place, hands the assistant a plan in that order with an
effort split, and lets the user filter their own applications by it. An unchosen
level stays null (rule #29); "off" is never searched.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.services.profile import job_priority
from src.services.profile.search_brief import build_search_brief

_OWNER = {"countries": [
    {"country": "GB", "work_authorization": "visa", "needs_sponsorship": False, "remote_ok": True,
     "onsite": "yes", "remote_priority": "highest", "onsite_priority": "high"},
    {"country": "IN", "work_authorization": "citizen", "needs_sponsorship": False, "remote_ok": True,
     "onsite": "yes", "remote_priority": "highest", "onsite_priority": "high"},
    {"country": "ANY", "needs_sponsorship": True, "remote_ok": True, "onsite": "only_if_sponsored",
     "remote_priority": "highest", "onsite_priority": "highest"},
]}


def test_plan_puts_visa_abroad_and_remote_first_with_the_most_effort():
    plan = build_search_brief(_OWNER, "GB")["plan"]
    top = [(p["country"], p["kind"]) for p in plan if p["priority"] == "highest"]
    assert ("ANY", "onsite") in top and ("ANY", "remote") in top
    assert [p["priority"] for p in plan[-2:]] == ["high", "high"]  # India + UK on-site last
    assert plan[0]["effort_pct"] > plan[-1]["effort_pct"]
    assert 98 <= sum(p["effort_pct"] for p in plan) <= 102  # rounding


def test_off_place_is_never_searched_and_unchosen_stays_null():
    rtw = {"countries": [
        {"country": "GB", "work_authorization": "citizen", "remote_ok": True, "onsite": "yes",
         "onsite_priority": "off"},
    ]}
    brief = build_search_brief(rtw)
    assert brief["rows"][0]["counts_if"] == [brief["plan"][0]["counts_if"]]  # on-site line gone
    assert len(brief["plan"]) == 1 and brief["plan"][0]["kind"] == "remote"
    assert brief["plan"][0]["priority"] is None  # never a default (rule #29)
    assert brief["rows"][0]["priority"] == {"remote": None}


def test_place_of_a_job_uses_its_country_record_else_any():
    assert job_priority.place_priority(_OWNER, "IN", False) == "high"
    assert job_priority.place_priority(_OWNER, "DE", False) == "highest"  # visa abroad
    assert job_priority.place_priority(_OWNER, None, True) == "highest"
    assert job_priority.place_priority(_OWNER, "GB", None) is None  # remote unknown


def test_bad_level_is_refused():
    from src.services.profile.user_info import validate_user_info

    with pytest.raises(Exception) as exc:
        validate_user_info("user_info.right_to_work", {"countries": [
            {"country": "GB", "remote_ok": True, "remote_priority": "urgent"}]})
    assert "highest" in str(exc.value)


async def _bring(client: Any, title: str, country: str, remote: bool) -> int:
    resp = await client.post("/api/jobs/bring", json={
        "title": title, "company": f"Co {title}", "description": f"{title} role. Python.",
        "apply_url": f"https://example.com/{title}", "country": country, "remote": remote,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["application_id"]


@pytest.mark.asyncio
async def test_applications_filter_by_priority_end_to_end(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.patch("/api/profile", json={"edits": [
            {"path": "user_info.right_to_work", "value": _OWNER}]})
        assert resp.status_code == 200, resp.text
        berlin = await _bring(client, "Berlin", "DE", False)
        pune = await _bring(client, "Pune", "IN", False)

        rows = (await client.get("/api/applications")).json()["applications"]
        by_id = {r["id"]: r["priority"] for r in rows}
        assert by_id[berlin] == "highest" and by_id[pune] == "high"

        top = (await client.get("/api/applications", params={"priority": "highest"})).json()
        assert [r["id"] for r in top["applications"]] == [berlin] and top["total"] == 1
        bad = await client.get("/api/applications", params={"priority": "urgent"})
        assert bad.status_code == 422
