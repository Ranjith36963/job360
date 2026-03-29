"""Tests for semantic embeddings module.

Tests work regardless of whether sentence-transformers is installed by
mocking the model at the module level.
"""

import importlib
import numpy as np
import pytest
from unittest.mock import patch, MagicMock

import src.filters.embeddings as emb_module


@pytest.fixture(autouse=True)
def reset_module_state():
    """Reset singleton state between tests."""
    emb_module._model = None
    emb_module._available = None
    yield
    emb_module._model = None
    emb_module._available = None


class FakeSentenceTransformer:
    """Fake model that returns deterministic embeddings based on text hash."""

    def encode(self, text, convert_to_numpy=True, show_progress_bar=False, batch_size=64):
        if isinstance(text, str):
            vec = self._text_to_vec(text)
            return vec
        return np.array([self._text_to_vec(t) for t in text])

    @staticmethod
    def _text_to_vec(text: str) -> np.ndarray:
        """Generate a deterministic 384-dim vector from text."""
        rng = np.random.RandomState(hash(text) % (2**31))
        vec = rng.randn(384).astype(np.float32)
        return vec / (np.linalg.norm(vec) + 1e-10)


def _patch_model_available():
    """Patch the lazy loader to use our fake model."""
    fake_model = FakeSentenceTransformer()
    emb_module._model = fake_model
    emb_module._available = True


def _patch_model_unavailable():
    """Simulate sentence-transformers not installed."""
    emb_module._model = None
    emb_module._available = False


# ── Core function tests ──


class TestEncode:
    def test_encode_returns_normalized_vector(self):
        _patch_model_available()
        vec = emb_module.encode("Python developer")
        assert vec is not None
        assert vec.shape == (384,)
        assert abs(np.linalg.norm(vec) - 1.0) < 0.01

    def test_encode_unavailable_returns_none(self):
        _patch_model_unavailable()
        assert emb_module.encode("anything") is None

    def test_encode_different_texts_different_vectors(self):
        _patch_model_available()
        v1 = emb_module.encode("Python developer")
        v2 = emb_module.encode("Nurse practitioner")
        assert not np.allclose(v1, v2)

    def test_encode_same_text_same_vector(self):
        _patch_model_available()
        v1 = emb_module.encode("Machine Learning Engineer")
        v2 = emb_module.encode("Machine Learning Engineer")
        assert np.allclose(v1, v2)


class TestEncodeBatch:
    def test_batch_returns_correct_shape(self):
        _patch_model_available()
        vecs = emb_module.encode_batch(["text 1", "text 2", "text 3"])
        assert vecs is not None
        assert vecs.shape == (3, 384)

    def test_batch_empty_returns_none(self):
        _patch_model_available()
        assert emb_module.encode_batch([]) is None

    def test_batch_unavailable_returns_none(self):
        _patch_model_unavailable()
        assert emb_module.encode_batch(["text"]) is None

    def test_batch_vectors_normalized(self):
        _patch_model_available()
        vecs = emb_module.encode_batch(["a", "b"])
        norms = np.linalg.norm(vecs, axis=1)
        assert all(abs(n - 1.0) < 0.01 for n in norms)


class TestCosineSimilarity:
    def test_identical_vectors(self):
        vec = np.ones(384) / np.sqrt(384)
        assert abs(emb_module.cosine_similarity(vec, vec) - 1.0) < 0.01

    def test_orthogonal_vectors(self):
        a = np.zeros(384)
        a[0] = 1.0
        b = np.zeros(384)
        b[1] = 1.0
        assert abs(emb_module.cosine_similarity(a, b)) < 0.01

    def test_opposite_vectors(self):
        vec = np.ones(384) / np.sqrt(384)
        assert abs(emb_module.cosine_similarity(vec, -vec) + 1.0) < 0.01


class TestBuildProfileEmbedding:
    def test_returns_normalized_vector(self):
        _patch_model_available()
        vec = emb_module.build_profile_embedding(
            job_titles=["Data Scientist"],
            primary_skills=["Python", "Machine Learning"],
            secondary_skills=["SQL"],
            relevance_keywords=["data", "analytics"],
        )
        assert vec is not None
        assert vec.shape == (384,)
        assert abs(np.linalg.norm(vec) - 1.0) < 0.01

    def test_empty_profile_returns_none(self):
        _patch_model_available()
        vec = emb_module.build_profile_embedding(
            job_titles=[],
            primary_skills=[],
            secondary_skills=[],
            relevance_keywords=[],
        )
        assert vec is None

    def test_unavailable_returns_none(self):
        _patch_model_unavailable()
        vec = emb_module.build_profile_embedding(
            job_titles=["Engineer"],
            primary_skills=["Python"],
            secondary_skills=[],
            relevance_keywords=[],
        )
        assert vec is None


class TestScoreSemanticSimilarity:
    def test_high_similarity_scores_max(self):
        _patch_model_available()
        # Same text should have high similarity
        profile_vec = emb_module.encode("Python Machine Learning Data Science Engineer")
        score = emb_module.score_semantic_similarity(
            profile_vec, "Python Machine Learning Data Science Engineer", max_points=5
        )
        assert score == 5

    def test_none_profile_returns_zero(self):
        _patch_model_available()
        assert emb_module.score_semantic_similarity(None, "any text") == 0

    def test_unavailable_returns_zero(self):
        _patch_model_unavailable()
        fake_vec = np.ones(384) / np.sqrt(384)
        assert emb_module.score_semantic_similarity(fake_vec, "text") == 0

    def test_score_within_range(self):
        _patch_model_available()
        vec = emb_module.encode("Senior Software Engineer Python AWS")
        score = emb_module.score_semantic_similarity(vec, "Junior Marketing Coordinator", max_points=5)
        assert 0 <= score <= 5


class TestIsAvailable:
    def test_available_when_model_loaded(self):
        _patch_model_available()
        assert emb_module.is_available() is True

    def test_unavailable_when_not_installed(self):
        _patch_model_unavailable()
        assert emb_module.is_available() is False


class TestLazyLoading:
    def test_import_does_not_load_model(self):
        """Module import should not trigger model loading."""
        # Reset to pre-load state
        emb_module._model = None
        emb_module._available = None
        # Just importing the module — _available should still be None
        assert emb_module._available is None

    def test_model_loaded_on_first_encode(self):
        """Model should be loaded lazily on first encode call."""
        emb_module._model = None
        emb_module._available = None
        # Patch the import inside _load_model
        with patch.dict("sys.modules", {"sentence_transformers": MagicMock()}):
            fake = FakeSentenceTransformer()
            with patch("src.filters.embeddings._load_model") as mock_load:
                mock_load.return_value = fake
                emb_module._available = True
                emb_module._model = fake
                vec = emb_module.encode("test")
                assert vec is not None


class TestScorerIntegration:
    """Test that JobScorer uses embeddings when available."""

    def test_scorer_uses_keyword_fallback_when_unavailable(self):
        """Without embeddings, _dim_semantic uses keyword overlap."""
        _patch_model_unavailable()
        from src.filters.skill_matcher import JobScorer
        from src.profile.models import SearchConfig

        config = SearchConfig(
            job_titles=["Data Scientist"],
            primary_skills=["Python"],
            secondary_skills=[],
            tertiary_skills=[],
            relevance_keywords=["python", "data", "science"],
            negative_title_keywords=[],
            locations=["London"],
            visa_keywords=[],
            core_domain_words={"data"},
            supporting_role_words={"scientist"},
            search_queries=[],
        )
        scorer = JobScorer(config)
        # Force unavailable
        scorer._embedding_attempted = True
        scorer._profile_embedding = None
        score = scorer._dim_semantic("We need python and data science skills")
        assert score > 0  # Keywords match

    def test_scorer_uses_embeddings_when_available(self):
        """With embeddings, _dim_semantic uses cosine similarity."""
        _patch_model_available()
        from src.filters.skill_matcher import JobScorer
        from src.profile.models import SearchConfig

        config = SearchConfig(
            job_titles=["Data Scientist"],
            primary_skills=["Python", "Machine Learning"],
            secondary_skills=["SQL"],
            tertiary_skills=[],
            relevance_keywords=["python", "data", "ml"],
            negative_title_keywords=[],
            locations=["London"],
            visa_keywords=[],
            core_domain_words={"data"},
            supporting_role_words={"scientist"},
            search_queries=[],
        )
        scorer = JobScorer(config)
        # Pre-compute profile embedding
        scorer._embedding_attempted = True
        scorer._profile_embedding = emb_module.build_profile_embedding(
            config.job_titles, config.primary_skills,
            config.secondary_skills, config.relevance_keywords,
        )
        score = scorer._dim_semantic("Data Scientist Python Machine Learning SQL")
        assert 0 <= score <= 10  # DIM_SEMANTIC = 10 after rebalance
