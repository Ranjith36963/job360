"""Apps 952 958 1016 (owner CV run, 2026-11-17): three ways a true CV was stored weak.

A. The profile's raw CV text keeps the CV's contact line as a line of its own, and that line is header-shaped
   ("United Kingdom | +44 ... | ada@example.com"), so ``cv_records.top_lines`` read it as a job header a record holds
   and ended the top there: ``top`` [] and ``headline`` null, the headline stored ``claim_not_in_quote``.
B. A skills line of ONE labelled item ("Skills: prompt engineering") was no list (``list_parts`` wants 2+ items), so
   a LinkedIn-only skill the profile holds was never held (``quote_too_thin``, ``claim_not_in_quote``).
C. The recipe never said the jd list has a cap, so the review record was refused with a 422.
"""
from __future__ import annotations

import pytest

from src.api.routes.recipes import load_recipe
from src.services.applications import cv_records, cv_review, review_rows
from src.services.applications.review_jd import JD_MAX_ITEMS
from src.services.profile.models import CVData, UserProfile
from tests.test_cv_review_line_kinds import CALNEX_B, CALNEX_H, CV_DATA, HEADLINE, SUMMARY, TIDE_B, TIDE_H

CONTACT = "United Kingdom | +44 20 7946 0000 | ada@example.com"
LINKS = "github.com/ada-lovelace | linkedin.com/in/ada-lovelace | ada-lovelace.example.com"


def _profile(**extra) -> UserProfile:
    data = {**CV_DATA, "location": "United Kingdom", "links": ["github.com/ada-lovelace"], **extra}
    data["raw_text"] = f"Ada Lovelace\n{CONTACT}\n{LINKS}\n{data['raw_text']}"
    return UserProfile(cv_data=CVData(**data))


def _cv(name: str = "Ada Lovelace") -> str:
    return (f"{name}\n{CONTACT}\n{LINKS}\n\n{HEADLINE}\n\nSUMMARY\n{SUMMARY}\n\nEXPERIENCE\n"
            f"{CALNEX_H}\n- {CALNEX_B}\n{TIDE_H}\n- {TIDE_B}\n")


@pytest.mark.parametrize("name", ["Ada Lovelace", "ADA LOVELACE"])
def test_a_contact_line_the_raw_text_keeps_is_not_the_end_of_the_top(name):
    text, profile = _cv(name), _profile()
    assert cv_review.content_lines(text, profile)[:2] == [HEADLINE, SUMMARY]
    assert cv_records.top_lines(text, profile) == (frozenset({1, 2}), 1)
    assert 1 in review_rows.held_numbers(text, profile)


def test_a_real_record_header_still_ends_the_top_behind_a_contact_line():
    text = f"Ada Lovelace\n{CONTACT}\n{HEADLINE}\n{CALNEX_H}\n- {CALNEX_B}\n"
    assert cv_records.top_lines(text, _profile()) == (frozenset({1}), 1)


def test_a_contact_line_is_skipped_only_when_it_is_one():
    # a header line the profile holds that is NO contact line still ends the top
    text = f"Ada Lovelace\n{CONTACT}\n{CALNEX_H}\n{HEADLINE}\n"
    assert cv_records.top_lines(text, _profile()) == (frozenset(), None)


SKILLS = ["Python", "LangChain"]
LINKEDIN = ["Prompt engineering", "Vector databases"]


def _skills_profile() -> UserProfile:
    return UserProfile(cv_data=CVData(name="Ada Lovelace", skills=SKILLS, linkedin_skills=LINKEDIN))


@pytest.mark.parametrize("line", [
    "Skills: prompt engineering", "Skills: Prompt engineering", "Skills: vector databases",
    "Skills: prompt engineering, vector databases", "Skills: Python",
])
def test_b_linkedin_skills_on_a_skills_line_are_held(line):
    book = cv_review.skill_book(_skills_profile())
    assert cv_review.list_line_holds(line, book)


@pytest.mark.parametrize("line", [
    "Skills: Haskell", "Skills: prompt engineering, Haskell", "Skills: Haskell, Erlang",
    "Education: prompt engineering",  # a label that is no skills group
    "prompt engineering",  # a bare item is no skills line
])
def test_b_a_skill_the_profile_lacks_is_never_held(line):
    assert not cv_review.list_line_holds(line, cv_review.skill_book(_skills_profile()))


def test_b_a_one_item_skills_line_under_a_job_needs_that_jobs_own_text():
    profile = _skills_profile()
    own = cv_review.sources_from(["Built a RAG pipeline with LangChain."])
    assert not review_rows._list_in_record("Skills: prompt engineering", own)
    assert review_rows._list_in_record("Skills: LangChain", own)
    assert review_rows._list_in_record("Skills: prompt engineering", None)
    assert profile  # the profile is the book's only source


def test_c_the_recipe_states_the_jd_cap():
    text = " ".join(load_recipe("cv").text.split())
    assert f"at most {JD_MAX_ITEMS} items: the ad's must-haves first" in text
