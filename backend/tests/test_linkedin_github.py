"""Tests for LinkedIn PDF parser and GitHub profile enricher.

LinkedIn input format is a profile PDF (LinkedIn's "Save to PDF" export),
not a ZIP of CSVs. The enrichment dict schema and ``enrich_cv_from_linkedin``
signature are preserved, so downstream tests (``TestEnrichCVFromLinkedIn``,
``TestKeywordGeneratorWithEnrichedData``) are unchanged.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from src.services.profile.models import CVData, UserPreferences, UserProfile, SearchConfig
from src.services.profile.linkedin_parser import (
    parse_linkedin_pdf,
    parse_linkedin_pdf_async,
    enrich_cv_from_linkedin,
    is_linkedin_pdf,
    _split_sections,
    _extract_header_fields,
    _extract_skills,
    _looks_like_linkedin,
    _coerce_positions,
    _coerce_education,
    _coerce_certifications,
)
from src.services.profile.github_enricher import (
    fetch_github_profile,
    enrich_cv_from_github,
    _infer_skills,
    LANGUAGE_TO_SKILL,
    TOPIC_TO_SKILL,
)
from src.services.profile.keyword_generator import generate_search_config
from src.services.profile.storage import save_profile, load_profile


# ---------------------------------------------------------------------------
# Helpers — build LinkedIn-shaped PDFs in memory
# ---------------------------------------------------------------------------

def _make_linkedin_pdf(
    tmp_path: Path,
    name: str = "John Doe",
    headline: str = "ML Engineer, Technology",
    location: str = "London, United Kingdom",
    url: str = "linkedin.com/in/johndoe",
    summary: str = "Experienced ML engineer",
    experience: list[str] | None = None,
    education: list[str] | None = None,
    skills: list[str] | None = None,
    certifications: list[str] | None = None,
    include_footer: bool = True,
    filename: str = "linkedin.pdf",
) -> Path:
    """Render a PDF that mimics LinkedIn's 'Save to PDF' layout."""
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)

    def _line(text: str) -> None:
        pdf.cell(0, 6, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    if include_footer:
        _line(url)
    _line(name)
    _line(headline)
    _line(location)
    _line("")

    if summary:
        _line("Summary")
        for para_line in summary.splitlines():
            _line(para_line)
        _line("")

    if experience:
        _line("Experience")
        for exp_line in experience:
            _line(exp_line)
        _line("")

    if education:
        _line("Education")
        for edu_line in education:
            _line(edu_line)
        _line("")

    if skills:
        _line("Skills")
        for skill in skills:
            _line(skill)
        _line("")

    if certifications:
        _line("Certifications")
        for cert in certifications:
            _line(cert)
        _line("")

    if include_footer:
        _line("Page 1 of 1")

    path = tmp_path / filename
    pdf.output(str(path))
    return path


def _make_plain_cv_pdf(tmp_path: Path) -> Path:
    """Render a PDF that does NOT look like LinkedIn (no URL, no footer, no headings)."""
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in [
        "Jane Smith",
        "Senior Software Engineer",
        "",
        "A software engineer with 10 years of experience in distributed systems.",
        "",
        "Google, 2015-2020: Built large-scale infrastructure.",
        "University of Cambridge, BSc Computer Science.",
    ]:
        pdf.cell(0, 6, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    path = tmp_path / "plain_cv.pdf"
    pdf.output(str(path))
    return path


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

class TestLinkedInPdfDetection:
    def test_full_linkedin_pdf_detected(self, tmp_path):
        path = _make_linkedin_pdf(
            tmp_path,
            experience=["Engineer at Google", "Jan 2020 - Dec 2022"],
            education=["MIT", "MSc Computer Science", "2016 - 2018"],
            skills=["Python", "SQL"],
        )
        assert is_linkedin_pdf(str(path)) is True

    def test_plain_cv_not_detected(self, tmp_path):
        path = _make_plain_cv_pdf(tmp_path)
        assert is_linkedin_pdf(str(path)) is False

    def test_empty_text_not_detected(self):
        assert _looks_like_linkedin("") is False

    def test_requires_two_markers(self):
        # Only a URL — not enough.
        assert _looks_like_linkedin("linkedin.com/in/johndoe") is False
        # URL + footer (2 markers, no headings) passes.
        assert _looks_like_linkedin("linkedin.com/in/johndoe\nPage 1 of 2") is True
        # Three headings alone (no URL, no footer) → only 1 marker.
        assert _looks_like_linkedin("Summary\nExperience\nEducation") is False

    def test_corrupt_file_not_detected(self, tmp_path):
        path = tmp_path / "bad.pdf"
        path.write_bytes(b"not a real pdf")
        assert is_linkedin_pdf(str(path)) is False


# ---------------------------------------------------------------------------
# Section split & deterministic extraction
# ---------------------------------------------------------------------------

class TestSectionSplit:
    def test_split_recognises_known_headings(self):
        text = "John Doe\nML Engineer\n\nSummary\nI build models.\n\nExperience\nGoogle 2020-2022\n\nSkills\nPython\nSQL\n"
        sections = _split_sections(text)
        assert "summary" in sections
        assert sections["summary"].strip() == "I build models."
        assert "experience" in sections
        assert "skills" in sections
        assert "Python" in sections["skills"]

    def test_split_preserves_header_block(self):
        text = "John Doe\nSenior Engineer\n\nSummary\nHello"
        sections = _split_sections(text)
        header = sections["header"]
        assert "John Doe" in header
        assert "Summary" not in header  # heading itself isn't in the header block

    def test_split_is_case_insensitive(self):
        text = "SUMMARY\nMy summary.\nexperience\nRole details."
        sections = _split_sections(text)
        assert "summary" in sections and "experience" in sections


class TestDeterministicExtraction:
    def test_header_name_and_headline(self):
        fields = _extract_header_fields(
            "linkedin.com/in/johndoe\nJohn Doe\nML Engineer, Technology\nLondon\nPage 1 of 2"
        )
        assert fields["name"] == "John Doe"
        assert fields["headline"] == "ML Engineer, Technology"
        assert fields["industry"] == "Technology"

    def test_header_without_industry_suffix(self):
        fields = _extract_header_fields("Jane Doe\nEngineer")
        assert fields["name"] == "Jane Doe"
        assert fields["headline"] == "Engineer"
        assert fields["industry"] == ""

    def test_skills_one_per_line(self):
        skills = _extract_skills("Python\nSQL\nDocker\n")
        assert skills == ["Python", "SQL", "Docker"]

    def test_skills_dedup_case_insensitive(self):
        skills = _extract_skills("Python\npython\nSQL\n")
        assert skills == ["Python", "SQL"]

    def test_skills_strip_endorsement_counts(self):
        skills = _extract_skills("Python (24)\nSQL (8)\n")
        assert skills == ["Python", "SQL"]


class TestCoercionShaping:
    def test_coerce_positions_drops_missing_title(self):
        raw = [{"company": "Google"}, {"title": "Engineer", "company": "Meta"}]
        assert _coerce_positions(raw) == [
            {"title": "Engineer", "company": "Meta", "start": "", "end": "", "description": ""}
        ]

    def test_coerce_education_drops_missing_school(self):
        raw = [{"degree": "MSc"}, {"school": "MIT", "degree": "MSc CS"}]
        out = _coerce_education(raw)
        assert len(out) == 1 and out[0]["school"] == "MIT"

    def test_coerce_certifications_drops_missing_name(self):
        raw = [{"authority": "AWS"}, {"name": "AWS SA", "authority": "Amazon"}]
        out = _coerce_certifications(raw)
        assert len(out) == 1 and out[0]["name"] == "AWS SA"

    def test_coerce_handles_non_list(self):
        assert _coerce_positions(None) == []
        assert _coerce_education("oops") == []
        assert _coerce_certifications({"not": "a list"}) == []


# ---------------------------------------------------------------------------
# End-to-end parse_linkedin_pdf (with mocked LLM)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_linkedin_llm():
    """Mock llm_extract to return canned responses keyed on prompt section."""
    async def fake_llm(prompt: str, system: str = "") -> dict:
        if "Experience section" in prompt:
            return {"positions": [
                {"title": "Software Engineer", "company": "Google",
                 "start": "Jan 2020", "end": "Dec 2022", "description": "Built ML pipelines"},
                {"title": "Senior Engineer", "company": "Meta",
                 "start": "Jan 2023", "end": "Present", "description": "Led AI team"},
            ]}
        if "Education section" in prompt:
            return {"education": [
                {"school": "MIT", "degree": "MSc Computer Science",
                 "start": "2016", "end": "2018", "notes": ""}
            ]}
        if "certifications section" in prompt:
            return {"certifications": [
                {"name": "AWS Solutions Architect", "authority": "Amazon",
                 "start": "2021", "end": "2024"}
            ]}
        return {}
    # _llm_json imports llm_extract lazily from the provider module, so patching
    # the provider module is what takes effect at call time.
    with patch("src.services.profile.llm_provider.llm_extract", new=fake_llm):
        yield


class TestParseLinkedInPdfEndToEnd:
    @pytest.mark.asyncio
    async def test_full_parse(self, tmp_path, mock_linkedin_llm):
        path = _make_linkedin_pdf(
            tmp_path,
            summary="Experienced ML engineer",
            experience=["Software Engineer at Google", "Jan 2020 - Dec 2022", "Built ML pipelines"],
            education=["MIT", "MSc Computer Science", "2016 - 2018"],
            skills=["Python", "SQL", "Machine Learning", "Docker"],
            certifications=["AWS Solutions Architect - Amazon - 2021"],
        )
        data = await parse_linkedin_pdf_async(str(path))
        assert data["summary"] == "Experienced ML engineer"
        assert data["headline"] == "ML Engineer, Technology"
        assert data["industry"] == "Technology"
        assert data["skills"] == ["Python", "SQL", "Machine Learning", "Docker"]
        assert len(data["positions"]) == 2
        assert data["positions"][0]["title"] == "Software Engineer"
        assert data["positions"][0]["company"] == "Google"
        assert len(data["education"]) == 1
        assert data["education"][0]["school"] == "MIT"
        assert len(data["certifications"]) == 1
        assert data["certifications"][0]["name"] == "AWS Solutions Architect"

    def test_sync_wrapper_returns_same_shape(self, tmp_path, mock_linkedin_llm):
        path = _make_linkedin_pdf(
            tmp_path,
            summary="x",
            experience=["E"], education=["M"],
            skills=["Python"], certifications=["Cert"],
        )
        data = parse_linkedin_pdf(str(path))
        # Batch 1.5 expanded the canonical dict with 4 new section keys
        # (languages, projects, volunteer, courses). The assertion is
        # updated rather than relaxed so the shape remains explicit.
        assert set(data.keys()) == {
            "positions", "skills", "education", "certifications",
            "summary", "industry", "headline",
            "languages", "projects", "volunteer", "courses",
        }
        assert data["skills"] == ["Python"]

    @pytest.mark.asyncio
    async def test_skills_only_requires_no_llm(self, tmp_path):
        """If only Skills section exists, no LLM call needed — still works offline."""
        path = _make_linkedin_pdf(
            tmp_path,
            summary="",
            experience=None, education=None,
            skills=["Python", "Rust"],
            certifications=None,
        )
        # No mock installed — should still succeed because no LLM calls fire.
        data = await parse_linkedin_pdf_async(str(path))
        assert data["skills"] == ["Python", "Rust"]
        assert data["positions"] == []
        assert data["education"] == []


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestLinkedInPdfErrors:
    def test_corrupt_pdf_returns_empty(self, tmp_path):
        path = tmp_path / "bad.pdf"
        path.write_bytes(b"this is not a pdf")
        data = parse_linkedin_pdf(str(path))
        assert data["positions"] == []
        assert data["skills"] == []
        assert data["summary"] == ""

    def test_missing_file_returns_empty(self):
        data = parse_linkedin_pdf("/path/does/not/exist.pdf")
        assert data["positions"] == []
        assert data["skills"] == []

    def test_non_linkedin_pdf_returns_empty(self, tmp_path):
        path = _make_plain_cv_pdf(tmp_path)
        data = parse_linkedin_pdf(str(path))
        assert data["positions"] == []
        assert data["skills"] == []
        assert data["summary"] == ""

    @pytest.mark.asyncio
    async def test_llm_failure_returns_partial_data(self, tmp_path):
        """If the LLM provider raises, deterministic fields still populate."""
        path = _make_linkedin_pdf(
            tmp_path,
            summary="Hello world",
            experience=["Some role"],
            skills=["Python"],
        )

        async def boom(*_a, **_kw):
            raise RuntimeError("llm down")

        with patch("src.services.profile.llm_provider.llm_extract", new=boom):
            data = await parse_linkedin_pdf_async(str(path))
        assert data["summary"] == "Hello world"
        assert data["skills"] == ["Python"]
        # LLM-sourced fields stay empty, not crashed:
        assert data["positions"] == []
        assert data["education"] == []
        assert data["certifications"] == []


# ---------------------------------------------------------------------------
# Enrichment — contract with downstream is unchanged
# ---------------------------------------------------------------------------

class TestEnrichCVFromLinkedIn:
    def test_merges_skills(self):
        cv = CVData(skills=["Python", "Java"])
        linkedin_data = {"skills": ["Python", "SQL", "Docker"], "positions": [], "education": [], "certifications": []}
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert "SQL" in cv.linkedin_skills
        assert "Docker" in cv.linkedin_skills

    def test_merges_job_titles(self):
        cv = CVData(job_titles=["Software Engineer"])
        linkedin_data = {
            "skills": [],
            "positions": [
                {"title": "Software Engineer", "company": "Google"},
                {"title": "Senior Engineer", "company": "Meta"},
            ],
            "education": [], "certifications": [],
        }
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert "Senior Engineer" in cv.job_titles
        assert cv.job_titles.count("Software Engineer") == 1

    def test_merges_education(self):
        cv = CVData()
        linkedin_data = {
            "skills": [], "positions": [],
            "education": [{"school": "MIT", "degree": "MSc CS"}],
            "certifications": [],
        }
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert any("MIT" in e for e in cv.education)

    def test_merges_certifications(self):
        cv = CVData()
        linkedin_data = {
            "skills": [], "positions": [], "education": [],
            "certifications": [{"name": "AWS SA"}],
        }
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert "AWS SA" in cv.certifications

    def test_fills_empty_summary(self):
        cv = CVData()
        linkedin_data = {"skills": [], "positions": [], "education": [], "certifications": [], "summary": "I am an engineer"}
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert cv.summary == "I am an engineer"

    def test_does_not_overwrite_existing_summary(self):
        cv = CVData(summary="My existing summary")
        linkedin_data = {"skills": [], "positions": [], "education": [], "certifications": [], "summary": "LinkedIn summary"}
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert cv.summary == "My existing summary"

    def test_stores_industry(self):
        cv = CVData()
        linkedin_data = {"skills": [], "positions": [], "education": [], "certifications": [], "industry": "Technology"}
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert cv.linkedin_industry == "Technology"

    def test_double_enrich_no_dupes(self):
        cv = CVData(skills=["Python"])
        linkedin_data = {
            "skills": ["SQL", "Docker"],
            "positions": [{"title": "Engineer", "company": "Google"}],
            "education": [], "certifications": [],
            "industry": "Tech",
        }
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert len(cv.linkedin_skills) == 2
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert len(cv.linkedin_skills) == 2


# ---------------------------------------------------------------------------
# GitHub Enricher — unchanged
# ---------------------------------------------------------------------------

class TestInferSkills:
    def test_languages_mapped(self):
        languages = {"Python": 50000, "JavaScript": 30000, "HCL": 10000}
        skills = _infer_skills(languages, set())
        assert skills[0] == "Python"
        assert "JavaScript" in skills
        assert "Terraform" in skills

    def test_topics_mapped(self):
        topics = {"react", "docker", "machine-learning"}
        skills = _infer_skills({}, topics)
        assert "React" in skills
        assert "Docker" in skills
        assert "Machine Learning" in skills

    def test_deduplicates_across_lang_and_topic(self):
        languages = {"Python": 50000}
        topics = {"docker"}
        languages["Dockerfile"] = 5000
        skills = _infer_skills(languages, topics)
        assert sum(1 for s in skills if s == "Docker") == 1

    def test_empty_inputs(self):
        assert _infer_skills({}, set()) == []

    def test_unknown_language_skipped(self):
        assert _infer_skills({"COBOL": 1000}, set()) == []

    def test_unknown_topic_skipped(self):
        assert _infer_skills({}, {"some-random-topic"}) == []


class TestFetchGitHubProfile:
    @pytest.mark.asyncio
    async def test_fetch_repos_and_languages(self):
        mock_repos = [
            {"name": "ml-project", "language": "Python", "description": "ML pipeline",
             "stargazers_count": 10, "topics": ["machine-learning", "pytorch"], "fork": False},
            {"name": "web-app", "language": "TypeScript", "description": "React app",
             "stargazers_count": 5, "topics": ["react", "nextjs"], "fork": False},
        ]
        mock_languages_ml = {"Python": 50000, "Jupyter Notebook": 10000}
        mock_languages_web = {"TypeScript": 30000, "CSS": 5000}

        async def mock_get(url, **kwargs):
            resp = AsyncMock()
            resp.status = 200
            if "repos?per_page" in url:
                resp.json = AsyncMock(return_value=mock_repos)
            elif "ml-project/languages" in url:
                resp.json = AsyncMock(return_value=mock_languages_ml)
            elif "web-app/languages" in url:
                resp.json = AsyncMock(return_value=mock_languages_web)
            else:
                resp.json = AsyncMock(return_value={})
            return resp

        session = AsyncMock()
        session.get = MagicMock(side_effect=lambda url, **kw: _async_context(mock_get(url, **kw)))

        result = await fetch_github_profile("testuser", session=session)
        assert len(result["repositories"]) == 2
        assert result["languages"]["Python"] == 50000
        assert "machine-learning" in result["topics"]
        assert "Python" in result["skills_inferred"]

    @pytest.mark.asyncio
    async def test_skips_forks(self):
        mock_repos = [
            {"name": "forked-repo", "language": "Python", "description": "", "stargazers_count": 0, "topics": [], "fork": True},
            {"name": "own-repo", "language": "Go", "description": "", "stargazers_count": 1, "topics": [], "fork": False},
        ]

        async def mock_get(url, **kwargs):
            resp = AsyncMock()
            resp.status = 200
            if "repos?per_page" in url:
                resp.json = AsyncMock(return_value=mock_repos)
            else:
                resp.json = AsyncMock(return_value={"Go": 20000})
            return resp

        session = AsyncMock()
        session.get = MagicMock(side_effect=lambda url, **kw: _async_context(mock_get(url, **kw)))

        result = await fetch_github_profile("testuser", session=session)
        assert len(result["repositories"]) == 1
        assert result["repositories"][0]["name"] == "own-repo"

    @pytest.mark.asyncio
    async def test_handles_api_error(self):
        async def mock_get(url, **kwargs):
            resp = AsyncMock()
            resp.status = 404
            return resp

        session = AsyncMock()
        session.get = MagicMock(side_effect=lambda url, **kw: _async_context(mock_get(url, **kw)))

        result = await fetch_github_profile("nonexistent", session=session)
        assert result["repositories"] == []
        assert result["skills_inferred"] == []

    @pytest.mark.asyncio
    async def test_handles_rate_limit(self):
        async def mock_get(url, **kwargs):
            resp = AsyncMock()
            resp.status = 403
            return resp

        session = AsyncMock()
        session.get = MagicMock(side_effect=lambda url, **kw: _async_context(mock_get(url, **kw)))

        result = await fetch_github_profile("testuser", session=session)
        assert result["repositories"] == []


class TestEnrichCVFromGitHub:
    def test_merges_skills(self):
        cv = CVData(skills=["Python", "Java"])
        github_data = {
            "skills_inferred": ["Python", "TypeScript", "Docker"],
            "languages": {"Python": 50000, "TypeScript": 30000},
            "topics": ["docker"],
        }
        cv = enrich_cv_from_github(cv, github_data)
        assert "TypeScript" in cv.github_skills_inferred
        assert "Docker" in cv.github_skills_inferred
        assert "Python" not in cv.github_skills_inferred

    def test_stores_languages_and_topics(self):
        cv = CVData()
        github_data = {
            "skills_inferred": ["Go"],
            "languages": {"Go": 20000},
            "topics": ["microservices"],
        }
        cv = enrich_cv_from_github(cv, github_data)
        assert cv.github_languages == {"Go": 20000}
        assert "microservices" in cv.github_topics


# ---------------------------------------------------------------------------
# Storage with new fields
# ---------------------------------------------------------------------------

# TestStorageWithNewFields was deleted in Batch 3.5.2 — storage moved
# from data/user_profile.json to the user_profiles DB table, so the
# PROFILE_PATH monkey-patch it used no longer exists. Equivalent
# per-user round-trip + schema-drift + unknown-key coverage lives in
# tests/test_profile_storage.py (Batch 3.5.2). See
# docs/plans/batch-3.5.2-plan.md Deliverable B for the migration
# rationale.


# ---------------------------------------------------------------------------
# Keyword generator with LinkedIn + GitHub data
# ---------------------------------------------------------------------------

class TestKeywordGeneratorWithEnrichedData:
    def test_linkedin_skills_in_search_config(self):
        # Batch 2.3 — skill lists exit the SearchConfig in canonical (lower-case,
        # alias-resolved) form. "Spark" aliases to "apache spark".
        profile = UserProfile(
            cv_data=CVData(raw_text="test", skills=["Python"], linkedin_skills=["SQL", "Docker"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"], additional_skills=["Spark"]),
        )
        config = generate_search_config(profile)
        all_skills = config.primary_skills + config.secondary_skills + config.tertiary_skills
        assert "apache spark" in all_skills  # canonical for "Spark"
        assert "python" in all_skills
        assert "sql" in all_skills
        assert "docker" in all_skills

    def test_github_skills_in_search_config(self):
        # Batch 2.3 — canonical (lower-case) skill assertion.
        profile = UserProfile(
            cv_data=CVData(raw_text="test", skills=["Python"], github_skills_inferred=["TypeScript", "React"]),
            preferences=UserPreferences(target_job_titles=["Full Stack Developer"]),
        )
        config = generate_search_config(profile)
        all_skills = config.primary_skills + config.secondary_skills + config.tertiary_skills
        assert "typescript" in all_skills
        assert "react" in all_skills

    def test_linkedin_positions_as_titles(self):
        profile = UserProfile(
            cv_data=CVData(
                raw_text="test",
                linkedin_positions=[
                    {"title": "Senior Engineer", "company": "Google"},
                    {"title": "Tech Lead", "company": "Meta"},
                ],
            ),
            preferences=UserPreferences(target_job_titles=["Software Engineer"]),
        )
        config = generate_search_config(profile)
        assert "Software Engineer" in config.job_titles
        assert "Senior Engineer" in config.job_titles
        assert "Tech Lead" in config.job_titles

    def test_linkedin_industry_in_relevance_keywords(self):
        profile = UserProfile(
            cv_data=CVData(raw_text="test", linkedin_industry="Information Technology"),
            preferences=UserPreferences(target_job_titles=["Engineer"]),
        )
        config = generate_search_config(profile)
        assert "information" in config.relevance_keywords
        assert "technology" in config.relevance_keywords

    def test_deduplication_across_all_sources(self):
        # Batch 2.3 — canonical (lower-case) skill assertion.
        profile = UserProfile(
            cv_data=CVData(
                raw_text="test",
                skills=["Python", "SQL"],
                linkedin_skills=["Python", "Docker"],
                github_skills_inferred=["Python", "SQL", "Go"],
            ),
            preferences=UserPreferences(target_job_titles=["Engineer"], additional_skills=["Python"]),
        )
        config = generate_search_config(profile)
        all_skills = config.primary_skills + config.secondary_skills + config.tertiary_skills
        assert all_skills.count("python") == 1
        assert all_skills.count("sql") == 1
        assert "docker" in all_skills
        assert "go" in all_skills

    def test_empty_enrichment_fields_no_change(self):
        # Batch 2.3 — canonical (lower-case) skill assertion.
        profile = UserProfile(
            cv_data=CVData(raw_text="test", skills=["Python", "SQL"]),
            preferences=UserPreferences(target_job_titles=["Engineer"]),
        )
        config = generate_search_config(profile)
        all_skills = config.primary_skills + config.secondary_skills + config.tertiary_skills
        assert set(all_skills) == {"python", "sql"}


# ---------------------------------------------------------------------------
# GitHub error handling + combined enrichment
# ---------------------------------------------------------------------------

class TestGitHubErrors:
    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self):
        async def mock_get(url, **kwargs):
            raise asyncio.TimeoutError("Timed out")
        session = AsyncMock()
        session.get = MagicMock(side_effect=lambda url, **kw: _async_context(mock_get(url, **kw)))
        result = await fetch_github_profile("testuser", session=session)
        assert result["repositories"] == []
        assert result["skills_inferred"] == []

    @pytest.mark.asyncio
    async def test_partial_language_fetch_failure(self):
        mock_repos = [
            {"name": "repo1", "language": "Python", "description": "", "stargazers_count": 1, "topics": [], "fork": False},
            {"name": "repo2", "language": "Go", "description": "", "stargazers_count": 1, "topics": [], "fork": False},
        ]

        async def mock_get(url, **kwargs):
            resp = AsyncMock()
            resp.status = 200
            if "repos?per_page" in url:
                resp.json = AsyncMock(return_value=mock_repos)
            elif "repo1/languages" in url:
                resp.json = AsyncMock(return_value={"Python": 50000})
            elif "repo2/languages" in url:
                resp.status = 500
                resp.json = AsyncMock(return_value=None)
                return resp
            return resp

        session = AsyncMock()
        session.get = MagicMock(side_effect=lambda url, **kw: _async_context(mock_get(url, **kw)))
        result = await fetch_github_profile("testuser", session=session)
        assert len(result["repositories"]) == 2
        assert "Python" in result["skills_inferred"]

    def test_double_enrich_github_no_dupes(self):
        cv = CVData(skills=["Python"])
        github_data = {
            "skills_inferred": ["TypeScript", "Docker"],
            "languages": {"TypeScript": 30000},
            "topics": ["docker"],
        }
        cv = enrich_cv_from_github(cv, github_data)
        assert len(cv.github_skills_inferred) == 2
        cv = enrich_cv_from_github(cv, github_data)
        assert len(cv.github_skills_inferred) == 2


class TestCombinedEnrichment:
    def test_linkedin_then_github_no_data_loss(self):
        cv = CVData(skills=["Python"])
        linkedin_data = {
            "skills": ["SQL"], "positions": [{"title": "Engineer", "company": "Co"}],
            "education": [{"school": "MIT", "degree": "MSc"}],
            "certifications": [{"name": "AWS"}],
            "industry": "Tech", "summary": "Hi",
        }
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        github_data = {
            "skills_inferred": ["TypeScript", "Docker"],
            "languages": {"TypeScript": 30000},
            "topics": ["docker"],
        }
        cv = enrich_cv_from_github(cv, github_data)
        assert cv.linkedin_skills == ["SQL"]
        assert cv.linkedin_industry == "Tech"
        assert "TypeScript" in cv.github_skills_inferred
        assert "Docker" in cv.github_skills_inferred
        assert "Engineer" in cv.job_titles

    def test_all_sources_skill_dedup_in_search_config(self):
        # Batch 2.3 — canonical (lower-case) skill assertion.
        profile = UserProfile(
            cv_data=CVData(
                raw_text="test",
                skills=["Python", "SQL"],
                linkedin_skills=["Python", "Docker"],
                github_skills_inferred=["Python", "SQL", "Go"],
            ),
            preferences=UserPreferences(target_job_titles=["Engineer"], additional_skills=["Python"]),
        )
        config = generate_search_config(profile)
        all_skills = config.primary_skills + config.secondary_skills + config.tertiary_skills
        assert all_skills.count("python") == 1
        assert all_skills.count("sql") == 1
        assert "docker" in all_skills
        assert "go" in all_skills


# ---------------------------------------------------------------------------
# Async context manager helper for mocking aiohttp
# ---------------------------------------------------------------------------

class _async_context:
    """Wrap a coroutine as an async context manager for aiohttp mocking."""
    def __init__(self, coro):
        self._coro = coro

    async def __aenter__(self):
        return await self._coro

    async def __aexit__(self, *args):
        pass
