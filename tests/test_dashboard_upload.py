"""Tests for the dashboard CV upload flow.

Validates that uploading a CV via the web dashboard correctly extracts
a skills profile, persists it to disk, and that resetting removes it.
These are unit tests for the underlying pipeline — Streamlit widget
interactions are not tested here (they require st.testing or E2E tools).
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from src.cv_parser import (
    extract_text,
    extract_profile,
    save_profile,
    load_profile,
)
from src.filters.skill_matcher import reload_profile, _load_active_profile
from src.config.settings import CV_PROFILE_PATH


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_cv_text():
    return (
        "AI Engineer with 5 years of experience in Python, PyTorch, TensorFlow. "
        "Expertise in LangChain, RAG pipelines, and LLM fine-tuning. "
        "Worked extensively with AWS, Docker, Kubernetes, FastAPI. "
        "Familiar with CI/CD, MLflow, Git, and Linux administration. "
        "Based in London, UK. Open to Remote and Hybrid roles."
    )


@pytest.fixture
def sample_profile(sample_cv_text):
    profile = extract_profile(sample_cv_text)
    profile["source_file"] = "test_cv.pdf"
    return profile


@pytest.fixture
def profile_path(tmp_path):
    return tmp_path / "cv_profile.json"


@pytest.fixture(autouse=True)
def _isolate_scorer():
    """Ensure scorer cache is cleared before and after each test."""
    reload_profile()
    yield
    reload_profile()


# ---------------------------------------------------------------------------
# Profile extraction produces correct structure
# ---------------------------------------------------------------------------

class TestProfileExtraction:

    def test_extracts_skills(self, sample_profile):
        all_skills = (
            sample_profile["primary_skills"]
            + sample_profile["secondary_skills"]
            + sample_profile["tertiary_skills"]
        )
        assert "Python" in all_skills
        assert "PyTorch" in all_skills
        assert "TensorFlow" in all_skills
        assert "LangChain" in all_skills
        assert "RAG" in all_skills

    def test_extracts_secondary_level_skills(self, sample_profile):
        all_skills = (
            sample_profile["primary_skills"]
            + sample_profile["secondary_skills"]
            + sample_profile["tertiary_skills"]
        )
        assert "AWS" in all_skills
        assert "Docker" in all_skills
        assert "Kubernetes" in all_skills
        assert "FastAPI" in all_skills

    def test_extracts_tertiary_level_skills(self, sample_profile):
        all_skills = (
            sample_profile["primary_skills"]
            + sample_profile["secondary_skills"]
            + sample_profile["tertiary_skills"]
        )
        assert "CI/CD" in all_skills
        assert "MLflow" in all_skills
        assert "Git" in all_skills
        assert "Linux" in all_skills

    def test_extracts_locations(self, sample_profile):
        assert "London" in sample_profile["locations"]
        assert "UK" in sample_profile["locations"]
        assert "Remote" in sample_profile["locations"]
        assert "Hybrid" in sample_profile["locations"]

    def test_extracts_job_titles(self, sample_profile):
        assert "AI Engineer" in sample_profile["job_titles"]

    def test_has_timestamp(self, sample_profile):
        assert "extracted_at" in sample_profile
        datetime.fromisoformat(sample_profile["extracted_at"])

    def test_has_source_file(self, sample_profile):
        assert sample_profile["source_file"] == "test_cv.pdf"


# ---------------------------------------------------------------------------
# Save / load / delete lifecycle
# ---------------------------------------------------------------------------

class TestProfileLifecycle:

    def test_save_and_load(self, sample_profile, profile_path):
        save_profile(sample_profile, profile_path)
        loaded = load_profile(profile_path)
        assert loaded == sample_profile

    def test_overwrite_on_reupload(self, sample_profile, profile_path):
        save_profile(sample_profile, profile_path)

        new_text = (
            "Software Engineer with Java, Spring Boot, MySQL expertise. "
            "Based in Manchester."
        )
        new_profile = extract_profile(new_text)
        new_profile["source_file"] = "updated_cv.pdf"
        save_profile(new_profile, profile_path)

        loaded = load_profile(profile_path)
        assert loaded["source_file"] == "updated_cv.pdf"
        all_skills = (
            loaded["primary_skills"]
            + loaded["secondary_skills"]
            + loaded["tertiary_skills"]
        )
        # Python should NOT be in the new Java profile
        assert "Python" not in all_skills

    def test_delete_resets_to_none(self, sample_profile, profile_path):
        save_profile(sample_profile, profile_path)
        assert load_profile(profile_path) is not None

        profile_path.unlink()
        assert load_profile(profile_path) is None

    def test_load_nonexistent_returns_none(self, profile_path):
        assert load_profile(profile_path) is None


# ---------------------------------------------------------------------------
# Scorer integration — profile switching
# ---------------------------------------------------------------------------

class TestScorerProfileSwitching:

    def test_scorer_uses_uploaded_profile(self, sample_profile, tmp_path, monkeypatch):
        import src.cv_parser as cv_mod
        import src.filters.skill_matcher as sm

        path = tmp_path / "cv_profile.json"
        save_profile(sample_profile, path)
        monkeypatch.setattr(cv_mod, "CV_PROFILE_PATH", path)
        sm.reload_profile()

        active = sm._load_active_profile()
        # The active profile should use the CV's extracted skills
        all_active = (
            active["primary_skills"]
            + active["secondary_skills"]
            + active["tertiary_skills"]
        )
        all_sample = (
            sample_profile["primary_skills"]
            + sample_profile["secondary_skills"]
            + sample_profile["tertiary_skills"]
        )
        # Skills from the CV should appear in the active profile
        for skill in all_sample:
            assert skill in all_active

    def test_scorer_falls_back_after_delete(self, sample_profile, tmp_path, monkeypatch):
        import src.cv_parser as cv_mod
        import src.filters.skill_matcher as sm
        from src.config.keywords import PRIMARY_SKILLS

        path = tmp_path / "cv_profile.json"
        save_profile(sample_profile, path)
        monkeypatch.setattr(cv_mod, "CV_PROFILE_PATH", path)

        sm.reload_profile()
        active_with_cv = sm._load_active_profile()
        # Should have CV-derived skills
        all_cv_skills = (
            active_with_cv["primary_skills"]
            + active_with_cv["secondary_skills"]
            + active_with_cv["tertiary_skills"]
        )
        assert len(all_cv_skills) > 0

        path.unlink()
        sm.reload_profile()
        active_default = sm._load_active_profile()
        assert active_default["primary_skills"] == PRIMARY_SKILLS

    def test_scorer_updates_on_reupload(self, tmp_path, monkeypatch):
        import src.cv_parser as cv_mod
        import src.filters.skill_matcher as sm

        path = tmp_path / "cv_profile.json"
        monkeypatch.setattr(cv_mod, "CV_PROFILE_PATH", path)

        profile_v1 = extract_profile(
            "Python developer in London using Python and Django extensively."
        )
        profile_v1["source_file"] = "v1.pdf"
        save_profile(profile_v1, path)
        sm.reload_profile()
        v1_all = (
            sm._load_active_profile()["primary_skills"]
            + sm._load_active_profile()["secondary_skills"]
            + sm._load_active_profile()["tertiary_skills"]
        )
        assert "Python" in v1_all

        profile_v2 = extract_profile(
            "Java developer in Manchester using Java and Spring Boot extensively."
        )
        profile_v2["source_file"] = "v2.pdf"
        save_profile(profile_v2, path)
        sm.reload_profile()
        active = sm._load_active_profile()
        v2_all = (
            active["primary_skills"]
            + active["secondary_skills"]
            + active["tertiary_skills"]
        )
        assert "Java" in v2_all
        assert "Manchester" in active["locations"]


# ---------------------------------------------------------------------------
# Multi-user: different CVs produce different scoring
# ---------------------------------------------------------------------------

class TestMultiUserScoring:

    def test_java_cv_scores_java_jobs_higher(self):
        from datetime import datetime, timezone
        from src.models import Job
        from src.filters.skill_matcher import score_job

        java_profile = extract_profile(
            "Software Engineer\n\n"
            "Skills:\n"
            "Java, Spring Boot, MySQL, PostgreSQL, Docker, Kubernetes, Git\n\n"
            "Experience:\n"
            "Built microservices in Java using Spring Boot. Deployed with Docker.\n"
            "Location: Manchester, UK"
        )

        java_job = Job(
            title="Software Engineer",
            company="Test",
            apply_url="https://example.com",
            source="test",
            date_found=datetime.now(timezone.utc).isoformat(),
            location="Manchester",
            description="Java Spring Boot developer with MySQL and Docker",
        )

        ai_job = Job(
            title="AI Engineer",
            company="Test",
            apply_url="https://example.com",
            source="test",
            date_found=datetime.now(timezone.utc).isoformat(),
            location="London",
            description="Python PyTorch TensorFlow LangChain RAG Deep Learning",
        )

        java_score = score_job(java_job, profile=java_profile)
        ai_score = score_job(ai_job, profile=java_profile)
        assert java_score > ai_score

    def test_ai_cv_scores_ai_jobs_higher(self):
        from datetime import datetime, timezone
        from src.models import Job
        from src.filters.skill_matcher import score_job

        ai_profile = extract_profile(
            "AI Engineer\n\n"
            "Skills:\n"
            "Python, PyTorch, TensorFlow, LangChain, RAG, LLM, NLP\n\n"
            "Experience:\n"
            "Built RAG pipelines using LangChain and Python. Fine-tuned LLMs.\n"
            "Location: London, UK"
        )

        ai_job = Job(
            title="AI Engineer",
            company="Test",
            apply_url="https://example.com",
            source="test",
            date_found=datetime.now(timezone.utc).isoformat(),
            location="London",
            description="Python PyTorch TensorFlow LangChain RAG LLM NLP Deep Learning",
        )

        java_job = Job(
            title="Software Engineer",
            company="Test",
            apply_url="https://example.com",
            source="test",
            date_found=datetime.now(timezone.utc).isoformat(),
            location="Manchester",
            description="Java Spring Boot MySQL Docker Kubernetes microservices",
        )

        ai_score = score_job(ai_job, profile=ai_profile)
        java_score = score_job(java_job, profile=ai_profile)
        assert ai_score > java_score


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_empty_cv_produces_empty_profile(self):
        profile = extract_profile("")
        assert profile["primary_skills"] == []
        assert profile["secondary_skills"] == []
        assert profile["tertiary_skills"] == []
        assert profile["locations"] == []
        assert profile["job_titles"] == []

    def test_cv_with_no_matching_skills(self):
        text = "A brief biography of a historical figure with no tech background."
        profile = extract_profile(text)
        all_skills = (
            profile["primary_skills"]
            + profile["secondary_skills"]
            + profile["tertiary_skills"]
        )
        assert len(all_skills) == 0

    def test_extract_text_from_docx(self, tmp_path):
        from docx import Document
        doc = Document()
        doc.add_paragraph("Senior ML Engineer proficient in PyTorch and AWS.")
        path = tmp_path / "test.docx"
        doc.save(str(path))

        text = extract_text(str(path))
        assert "ML Engineer" in text
        assert "PyTorch" in text

    def test_profile_json_is_valid(self, sample_profile, profile_path):
        save_profile(sample_profile, profile_path)
        raw = profile_path.read_text()
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)
        assert "primary_skills" in parsed
