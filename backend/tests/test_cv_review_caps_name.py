"""A CV whose first line is the name in CAPITALS still has a headline and a summary (apps 484 483 482 489 490, 2026-11-15).

``content_lines`` leaves the name line out of the review (it is no claim), but ``cv_records.top_lines`` still walked
it, read an all-capitals line as a section heading that is no summary heading, and ended the top there: ``top`` was
``[]`` and ``headline`` null, so the headline was asked for a quote and stored ``claim_not_in_quote``. A name in title
case (app 479) never hit it. The name line is no heading; every other end of the top keeps working.
"""
from __future__ import annotations

import pytest

from src.services.applications import cv_records, cv_review, review_rows
from tests.test_cv_review_line_kinds import (
    CALNEX_B,
    CALNEX_H,
    HEADLINE,
    SUMMARY,
    TIDE_B,
    TIDE_H,
    _judge,
    _profile,
)

NAME = "ADA LOVELACE"


def _cv(name: str = NAME, headline: str = HEADLINE, summary: str = SUMMARY) -> str:
    return (f"{name}\n{headline}\n{summary}\nEXPERIENCE\n"
            f"{CALNEX_H}\n- {CALNEX_B}\n{TIDE_H}\n- {TIDE_B}\n")


@pytest.mark.parametrize("name", [NAME, "Ada Lovelace", "ADA  LOVELACE"])
def test_a_name_in_capitals_is_not_the_end_of_the_top(name):
    text = _cv(name)
    assert cv_review.content_lines(text, _profile())[:2] == [HEADLINE, SUMMARY]
    assert cv_records.top_lines(text, _profile()) == (frozenset({1, 2}), 1)
    assert 1 in review_rows.held_numbers(text, _profile())


def test_the_headline_of_a_capitals_name_cv_needs_no_quote():
    assert _judge(_cv(), job_title="AI Product Engineer") == {}


@pytest.mark.parametrize("headline", [
    "Forward Deployed Engineer | agentic systems and voice agents",
    "AI Engineer | RAG pipelines, LangGraph and multi-agent systems",
    "AI Engineer | Python, TypeScript and Next.js",
])
def test_a_bar_joined_headline_of_skill_words_is_the_headline(headline):
    assert cv_records.top_lines(_cv(headline=headline), _profile()) == (frozenset({1, 2}), 1)


def test_a_summary_naming_an_employer_stays_a_job_line():  # B4, behind a capitals name
    summary = "AI engineer who engineered a RAG pipeline at Calnex Solutions."
    top, headline = cv_records.top_lines(_cv(summary=summary), _profile())
    assert (top, headline) == (frozenset({1}), 1)


@pytest.mark.parametrize("name", [NAME, "Ada Lovelace"])
def test_a_real_record_header_still_ends_the_top(name):
    text = f"{name}\n{HEADLINE}\n{CALNEX_H}\n- {CALNEX_B}\n"
    assert cv_records.top_lines(text, _profile()) == (frozenset({1}), 1)


@pytest.mark.parametrize("heading", ["EXPERIENCE", "SKILLS", "EDUCATION"])
def test_a_capitals_heading_first_still_ends_the_top(heading):
    text = f"{NAME}\n{heading}\n{CALNEX_H}\n- {CALNEX_B}\n"
    assert cv_records.top_lines(text, _profile()) == (frozenset(), None)


def test_only_the_first_line_is_a_name_line():
    text = f"{HEADLINE}\nADA LOVELACE\n{SUMMARY}\nEXPERIENCE\n{CALNEX_H}\n- {CALNEX_B}\n"
    top, _ = cv_records.top_lines(text, _profile())
    assert 3 not in top and top <= frozenset({1})
