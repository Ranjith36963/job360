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

    def test_extracts_primary_skills(self, sample_profile):
        assert "Python" in sample_profile["primary_skills"]
        assert "PyTorch" in sample_profile["primary_skills"]
        assert "TensorFlow" in sample_profile["primary_skills"]
        assert "LangChain" in sample_profile["primary_skills"]
        assert "RAG" in sample_profile["primary_skills"]

    def test_extracts_secondary_skills(self, sample_profile):
        assert "AWS" in sample_profile["secondary_skills"]
        assert "Docker" in sample_profile["secondary_skills"]
        assert "Kubernetes" in sample_profile["secondary_skills"]
        assert "FastAPI" in sample_profile["secondary_skills"]
        assert "LLM fine-tuning" in sample_profile["secondary_skills"]

    def test_extracts_tertiary_skills(self, sample_profile):
        assert "CI/CD" in sample_profile["tertiary_skills"]
        assert "MLflow" in sample_profile["tertiary_skills"]
        assert "Git" in sample_profile["tertiary_skills"]
        assert "Linux" in sample_profile["tertiary_skills"]

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

        new_text = "Data Scientist with R, SQL, and Tableau expertise."
        new_profile = extract_profile(new_text)
        new_profile["source_file"] = "updated_cv.pdf"
        save_profile(new_profile, profile_path)

        loaded = load_profile(profile_path)
        assert loaded["source_file"] == "updated_cv.pdf"
        assert "Python" not in loaded["primary_skills"]

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
        assert active["primary_skills"] == sample_profile["primary_skills"]
        assert active["locations"] == sample_profile["locations"]

    def test_scorer_falls_back_after_delete(self, sample_profile, tmp_path, monkeypatch):
        import src.cv_parser as cv_mod
        import src.filters.skill_matcher as sm
        from src.config.keywords import PRIMARY_SKILLS

        path = tmp_path / "cv_profile.json"
        save_profile(sample_profile, path)
        monkeypatch.setattr(cv_mod, "CV_PROFILE_PATH", path)

        sm.reload_profile()
        active_with_cv = sm._load_active_profile()
        assert active_with_cv["primary_skills"] == sample_profile["primary_skills"]

        path.unlink()
        sm.reload_profile()
        active_default = sm._load_active_profile()
        assert active_default["primary_skills"] == PRIMARY_SKILLS

    def test_scorer_updates_on_reupload(self, tmp_path, monkeypatch):
        import src.cv_parser as cv_mod
        import src.filters.skill_matcher as sm

        path = tmp_path / "cv_profile.json"
        monkeypatch.setattr(cv_mod, "CV_PROFILE_PATH", path)

        profile_v1 = extract_profile("Python developer in London")
        profile_v1["source_file"] = "v1.pdf"
        save_profile(profile_v1, path)
        sm.reload_profile()
        assert "Python" in sm._load_active_profile()["primary_skills"]

        profile_v2 = extract_profile("Java developer in Manchester")
        profile_v2["source_file"] = "v2.pdf"
        save_profile(profile_v2, path)
        sm.reload_profile()
        active = sm._load_active_profile()
        assert "Python" not in active["primary_skills"]
        assert "Manchester" in active["locations"]


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
        text = "Marketing manager with SEO and Google Analytics experience."
        profile = extract_profile(text)
        assert profile["primary_skills"] == []
        assert profile["secondary_skills"] == []

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
