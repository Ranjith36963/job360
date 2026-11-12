"""Checker faults the production dry run of 2026-11-12 found (a local copy of the owner's profile, real ads 811 882
435 449 454): true CV lines the checker stored weak.

1. A skills label made of closed generic words ("AI and LLMs:", "Machine learning:") was refused because a word of
   it also sits in an education or certification string ("AWS Certified AI Practitioner", "dissertation: machine
   learning for ..."). A generic label word is never an employer; a non-generic record word still is.
2. A quote of a profile link with its scheme ("https://github.com/x", as cv_work's compact profile sends
   ``cv_data.links``) read as not in a profile that says "github.com/x".
3. "AI-enabled" in a summary read as an unseen name although "AI" is in the bank and "enabled" is no claim.
Every case that must stay weak is pinned next to its fix.
"""
from __future__ import annotations

from src.services.applications import cv_checks, cv_review, evidence, review_rows
from src.services.profile.models import CVData, UserProfile

RAW = (
    "Ada Lovelace\ngithub.com/Ranjith36963 | linkedin.com/in/ada\nEXPERIENCE\n"
    "Built RAG agents in Python with LangGraph at Calnex.\n"
    "SKILLS\nLanguages: Python, SQL\n"
)
CV_DATA = {
    "name": "Ada Lovelace", "raw_text": RAW,
    "skills": ["Python", "SQL", "RAG", "LangGraph", "PyTorch", "TensorFlow"],
    "education": ["MSc Data Science, University of Hertfordshire, 2024, dissertation: machine learning for online "
                  "payment fraud detection"],
    "certifications": ["AWS Certified AI Practitioner, 2025"],
    "cv_positions": [{"company": "Calnex Solutions", "title": "AI Solutions Engineer", "dates": "Jun 2025 – Sep 2025",
                      "bullets": ["Built RAG agents in Python with LangGraph at Calnex."]}],
    "links": ["https://www.ada.dev/portfolio"],
}


def _profile() -> UserProfile:
    return UserProfile(cv_data=CVData(**CV_DATA))


def test_a_generic_label_word_is_never_blocked_by_a_record_word():
    book = cv_review.skill_book(_profile())
    assert {"ai", "machine", "learning"} <= book.entities  # the dry run's shape: the words ARE in the records
    for line in ("AI and LLMs: RAG, LangGraph", "Machine learning: PyTorch, TensorFlow"):
        assert cv_review.list_line_holds(line, book), line


def test_a_record_word_that_is_no_generic_word_still_blocks_a_label():
    book = cv_review.skill_book(_profile())
    for line in ("Calnex: Python, SQL", "Hertfordshire: Python, SQL", "Education: Python, SQL",
                 "Practitioner: Python, SQL", "Dissertation: Python, SQL", "Fraud Detection: Python, SQL",
                 "AI and LLMs: RAG, Kubernetes"):  # an item the profile lacks
        assert not cv_review.list_line_holds(line, book), line


def test_a_url_quote_matches_without_its_scheme_or_www():
    source, words = cv_review.quote_sources(_profile())
    for quote in ("https://github.com/Ranjith36963", "http://github.com/Ranjith36963", "github.com/Ranjith36963",
                  "https://www.github.com/Ranjith36963", "HTTPS://linkedin.com/in/ada"):
        assert cv_review.quote_in_profile(quote, source, words), quote
    for quote in ("https://github.com/someone-else", "https://gitlab.com/Ranjith36963", "https://github.com/ada"):
        assert not cv_review.quote_in_profile(quote, source, words), quote


def test_the_profile_links_are_a_quote_source():
    assert "https://www.ada.dev/portfolio" in cv_checks.profile_source_text(_profile())
    source, words = cv_review.quote_sources(_profile())
    for quote in ("https://www.ada.dev/portfolio", "ada.dev/portfolio", "http://ada.dev/portfolio"):
        assert cv_review.quote_in_profile(quote, source, words), quote
    assert not cv_review.quote_in_profile("https://www.ada.dev/blog", source, words)


def test_a_hyphenated_word_is_supported_when_its_claim_parts_are_in_the_bank():
    bank = "AI Solutions Engineer building RAG agents in Python"
    assert evidence.unsupported_tokens("Engineer who builds AI-enabled workflows.", bank) == []
    assert not review_rows.hard_facts("Engineer who builds AI-enabled workflows.", [bank], set(), frozenset(), set())
    assert evidence.unsupported_tokens("Builds RAG-based agents.", bank) == []


def test_a_hyphenated_word_with_an_unseen_claim_part_stays_a_claim():
    bank = "AI Solutions Engineer building RAG agents in Python"
    assert evidence.unsupported_tokens("Builds Kubernetes-native agents.", bank) == ["Kubernetes-native"]
    assert evidence.unsupported_tokens("Builds AI-Kubernetes agents.", bank) == ["AI-Kubernetes"]
    assert evidence.unsupported_tokens("Builds AI-Enabled agents.", bank) == ["AI-Enabled"]  # "Enabled": unseen name
    assert review_rows.hard_facts("Engineer who builds Kubernetes-native workflows.", [bank], set(), frozenset(), set())
    # a Titlecase word the bank never says stays a claim (zero wrong facts)
    assert evidence.unsupported_tokens("Engineer who builds Snowflake workflows.", bank) == ["Snowflake"]
