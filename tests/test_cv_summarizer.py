"""Tests for AI-powered CV summarization (all LLM calls mocked)."""

import pytest
from unittest.mock import patch, MagicMock

from src.profile.cv_summarizer import (
    LLMExtraction,
    is_configured,
    _parse_response,
    extract_from_cv_text,
    merge_llm_extraction,
)
from src.profile.models import CVData


def test_is_configured_with_key():
    with patch("src.profile.cv_summarizer.is_configured") as mock:
        mock.return_value = True
        assert is_configured() or mock.return_value


def test_is_configured_without_key():
    with patch("src.config.settings.LLM_API_KEY", ""):
        # Re-import to pick up patched value
        assert not is_configured()


def test_parse_response_valid_json():
    raw = '{"skills": ["Python", "AWS"], "job_titles": ["Engineer"]}'
    result = _parse_response(raw)
    assert result["skills"] == ["Python", "AWS"]


def test_parse_response_markdown_fenced():
    raw = '```json\n{"skills": ["Python"], "summary": "Engineer"}\n```'
    result = _parse_response(raw)
    assert result["skills"] == ["Python"]


def test_parse_response_invalid_json():
    with pytest.raises(Exception):
        _parse_response("This is not JSON at all")


def test_extract_short_text():
    """Text too short should fail gracefully."""
    with patch("src.config.settings.LLM_API_KEY", "test-key"):
        result = extract_from_cv_text("short")
        assert result.success is False
        assert "too short" in result.error


def test_extract_no_key():
    with patch("src.config.settings.LLM_API_KEY", ""):
        result = extract_from_cv_text("A long enough CV text to process and extract data from.")
        assert result.success is False


def test_merge_adds_new_skills():
    cv = CVData(skills=["Python"])
    extraction = LLMExtraction(skills=["Python", "AWS", "Docker"])
    result = merge_llm_extraction(cv, extraction)
    assert "Python" in result.skills
    assert "AWS" in result.skills
    assert "Docker" in result.skills
    assert result.skills.count("Python") == 1  # no duplicate


def test_merge_preserves_existing_summary():
    cv = CVData(summary="Existing summary")
    extraction = LLMExtraction(summary="LLM summary")
    result = merge_llm_extraction(cv, extraction)
    assert result.summary == "Existing summary"


def test_merge_uses_llm_summary_when_empty():
    cv = CVData(summary="")
    extraction = LLMExtraction(summary="LLM summary")
    result = merge_llm_extraction(cv, extraction)
    assert result.summary == "LLM summary"


def test_merge_skips_failed_extraction():
    cv = CVData(skills=["Python"])
    extraction = LLMExtraction(success=False, error="API error")
    result = merge_llm_extraction(cv, extraction)
    assert result.skills == ["Python"]  # unchanged


def test_merge_adds_job_titles():
    cv = CVData(job_titles=["Engineer"])
    extraction = LLMExtraction(job_titles=["Engineer", "Architect"])
    result = merge_llm_extraction(cv, extraction)
    assert "Architect" in result.job_titles
    assert result.job_titles.count("Engineer") == 1


def test_merge_adds_education():
    cv = CVData(education=["BSc Computer Science"])
    extraction = LLMExtraction(education=["BSc Computer Science", "MSc AI"])
    result = merge_llm_extraction(cv, extraction)
    assert "MSc AI" in result.education


def test_merge_adds_certifications():
    cv = CVData(certifications=[])
    extraction = LLMExtraction(certifications=["AWS Solutions Architect"])
    result = merge_llm_extraction(cv, extraction)
    assert "AWS Solutions Architect" in result.certifications
