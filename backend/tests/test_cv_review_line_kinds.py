"""Rules 7 (owner 2026-11-12, apps 435 449 454 455 459 460 461 462): "where to tighten, where to loosen".

The CV's TOP lines (headline and summary: content lines before the first job / project header) pitch the WHOLE
career at the job, so they need no evidence quotes; every hard fact of one must still be somewhere in the profile
(``claim_not_in_quote`` otherwise). The headline may use the ad's job-title words, never a seniority word or number
the profile lacks. Experience / project bullets, education and skills keep the per-record rules. Skills labels such
as "Machine learning:" are generic words.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

import pytest

from src.services.applications import cv_review, review_rows
from src.services.profile.models import CVData, UserProfile
from tests._cv_review import ATS_LINES, ATS_RAW, ats_cv, blank_ad

PROFILE_SUMMARY = ("AI engineer who designs, ships and evaluates agentic systems: multi-agent workflows, RAG pipelines "
                   "and MCP integrations.")
CALNEX_B = "Built a RAG pipeline with LangChain and FastAPI that cut support search time by 40%."
TIDE_B = "Developed data preprocessing pipelines in Python for model training."
CV_DATA: dict[str, Any] = {
    "name": "Ada Lovelace", "raw_text": "Ada Lovelace\nEXPERIENCE\n" + ATS_RAW, "summary": PROFILE_SUMMARY,
    "education": ["MSc Artificial Intelligence and Robotics, University of Hertfordshire, 2024"],
    "skills": ["LangGraph", "LangChain", "Python", "FastAPI", "PyTorch", "TensorFlow", "scikit-learn"],
    "cv_positions": [
        {"company": "Calnex Solutions", "title": "AI Solutions Engineer", "dates": "Jun 2025 – Sep 2025",
         "location": "Stevenage", "bullets": [CALNEX_B]},
        {"company": "Tidewater", "title": "ML Engineer Intern", "dates": "Oct 2024 – Jan 2025",
         "location": "Remote", "bullets": [TIDE_B]},
    ],
}
HEADLINE = "AI Product Engineer | Agentic Systems"
SUMMARY = ("AI engineer with an MSc in Artificial Intelligence and Robotics who builds AI agents, multi-agent workflows "
           "and RAG pipelines with LangGraph, LangChain, Python and FastAPI.")
CALNEX_H = "Calnex Solutions | AI Solutions Engineer | Jun 2025 – Sep 2025"
TIDE_H = "Tidewater | ML Engineer Intern | Oct 2024 – Jan 2025"


def _profile() -> UserProfile:
    return UserProfile(cv_data=CVData(**CV_DATA))


def _cv(headline: str = HEADLINE, summary: str = SUMMARY, bullet: str = CALNEX_B) -> str:
    return f"Ada Lovelace\n{headline}\n{summary}\nEXPERIENCE\n{CALNEX_H}\n- {bullet}\n{TIDE_H}\n- {TIDE_B}\n"


def _rows(text: str, evidence: dict[str, list[str]], verdict: str = "supported") -> list[dict[str, Any]]:
    """One row per review line: a bullet quotes itself, every other line takes ``evidence`` (else none)."""
    lines = cv_review.content_lines(text, _profile())
    return [{"n": n, "verdict": verdict, "evidence": evidence.get(ln, [ln[2:]] if ln.startswith("- ") else [])}
            for n, ln in enumerate(lines, 1)]


def _judge(text: str, evidence: Optional[dict[str, list[str]]] = None, job_title: str = "") -> dict[str, list[Any]]:
    return review_rows.judge(text, _rows(text, evidence or {}), _profile(), job_title=job_title)[0]


def test_a_true_summary_drawn_from_four_records_needs_no_quote():
    text = _cv()
    assert cv_review.content_lines(text, _profile())[:2] == [HEADLINE, SUMMARY]
    assert _judge(text, job_title="AI Product Engineer") == {}
    # a quote the reviewer gives anyway is fine when it is in the profile, however short
    assert _judge(text, {SUMMARY: ["RAG pipelines"]}, job_title="AI Product Engineer") == {}


def test_the_headline_may_echo_the_ads_title_only():
    assert _judge(_cv(), job_title="") == {"claim_not_in_quote": [1]}  # "Product": neither profile nor ad
    assert _judge(_cv(), job_title="AI Product Engineer") == {}
    senior = "Senior AI Product Engineer | Agentic Systems"
    assert _judge(_cv(headline=senior), job_title="Senior AI Product Engineer") == {"claim_not_in_quote": [1]}
    numbered = "AI Product Engineer III | Agentic Systems"
    assert _judge(_cv(headline=numbered), job_title="AI Product Engineer III") == {"claim_not_in_quote": [1]}
    # the ad's title words are the headline's only: the summary may not use them
    summary = "AI engineer who builds Product agents with LangGraph and Python."
    assert _judge(_cv(summary=summary), job_title="AI Product Engineer") == {"claim_not_in_quote": [2]}


@pytest.mark.parametrize("summary", [
    "AI engineer who cut support search time by 60% with RAG pipelines in LangChain and FastAPI.",  # a false number
    "AI engineer who builds RAG pipelines with LangGraph, Python and Kubernetes.",  # a tool not in the profile
    "Senior AI engineer who builds RAG pipelines with LangGraph and Python.",  # a seniority word not in the profile
    "AI engineer who led RAG pipeline work with LangGraph and Python.",  # a role word not in the profile
    "AI engineer with an MSc from Oxford who builds RAG pipelines with LangGraph.",  # a school not in the profile
])
def test_a_summary_fact_found_nowhere_in_the_profile_is_weak(summary):
    assert _judge(_cv(summary=summary), job_title="AI Product Engineer") == {"claim_not_in_quote": [2]}, summary


def test_a_top_lines_quote_must_still_be_in_the_profile_and_its_verdict_counts():
    assert _judge(_cv(), {SUMMARY: ["builds agents for Kubernetes"]}, "AI Product Engineer") == {
        "quote_not_in_profile": [2]}
    text = _cv()
    rows = _rows(text, {})
    rows[1]["verdict"] = "unsupported"
    assert review_rows.judge(text, rows, _profile(), job_title="AI Product Engineer")[0] == {"line_unsupported": [2]}


def test_bullets_keep_the_per_record_rules():
    # a true fact moved to another job: Tidewater's bullet under Calnex
    got = _judge(_cv(bullet=TIDE_B), {f"- {TIDE_B}": [TIDE_B]}, "AI Product Engineer")
    assert got == {"evidence_wrong_record": [4]}, got
    # a bullet still needs a quote, and a thin one
    assert _judge(_cv(), {f"- {CALNEX_B}": []}, "AI Product Engineer") == {"no_evidence": [4]}
    assert _judge(_cv(), {f"- {CALNEX_B}": ["RAG pipeline"]}, "AI Product Engineer") == {
        "quote_too_thin": [4], "claim_not_in_quote": [4]}
    # a bullet's fact must be in ITS quote, even when the profile says it elsewhere
    line = "Built a RAG pipeline with LangChain and FastAPI for an MSc project."
    assert _judge(_cv(bullet=line), {f"- {line}": [CALNEX_B]}, "AI Product Engineer") == {"claim_not_in_quote": [4]}


def test_lines_after_the_first_job_or_in_other_sections_are_no_top_lines():
    text = f"Ada Lovelace\nEDUCATION\n{SUMMARY}\n"  # an education section is never the CV's top
    assert "no_evidence" in _judge(text, job_title="AI Product Engineer")
    text = f"Ada Lovelace\nEXPERIENCE\n{CALNEX_H}\n{SUMMARY}\n"
    assert "no_evidence" in _judge(text, job_title="AI Product Engineer")


def test_held_numbers_hold_the_top_lines_and_judge_agrees():
    text = _cv()
    held = review_rows.held_numbers(text, _profile())
    assert {1, 2} <= set(held) and 4 not in held, held
    lines = cv_review.content_lines(text, _profile())
    rows = [{"n": n, "verdict": "supported", "evidence": [] if n in held else [ln.lstrip("- ")]}
            for n, ln in enumerate(lines, 1)]
    assert review_rows.judge(text, rows, _profile(), job_title="AI Product Engineer")[0] == {}


def test_generic_skills_labels_hold_and_an_employer_label_does_not():
    book = cv_review.skill_book(_profile())
    for line in ("Machine learning: PyTorch, TensorFlow, scikit-learn", "Agentic AI: LangGraph, LangChain",
                 "AI agents and LLMs: LangGraph, LangChain", "Python and APIs: Python, FastAPI"):
        assert cv_review.list_line_holds(line, book), line
    assert not cv_review.list_line_holds("Calnex: Python, FastAPI", book)
    assert not cv_review.list_line_holds("Machine learning: PyTorch, Kubernetes", book)


@pytest.mark.asyncio
async def test_through_the_door_the_ads_title_frees_the_headline(authenticated_async_context, fixture_user_id):
    from src.services.profile.storage import save_profile

    text = ats_cv(_cv())  # rules 6: a contact line and the ATS tail (its lines quote themselves)
    save_profile(_profile(), fixture_user_id, source_action="cv_upload")
    async with authenticated_async_context() as client:
        ad = {"title": "AI Product Engineer", "company": "Northwind", "location": "London", "country": "GB",
              "remote": False, "apply_url": "https://northwind.example/careers/435",
              "description": "Build agents. LangGraph, Python."}
        brought = await client.post("/api/jobs/bring", json=ad)
        assert brought.status_code == 200, brought.text
        app_id = int(brought.json()["application_id"])
        await blank_ad(app_id)  # rules 6: no ad text = jd_no_ad
        saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": text})
        assert saved.status_code == 201, saved.text
        review = (await client.get(f"/api/applications/{app_id}", params={"view": "review"})).json()["review"]
        assert {1, 2} <= set(review["held"]), review["held"]
        rows = [{"n": n, "verdict": "supported",
                 "evidence": [] if n in review["held"] else [ln[2:] if ln.startswith("- ") else ln]}
                for n, ln in enumerate(review["lines"], 1)]
        assert set(ATS_LINES) <= set(review["lines"])
        payload = {"artifact_id": saved.json()["artifact_id"], "confidence": "strong", "lines": rows,
                   "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "reviewer": "fresh helper"}
        resp = await client.post(f"/api/applications/{app_id}/events",
                                 json={"event_type": "cv_reviewed", "payload": payload})
        assert resp.status_code == 201, resp.text
        events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
    got = next(e["payload"] for e in events if e["id"] == resp.json()["event_id"])
    assert (got["confidence"], got["downgraded"], got["rules"]) == ("strong", [], cv_review.RULES), got
    assert cv_review.RULES == 7


# Checker round 1 (2026-11-12): the TOP is the headline and summary only; a bullet is never one.
LIE = "Cut model training time by 40%."  # Tidewater's "model training" + Calnex's "40%": a cross-record lie


@pytest.mark.parametrize("before", [
    "",  # straight after the summary
    "Highlights\n",  # a heading no rule knows
    "AI Solutions Engineer\n",  # a title-only header line
    "Work History\n",
    "Selected Projects\n",
])
@pytest.mark.parametrize("mark", ["- ", "· ", "➤ ", "→ ", "> ", "o ", "+ ", "1. ", "2) "])
def test_a_bullet_before_any_known_header_is_no_top_line(before, mark):
    text = f"Ada Lovelace\n{HEADLINE}\n{SUMMARY}\n{before}{mark}{LIE}\nEXPERIENCE\n{CALNEX_H}\n- {CALNEX_B}\n"
    n = cv_review.content_lines(text, _profile()).index(f"{mark}{LIE}") + 1
    got = _judge(text, {f"{mark}{LIE}": []}, "AI Product Engineer")
    assert n in got.get("no_evidence", []), got
    assert n not in review_rows.held_numbers(text, _profile())


@pytest.mark.parametrize("headline", ["AI Engineer, Agentic Systems", "AI Engineer (Agentic Systems)"])
def test_a_headline_with_a_comma_or_brackets_is_the_headline(headline):
    from src.services.applications import cv_records

    assert cv_records.top_lines(_cv(headline=headline), _profile()) == (frozenset({1, 2}), 1)
    assert _judge(_cv(headline=headline), job_title="AI Engineer") == {}


@pytest.mark.parametrize("heading", ["PROFESSIONAL SUMMARY", "PROFILE"])
def test_a_summary_heading_is_a_top_line(heading):
    for text in (f"Ada Lovelace\n{HEADLINE}\n{heading}\n{SUMMARY}\nEXPERIENCE\n{CALNEX_H}\n- {CALNEX_B}\n",
                 f"Ada Lovelace\n{heading}\n{SUMMARY}\nEXPERIENCE\n{CALNEX_H}\n- {CALNEX_B}\n"):
        assert _judge(text, job_title="AI Product Engineer") == {}, text


def test_top_lines_reusing_a_quote_never_sweep_in_a_quoted_bullet():
    third = "I build RAG pipelines in Python."
    text = f"Ada Lovelace\n{HEADLINE}\n{SUMMARY}\n{third}\nEXPERIENCE\n{CALNEX_H}\n- {CALNEX_B}\n"
    got = _judge(text, {HEADLINE: [CALNEX_B], SUMMARY: [CALNEX_B], third: [CALNEX_B]}, "AI Product Engineer")
    assert 5 not in got.get("evidence_reused", []), got


@pytest.mark.parametrize(("headline", "title"), [
    ("Sr AI Engineer", "Sr AI Engineer"), ("Sr. AI Engineer", "Sr. AI Engineer"), ("Sr AI Engineer", ""),
    ("Snr AI Engineer", "Snr AI Engineer"), ("Leader AI Engineer", "Leader AI Engineer"),
    ("Founding AI Engineer", "Founding AI Engineer"),
])
def test_the_headline_never_borrows_a_seniority_or_role_word_from_the_ad(headline, title):
    assert _judge(_cv(headline=headline), job_title=title) == {"claim_not_in_quote": [1]}, headline


@pytest.mark.parametrize("headline", ["AI PRODUCT ENGINEER", "AI ENGINEER | AGENTIC SYSTEMS"])
def test_an_all_caps_headline_is_the_headline(headline):
    from src.services.applications import cv_records

    assert cv_records.top_lines(_cv(headline=headline), _profile()) == (frozenset({1, 2}), 1)
    assert _judge(_cv(headline=headline), job_title="AI Product Engineer") == {}
