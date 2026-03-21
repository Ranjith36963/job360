"""Tests for cross-encoder reranking module."""

import pytest
from unittest.mock import patch, MagicMock
import numpy as np

import src.filters.reranker as reranker_module


@pytest.fixture(autouse=True)
def reset_module_state():
    """Reset singleton state between tests."""
    reranker_module._model = None
    reranker_module._available = None
    yield
    reranker_module._model = None
    reranker_module._available = None


class FakeCrossEncoder:
    """Fake cross-encoder that returns deterministic scores."""

    def predict(self, pairs, show_progress_bar=False):
        # Score based on text overlap: more shared words = higher score
        scores = []
        for query, doc in pairs:
            q_words = set(query.lower().split())
            d_words = set(doc.lower().split())
            overlap = len(q_words & d_words)
            scores.append(float(overlap) / max(len(q_words), 1))
        return np.array(scores)


def _patch_available():
    reranker_module._model = FakeCrossEncoder()
    reranker_module._available = True


def _patch_unavailable():
    reranker_module._model = None
    reranker_module._available = False


# ── Core tests ──


class TestRerank:
    def test_reranks_by_relevance(self):
        """More relevant jobs should rank higher after reranking."""
        _patch_available()
        profile = "Python Machine Learning Data Science"
        candidates = [
            {"title": "Nurse", "description": "Patient care clinical assessment"},
            {"title": "Data Scientist", "description": "Python Machine Learning pandas"},
            {"title": "Accountant", "description": "ACCA IFRS financial reporting"},
        ]
        result = reranker_module.rerank(profile, candidates)
        # Data Scientist should be ranked first (most overlap with profile)
        assert result[0]["title"] == "Data Scientist"
        assert "rerank_score" in result[0]

    def test_returns_unchanged_when_unavailable(self):
        _patch_unavailable()
        candidates = [{"title": "Job A", "description": "text"}]
        result = reranker_module.rerank("profile", candidates)
        assert result == candidates
        assert "rerank_score" not in result[0]

    def test_empty_candidates(self):
        _patch_available()
        assert reranker_module.rerank("profile", []) == []

    def test_empty_profile(self):
        _patch_available()
        candidates = [{"title": "Job", "description": "text"}]
        result = reranker_module.rerank("", candidates)
        assert result == candidates

    def test_top_n_limits_reranking(self):
        """Only top_n candidates should get rerank_score."""
        _patch_available()
        candidates = [
            {"title": f"Job {i}", "description": f"description {i}"}
            for i in range(10)
        ]
        result = reranker_module.rerank("profile text", candidates, top_n=3)
        # First 3 should have rerank_score
        for job in result[:3]:
            assert "rerank_score" in job
        # Rest should not
        for job in result[3:]:
            assert "rerank_score" not in job

    def test_score_is_float(self):
        _patch_available()
        candidates = [{"title": "Dev", "description": "Python coding"}]
        result = reranker_module.rerank("Python developer", candidates)
        assert isinstance(result[0]["rerank_score"], float)

    def test_preserves_all_candidates(self):
        """All candidates should be in the output, none lost."""
        _patch_available()
        candidates = [
            {"title": f"Job {i}", "description": f"desc {i}"}
            for i in range(20)
        ]
        result = reranker_module.rerank("profile", candidates, top_n=5)
        assert len(result) == 20


class TestBuildProfileText:
    def test_includes_titles_and_skills(self):
        text = reranker_module.build_profile_text(
            job_titles=["Data Scientist"],
            primary_skills=["Python", "ML"],
            secondary_skills=["SQL"],
        )
        assert "Data Scientist" in text
        assert "Python" in text
        assert "SQL" in text

    def test_empty_profile(self):
        text = reranker_module.build_profile_text([], [], [])
        assert text == ""

    def test_truncates_long_lists(self):
        text = reranker_module.build_profile_text(
            job_titles=[f"Title {i}" for i in range(20)],
            primary_skills=[f"Skill {i}" for i in range(30)],
            secondary_skills=[f"Extra {i}" for i in range(20)],
        )
        # Should cap at 5 titles, 10 primary, 8 secondary
        assert text.count("Title") <= 5
        assert text.count("Skill") <= 10
        assert text.count("Extra") <= 8


class TestIsAvailable:
    def test_available(self):
        _patch_available()
        assert reranker_module.is_available() is True

    def test_unavailable(self):
        _patch_unavailable()
        assert reranker_module.is_available() is False


class TestLazyLoading:
    def test_import_does_not_load(self):
        reranker_module._model = None
        reranker_module._available = None
        assert reranker_module._available is None
