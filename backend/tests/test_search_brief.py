"""SEARCH BRIEF (owner decision 2026-10-12): the hunt rules are written once for
every user and name no country; WHERE to search comes only from each user's own
``user_info.right_to_work`` answers.

The live miss it guards: a hunt for "remote worldwide" came back all UK, because
the free-text location was not a place and the search fell back to where the
user lives. Four different users run through the SAME brief builder here - a UK
citizen searching only the UK, an Indian citizen searching only India, a person
who needs a visa for Germany, and a worldwide-remote seeker - and each must get
their own places and nothing else. Pure; no database.
"""
from __future__ import annotations

from typing import Any

from src.services.profile.models import UserProfile
from src.services.profile.search_brief import (
    RIGHT_TO_WORK_PATH,
    WHERE_TO_SEARCH_ASK,
    build_search_brief,
)


def _rtw(*records: dict[str, Any]) -> dict[str, Any]:
    return {"countries": list(records)}


def _places(brief: dict[str, Any]) -> list[str]:
    return [row["country"] for row in brief["rows"]]


def test_uk_only_user_gets_only_the_uk_and_no_visa_rule():
    brief = build_search_brief(_rtw(
        {"country": "GB", "work_authorization": "citizen", "remote_ok": True, "onsite": "yes"},
    ))
    assert _places(brief) == ["GB"] and brief["gaps"] == []
    assert brief["work_from"] == ["GB"]
    lines = " ".join(brief["rows"][0]["counts_if"])
    assert "work from GB or anywhere" in lines
    assert "sponsor" not in lines  # a citizen never gets a visa rule


def test_india_only_user_never_gets_the_uk():
    brief = build_search_brief(_rtw(
        {"country": "IN", "work_authorization": "citizen", "remote_ok": True, "onsite": "yes"},
    ))
    assert _places(brief) == ["IN"] and brief["gaps"] == []
    assert "GB" not in str(brief)


def test_germany_user_who_needs_a_visa_gets_on_site_only_when_the_ad_sponsors():
    brief = build_search_brief(_rtw(
        {"country": "DE", "needs_sponsorship": True, "remote_ok": False, "onsite": "only_if_sponsored"},
    ))
    assert _places(brief) == ["DE"] and brief["gaps"] == []
    assert brief["rows"][0]["counts_if"] == ["on-site or hybrid in DE, only when the ad says it sponsors a visa"]
    assert brief["work_from"] == []


def test_worldwide_remote_seeker_gets_every_named_place_and_any_other_country_last():
    brief = build_search_brief(_rtw(
        {"country": "ANY", "needs_sponsorship": True, "remote_ok": True, "onsite": "only_if_sponsored"},
        {"country": "IN", "work_authorization": "citizen", "remote_ok": True, "onsite": "yes"},
        {"country": "GB", "work_authorization": "needs_sponsorship", "remote_ok": True, "onsite": "only_if_sponsored"},
    ))
    assert _places(brief) == ["IN", "GB", "ANY"] and brief["gaps"] == []
    assert brief["work_from"] == ["IN"]
    anywhere = brief["rows"][-1]["counts_if"]
    assert anywhere[0] == "fully remote with an employer in any other country; the ad must let you work from IN or anywhere"
    assert anywhere[1] == "on-site or hybrid in any other country, only when the ad says it sponsors a visa"


def test_the_live_profile_of_2026_10_12_has_no_place_and_asks_once():
    """The owner's real answers that day: visa facts per country, but no
    remote_ok / onsite anywhere. The brief must not fall back to where he
    lives - it asks the one question instead."""
    brief = build_search_brief(_rtw(
        {"country": "GB", "work_authorization": "needs_sponsorship", "needs_sponsorship": True},
        {"country": "IN", "work_authorization": "citizen", "needs_sponsorship": False},
    ))
    assert brief["rows"] == []
    assert brief["gaps"] == [{"path": RIGHT_TO_WORK_PATH, "country": None, "ask": WHERE_TO_SEARCH_ASK}]


def test_a_half_answer_is_asked_never_guessed():
    brief = build_search_brief(_rtw(
        {"country": "FR", "remote_ok": True},
        {"country": "NL", "onsite": "yes"},
        {"country": "GB", "work_authorization": "citizen", "onsite": "no"},
    ))
    asks = [g["ask"] for g in brief["gaps"]]
    assert asks == [
        "On-site in FR: yes, only if they sponsor your visa, or no?",
        "Remote jobs with an employer in NL: OK or not?",
        "Do you need visa sponsorship to work in NL?",
        # on-site "no" with remote unanswered is not a refusal of remote work
        "Remote jobs with an employer in GB: OK or not?",
    ]
    assert _places(brief) == ["FR", "NL"]


def test_a_country_they_will_not_work_in_is_never_searched():
    brief = build_search_brief(_rtw(
        {"country": "GB", "work_authorization": "citizen", "remote_ok": False, "onsite": "no"},
        {"country": "IE", "work_authorization": "citizen", "remote_ok": True, "onsite": "no"},
    ))
    assert _places(brief) == ["IE"]


def test_empty_or_malformed_answers_read_as_nothing_answered():
    for value in ({}, None, {"countries": "GB"}, {"countries": [None, {"remote_ok": True}]}):
        brief = build_search_brief(value)
        assert brief["rows"] == [] and len(brief["gaps"]) == 1


def test_setup_targets_round_needs_a_place_to_search():
    from src.services.profile.assistant_settings import setup_round_missing

    profile = UserProfile()
    profile.preferences.target_job_titles = ["AI Engineer"]
    profile.assistant_settings.apply_min_score = 70
    assert setup_round_missing("targets", profile) == [RIGHT_TO_WORK_PATH]
    profile.user_info.right_to_work = _rtw({"country": "ANY", "remote_ok": True, "onsite": "no"})
    assert setup_round_missing("targets", profile) == []


def test_hunt_recipe_reads_the_brief_and_names_no_country():
    from src.api.routes.recipes import load_recipe

    text = load_recipe("hunt").text
    flat = " ".join(text.split()).lower()
    for words in ("search_brief", "counts_if", "coverage table", "word for word", "never your own summary",
                  "never scrape or browser-search linkedin", "terms forbid automated access", "7 in 10"):
        assert words in flat, words
    # the old rule that turned "remote worldwide" into "remote from where you live"
    assert "work from where they are" not in flat
    # universal: the recipe itself never names a country (an ISO code in an example is fine)
    for country in ("United Kingdom", "Germany", "India", " UK ", "London"):
        assert country not in text, country


# --- residence vs work_from (2026-10-13): a remote job in IN means working from IN ---
_Q = "You live in GB. Would you work from IN for a remote job there?"
_IN_CITIZEN_REMOTE = {"country": "IN", "work_authorization": "citizen", "remote_ok": True, "onsite": "no"}


def test_lives_in_gb_indian_citizen_remote_row_asks_would_you_work_from_in():
    brief = build_search_brief(_rtw(_IN_CITIZEN_REMOTE), "GB", [])
    asks = [g for g in brief["gaps"] if g["path"] == "user_info.answers"]
    assert len(asks) == 1 and asks[0]["country"] == "IN"
    assert asks[0]["ask"].startswith("You live in GB. Would you work from IN")
    assert brief["residence_country"] == "GB" and brief["work_from"] == ["IN"]


def test_approved_yes_answer_means_no_gap_and_in_stays():
    ans = [{"question": _Q, "answer": "Yes", "approved": True}]
    brief = build_search_brief(_rtw(_IN_CITIZEN_REMOTE), "GB", ans)
    assert brief["gaps"] == [] and brief["work_from"] == ["IN"]


def test_unapproved_answer_is_still_a_gap():
    ans = [{"question": _Q, "answer": "No", "approved": False}]
    assert build_search_brief(_rtw(_IN_CITIZEN_REMOTE), "GB", ans)["gaps"]


def test_approved_no_drops_in_from_work_from_and_row_says_anywhere_only():
    ans = [{"question": _Q, "answer": "No", "approved": True}]
    brief = build_search_brief(_rtw(_IN_CITIZEN_REMOTE), "GB", ans)
    assert brief["gaps"] == [] and brief["work_from"] == []
    line = brief["rows"][0]["counts_if"][0]
    assert line.endswith("work from anywhere") and "IN or" not in line


def test_lives_in_the_same_country_asks_nothing():
    brief = build_search_brief(_rtw(_IN_CITIZEN_REMOTE), "IN", [])
    assert brief["gaps"] == [] and brief["work_from"] == ["IN"]


def test_no_residence_means_no_question():
    assert build_search_brief(_rtw(_IN_CITIZEN_REMOTE))["gaps"] == []


def test_yes_variants_keep_the_country():
    for word in ("Yes", "yes ", "Y", "true"):
        ans = [{"question": _Q, "answer": word, "approved": True}]
        brief = build_search_brief(_rtw(_IN_CITIZEN_REMOTE), "GB", ans)
        assert brief["gaps"] == [] and brief["work_from"] == ["IN"], word


def test_unclear_approved_answer_stays_a_gap_with_same_wording():
    for word in ("maybe", "only if paid in GBP", "Not sure"):
        ans = [{"question": _Q, "answer": word, "approved": True}]
        brief = build_search_brief(_rtw(_IN_CITIZEN_REMOTE), "GB", ans)
        assert [g["ask"] for g in brief["gaps"]] == [_Q] and brief["work_from"] == ["IN"], word


def test_no_variants_drop_the_country():
    for word in ("No", "n", "false"):
        ans = [{"question": _Q, "answer": word, "approved": True}]
        assert build_search_brief(_rtw(_IN_CITIZEN_REMOTE), "GB", ans)["work_from"] == [], word


def test_only_remote_ok_rows_get_the_residence_question():
    in_onsite = {"country": "IN", "work_authorization": "citizen", "remote_ok": False, "onsite": "yes"}
    us_remote = {"country": "US", "work_authorization": "citizen", "remote_ok": True, "onsite": "no"}
    brief = build_search_brief(_rtw(in_onsite, us_remote), "GB", [])
    assert [g["country"] for g in brief["gaps"] if g["path"] == "user_info.answers"] == ["US"]


def test_no_remote_row_means_no_residence_question():
    in_onsite = {"country": "IN", "work_authorization": "citizen", "remote_ok": False, "onsite": "yes"}
    assert build_search_brief(_rtw(in_onsite), "GB", [])["gaps"] == []
