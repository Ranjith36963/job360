"""Batch 1.1 (Pillar 1) — Pydantic CV schema + retry-validated extract.

Tests the strict-schema upgrade path:
  * ``CVSchema`` validates / coerces LLM output
  * ``cv_schema_to_cvdata`` flattens it into the legacy ``CVData``
  * ``llm_extract_validated`` retries on ``ValidationError``
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from src.services.profile.schemas import (
    CareerDomain,
    CVSchema,
    cv_schema_to_cvdata,
)

# ── CVSchema validation ─────────────────────────────────────────────


def test_schema_accepts_complete_valid_payload():
    payload = {
        "name": "Jane Doe",
        "headline": "Senior RN",
        "location": "Manchester, UK",
        "summary": "10 years ICU experience.",
        "skills": ["IV therapy", "Triage"],
        "experience": [
            {
                "company": "Manchester Royal",
                "title": "Charge Nurse",
                "dates": "2020-present",
                "location": "Manchester",
                "bullets": ["Led 12-bed unit", "Reduced falls 30%"],
            }
        ],
        "education": [
            {"degree": "BSc Nursing", "institution": "Uni of Manchester", "dates": "2012-2015", "details": []}
        ],
        "certifications": ["ACLS 2023"],
        "achievements": ["Reduced falls 30%"],
        "industries": ["Healthcare"],
        "languages": ["English"],
        "experience_level": "senior",
        "career_domain": "healthcare_and_lifesciences",
    }
    schema = CVSchema.model_validate(payload)
    assert schema.name == "Jane Doe"
    assert schema.career_domain == CareerDomain.HEALTHCARE_AND_LIFESCIENCES
    assert len(schema.experience) == 1
    assert schema.experience[0].bullets == ["Led 12-bed unit", "Reduced falls 30%"]


def test_schema_accepts_empty_payload():
    """Empty LLM output must not fail validation — retries should be reserved for real errors."""
    schema = CVSchema.model_validate({})
    assert schema.name == ""
    assert schema.skills == []
    assert schema.experience == []
    assert schema.career_domain is None


def test_schema_coerces_comma_string_to_list():
    """Weak LLMs sometimes return 'a, b, c' for list fields — coerce, don't retry."""
    schema = CVSchema.model_validate({"skills": "Python, Docker, AWS"})
    assert schema.skills == ["Python", "Docker", "AWS"]


def test_schema_coerces_none_list_to_empty():
    schema = CVSchema.model_validate({"skills": None, "certifications": None})
    assert schema.skills == []
    assert schema.certifications == []


def test_schema_normalises_empty_career_domain_to_none():
    for blank in ["", "unknown", "n/a", "none", None]:
        schema = CVSchema.model_validate({"career_domain": blank})
        assert schema.career_domain is None, f"{blank!r} should map to None"


def test_schema_rejects_invalid_career_domain_enum():
    """A genuinely wrong enum value MUST raise — retry loop relies on this signal."""
    with pytest.raises(ValidationError):
        CVSchema.model_validate({"career_domain": "banana"})


def test_schema_ignores_extra_keys():
    """LLMs hallucinate keys; we do not want that to trigger a retry."""
    schema = CVSchema.model_validate({"name": "Bob", "invented_key": 42, "another": "junk"})
    assert schema.name == "Bob"


def test_schema_extracts_skills_from_list_of_dicts():
    payload = {"skills": [{"name": "Python"}, {"skill": "Docker"}, "AWS"]}
    schema = CVSchema.model_validate(payload)
    assert set(schema.skills) == {"Python", "Docker", "AWS"}


# ── cv_schema_to_cvdata adapter ─────────────────────────────────────


def test_adapter_splits_experience_into_titles_and_companies():
    schema = CVSchema.model_validate({
        "name": "Alex",
        "skills": ["SQL"],
        "experience": [
            {"company": "Acme", "title": "Analyst", "bullets": ["built dashboards"]},
            {"company": "Globex", "title": "Senior Analyst", "bullets": ["led team"]},
        ],
    })
    cv = cv_schema_to_cvdata(schema, raw_text="raw")
    assert cv.name == "Alex"
    assert cv.job_titles == ["Analyst", "Senior Analyst"]
    assert cv.companies == ["Acme", "Globex"]
    assert "built dashboards" in cv.experience_text
    assert "led team" in cv.experience_text
    assert cv.raw_text == "raw"


def test_adapter_flattens_education_lines():
    schema = CVSchema.model_validate({
        "education": [
            {"degree": "BSc CS", "institution": "MIT", "dates": "2018-2022",
             "details": ["Thesis: NN", "Neural Networks", "Machine Learning"]}
        ]
    })
    cv = cv_schema_to_cvdata(schema, raw_text="")
    # ONE entry per degree, combining degree + institution + dates so the
    # "Education: N" stat counts qualifications, not lines. Coursework/thesis
    # details are NOT flattened in (each was a separate fake "education entry").
    assert len(cv.education) == 1
    entry = cv.education[0]
    assert "BSc CS" in entry and "MIT" in entry and "2018-2022" in entry
    assert "Thesis: NN" not in entry
    assert "Neural Networks" not in cv.education


def test_adapter_surfaces_career_domain_string():
    schema = CVSchema.model_validate({"career_domain": "data_and_ai"})
    cv = cv_schema_to_cvdata(schema, raw_text="")
    assert cv.career_domain == "data_and_ai"


def test_adapter_career_domain_none_when_unset():
    schema = CVSchema.model_validate({})
    cv = cv_schema_to_cvdata(schema, raw_text="")
    assert cv.career_domain is None


# ── llm_extract_validated retry loop ────────────────────────────────


@pytest.mark.asyncio
async def test_validated_extract_succeeds_first_try():
    valid_payload = {"name": "Alice", "skills": ["Python"]}
    with patch(
        "src.services.profile.llm_provider.llm_extract",
        new_callable=AsyncMock,
        return_value=valid_payload,
    ) as mock_extract:
        from src.services.profile.llm_provider import llm_extract_validated
        schema = await llm_extract_validated("prompt", CVSchema, max_retries=2)
    assert schema.name == "Alice"
    assert mock_extract.call_count == 1  # no retries needed


@pytest.mark.asyncio
async def test_validated_extract_retries_on_validation_error():
    """First call returns invalid enum, second returns valid — should retry and succeed."""
    bad = {"career_domain": "invalid_bucket"}
    good = {"career_domain": "software_engineering"}
    with patch(
        "src.services.profile.llm_provider.llm_extract",
        new_callable=AsyncMock,
        side_effect=[bad, good],
    ) as mock_extract:
        from src.services.profile.llm_provider import llm_extract_validated
        schema = await llm_extract_validated("prompt", CVSchema, max_retries=2)
    assert schema.career_domain == CareerDomain.SOFTWARE_ENGINEERING
    assert mock_extract.call_count == 2


@pytest.mark.asyncio
async def test_validated_extract_raises_after_exhausting_retries():
    """All attempts return invalid enum — RuntimeError surfaces the last ValidationError."""
    bad = {"career_domain": "not_a_real_bucket"}
    with patch(
        "src.services.profile.llm_provider.llm_extract",
        new_callable=AsyncMock,
        return_value=bad,
    ) as mock_extract:
        from src.services.profile.llm_provider import llm_extract_validated
        with pytest.raises(RuntimeError, match="CVSchema validation"):
            await llm_extract_validated("prompt", CVSchema, max_retries=2)
    assert mock_extract.call_count == 3  # 1 initial + 2 retries


@pytest.mark.asyncio
async def test_validated_extract_max_retries_zero_no_retry():
    bad = {"career_domain": "bogus"}
    with patch(
        "src.services.profile.llm_provider.llm_extract",
        new_callable=AsyncMock,
        return_value=bad,
    ) as mock_extract:
        from src.services.profile.llm_provider import llm_extract_validated
        with pytest.raises(RuntimeError):
            await llm_extract_validated("prompt", CVSchema, max_retries=0)
    assert mock_extract.call_count == 1


@pytest.mark.asyncio
async def test_validated_extract_appends_error_to_retry_prompt():
    """Second call should receive a prompt that includes the validation error text."""
    bad = {"career_domain": "wrong_bucket"}
    good = {"name": "Eve"}
    captured_prompts: list[str] = []

    async def fake_extract(prompt: str, system: str = ""):
        captured_prompts.append(prompt)
        return bad if len(captured_prompts) == 1 else good

    with patch("src.services.profile.llm_provider.llm_extract", side_effect=fake_extract):
        from src.services.profile.llm_provider import llm_extract_validated
        schema = await llm_extract_validated("base prompt", CVSchema, max_retries=2)

    assert schema.name == "Eve"
    assert len(captured_prompts) == 2
    assert "base prompt" in captured_prompts[1]
    assert "failed schema validation" in captured_prompts[1]
    # Error text must reference the schema class / field for LLM self-correction
    assert "career_domain" in captured_prompts[1]
