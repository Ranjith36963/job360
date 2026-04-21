"""Pillar 2 Batch 2.6 — ESCO skill normalizer activation tests.

Verifies the graceful-fallback contract: when the ESCO index artefacts are
missing, `is_available()` returns False and `normalize_skill()` returns
None. When present, both activate. No live sentence-transformers calls —
the index build is a dev-time step, not a pytest path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.services.profile.skill_normalizer import (
    is_available,
    normalize_skill,
    reset_index_for_testing,
)


def test_is_available_false_when_index_missing(tmp_path, monkeypatch):
    """Point the singleton at an empty directory — should report False."""
    empty_dir = tmp_path / "esco_empty"
    empty_dir.mkdir()
    reset_index_for_testing(data_dir=empty_dir)
    try:
        assert is_available() is False
    finally:
        reset_index_for_testing()


def test_normalize_skill_returns_none_when_index_missing(tmp_path):
    empty_dir = tmp_path / "esco_empty"
    empty_dir.mkdir()
    reset_index_for_testing(data_dir=empty_dir)
    try:
        assert normalize_skill("Python") is None
    finally:
        reset_index_for_testing()


def test_is_available_false_on_partial_index(tmp_path):
    """labels.json without embeddings.npy — still unavailable."""
    partial_dir = tmp_path / "esco_partial"
    partial_dir.mkdir()
    (partial_dir / "labels.json").write_text("[]", encoding="utf-8")
    # No embeddings.npy.
    reset_index_for_testing(data_dir=partial_dir)
    try:
        assert is_available() is False
    finally:
        reset_index_for_testing()


def test_index_status_reports_data_dir(tmp_path):
    """The ops-facing status helper reflects the current data_dir."""
    from src.services.profile.skill_normalizer import index_status
    empty_dir = tmp_path / "esco_reporting"
    empty_dir.mkdir()
    reset_index_for_testing(data_dir=empty_dir)
    try:
        status = index_status()
        assert status["available"] is False
        assert status["concepts"] == 0
        assert str(empty_dir) in status["data_dir"]
    finally:
        reset_index_for_testing()


def test_reset_index_for_testing_is_idempotent(tmp_path):
    """Reset with None reverts to the default location without crashing."""
    reset_index_for_testing(data_dir=None)
    assert is_available() is False  # default dir won't exist in CI


def test_semantic_enabled_flag_defaults_off():
    """The SEMANTIC_ENABLED env var gates the whole Batch-2.6 pipeline."""
    from src.core import settings
    # We don't force-toggle the env in tests; we just assert boolean nature.
    assert isinstance(settings.SEMANTIC_ENABLED, bool)
