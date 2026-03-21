"""Tests for feedback loop: preference vector + score adjustment."""

import numpy as np
import pytest

from src.filters.feedback import (
    build_preference_vector,
    compute_feedback_adjustment,
    _keyword_feedback,
    FEEDBACK_BONUS,
    FEEDBACK_PENALTY,
)


# ── Helper ──


def _make_embedding(seed: int, dim: int = 384) -> np.ndarray:
    """Deterministic L2-normalized embedding from seed."""
    rng = np.random.RandomState(seed)
    vec = rng.randn(dim).astype(np.float32)
    return vec / np.linalg.norm(vec)


# ── build_preference_vector ──


class TestBuildPreferenceVector:
    def test_no_embeddings_returns_none(self):
        signals = {"liked_embeddings": [], "rejected_embeddings": []}
        assert build_preference_vector(signals) is None

    def test_liked_only(self):
        emb = _make_embedding(42)
        signals = {"liked_embeddings": [emb], "rejected_embeddings": []}
        vec = build_preference_vector(signals)
        assert vec is not None
        assert vec.shape == (384,)
        # Should be L2-normalized
        assert abs(np.linalg.norm(vec) - 1.0) < 1e-5

    def test_rejected_only(self):
        emb = _make_embedding(99)
        signals = {"liked_embeddings": [], "rejected_embeddings": [emb]}
        vec = build_preference_vector(signals)
        assert vec is not None
        # Direction should be opposite to rejected
        assert float(np.dot(vec, emb)) < 0

    def test_liked_and_rejected(self):
        liked = _make_embedding(10)
        rejected = _make_embedding(20)
        signals = {"liked_embeddings": [liked], "rejected_embeddings": [rejected]}
        vec = build_preference_vector(signals)
        assert vec is not None
        # Should be closer to liked than to rejected
        sim_liked = float(np.dot(vec, liked))
        sim_rejected = float(np.dot(vec, rejected))
        assert sim_liked > sim_rejected

    def test_multiple_liked(self):
        embs = [_make_embedding(i) for i in range(5)]
        signals = {"liked_embeddings": embs, "rejected_embeddings": []}
        vec = build_preference_vector(signals)
        assert vec is not None
        assert abs(np.linalg.norm(vec) - 1.0) < 1e-5

    def test_cancellation_returns_none(self):
        """If liked and rejected average to zero vector, returns None."""
        emb = _make_embedding(42)
        # 0.5*emb - 0.5*emb = 0 → None
        signals = {"liked_embeddings": [emb * 0.5], "rejected_embeddings": [emb]}
        vec = build_preference_vector(signals)
        assert vec is None


# ── compute_feedback_adjustment ──


class TestComputeFeedbackAdjustment:
    def test_no_signals_returns_zero(self):
        signals = {"liked_texts": [], "rejected_texts": []}
        assert compute_feedback_adjustment("some job", signals) == 0

    def test_embedding_high_similarity_gives_bonus(self):
        pref = _make_embedding(42)
        # Job embedding very similar to preference
        job_emb = pref + np.random.RandomState(1).randn(384).astype(np.float32) * 0.01
        job_emb = job_emb / np.linalg.norm(job_emb)
        signals = {"liked_texts": ["x"], "rejected_texts": []}
        result = compute_feedback_adjustment("job", signals, pref, job_emb)
        assert result == FEEDBACK_BONUS

    def test_embedding_low_similarity_gives_penalty(self):
        pref = _make_embedding(42)
        # Job embedding opposite to preference
        job_emb = -pref
        signals = {"liked_texts": ["x"], "rejected_texts": []}
        result = compute_feedback_adjustment("job", signals, pref, job_emb)
        assert result == FEEDBACK_PENALTY

    def test_embedding_moderate_similarity(self):
        pref = _make_embedding(42)
        # Orthogonal = near zero similarity
        job_emb = _make_embedding(999)
        signals = {"liked_texts": ["x"], "rejected_texts": []}
        result = compute_feedback_adjustment("job", signals, pref, job_emb)
        assert FEEDBACK_PENALTY <= result <= FEEDBACK_BONUS

    def test_no_embeddings_falls_back_to_keywords(self):
        signals = {
            "liked_texts": ["Python Django REST API PostgreSQL backend developer"],
            "rejected_texts": [],
        }
        # Job text with high keyword overlap
        job_text = "Python Django REST API developer backend engineer PostgreSQL"
        result = compute_feedback_adjustment(job_text, signals)
        assert result > 0

    def test_return_type_is_int(self):
        signals = {"liked_texts": ["data science python"], "rejected_texts": []}
        result = compute_feedback_adjustment("data science", signals)
        assert isinstance(result, int)

    def test_bounds(self):
        """Result must always be in [-5, +5]."""
        pref = _make_embedding(42)
        for seed in range(20):
            job_emb = _make_embedding(seed)
            signals = {"liked_texts": ["x"], "rejected_texts": ["y"]}
            result = compute_feedback_adjustment("job", signals, pref, job_emb)
            assert FEEDBACK_PENALTY <= result <= FEEDBACK_BONUS


# ── _keyword_feedback ──


class TestKeywordFeedback:
    def test_liked_overlap(self):
        signals = {
            "liked_texts": ["Python Django REST API PostgreSQL backend developer senior"],
            "rejected_texts": [],
        }
        job_text = "Python Django REST API developer backend engineer PostgreSQL senior"
        result = _keyword_feedback(job_text, signals)
        assert result > 0

    def test_rejected_overlap(self):
        signals = {
            "liked_texts": [],
            "rejected_texts": ["receptionist admin filing data entry general office clerk"],
        }
        job_text = "receptionist admin filing data entry general office clerk assistant"
        result = _keyword_feedback(job_text, signals)
        assert result < 0

    def test_no_overlap(self):
        signals = {
            "liked_texts": ["Python Django REST API"],
            "rejected_texts": ["receptionist admin filing"],
        }
        job_text = "quantum physics nuclear reactor engineer"
        result = _keyword_feedback(job_text, signals)
        assert result == 0

    def test_equal_overlap_returns_zero(self):
        """When liked and rejected have equal overlap count → 0."""
        common = "Python Django REST API PostgreSQL backend developer senior engineer"
        signals = {"liked_texts": [common], "rejected_texts": [common]}
        result = _keyword_feedback(common, signals)
        assert result == 0

    def test_multiple_liked_texts(self):
        signals = {
            "liked_texts": [
                "Python Django REST API PostgreSQL backend developer",
                "Python Flask microservices API Docker Kubernetes deploy",
                "Python FastAPI async PostgreSQL Redis cache backend",
            ],
            "rejected_texts": [],
        }
        job_text = "Python Django REST API PostgreSQL backend developer engineer"
        result = _keyword_feedback(job_text, signals)
        assert result > 0
        assert result <= FEEDBACK_BONUS

    def test_capped_at_bonus(self):
        """Even many liked matches cap at FEEDBACK_BONUS."""
        signals = {
            "liked_texts": [
                f"word{i} word{i+1} word{i+2} word{i+3} word{i+4} word{i+5} extra"
                for i in range(0, 60, 6)
            ],
            "rejected_texts": [],
        }
        job_text = " ".join(f"word{i}" for i in range(100))
        result = _keyword_feedback(job_text, signals)
        assert result <= FEEDBACK_BONUS

    def test_capped_at_penalty(self):
        """Even many rejected matches cap at FEEDBACK_PENALTY."""
        signals = {
            "liked_texts": [],
            "rejected_texts": [
                f"word{i} word{i+1} word{i+2} word{i+3} word{i+4} word{i+5} extra"
                for i in range(0, 60, 6)
            ],
        }
        job_text = " ".join(f"word{i}" for i in range(100))
        result = _keyword_feedback(job_text, signals)
        assert result >= FEEDBACK_PENALTY


# ── load_feedback_signals (async) ──


class TestLoadFeedbackSignals:
    @pytest.mark.asyncio
    async def test_empty_db(self):
        """With no user_actions rows, returns empty signals."""
        from src.filters.feedback import load_feedback_signals
        from unittest.mock import AsyncMock

        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        signals = await load_feedback_signals(mock_conn)
        assert signals["liked_texts"] == []
        assert signals["rejected_texts"] == []
        assert signals["liked_embeddings"] == []
        assert signals["rejected_embeddings"] == []

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        """Database errors return empty signals gracefully."""
        from src.filters.feedback import load_feedback_signals
        from unittest.mock import AsyncMock

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=Exception("DB error"))

        signals = await load_feedback_signals(mock_conn)
        assert signals["liked_texts"] == []

    @pytest.mark.asyncio
    async def test_liked_and_rejected_rows(self):
        """Rows are correctly partitioned into liked/rejected."""
        from src.filters.feedback import load_feedback_signals
        from unittest.mock import AsyncMock

        rows = [
            ("Data Scientist", "Python ML pandas", None, "liked"),
            ("Receptionist", "Filing admin office", None, "not_interested"),
        ]
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=rows)
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        signals = await load_feedback_signals(mock_conn)
        assert len(signals["liked_texts"]) == 1
        assert "Data Scientist" in signals["liked_texts"][0]
        assert len(signals["rejected_texts"]) == 1
        assert "Receptionist" in signals["rejected_texts"][0]
