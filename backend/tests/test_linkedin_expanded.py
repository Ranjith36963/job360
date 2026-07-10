"""Batch 1.5 (Pillar 1) — expanded LinkedIn sections tests.

Adds coverage for Languages / Projects / Volunteer Experience /
Courses — the 4 sections whose bodies were previously discarded by
``parse_linkedin_pdf_async``. LLM is mocked throughout — no live HTTP,
consistent with CLAUDE.md rule #4.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.services.profile import linkedin_parser
from src.services.profile.linkedin_parser import (
    _coerce_courses,
    _coerce_languages,
    _coerce_projects,
    _coerce_volunteer,
    _empty_linkedin_data,
    enrich_cv_from_linkedin,
)
from src.services.profile.models import CVData

# ── Coercion (unit, no LLM) ─────────────────────────────────────────


def test_coerce_languages_filters_and_strips():
    raw = [
        {"language": "English", "proficiency": "Native or bilingual"},
        {"language": "", "proficiency": "Professional working"},   # dropped
        {"language": "  Spanish  ", "proficiency": None},
    ]
    out = _coerce_languages(raw)
    assert out == [
        {"language": "English", "proficiency": "Native or bilingual"},
        {"language": "Spanish", "proficiency": ""},
    ]


def test_coerce_languages_handles_non_list():
    assert _coerce_languages(None) == []
    assert _coerce_languages({"oops": True}) == []
    assert _coerce_languages("text") == []


def test_coerce_projects_keeps_required_and_empty_optional():
    raw = [
        {
            "title": "Job360",
            "description": "UK job aggregator.",
            "start": "Mar 2024",
            "end": "Present",
            "url": "https://github.com/x/job360",
        },
        {"title": "", "description": "no title — dropped"},
        {"title": "Nameless sidequest"},  # description / dates / url missing
    ]
    out = _coerce_projects(raw)
    assert len(out) == 2
    assert out[0]["title"] == "Job360"
    assert out[0]["url"] == "https://github.com/x/job360"
    assert out[1] == {"title": "Nameless sidequest", "description": "",
                      "start": "", "end": "", "url": ""}


def test_coerce_volunteer_accepts_organisation_or_organization_spelling():
    raw = [
        {"role": "Mentor", "organization": "Code First Girls", "cause": "Education"},
        {"role": "", "organisation": "UK-spelled org"},  # role missing but org present
        {"role": "", "organisation": ""},  # both missing — dropped
    ]
    out = _coerce_volunteer(raw)
    assert len(out) == 2
    assert out[0]["organisation"] == "Code First Girls"
    assert out[1]["organisation"] == "UK-spelled org"


def test_coerce_courses_requires_title():
    raw = [
        {"title": "Statistical Learning", "institution": "Stanford Online", "date": "2022"},
        {"title": "", "institution": "Nowhere"},  # dropped
        {"title": "Unattributed Course"},
    ]
    out = _coerce_courses(raw)
    assert len(out) == 2
    assert out[1] == {"title": "Unattributed Course", "institution": "", "date": ""}


def test_empty_linkedin_data_includes_new_fields():
    empty = _empty_linkedin_data()
    for key in ("languages", "projects", "volunteer", "courses"):
        assert key in empty
        assert empty[key] == []


# ── enrich_cv_from_linkedin — writes the new fields ─────────────────


def test_enrich_writes_new_section_fields_onto_cvdata():
    cv = CVData()
    linkedin_data = _empty_linkedin_data() | {
        "languages": [{"language": "English", "proficiency": "Native"}],
        "projects": [{"title": "Job360", "description": "x", "start": "", "end": "", "url": ""}],
        "volunteer": [{"role": "Mentor", "organisation": "CFG", "cause": "Education",
                       "start": "", "end": "", "description": ""}],
        "courses": [{"title": "Stats", "institution": "Stanford", "date": "2022"}],
    }
    cv = enrich_cv_from_linkedin(cv, linkedin_data)
    assert cv.linkedin_languages == [{"language": "English", "proficiency": "Native"}]
    assert cv.linkedin_projects[0]["title"] == "Job360"
    assert cv.linkedin_volunteer[0]["organisation"] == "CFG"
    assert cv.linkedin_courses[0]["title"] == "Stats"


def test_enrich_overwrites_new_section_fields_on_rerun():
    """Re-parsing must reflect the new LinkedIn state — no stale accumulation."""
    cv = CVData(
        linkedin_languages=[{"language": "Old", "proficiency": ""}],
        linkedin_projects=[{"title": "Old", "description": "", "start": "", "end": "", "url": ""}],
    )
    fresh = _empty_linkedin_data() | {
        "languages": [{"language": "Fresh", "proficiency": ""}],
        "projects": [],  # deleted on LinkedIn since last parse
    }
    cv = enrich_cv_from_linkedin(cv, fresh)
    assert cv.linkedin_languages == [{"language": "Fresh", "proficiency": ""}]
    assert cv.linkedin_projects == []


# ── parse_linkedin_pdf_async end-to-end (LLM mocked) ────────────────


@pytest.mark.asyncio
async def test_parse_linkedin_pdf_populates_all_new_sections(tmp_path):
    """End-to-end smoke: a fake PDF's section split + mocked LLM yields all 7 dict keys."""
    # Fabricate LinkedIn-shaped text directly (bypass pdfplumber).
    fake_text = (
        "Ada Lovelace\n"
        "Founding Engineer, Technology\n"
        "linkedin.com/in/ada\n"
        "Page 1 of 1\n"
        "\n"
        "Experience\n"
        "Senior Engineer at ACME\n"
        "\n"
        "Languages\n"
        "English — Native\n"
        "French — Professional working\n"
        "\n"
        "Projects\n"
        "Analytical Engine\n"
        "\n"
        "Volunteer Experience\n"
        "Mentor at CodeFirstGirls\n"
        "\n"
        "Courses\n"
        "Symbolic Logic\n"
    )

    # Markers chosen to be unique across prompt bodies (``Volunteer`` and
    # ``position/role`` never collide; a plain ``Experience`` would match
    # both the Experience and Volunteer-Experience prompts).
    llm_payloads = [
        ("position/role", {"positions": [{"title": "Senior Engineer", "company": "ACME"}]}),
        ("Languages section", {"languages": [
            {"language": "English", "proficiency": "Native"},
            {"language": "French", "proficiency": "Professional working"},
        ]}),
        ("Projects section", {"projects": [{"title": "Analytical Engine"}]}),
        ("volunteer role", {"volunteer": [
            {"role": "Mentor", "organisation": "CodeFirstGirls"}
        ]}),
        ("Courses section", {"courses": [{"title": "Symbolic Logic"}]}),
    ]

    async def fake_llm_json(prompt: str):
        for marker, payload in llm_payloads:
            if marker in prompt:
                return payload
        return {}

    with patch("src.services.profile.linkedin_parser._extract_text", return_value=fake_text), \
         patch("src.services.profile.linkedin_parser._llm_json", side_effect=fake_llm_json):
        result = await linkedin_parser.parse_linkedin_pdf_async("fake.pdf")

    assert result["languages"] == [
        {"language": "English", "proficiency": "Native"},
        {"language": "French", "proficiency": "Professional working"},
    ]
    assert result["projects"][0]["title"] == "Analytical Engine"
    assert result["volunteer"][0]["role"] == "Mentor"
    assert result["courses"][0]["title"] == "Symbolic Logic"
    # Pre-Batch-1.5 fields still present (no regression)
    assert result["positions"][0]["title"] == "Senior Engineer"


@pytest.mark.asyncio
async def test_parse_skips_llm_calls_when_section_missing(tmp_path):
    """If ``Languages`` section isn't in the PDF, LLM is NOT called for it."""
    fake_text = (
        "Ada Lovelace\n"
        "linkedin.com/in/ada\n"
        "Page 1 of 1\n"
        "\n"
        "Experience\n"
        "Engineer at X\n"
        "\n"
        "Education\n"
        "BSc Maths\n"
    )

    mock_llm = AsyncMock(return_value={"positions": [], "education": []})
    with patch("src.services.profile.linkedin_parser._extract_text", return_value=fake_text), \
         patch("src.services.profile.linkedin_parser._llm_json", new=mock_llm):
        result = await linkedin_parser.parse_linkedin_pdf_async("fake.pdf")

    prompts_called = [call.args[0] for call in mock_llm.call_args_list]
    # Only Experience + Education prompts should fire — the other 5 have empty text.
    # Use unique markers ("position/role" only in Experience prompt; "school"
    # only in Education prompt) so this assertion doesn't false-positive.
    assert any("position/role" in p for p in prompts_called)
    assert any("school" in p for p in prompts_called)
    assert not any("Languages section" in p for p in prompts_called)
    assert not any("Projects section" in p for p in prompts_called)
    assert not any("volunteer role" in p for p in prompts_called)
    assert not any("Courses section" in p for p in prompts_called)

    # Fields still present, just empty
    assert result["languages"] == []
    assert result["projects"] == []
    assert result["volunteer"] == []
    assert result["courses"] == []
