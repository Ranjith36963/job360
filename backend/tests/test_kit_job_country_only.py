"""The kit is for ONE job in ONE country: right_to_work / logistics / salary carry the global keys and
this job's country only (prod 2026-10-30: a kit of 40,628 chars, 16,856 of it other countries' records).
An unknown country keeps everything. Pure; no database."""
from __future__ import annotations

import json

from src.services.applications import kit
from src.services.profile.models import UserInfo, UserPreferences, UserProfile


def _profile(*extra_rtw: dict) -> UserProfile:
    rtw = [
        {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False, "visa_type": "none"},
        {"country": "IN", "work_authorization": "citizen", "needs_sponsorship": False},
        {"country": "US", "work_authorization": "needs_sponsorship", "needs_sponsorship": True},
        *extra_rtw,
    ]
    return UserProfile(
        user_info=UserInfo(
            right_to_work={"countries": rtw, "citizenship": "GB", "sanctions_country_citizen": False},
            logistics={
                "notice_period": "1 month",
                "countries": [{"country": "GB", "relocate": True}, {"country": "IN", "relocate": False}],
            },
        ),
        preferences=UserPreferences(salary_by_country=[
            {"country": "GB", "min": 1, "max": 2, "currency": "GBP", "period": "year"},
            {"country": "IN", "min": 3, "max": 4, "currency": "INR", "period": "year"},
        ]),
    )


def _keys(blocks: dict, block: str) -> set[str]:
    return {i["key"] for i in blocks[block]}


def test_a_job_in_india_keeps_global_keys_and_india_only():
    blocks, _ = kit.compute_answers(_profile(), [], "IN", job_country_only=True)
    assert _keys(blocks, "right_to_work") == {
        "right_to_work.citizenship", "right_to_work.sanctions_country_citizen",
        "right_to_work.IN.work_authorization", "right_to_work.IN.needs_sponsorship",
    }
    assert _keys(blocks, "logistics") == {"logistics.notice_period", "logistics.IN.relocate"}
    assert _keys(blocks, "salary") == {"salary.IN"}
    unfiltered, _ = kit.compute_answers(_profile(), [], None, job_country_only=True)
    before = len(json.dumps(unfiltered["right_to_work"]))
    after = len(json.dumps(blocks["right_to_work"]))
    print(f"right_to_work chars {before} -> {after}")
    assert after < before


def test_an_unknown_country_keeps_everything():
    blocks, missing = kit.compute_answers(_profile(), [], None, job_country_only=True)
    rtw = _keys(blocks, "right_to_work")
    assert {"right_to_work.GB.work_authorization", "right_to_work.IN.work_authorization",
            "right_to_work.US.work_authorization", "right_to_work.citizenship"} <= rtw
    assert _keys(blocks, "salary") == {"salary.GB", "salary.IN"}
    assert {"logistics.GB.relocate", "logistics.IN.relocate"} <= _keys(blocks, "logistics")
    assert any(m["key"] == "job_country" for m in missing)


def test_a_country_with_no_own_record_gets_its_any_line_via_the_fallback_and_no_other_country():
    profile = _profile({"country": "ANY", "work_authorization": "visa", "needs_sponsorship": False})
    blocks, missing = kit.compute_answers(profile, [], "JP", job_country_only=True)
    items = {i["key"]: i for i in blocks["right_to_work"]}
    assert set(items) == {
        "right_to_work.citizenship", "right_to_work.sanctions_country_citizen",
        "right_to_work.JP.work_authorization", "right_to_work.JP.needs_sponsorship",
    }
    assert items["right_to_work.JP.work_authorization"]["applies_via"] == "ANY"
    assert items["right_to_work.JP.work_authorization"]["value"] == "visa"
    assert not [m for m in missing if m["key"].startswith("right_to_work.JP.")]


def test_a_region_line_answers_its_country_and_other_places_are_dropped():
    profile = _profile({"country": "EUROPE", "work_authorization": "needs_sponsorship", "needs_sponsorship": True})
    blocks, _ = kit.compute_answers(profile, [], "FR", job_country_only=True)
    items = {i["key"]: i for i in blocks["right_to_work"]}
    assert items["right_to_work.FR.needs_sponsorship"]["applies_via"] == "EUROPE"
    assert not [k for k in items if k.startswith(("right_to_work.GB.", "right_to_work.IN.", "right_to_work.EUROPE."))]
