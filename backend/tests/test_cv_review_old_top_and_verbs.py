"""Two false-weak causes of the 00:34 CV run (production 2026-11-19, apps 555-577: 10 re-reviews, all stored weak).

1. A tailored CV that keeps the uploaded CV's OWN headline word for word ("AI Engineer | Agentic Systems", apps 570
   574 575 577) had ``top`` ``[]``: ``cv_review.keyed_records`` keeps that raw CV line as a header-shaped record
   (``raw_text:1``), and ``cv_records.top_lines`` ended the top at "a header line a profile record holds" before it
   began. The summary under it then needed quotes, and its ``[]`` was stored ``no_evidence``. The uploaded CV's own
   headline and summary sit above its first section heading: they are the CV's top, never a job's header.
2. ``review_rows.hard_facts`` asks a quote for "a profile name opening the line". ``names`` took every word the
   profile capitalises mid-line, so a LinkedIn bullet joined into one line ("Key Achievements: - Built ...") made
   the verb "Built" a name, and every bullet opening "Built" whose quote said "Architected" was stored
   ``claim_not_in_quote`` (the Calnex line of all 10 apps). A word the profile itself writes in lower case is a
   plain word, never a name; real names (skills, employers, projects) stay names.
"""
from __future__ import annotations

from typing import Any

from src.services.applications import cv_records, cv_review, review_rows
from src.services.profile.models import CVData, UserProfile
from tests.test_cv_review_line_kinds import CALNEX_H, CV_DATA, TIDE_B, TIDE_H

OWN_HEADLINE = "AI Engineer | Agentic Systems"
OWN_SUMMARY = ("AI engineer who designs, ships and evaluates agentic systems: multi-agent workflows, RAG pipelines "
               "and MCP integrations.")
ARCHITECTED = "Architected a RAG pipeline with LangChain and FastAPI that cut support search time by 40%."
BUILT_LINE = "Built a RAG pipeline with LangChain and FastAPI that cut support search time by 40%."


def _profile(raw_top: bool = True, joined: str = "", lower: bool = True) -> UserProfile:
    data: dict[str, Any] = {**CV_DATA}
    if raw_top:  # the uploaded CV: name, its own headline, its summary, then its sections
        data["raw_text"] = f"Ada Lovelace\n{OWN_HEADLINE}\nSUMMARY\n{OWN_SUMMARY}\n" + data["raw_text"].partition(
            "\n")[2]
    tide = "Developed data preprocessing pipelines in Python for model training." if not lower else TIDE_B
    data["cv_positions"] = [
        {**data["cv_positions"][0], "bullets": [ARCHITECTED]},
        {**data["cv_positions"][1], "bullets": [tide + (" Designed and built the tests." if lower else "")]},
    ]
    if joined:
        data["linkedin_raw_text"] = joined
    return UserProfile(cv_data=CVData(**data))


def _cv(headline: str = OWN_HEADLINE, bullet: str = ARCHITECTED) -> str:
    return (f"Ada Lovelace\n{headline}\nSUMMARY\n{OWN_SUMMARY}\nEXPERIENCE\n"
            f"{CALNEX_H}\n- {bullet}\n{TIDE_H}\n- {TIDE_B}\n")


def _rows(text: str, profile: UserProfile, quotes: dict[str, list[str]]) -> list[dict[str, Any]]:
    lines = cv_review.content_lines(text, profile)
    return [{"n": n, "verdict": "supported", "evidence": quotes.get(ln, [ln[2:]] if ln.startswith("- ") else [])}
            for n, ln in enumerate(lines, 1)]


def test_the_uploaded_cvs_own_headline_is_the_top_not_a_job_header():
    profile = _profile()
    text = _cv()
    assert cv_review.content_lines(text, profile)[:2] == [OWN_HEADLINE, OWN_SUMMARY]
    assert cv_records.top_lines(text, profile) == (frozenset({1, 2}), 1)
    where = review_rows.judge(text, _rows(text, profile, {}), profile, job_title="Senior Agentic Software Engineer")[0]
    assert "no_evidence" not in where and where == {}


def test_a_new_headline_keeps_its_top_too():  # the layout that always worked (apps 561 565): unchanged
    profile = _profile()
    assert cv_records.top_lines(_cv(headline="AI Delivery Consultant | Agentic Systems"), profile) == (
        frozenset({1, 2}), 1)


def test_a_job_header_still_ends_the_top():
    profile = _profile()
    text = f"Ada Lovelace\n{OWN_HEADLINE}\n{CALNEX_H}\n- {ARCHITECTED}\n"
    assert cv_records.top_lines(text, profile) == (frozenset({1}), 1)


def test_a_verb_the_profile_writes_in_lower_case_is_no_name():
    joined = "Key Achievements: - Built containerized systems - Designed RAG pipelines"
    profile = _profile(joined=joined)
    names = review_rows.profile_terms(profile)[0]
    assert "built" not in names
    text = _cv(bullet=BUILT_LINE)
    rows = _rows(text, profile, {f"- {BUILT_LINE}": [ARCHITECTED]})
    assert review_rows.judge(text, rows, profile, job_title="AI Engineer")[0] == {}


def test_a_real_name_opening_a_line_still_needs_its_quote():
    joined = "Key Achievements: - Built containerized systems at Snowflake"
    profile = _profile(joined=joined)
    assert "snowflake" in review_rows.profile_terms(profile)[0]
    bullet = "Snowflake pipeline with LangChain and FastAPI that cut support search time by 40%."
    text = _cv(bullet=bullet)
    rows = _rows(text, profile, {f"- {bullet}": [ARCHITECTED]})
    where = review_rows.judge(text, rows, profile, job_title="AI Engineer")[0]
    assert where.get("claim_not_in_quote") == [4]
