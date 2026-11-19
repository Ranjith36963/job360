"""App 1105 (2026-11-19, CV run 03:33): "Technologies: FastAPI, PostgreSQL, Redis, AWS, SageMaker, S3, Docker, GitHub
Actions" - every item a saved profile skill - was never held and stored weak with no tries left. The owner's LinkedIn
export has per-job "Technologies: Docker • AWS Bedrock • ..." lines, so "technologies" became one of the profile's
own group labels, and a label made only of group words was read as claiming THAT group: FastAPI and PostgreSQL are
not in it. LinkedIn's per-job tool list is no skills group the user labelled: it adds items, never a group. The
user's own groups still bind their words. A label word that can carry a fact (an employer, title, degree, unknown word) still blocks, and an
item the profile lacks still blocks."""
from __future__ import annotations

from src.services.applications import cv_review, review_rows
from src.services.profile.models import CVData, UserProfile

SKILLS = ["LangGraph", "MCP", "RAG", "PyTorch", "TensorFlow", "scikit-learn", "Python", "FastAPI", "Next.js",
          "TypeScript", "PostgreSQL", "Redis", "AWS", "Bedrock", "SageMaker", "S3", "Docker", "Railway",
          "GitHub Actions", "Linux"]
LINKEDIN = "Experience\nCalnex Solutions\nTechnologies: Docker • AWS Bedrock • AWS S3 • Redis • Linux\n"
RAW = ("Ada Lovelace\nEXPERIENCE\nCalnex Solutions | AI Solutions Engineer\n- Built RAG agents in Python.\nSKILLS\n"
       "Agents & LLMs: LangGraph, MCP, RAG\n"
       "ML & Engineering: PyTorch, TensorFlow, scikit-learn, Python, FastAPI, Next.js, TypeScript, PostgreSQL, Redis\n"
       "Cloud & Ops: AWS (Bedrock, SageMaker, S3), Docker, Railway, GitHub Actions\n")
LINE = "Technologies: FastAPI, PostgreSQL, Redis, AWS, SageMaker, S3, Docker, GitHub Actions"


def _profile() -> UserProfile:
    return UserProfile(cv_data=CVData(name="Ada Lovelace", raw_text=RAW, linkedin_raw_text=LINKEDIN, skills=SKILLS,
                                      cv_positions=[{"company": "Calnex Solutions", "title": "AI Solutions Engineer",
                                                     "dates": "Jun 2025 - Sep 2025",
                                                     "bullets": ["Built RAG agents in Python."]}]))


def test_linkedins_per_job_technologies_list_is_no_skills_group():
    book = cv_review.skill_book(_profile())
    assert not any(k == frozenset({"technologies"}) for k, _ in book.groups)  # the 1105 shape: no group from LinkedIn
    assert {"linux", "aws bedrock"} <= book.items  # its items still count as profile items
    assert cv_review.list_line_holds(LINE, book)
    # the user's OWN groups still bind a label made of their words ("Agents: Python, SQL" stays weak, Checker 11-10)
    assert not cv_review.list_line_holds("Cloud: Python, FastAPI", book)


def test_the_1105_line_is_held_and_needs_no_quote():
    text = f"Ada Lovelace\nEXPERIENCE\n- Built RAG agents in Python.\nSKILLS\n{LINE}\n"
    content = cv_review.content_lines(text, _profile())
    n = next(i for i, ln in enumerate(content, 1) if LINE in ln)
    rows = [{"n": i, "verdict": "supported", "evidence": []} for i in range(1, len(content) + 1)]
    where = review_rows.judge(text, rows, _profile())[0]
    assert not any(n in v for v in where.values()), where


def test_a_fact_label_or_a_missing_item_still_blocks():
    book = cv_review.skill_book(_profile())
    for line in ("Calnex: Docker, Linux",  # an employer
                 "Education: Docker, Linux", "Experience: Docker, Linux",  # never a skills group
                 "Technologies: FastAPI, Kubernetes",  # an item the profile lacks
                 "Practice: Docker, Linux"):  # an unknown word may carry a fact: it still needs the reviewer's quote
        assert not cv_review.list_line_holds(line, book), line
