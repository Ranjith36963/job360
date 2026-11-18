"""Production 2026-11-18 (apps 1056, 1057 line 25; 1019 jd evidence): a skills line whose every item is a saved skill
was stored weak. The profile keeps ``skills`` / ``linkedin_skills`` one entry per field value, so a reviewer quote
listing them ("AWS, Bedrock, SageMaker") is no ONE field or line (``quote_not_in_profile`` /
``jd_evidence_not_in_profile``), and "Cloud:" named the profile's "Cloud & Ops" group, so the label needed a quote too
(``claim_not_in_quote``). A quote whose comma / semicolon items are each one whole saved entry is now a profile quote,
and a generic label word the profile's own group uses is lent to a line whose items are all saved entries when one of
them is from that group. Every item still needs a quote; an item no entry holds whole still fails."""
from __future__ import annotations

from typing import Any

from src.services.applications import cv_review, review_jd, review_rows
from src.services.profile.models import CVData, UserProfile

RAW = (
    "Ada Lovelace\nEXPERIENCE\nBuilt RAG agents in Python with LangGraph at Calnex.\nSKILLS\n"
    "ML & Engineering: PyTorch, Python, FastAPI, PostgreSQL, Redis\n"
    "Cloud & Ops: AWS (Bedrock, SageMaker, S3), Docker, Railway, GitHub Actions, LangSmith, Grafana\n"
)
SKILLS = ["PyTorch", "Python", "FastAPI", "PostgreSQL", "Redis", "AWS", "Bedrock", "SageMaker", "S3", "Docker",
          "Railway", "GitHub Actions", "LangSmith", "Grafana"]
PROFILE = UserProfile(cv_data=CVData(
    name="Ada Lovelace", raw_text=RAW, skills=SKILLS, linkedin_skills=["AWS Bedrock", "Chroma DB", "Linux"],
    cv_positions=[{"company": "Calnex Solutions", "title": "AI Solutions Engineer", "dates": "Jun 2025 – Sep 2025",
                   "bullets": ["Built RAG agents in Python with LangGraph at Calnex."]}],
))
LINE = "Cloud: AWS, Bedrock, SageMaker, S3, Docker, Railway, GitHub Actions, PostgreSQL, Redis"
ITEMS = "AWS, Bedrock, SageMaker, S3, Docker, Railway, GitHub Actions, PostgreSQL, Redis"
BULLET = "Built RAG agents in Python with LangGraph at Calnex."


def _judge(line: str, quotes: list[str]) -> dict[str, list[Any]]:
    text = f"Ada Lovelace\nEXPERIENCE\n- {BULLET}\nSKILLS\n{line}\n"
    content = cv_review.content_lines(text, PROFILE)
    rows = [{"n": i, "verdict": "supported", "evidence": [BULLET]} for i in range(1, len(content) + 1)]
    n = next(i for i, ln in enumerate(content, 1) if ln.strip() == line)
    rows[n - 1]["evidence"] = quotes
    where, _, _ = review_rows.judge(text, rows, PROFILE)
    return {code: ns for code, ns in where.items() if n in ns}


def test_the_production_cloud_line_is_not_held_and_its_skill_quotes_are_profile_quotes():
    assert not review_rows.held_numbers(f"Ada Lovelace\nSKILLS\n{LINE}\n", PROFILE)  # the group rule stands
    assert _judge(LINE, [ITEMS]) == {}  # app 1056 reviewer 1: one quote of the items
    assert _judge(LINE, ["AWS, Bedrock, SageMaker", "S3, Docker, Railway", "GitHub Actions; PostgreSQL; Redis"]) == {}
    assert _judge("Skills: AWS, Docker, Redis", ["AWS, Docker, Redis"]) == {}


def test_an_item_no_entry_holds_whole_still_fails():
    for line, quote in ((f"{LINE}, Kubernetes", f"{ITEMS}, Kubernetes"),  # a skill the profile lacks
                        ("Cloud: AWS, Docker, Claude code", "AWS, Docker, Claude code"),
                        ("Cloud: AWS, Docker, Postgres", "AWS, Docker, Postgres"),  # a substring of an entry
                        ("Cloud: AWS, Docker, Redis", "AWS, Docker, Red")):  # the quote cuts an entry
        assert "quote_not_in_profile" in _judge(line, [quote]), line
    # every item still needs a quote: the items quote shows AWS and Docker, never Redis
    assert list(_judge("Cloud: AWS, Docker, Redis", ["AWS, Docker, Railway"])) == ["claim_not_in_quote"]
    # a label is lent only by a group one of the items comes from ("Agents: Python, SQL" stays weak)
    assert "claim_not_in_quote" in _judge("Cloud: Python, FastAPI, PyTorch", ["Python, FastAPI, PyTorch"])


def test_jd_evidence_listing_saved_skills_is_a_profile_quote():
    ad = "Must have: AWS Bedrock and Docker. Nice: Kubernetes."
    lines = ["Cloud: AWS Bedrock, Docker"]

    def where(evidence: list[str]) -> dict[str, list[Any]]:
        jd = [{"term": "AWS Bedrock", "must": True, "have": True, "evidence": evidence, "n": [1]}]
        return review_jd.judge(jd, ad, lines, lines[0], PROFILE, 0)[0]

    assert where(["AWS Bedrock, Chroma DB, Linux"]) == {}  # app 1019: a segment of linkedin_skills
    assert where(["Docker; AWS Bedrock"]) == {}
    assert where(["AWS Bedrock, Kubernetes"]) == {"jd_evidence_not_in_profile": [1], "jd_coverage_low": []}
    assert where(["AWS Bedrock Docker"]) == {"jd_evidence_not_in_profile": [1], "jd_coverage_low": []}
