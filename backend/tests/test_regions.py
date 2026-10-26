"""REGIONS (owner decision 2026-10-26): one Where-you'll-work line can cover a whole
region ("Europe, on-site only if sponsored") as well as one country or ANY.

The rule every reader shares: a job in a country takes the country's own line, else
its region's line, else ANY. Pure; no database.
"""
from __future__ import annotations

import re
from typing import Any

import pytest

from src.services.profile import countries, regions
from src.services.profile.edits import ProfileEditError, validate_edit

RTW = "user_info.right_to_work"
SALARY = "preferences.salary_by_country"


def _rtw(*records: dict[str, Any]) -> dict[str, Any]:
    return {"countries": list(records)}


def test_every_country_sits_in_exactly_one_region():
    codes = set(re.findall(r"(?:^|\|)([A-Z]{2}) ", countries._TABLE.replace("\n", "|")))
    seen: dict[str, str] = {}
    for region, members in regions.REGIONS.items():
        for cc in members:
            assert cc not in seen, f"{cc} is in {seen[cc]} and {region}"
            assert cc in codes, f"{cc} is not an ISO code"
            seen[cc] = region
    assert codes - set(seen) == {"AQ"}  # Antarctica: no job market


def test_a_region_is_a_valid_place_for_work_lines_and_salary():
    rtw = validate_edit(RTW, {"countries": [{"country": "Europe", "onsite": "only_if_sponsored"}, {"country": "GB"}]})
    assert [r["country"] for r in rtw["countries"]] == ["EUROPE", "GB"]
    sal = validate_edit(SALARY, [{"country": "middle east", "min": 20000, "max": 30000, "currency": "AED", "period": "month"}])
    assert sal[0]["country"] == "MIDDLE_EAST"
    with pytest.raises(ProfileEditError):
        validate_edit(RTW, {"countries": [{"country": "Atlantis"}]})


def test_a_country_line_wins_over_its_region_and_the_region_over_any():
    from src.services.profile import job_priority

    block = _rtw(
        {"country": "DE", "onsite": "yes", "onsite_priority": "highest"},
        {"country": "EUROPE", "onsite": "only_if_sponsored", "onsite_priority": "low"},
        {"country": "ANY", "remote_ok": True, "onsite_priority": "off"},
    )
    assert job_priority.place_priority(block, "DE", False) == "highest"
    assert job_priority.place_priority(block, "FR", False) == "low"  # France: Europe's line
    assert job_priority.place_priority(block, "JP", False) == "off"  # Japan: ANY
    assert job_priority.record_for(_rtw({"country": "ASIA"}), "IN")["country"] == "ASIA"


def test_the_filter_gives_a_region_its_countries_less_those_with_their_own_line():
    from src.services.profile import job_priority

    block = _rtw(
        {"country": "DE", "onsite": "yes", "onsite_priority": "high"},
        {"country": "EUROPE", "onsite": "yes", "onsite_priority": "low"},
        {"country": "ANY", "onsite": "yes", "onsite_priority": "medium"},
    )
    sql, params = job_priority.filter_sql(block, "low")
    assert "IN (" in sql and "FR" in params and "DE" not in params
    sql_any, params_any = job_priority.filter_sql(block, "medium")
    # ANY covers what neither Germany nor Europe answers: every European country is excluded
    assert "NOT IN" in sql_any and {"DE", "FR", "PL"} <= set(params_any)


def test_the_search_brief_names_a_region_and_never_counts_it_as_a_work_from_country():
    from src.services.profile.search_brief import build_search_brief

    brief = build_search_brief(_rtw(
        {"country": "EUROPE", "needs_sponsorship": False, "remote_ok": True, "onsite": "yes"},
        {"country": "IN", "work_authorization": "citizen", "remote_ok": True, "onsite": "yes"},
    ))
    assert [r["country"] for r in brief["rows"]] == ["IN", "EUROPE"]  # country first, then region
    assert brief["work_from"] == ["IN"]
    assert "Europe (any country there without its own line)" in " ".join(brief["rows"][1]["counts_if"])


def test_a_region_answers_visa_need_for_its_countries_but_never_over_a_countrys_own_line():
    from src.services.applications import visa

    block = _rtw(
        {"country": "MIDDLE_EAST", "needs_sponsorship": True},
        {"country": "AE", "needs_sponsorship": False},
        {"country": "EUROPE", "needs_sponsorship": False},
    )
    needs = visa._sponsorship_countries(block)
    free = visa._no_sponsorship_countries(block)
    assert "SA" in needs and "QA" in needs and "AE" not in needs
    assert "AE" in free and "FR" in free and "SA" not in free
    assert "MIDDLE_EAST" not in needs and "EUROPE" not in free  # codes are expanded, never passed as places


def test_the_kit_answers_a_country_from_its_region_before_any():
    from src.services.applications import kit
    from src.services.profile.models import UserInfo, UserPreferences, UserProfile

    profile = UserProfile(
        user_info=UserInfo(right_to_work=_rtw(
            {"country": "EUROPE", "needs_sponsorship": True, "work_authorization": "needs_sponsorship"},
            {"country": "ANY", "needs_sponsorship": False, "work_authorization": "visa"},
        )),
        preferences=UserPreferences(salary_by_country=[
            {"country": "EUROPE", "min": 60000, "max": 70000, "currency": "EUR", "period": "year"},
        ]),
    )
    blocks, missing = kit.compute_answers(profile, [], "FR")
    fr = {i["key"]: i for i in blocks["right_to_work"] if i["key"].startswith("right_to_work.FR.")}
    assert fr["right_to_work.FR.needs_sponsorship"]["value"] is True  # Europe's answer, not ANY's
    assert fr["right_to_work.FR.needs_sponsorship"]["applies_via"] == "EUROPE"
    salary = {i["key"]: i for i in blocks["salary"]}
    assert salary["salary.FR"]["value"]["currency"] == "EUR" and salary["salary.FR"]["applies_via"] == "EUROPE"
    assert not [m for m in missing if m["key"] in ("salary.FR", "right_to_work.FR.needs_sponsorship")]
    # outside Europe the ANY line still answers
    blocks_jp, _ = kit.compute_answers(profile, [], "JP")
    jp = {i["key"]: i for i in blocks_jp["right_to_work"] if i["key"].startswith("right_to_work.JP.")}
    assert jp["right_to_work.JP.needs_sponsorship"]["value"] is False
    assert "salary.JP" not in {i["key"] for i in blocks_jp["salary"]}


def test_readiness_counts_a_region_salary_for_its_countries():
    from src.services.applications import ready
    from src.services.profile.models import UserPreferences, UserProfile

    profile = UserProfile(preferences=UserPreferences(salary_by_country=[
        {"country": "EUROPE", "min": 60000, "max": 70000, "currency": "EUR", "period": "year"},
    ]))
    assert ready._salary_saved(profile, "FR") and not ready._salary_saved(profile, "IN")
