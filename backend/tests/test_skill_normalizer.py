"""Batch 1.3b (Pillar 1) — ESCO-backed skill normalizer tests.

Exercises the lookup logic with a fake embedding matrix + labels
(no sentence-transformers / real ESCO data needed). The encoder
itself is monkey-patched to return a vector the test chooses.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# numpy lives only in the optional `semantic` extra (pyproject). It used to
# arrive transitively via scikit-learn; the cleanup audit (#503) removed that,
# so a plain `pip install .[dev]` — what CI does — has no numpy. The normalizer
# itself is disabled without it (skill_normalizer._load), so skipping is the
# honest verdict, not a hidden failure.
np = pytest.importorskip("numpy")

from src.services.profile import skill_normalizer  # noqa: E402
from src.services.profile.skill_normalizer import (  # noqa: E402
    ESCOMatch,
    index_status,
    normalize_skill,
    reset_index_for_testing,
)


@pytest.fixture
def fake_esco_dir(tmp_path: Path) -> Path:
    """Materialise a tiny ESCO index on disk and reset the module singleton."""
    labels = [
        {"uri": "http://esco.example/python", "label": "Python programming", "alt_labels": []},
        {"uri": "http://esco.example/rust", "label": "Rust programming", "alt_labels": []},
        {"uri": "http://esco.example/nursing", "label": "Clinical nursing", "alt_labels": []},
    ]
    # Construct orthogonal unit vectors per label so tests can steer the
    # cosine argmax deterministically.
    embeddings = np.array(
        [
            [1.0, 0.0, 0.0],  # "python"
            [0.0, 1.0, 0.0],  # "rust"
            [0.0, 0.0, 1.0],  # "nursing"
        ],
        dtype="float32",
    )
    esco_dir = tmp_path / "esco"
    esco_dir.mkdir()
    (esco_dir / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    np.save(esco_dir / "embeddings.npy", embeddings)
    reset_index_for_testing(esco_dir)
    yield esco_dir
    reset_index_for_testing()  # restore default singleton for later tests


# ── unavailable paths ───────────────────────────────────────────────


def test_index_status_absent_when_no_data_dir(tmp_path):
    reset_index_for_testing(tmp_path / "missing")
    status = index_status()
    assert status["available"] is False
    assert status["concepts"] == 0
    reset_index_for_testing()


def test_normalize_returns_none_when_index_absent(tmp_path):
    reset_index_for_testing(tmp_path / "missing")
    assert normalize_skill("Python") is None
    reset_index_for_testing()


def test_normalize_returns_none_on_empty_input(fake_esco_dir):
    assert normalize_skill("") is None
    assert normalize_skill("   ") is None


def test_normalize_returns_none_when_encoder_unavailable(fake_esco_dir):
    """Index loaded but no sentence-transformers → None, not crash."""
    with patch.object(skill_normalizer._INDEX, "_get_encoder", return_value=None):
        assert normalize_skill("Python") is None


# ── matching paths ──────────────────────────────────────────────────


def _fake_encoder(query_vec: list[float]):
    """Return a fake encoder whose ``.encode`` returns ``query_vec``."""
    enc = MagicMock()
    enc.encode = MagicMock(return_value=np.array([query_vec], dtype="float32"))
    return enc


def test_normalize_returns_closest_label_above_threshold(fake_esco_dir):
    """A query aligned with the python row should match python."""
    with patch.object(skill_normalizer._INDEX, "_get_encoder",
                      return_value=_fake_encoder(np.array([0.99, 0.01, 0.0], dtype="float32"))):
        match = normalize_skill("Py")
    assert match is not None
    assert isinstance(match, ESCOMatch)
    assert match.uri.endswith("python")
    assert match.label == "Python programming"
    assert match.similarity > 0.9


def test_normalize_picks_rust_when_query_aligned_with_rust(fake_esco_dir):
    with patch.object(skill_normalizer._INDEX, "_get_encoder",
                      return_value=_fake_encoder(np.array([0.05, 0.99, 0.0], dtype="float32"))):
        match = normalize_skill("Rust lang")
    assert match is not None
    assert match.uri.endswith("rust")
    assert match.label == "Rust programming"


def test_normalize_below_threshold_returns_none(fake_esco_dir):
    """A query spread evenly across all dims → every cosine ≈ 0.57 — but
    since ``_MIN_COSINE_SIMILARITY = 0.55`` it passes. Pull below with a
    smaller mixing so no single row crosses the threshold.
    """
    with patch.object(skill_normalizer._INDEX, "_get_encoder",
                      return_value=_fake_encoder(np.array([0.3, 0.3, 0.3], dtype="float32"))):
        match = normalize_skill("something weird")
    assert match is None


def test_index_status_reports_available_and_concept_count(fake_esco_dir):
    status = index_status()
    assert status["available"] is True
    assert status["concepts"] == 3
    assert "esco" in status["data_dir"]


# ── defensive loading ───────────────────────────────────────────────


def test_mismatched_labels_embeddings_disables_index(tmp_path: Path):
    """Labels have 2 rows but embeddings have 3 — index should refuse to load."""
    (tmp_path / "esco").mkdir()
    (tmp_path / "esco" / "labels.json").write_text(
        json.dumps([{"uri": "x", "label": "x", "alt_labels": []},
                    {"uri": "y", "label": "y", "alt_labels": []}]),
        encoding="utf-8",
    )
    np.save(tmp_path / "esco" / "embeddings.npy",
            np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype="float32"))
    reset_index_for_testing(tmp_path / "esco")
    status = index_status()
    assert status["available"] is False
    reset_index_for_testing()


def test_corrupt_labels_json_disables_index(tmp_path: Path):
    (tmp_path / "esco").mkdir()
    (tmp_path / "esco" / "labels.json").write_text("not json", encoding="utf-8")
    np.save(tmp_path / "esco" / "embeddings.npy", np.array([[1.0]], dtype="float32"))
    reset_index_for_testing(tmp_path / "esco")
    status = index_status()
    assert status["available"] is False
    reset_index_for_testing()
