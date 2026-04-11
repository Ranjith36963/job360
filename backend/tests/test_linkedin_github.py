"""Tests for LinkedIn ZIP parser and GitHub profile enricher (Phase 2)."""

import csv
import io
import json
import zipfile
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from src.services.profile.models import CVData, UserPreferences, UserProfile, SearchConfig
from src.services.profile.linkedin_parser import (
    parse_linkedin_zip,
    parse_linkedin_zip_from_bytes,
    enrich_cv_from_linkedin,
    _find_csv_in_zip,
    _parse_positions,
    _parse_skills,
    _parse_education,
    _parse_certifications,
    _parse_profile,
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
# Helpers — create LinkedIn ZIP in memory
# ---------------------------------------------------------------------------

def _make_csv_bytes(headers: list[str], rows: list[list[str]]) -> bytes:
    """Create CSV content as UTF-8-BOM bytes."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def _make_linkedin_zip(files: dict[str, bytes]) -> bytes:
    """Create an in-memory ZIP from a dict of filename -> csv bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _make_full_linkedin_zip() -> bytes:
    """Create a realistic LinkedIn ZIP with all CSVs."""
    positions = _make_csv_bytes(
        ["Company Name", "Title", "Started On", "Finished On", "Description"],
        [
            ["Google", "Software Engineer", "Jan 2020", "Dec 2022", "Built ML pipelines"],
            ["Meta", "Senior Engineer", "Jan 2023", "", "Leading AI team"],
        ],
    )
    skills = _make_csv_bytes(
        ["Name"],
        [["Python"], ["SQL"], ["Machine Learning"], ["Docker"]],
    )
    education = _make_csv_bytes(
        ["School Name", "Degree Name", "Start Date", "End Date", "Notes"],
        [["MIT", "MSc Computer Science", "2016", "2018", ""]],
    )
    certifications = _make_csv_bytes(
        ["Name", "Authority", "Started On", "Finished On"],
        [["AWS Solutions Architect", "Amazon", "2021", "2024"]],
    )
    profile = _make_csv_bytes(
        ["First Name", "Last Name", "Headline", "Summary", "Industry"],
        [["John", "Doe", "ML Engineer", "Experienced ML engineer", "Technology"]],
    )
    return _make_linkedin_zip({
        "Positions.csv": positions,
        "Skills.csv": skills,
        "Education.csv": education,
        "Certifications.csv": certifications,
        "Profile.csv": profile,
    })


# ---------------------------------------------------------------------------
# LinkedIn Parser — Unit Tests
# ---------------------------------------------------------------------------

class TestLinkedInParsePositions:
    def test_basic_positions(self):
        rows = [
            {"Title": "Engineer", "Company Name": "Google", "Started On": "2020", "Finished On": "2022", "Description": "Built stuff"},
            {"Title": "Manager", "Company Name": "Meta", "Started On": "2023", "Finished On": "", "Description": ""},
        ]
        positions = _parse_positions(rows)
        assert len(positions) == 2
        assert positions[0]["title"] == "Engineer"
        assert positions[0]["company"] == "Google"
        assert positions[1]["title"] == "Manager"

    def test_skips_empty_title(self):
        rows = [{"Title": "", "Company Name": "Google", "Started On": "", "Finished On": "", "Description": ""}]
        assert _parse_positions(rows) == []


class TestLinkedInParseSkills:
    def test_basic_skills(self):
        rows = [{"Name": "Python"}, {"Name": "SQL"}, {"Name": "Docker"}]
        skills = _parse_skills(rows)
        assert skills == ["Python", "SQL", "Docker"]

    def test_deduplicates_case_insensitive(self):
        rows = [{"Name": "Python"}, {"Name": "python"}, {"Name": "PYTHON"}]
        skills = _parse_skills(rows)
        assert len(skills) == 1
        assert skills[0] == "Python"

    def test_skips_empty(self):
        rows = [{"Name": ""}, {"Name": "Python"}]
        skills = _parse_skills(rows)
        assert skills == ["Python"]


class TestLinkedInParseEducation:
    def test_basic_education(self):
        rows = [{"School Name": "MIT", "Degree Name": "MSc CS", "Start Date": "2016", "End Date": "2018", "Notes": ""}]
        entries = _parse_education(rows)
        assert len(entries) == 1
        assert entries[0]["school"] == "MIT"
        assert entries[0]["degree"] == "MSc CS"


class TestLinkedInParseCertifications:
    def test_basic_certifications(self):
        rows = [{"Name": "AWS SA", "Authority": "Amazon", "Started On": "2021", "Finished On": "2024"}]
        certs = _parse_certifications(rows)
        assert len(certs) == 1
        assert certs[0]["name"] == "AWS SA"
        assert certs[0]["authority"] == "Amazon"

    def test_skips_empty_name(self):
        rows = [{"Name": "", "Authority": "Amazon", "Started On": "", "Finished On": ""}]
        assert _parse_certifications(rows) == []


class TestLinkedInParseProfile:
    def test_basic_profile(self):
        rows = [{"Summary": "I am an engineer", "Industry": "Technology", "Headline": "ML Engineer"}]
        profile = _parse_profile(rows)
        assert profile["summary"] == "I am an engineer"
        assert profile["industry"] == "Technology"
        assert profile["headline"] == "ML Engineer"

    def test_empty_rows(self):
        profile = _parse_profile([])
        assert profile["summary"] == ""
        assert profile["industry"] == ""


class TestLinkedInZipParsing:
    def test_parse_full_zip(self):
        zip_bytes = _make_full_linkedin_zip()
        data = parse_linkedin_zip_from_bytes(zip_bytes)
        assert len(data["positions"]) == 2
        assert len(data["skills"]) == 4
        assert "Python" in data["skills"]
        assert len(data["education"]) == 1
        assert len(data["certifications"]) == 1
        assert data["summary"] == "Experienced ML engineer"
        assert data["industry"] == "Technology"
        assert data["headline"] == "ML Engineer"

    def test_parse_zip_with_missing_csvs(self):
        """Should gracefully handle missing CSV files."""
        zip_bytes = _make_linkedin_zip({
            "Skills.csv": _make_csv_bytes(["Name"], [["Python"]]),
        })
        data = parse_linkedin_zip_from_bytes(zip_bytes)
        assert data["skills"] == ["Python"]
        assert data["positions"] == []
        assert data["education"] == []
        assert data["summary"] == ""

    def test_parse_zip_nested_directory(self):
        """LinkedIn sometimes puts CSVs in a subdirectory."""
        skills_csv = _make_csv_bytes(["Name"], [["React"], ["Node.js"]])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("Basic_LinkedInDataExport_01-01-2024/Skills.csv", skills_csv)
        data = parse_linkedin_zip_from_bytes(buf.getvalue())
        assert data["skills"] == ["React", "Node.js"]

    def test_parse_zip_file_path(self, tmp_path):
        """Test parsing from a file path."""
        zip_bytes = _make_full_linkedin_zip()
        path = tmp_path / "linkedin.zip"
        path.write_bytes(zip_bytes)
        data = parse_linkedin_zip(str(path))
        assert len(data["positions"]) == 2
        assert len(data["skills"]) == 4


class TestEnrichCVFromLinkedIn:
    def test_merges_skills(self):
        cv = CVData(skills=["Python", "Java"])
        linkedin_data = {"skills": ["Python", "SQL", "Docker"], "positions": [], "education": [], "certifications": []}
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        # Python deduped, SQL and Docker added to linkedin_skills
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
        # "Software Engineer" not duplicated
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


# ---------------------------------------------------------------------------
# GitHub Enricher — Unit Tests
# ---------------------------------------------------------------------------

class TestInferSkills:
    def test_languages_mapped(self):
        languages = {"Python": 50000, "JavaScript": 30000, "HCL": 10000}
        skills = _infer_skills(languages, set())
        assert skills[0] == "Python"  # highest bytes
        assert "JavaScript" in skills
        assert "Terraform" in skills  # HCL -> Terraform

    def test_topics_mapped(self):
        topics = {"react", "docker", "machine-learning"}
        skills = _infer_skills({}, topics)
        assert "React" in skills
        assert "Docker" in skills
        assert "Machine Learning" in skills

    def test_deduplicates_across_lang_and_topic(self):
        languages = {"Python": 50000}
        topics = {"docker"}
        # Dockerfile language also maps to Docker
        languages["Dockerfile"] = 5000
        skills = _infer_skills(languages, topics)
        docker_count = sum(1 for s in skills if s == "Docker")
        assert docker_count == 1

    def test_empty_inputs(self):
        assert _infer_skills({}, set()) == []

    def test_unknown_language_skipped(self):
        skills = _infer_skills({"COBOL": 1000}, set())
        assert skills == []

    def test_unknown_topic_skipped(self):
        skills = _infer_skills({}, {"some-random-topic"})
        assert skills == []


class TestFetchGitHubProfile:
    @pytest.mark.asyncio
    async def test_fetch_repos_and_languages(self):
        mock_repos = [
            {
                "name": "ml-project",
                "language": "Python",
                "description": "ML pipeline",
                "stargazers_count": 10,
                "topics": ["machine-learning", "pytorch"],
                "fork": False,
            },
            {
                "name": "web-app",
                "language": "TypeScript",
                "description": "React app",
                "stargazers_count": 5,
                "topics": ["react", "nextjs"],
                "fork": False,
            },
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
        # Python already in cv.skills, not duplicated
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
# Integration — Storage with new fields
# ---------------------------------------------------------------------------

class TestStorageWithNewFields:
    def test_roundtrip_with_linkedin_github_fields(self, tmp_path):
        cv = CVData(
            raw_text="My CV",
            skills=["Python"],
            linkedin_skills=["SQL", "Docker"],
            linkedin_positions=[{"title": "Engineer", "company": "Google"}],
            linkedin_industry="Technology",
            github_languages={"Python": 50000},
            github_topics=["machine-learning"],
            github_skills_inferred=["TypeScript"],
        )
        prefs = UserPreferences(
            target_job_titles=["ML Engineer"],
            github_username="testuser",
        )
        profile = UserProfile(cv_data=cv, preferences=prefs)

        with patch("src.services.profile.storage.PROFILE_PATH", tmp_path / "profile.json"):
            save_profile(profile)
            loaded = load_profile()
            assert loaded is not None
            assert loaded.cv_data.linkedin_skills == ["SQL", "Docker"]
            assert loaded.cv_data.linkedin_industry == "Technology"
            assert loaded.cv_data.github_languages == {"Python": 50000}
            assert loaded.cv_data.github_topics == ["machine-learning"]
            assert loaded.cv_data.github_skills_inferred == ["TypeScript"]
            assert loaded.preferences.github_username == "testuser"

    def test_load_old_profile_without_new_fields(self, tmp_path):
        """Old profiles without LinkedIn/GitHub fields should load fine."""
        old_profile_data = {
            "cv_data": {"raw_text": "Old CV", "skills": ["Java"], "job_titles": [],
                        "education": [], "certifications": [], "summary": ""},
            "preferences": {"target_job_titles": ["Developer"], "additional_skills": [],
                           "excluded_skills": [], "preferred_locations": [], "industries": [],
                           "salary_min": None, "salary_max": None, "work_arrangement": "",
                           "experience_level": "", "negative_keywords": [], "about_me": ""},
        }
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(old_profile_data), encoding="utf-8")
        with patch("src.services.profile.storage.PROFILE_PATH", path):
            loaded = load_profile()
            assert loaded is not None
            assert loaded.cv_data.skills == ["Java"]
            assert loaded.cv_data.linkedin_skills == []
            assert loaded.cv_data.github_skills_inferred == []
            assert loaded.preferences.github_username == ""

    def test_load_profile_with_unknown_keys(self, tmp_path):
        """Profile with extra keys from future versions shouldn't crash."""
        future_data = {
            "cv_data": {"raw_text": "CV", "skills": ["Python"], "future_field": "something"},
            "preferences": {"target_job_titles": ["Engineer"], "unknown_pref": True},
        }
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(future_data), encoding="utf-8")
        with patch("src.services.profile.storage.PROFILE_PATH", path):
            loaded = load_profile()
            assert loaded is not None
            assert loaded.cv_data.skills == ["Python"]


# ---------------------------------------------------------------------------
# Integration — Keyword generator with LinkedIn + GitHub data
# ---------------------------------------------------------------------------

class TestKeywordGeneratorWithEnrichedData:
    def test_linkedin_skills_in_search_config(self):
        profile = UserProfile(
            cv_data=CVData(
                raw_text="test",
                skills=["Python"],
                linkedin_skills=["SQL", "Docker"],
            ),
            preferences=UserPreferences(
                target_job_titles=["Data Engineer"],
                additional_skills=["Spark"],
            ),
        )
        config = generate_search_config(profile)
        all_skills = config.primary_skills + config.secondary_skills + config.tertiary_skills
        assert "Spark" in all_skills
        assert "Python" in all_skills
        assert "SQL" in all_skills
        assert "Docker" in all_skills

    def test_github_skills_in_search_config(self):
        profile = UserProfile(
            cv_data=CVData(
                raw_text="test",
                skills=["Python"],
                github_skills_inferred=["TypeScript", "React"],
            ),
            preferences=UserPreferences(
                target_job_titles=["Full Stack Developer"],
            ),
        )
        config = generate_search_config(profile)
        all_skills = config.primary_skills + config.secondary_skills + config.tertiary_skills
        assert "TypeScript" in all_skills
        assert "React" in all_skills

    def test_linkedin_positions_as_titles(self):
        profile = UserProfile(
            cv_data=CVData(
                raw_text="test",
                linkedin_positions=[
                    {"title": "Senior Engineer", "company": "Google"},
                    {"title": "Tech Lead", "company": "Meta"},
                ],
            ),
            preferences=UserPreferences(
                target_job_titles=["Software Engineer"],
            ),
        )
        config = generate_search_config(profile)
        assert "Software Engineer" in config.job_titles
        assert "Senior Engineer" in config.job_titles
        assert "Tech Lead" in config.job_titles

    def test_linkedin_industry_in_relevance_keywords(self):
        profile = UserProfile(
            cv_data=CVData(
                raw_text="test",
                linkedin_industry="Information Technology",
            ),
            preferences=UserPreferences(
                target_job_titles=["Engineer"],
            ),
        )
        config = generate_search_config(profile)
        assert "information" in config.relevance_keywords
        assert "technology" in config.relevance_keywords

    def test_deduplication_across_all_sources(self):
        profile = UserProfile(
            cv_data=CVData(
                raw_text="test",
                skills=["Python", "SQL"],
                linkedin_skills=["Python", "Docker"],
                github_skills_inferred=["Python", "SQL", "Go"],
            ),
            preferences=UserPreferences(
                target_job_titles=["Engineer"],
                additional_skills=["Python"],
            ),
        )
        config = generate_search_config(profile)
        all_skills = config.primary_skills + config.secondary_skills + config.tertiary_skills
        # Each skill should appear exactly once
        assert all_skills.count("Python") == 1
        assert all_skills.count("SQL") == 1
        assert "Docker" in all_skills
        assert "Go" in all_skills

    def test_empty_enrichment_fields_no_change(self):
        """When LinkedIn/GitHub fields are empty, behavior is same as Phase 1."""
        profile = UserProfile(
            cv_data=CVData(raw_text="test", skills=["Python", "SQL"]),
            preferences=UserPreferences(target_job_titles=["Engineer"]),
        )
        config = generate_search_config(profile)
        all_skills = config.primary_skills + config.secondary_skills + config.tertiary_skills
        assert set(all_skills) == {"Python", "SQL"}


# ---------------------------------------------------------------------------
# Async context manager helper for mocking aiohttp
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# LinkedIn ZIP Error Handling (BUG-3 regression tests)
# ---------------------------------------------------------------------------

class TestLinkedInZipErrors:
    def test_corrupt_zip_returns_empty_dict(self):
        """BadZipFile should be handled gracefully."""
        result = parse_linkedin_zip_from_bytes(b"not a valid zip file")
        assert result["positions"] == []
        assert result["skills"] == []
        assert result["summary"] == ""
        assert result["industry"] == ""

    def test_empty_bytes_returns_empty_dict(self):
        """Zero-length input should return empty data."""
        result = parse_linkedin_zip_from_bytes(b"")
        assert result["positions"] == []
        assert result["skills"] == []

    def test_corrupt_zip_file_path(self, tmp_path):
        """Corrupt ZIP on disk should return empty data."""
        path = tmp_path / "bad.zip"
        path.write_bytes(b"corrupt data here")
        result = parse_linkedin_zip(str(path))
        assert result["positions"] == []
        assert result["skills"] == []

    def test_csv_with_missing_columns(self):
        """Positions.csv without 'Title' column should produce empty positions."""
        csv_bytes = _make_csv_bytes(
            ["Company Name", "Started On"],
            [["Google", "2020"]],
        )
        zip_bytes = _make_linkedin_zip({"Positions.csv": csv_bytes})
        data = parse_linkedin_zip_from_bytes(zip_bytes)
        assert data["positions"] == []

    def test_double_enrich_linkedin_no_dupes(self):
        """Calling enrich twice should replace, not accumulate linkedin_skills."""
        cv = CVData(skills=["Python"])
        linkedin_data = {
            "skills": ["SQL", "Docker"],
            "positions": [{"title": "Engineer", "company": "Google"}],
            "education": [], "certifications": [],
            "industry": "Tech",
        }
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert len(cv.linkedin_skills) == 2
        # Enrich again with same data
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        # Should replace, not double up
        assert len(cv.linkedin_skills) == 2


# ---------------------------------------------------------------------------
# GitHub Error Handling (ROB-3 regression tests)
# ---------------------------------------------------------------------------

class TestGitHubErrors:
    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self):
        """Network timeout should return empty profile."""
        async def mock_get(url, **kwargs):
            raise asyncio.TimeoutError("Timed out")

        session = AsyncMock()
        session.get = MagicMock(side_effect=lambda url, **kw: _async_context(mock_get(url, **kw)))

        result = await fetch_github_profile("testuser", session=session)
        assert result["repositories"] == []
        assert result["skills_inferred"] == []

    @pytest.mark.asyncio
    async def test_partial_language_fetch_failure(self):
        """Some repo language fetches fail, others succeed."""
        mock_repos = [
            {"name": "repo1", "language": "Python", "description": "", "stargazers_count": 1, "topics": [], "fork": False},
            {"name": "repo2", "language": "Go", "description": "", "stargazers_count": 1, "topics": [], "fork": False},
        ]

        call_count = 0
        async def mock_get(url, **kwargs):
            nonlocal call_count
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
        # Python should still be inferred even though repo2 failed
        assert "Python" in result["skills_inferred"]

    def test_double_enrich_github_no_dupes(self):
        """Calling enrich twice should replace, not accumulate github_skills_inferred."""
        cv = CVData(skills=["Python"])
        github_data = {
            "skills_inferred": ["TypeScript", "Docker"],
            "languages": {"TypeScript": 30000},
            "topics": ["docker"],
        }
        cv = enrich_cv_from_github(cv, github_data)
        assert len(cv.github_skills_inferred) == 2
        # Enrich again
        cv = enrich_cv_from_github(cv, github_data)
        # Should replace, not double up
        assert len(cv.github_skills_inferred) == 2


# ---------------------------------------------------------------------------
# Combined Enrichment
# ---------------------------------------------------------------------------

class TestCombinedEnrichment:
    def test_linkedin_then_github_no_data_loss(self):
        """Both enrichers on same CVData should retain all data."""
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
        """CV + LinkedIn + GitHub skills should dedup in SearchConfig."""
        profile = UserProfile(
            cv_data=CVData(
                raw_text="test",
                skills=["Python", "SQL"],
                linkedin_skills=["Python", "Docker"],
                github_skills_inferred=["Python", "SQL", "Go"],
            ),
            preferences=UserPreferences(
                target_job_titles=["Engineer"],
                additional_skills=["Python"],
            ),
        )
        config = generate_search_config(profile)
        all_skills = config.primary_skills + config.secondary_skills + config.tertiary_skills
        assert all_skills.count("Python") == 1
        assert all_skills.count("SQL") == 1
        assert "Docker" in all_skills
        assert "Go" in all_skills


# ---------------------------------------------------------------------------
# Path traversal defense (ROB-4)
# ---------------------------------------------------------------------------

class TestPathTraversalDefense:
    def test_path_traversal_blocked(self):
        """ZIP entries with '..' should be skipped."""
        csv_bytes = _make_csv_bytes(["Name"], [["Python"]])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../etc/passwd", b"bad data")
            zf.writestr("Skills.csv", csv_bytes)
        data = parse_linkedin_zip_from_bytes(buf.getvalue())
        assert data["skills"] == ["Python"]

    def test_absolute_path_blocked(self):
        """ZIP entries starting with '/' should be skipped."""
        csv_bytes = _make_csv_bytes(["Name"], [["React"]])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("/etc/Skills.csv", csv_bytes)
            zf.writestr("Skills.csv", csv_bytes)
        data = parse_linkedin_zip_from_bytes(buf.getvalue())
        assert data["skills"] == ["React"]


# ---------------------------------------------------------------------------
# Async context manager helper for mocking aiohttp
# ---------------------------------------------------------------------------

import asyncio

class _async_context:
    """Wrap a coroutine as an async context manager for aiohttp mocking."""
    def __init__(self, coro):
        self._coro = coro

    async def __aenter__(self):
        return await self._coro

    async def __aexit__(self, *args):
        pass
