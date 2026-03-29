"""Tests for LLM JD enrichment (all API calls mocked)."""

import asyncio
import json
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.jd_enricher import enrich_top_jobs, llm_parse_jd, merge_llm_jd


# ── Helpers ───────────────────────────────────────────────────────────

def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


@dataclass
class _FakeParsedJD:
    required_skills: list = field(default_factory=list)
    preferred_skills: list = field(default_factory=list)
    experience_years: int | None = None
    qualifications: list = field(default_factory=list)
    seniority_signal: str = ""
    salary_min: float | None = None
    salary_max: float | None = None


@dataclass
class _FakeJob:
    title: str = "Engineer"
    description: str = "Build things with Python and Docker."
    match_score: int = 50
    match_data: str = ""
    source: str = "test"
    location: str = "London"
    company: str = "TestCo"


@dataclass
class _FakeDetailedScore:
    total: int = 55
    role: int = 10
    skill: int = 15
    seniority: int = 5
    experience: int = 5
    credentials: int = 3
    location: int = 7
    recency: int = 5
    semantic: int = 5
    matched_skills: list = field(default_factory=list)
    missing_required: list = field(default_factory=list)
    missing_preferred: list = field(default_factory=list)
    transferable_skills: list = field(default_factory=list)


# ── llm_parse_jd ─────────────────────────────────────────────────────

def test_llm_parse_jd_not_configured():
    """Returns None when LLM is not configured."""
    with patch("src.llm.jd_enricher.is_configured", return_value=False):
        result = _run(llm_parse_jd("some JD text"))
    assert result is None


def test_llm_parse_jd_success():
    """Returns parsed dict on successful LLM response."""
    llm_response = json.dumps({
        "required_skills": ["Python", "Docker"],
        "preferred_skills": ["Kubernetes"],
        "experience_years": 3,
        "qualifications": ["BSc"],
        "seniority": "mid",
        "salary_min": 50000,
        "salary_max": 70000,
    })

    long_desc = "This is a sufficiently long job description for testing LLM JD parsing."

    with patch("src.llm.jd_enricher.is_configured", return_value=True), \
         patch("src.llm.jd_enricher.allm_complete", new_callable=AsyncMock, return_value=llm_response), \
         patch("src.llm.jd_enricher.cache.get_cached", return_value=None), \
         patch("src.llm.jd_enricher.cache.set_cached"):
        result = _run(llm_parse_jd(long_desc))

    assert result is not None
    assert "Python" in result["required_skills"]
    assert result["experience_years"] == 3


def test_llm_parse_jd_uses_cache():
    """Returns cached result without calling LLM."""
    cached = {"required_skills": ["cached"], "preferred_skills": []}
    long_desc = "This is a sufficiently long job description for testing LLM cache."

    with patch("src.llm.jd_enricher.is_configured", return_value=True), \
         patch("src.llm.jd_enricher.cache.get_cached", return_value=cached):
        result = _run(llm_parse_jd(long_desc))

    assert result == cached


def test_llm_parse_jd_failure():
    """Returns None when LLM returns None."""
    long_desc = "This is a sufficiently long job description for testing LLM failure."

    with patch("src.llm.jd_enricher.is_configured", return_value=True), \
         patch("src.llm.jd_enricher.allm_complete", new_callable=AsyncMock, return_value=None), \
         patch("src.llm.jd_enricher.cache.get_cached", return_value=None):
        result = _run(llm_parse_jd(long_desc))

    assert result is None


# ── merge_llm_jd ─────────────────────────────────────────────────────

def test_merge_adds_new_skills():
    """LLM skills not in regex result are added."""
    parsed = _FakeParsedJD(required_skills=["Python"])
    llm_data = {"required_skills": ["Python", "Docker", "Terraform"]}
    merge_llm_jd(parsed, llm_data)
    assert "Docker" in parsed.required_skills
    assert "Terraform" in parsed.required_skills


def test_merge_no_duplicates():
    """Skills already in regex result are not duplicated."""
    parsed = _FakeParsedJD(required_skills=["Python", "Docker"])
    llm_data = {"required_skills": ["python", "DOCKER"]}  # Different case
    merge_llm_jd(parsed, llm_data)
    assert len(parsed.required_skills) == 2  # No dupes


def test_merge_preserves_regex_experience():
    """Regex experience_years is NOT overwritten by LLM."""
    parsed = _FakeParsedJD(experience_years=5)
    llm_data = {"experience_years": 3}
    merge_llm_jd(parsed, llm_data)
    assert parsed.experience_years == 5  # Regex value kept


def test_merge_fills_missing_salary():
    """LLM salary used when regex found none."""
    parsed = _FakeParsedJD(salary_min=None, salary_max=None)
    llm_data = {"salary_min": 50000, "salary_max": 70000}
    merge_llm_jd(parsed, llm_data)
    assert parsed.salary_min == 50000
    assert parsed.salary_max == 70000


def test_merge_fills_missing_seniority():
    """LLM seniority used when regex found none."""
    parsed = _FakeParsedJD(seniority_signal="")
    llm_data = {"seniority": "senior"}
    merge_llm_jd(parsed, llm_data)
    assert parsed.seniority_signal == "senior"


# ── enrich_top_jobs ──────────────────────────────────────────────────

def test_enrich_top_jobs_not_configured():
    """Returns 0 when LLM is not configured."""
    with patch("src.llm.jd_enricher.is_configured", return_value=False):
        result = _run(enrich_top_jobs([], None, None, [], top_n=5))
    assert result == 0


def test_enrich_top_jobs_integration():
    """Top-N jobs get LLM-enriched and re-scored."""
    jobs = [_FakeJob(match_score=70), _FakeJob(match_score=40)]
    scorer = MagicMock()
    scorer.score_detailed.return_value = _FakeDetailedScore(total=75)

    llm_data = json.dumps({
        "required_skills": ["Python"],
        "preferred_skills": [],
        "experience_years": 3,
        "qualifications": [],
        "seniority": "mid",
        "salary_min": None,
        "salary_max": None,
    })

    with patch("src.llm.jd_enricher.is_configured", return_value=True), \
         patch("src.llm.jd_enricher.allm_complete", new_callable=AsyncMock, return_value=llm_data), \
         patch("src.llm.jd_enricher.cache.get_cached", return_value=None), \
         patch("src.llm.jd_enricher.cache.set_cached"), \
         patch("src.llm.client.pool_status", return_value={"configured": ["groq"], "cooldowns": {}}):
        result = _run(enrich_top_jobs(jobs, scorer, None, ["Python"], top_n=5))

    assert result == 2  # Both jobs enriched
    assert jobs[0].match_score == 75  # Re-scored
