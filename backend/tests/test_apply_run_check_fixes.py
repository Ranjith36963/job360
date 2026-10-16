"""Fixes from the first production apply runs: CV check false positives, ATS hosts,
the Needs-you wording and the save_fit verdict limit."""

from __future__ import annotations

import pytest

from src.core import settings
from src.services.applications import blocked
from src.services.applications import cv_checks as cc
from src.services.applications.job_checks import classify_host

SOURCE = (
    "AWS Bedrock S3 SageMaker Docker Railway OpenAI API Gemini API Python FastAPI "
    "AI Engineer Agent Harness MCP Tools Evals Job360 (job360.uk) Founder"
)


def _bad(text: str, source: str = SOURCE) -> str:
    issue = cc.unsupported_terms(text, source)
    return "" if issue is None else issue["detail"]


# ── Fix 1a: parentheses ─────────────────────────────────────────────────────
def test_split_items_keeps_parenthesised_commas_together():
    items = cc._split_items("AWS (Bedrock, S3, SageMaker), Docker, Railway")
    assert items == ["AWS", "Bedrock", "S3", "SageMaker", "Docker", "Railway"]
    assert not any("(" in i or ")" in i for i in items)


def test_production_skill_line_is_clean():
    cv = "Name\nSUMMARY\nBuilder.\nSKILLS\nCloud & Ops: AWS (Bedrock, S3, SageMaker), Docker, Railway\n"
    assert _bad(cv) == ""


# ── Fix 1b: header block ────────────────────────────────────────────────────
def test_headline_is_skipped_but_project_lines_are_checked():
    cv = (
        "RANJITH G\n"
        "AI Engineer | Agent Harness, MCP Tools & Evals\n"
        "a@b.co | London\n"
        "SUMMARY\nText.\n"
        "PROJECTS\n"
        "Job360 | Python, FastAPI, Kubernetes\n"
    )
    terms = cc.terms_to_check(cv)
    assert "Agent Harness" not in terms
    assert "Kubernetes" in terms
    assert "Kubernetes" in _bad(cv)
    assert "Harness" not in _bad(cv)


def test_no_heading_means_nothing_is_skipped():
    assert "Kubernetes" in cc.terms_to_check("Name\nJob360 | Python, Kubernetes\n")


# ── Fix 1c: combined phrases ────────────────────────────────────────────────
_COMBINED = "Name\nSUMMARY\nx\nSKILLS\nTools: OpenAI and Gemini APIs, Python, Docker\n"


def test_combined_phrase_accepted_when_every_part_found():
    assert _bad(_COMBINED) == ""


def test_combined_phrase_rejected_when_a_part_missing():
    assert "OpenAI and Gemini APIs" in _bad(_COMBINED, "OpenAI API Python Docker")


def test_real_catches_still_flagged():
    cv = "Name\nSUMMARY\nx\nSKILLS\nTools: REST APIs, Python, Kubernetes\n"
    msg = _bad(cv)
    assert "REST APIs" in msg and "Kubernetes" in msg


# ── Fix 2 ───────────────────────────────────────────────────────────────────
class _CV:
    cv_positions = [{"company": "Job360 (job360.uk)", "dates": "2026 - Present"}]


class _Profile:
    cv_data = _CV()


def test_current_role_matches_company_without_parenthetical():
    assert cc.missing_current_roles("Job360 | Founder & AI-Native Engineer | 2026 - Present", _Profile()) is None


def test_current_role_still_missing_when_company_absent():
    assert cc.missing_current_roles("Acme | Engineer | 2026 - Present", _Profile()) is not None


# ── Fix 3 ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "host",
    ["jobs.gem.com", "gem.com", "ats.rippling.com", "x.applytojob.com", "a.jazzhr.com", "x.dayforcehcm.com",
     "x.freshteam.com", "x.zohorecruit.com", "x.zohorecruit.in", "x.icims.com", "x.taleo.net",
     "x.ultipro.com", "x.careers-page.com"],
)
def test_new_ats_hosts_are_official(host):
    assert classify_host(host) == "official_ats"


@pytest.mark.parametrize("host", ["gem.com.evil.com", "evilgem.com", "rippling.com.evil.io"])
def test_lookalike_hosts_are_unknown(host):
    assert classify_host(host) == "unknown"


def test_tuple_contains_new_hosts():
    assert {"gem.com", "applytojob.com", "careers-page.com"} <= set(settings.OFFICIAL_ATS_HOSTS)


# ── Fix 4 ───────────────────────────────────────────────────────────────────
_SOLVE = "Open the form on h.example, solve the check and press Submit yourself, then mark this resolved."
_GENERIC = "Check the form on h.example and finish it or tell your assistant what to do."


@pytest.mark.parametrize(
    "reason,tail",
    [
        ("captcha", _SOLVE),
        ("bot_check", _SOLVE),
        ("login_needed", "Sign in on h.example yourself (never share the password), then mark this resolved."),
        ("upload_failed", "Attach the CV file on h.example yourself, then mark this resolved."),
        ("unknown_question", "Answer the question on h.example (the assistant could not), then mark this resolved."),
        ("site_error", _GENERIC),
        ("other", _GENERIC),
    ],
)
def test_ask_question_tells_the_user_what_to_do(reason, tail):
    q = blocked.ask_question({"reason": reason, "step": "submit", "page_host": "h.example"})
    assert q.startswith("Your assistant got stuck: ")
    assert " at submit on h.example. " in q
    assert q.endswith(tail)
    assert "What should it do?" not in q


# ── Fix 5 ───────────────────────────────────────────────────────────────────
def test_save_fit_description_states_verdict_limit():
    import inspect

    from src.api import mcp_server

    # Feedback loop slice 5 (DECISIONS S5 Q1): a scored fit's verdict is 1-140 characters,
    # an unscored one stays at most 200. The save_fit description must state both limits.
    doc = " ".join(inspect.getsource(mcp_server).split())
    assert "one-line reason (1-140 chars)" in doc
    assert "score `verdict` is optional (200 chars)" in doc


# ── Review regressions ──────────────────────────────────────────────────────
def test_caps_employer_late_in_cv_does_not_skip_skills():
    cv = "Jane Doe\nSkills\nLanguages: Python, Rust, Haskell, Elixir\nExperience\nIBM\nEngineer\n"
    assert cc.terms_to_check(cv) == ["Python", "Rust", "Haskell", "Elixir"]
    msg = _bad(cv, "Python, SQL")
    assert "Rust" in msg and "Haskell" in msg and "Elixir" in msg


def test_skill_line_before_early_heading_is_still_checked():
    cv = "Jane Doe\nLanguages: Python, Rust, Haskell\nSUMMARY\nx\n"
    assert {"Rust", "Haskell"} <= set(cc.terms_to_check(cv))


def test_unclosed_paren_still_checks_items():
    cv = "Jane Doe\nSKILLS\nBackend: Python (FastAPI, Rust, Haskell, Elixir\n"
    assert {"Rust", "Haskell", "Elixir"} <= set(cc.terms_to_check(cv))


def test_plural_stem_matches_whole_word_only():
    cv = "Name\nSUMMARY\nx\nSKILLS\nTools: Redis and Kafka, Python, Docker\n"
    assert "Redis and Kafka" in _bad(cv, "Built predictive models. Kafka streaming. Python Docker")


def test_empty_core_company_is_not_a_free_pass():
    class _C:
        cv_positions = [{"company": "(Stealth)", "dates": "2026 - Present"}]

    class _P:
        cv_data = _C()

    assert cc.missing_current_roles("Anything here", _P()) is not None
