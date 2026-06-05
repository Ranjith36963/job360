"""Tests for the tailored cover-letter generator (copy_roadmap Tier-2 #4 core).

Reuses the profile LLM provider chain (Gemini->Groq->Cerebras) via llm_extract.
The chain is mocked — no live LLM calls (offline, deterministic).
"""
import asyncio

import pytest

from src.models import Job
from src.services import cover_letter


def _run(coro):
    return asyncio.run(coro)


def _job(**kw) -> Job:
    base = dict(
        title="Senior ML Engineer",
        company="Acme AI",
        apply_url="https://acme.example/jobs/1",
        source="jsonld",
        date_found="2026-06-05T00:00:00+00:00",
        description="Build LLM pipelines. Python, PyTorch, AWS.",
    )
    base.update(kw)
    return Job(**base)


def _patch_llm(monkeypatch, capture: dict, *, returns):
    async def _fake(prompt, system=""):
        capture["prompt"] = prompt
        capture["system"] = system
        return returns
    monkeypatch.setattr("src.services.profile.llm_provider.llm_extract", _fake)


def test_generate_returns_cover_letter_text(monkeypatch):
    cap: dict = {}
    _patch_llm(monkeypatch, cap, returns={"cover_letter": "Dear Acme AI team, ..."})
    text = _run(cover_letter.generate_cover_letter(_job(), "10y Python/ML engineer"))
    assert text == "Dear Acme AI team, ..."


def test_prompt_includes_job_and_candidate(monkeypatch):
    cap: dict = {}
    _patch_llm(monkeypatch, cap, returns={"cover_letter": "x"})
    _run(cover_letter.generate_cover_letter(
        _job(title="Data Scientist", company="Globex"),
        "PhD statistician, R and Python",
    ))
    p = cap["prompt"]
    assert "Data Scientist" in p
    assert "Globex" in p
    assert "PhD statistician" in p


def test_raises_when_job_missing_title(monkeypatch):
    _patch_llm(monkeypatch, {}, returns={"cover_letter": "x"})
    with pytest.raises(ValueError):
        _run(cover_letter.generate_cover_letter(_job(title=""), "summary"))


def test_empty_company_is_normalized_not_an_error(monkeypatch):
    # Job.__post_init__ turns "" company into "Unknown" — so this generates,
    # it does not raise. Documents the boundary the validation relies on.
    cap: dict = {}
    _patch_llm(monkeypatch, cap, returns={"cover_letter": "ok"})
    text = _run(cover_letter.generate_cover_letter(_job(company=""), "summary"))
    assert text == "ok"
    assert "Unknown" in cap["prompt"]


def test_raises_when_candidate_summary_empty(monkeypatch):
    _patch_llm(monkeypatch, {}, returns={"cover_letter": "x"})
    with pytest.raises(ValueError):
        _run(cover_letter.generate_cover_letter(_job(), "   "))


def test_raises_when_llm_returns_no_text(monkeypatch):
    _patch_llm(monkeypatch, {}, returns={"cover_letter": "   "})
    with pytest.raises(RuntimeError):
        _run(cover_letter.generate_cover_letter(_job(), "summary"))


def test_raises_when_llm_returns_wrong_shape(monkeypatch):
    _patch_llm(monkeypatch, {}, returns={"unexpected": "key"})
    with pytest.raises(RuntimeError):
        _run(cover_letter.generate_cover_letter(_job(), "summary"))


def test_max_words_passed_into_prompt(monkeypatch):
    cap: dict = {}
    _patch_llm(monkeypatch, cap, returns={"cover_letter": "x"})
    _run(cover_letter.generate_cover_letter(_job(), "summary", max_words=120))
    assert "120" in cap["prompt"]
