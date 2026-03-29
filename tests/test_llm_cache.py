"""Tests for LLM response cache."""

from unittest.mock import patch

from src.llm.cache import cache_key, get_cached, set_cached


def test_cache_key_deterministic():
    """Same input always produces the same key."""
    k1 = cache_key("jd", "hello world")
    k2 = cache_key("jd", "hello world")
    assert k1 == k2


def test_cache_key_different_content():
    """Different content produces different keys."""
    k1 = cache_key("jd", "content A")
    k2 = cache_key("jd", "content B")
    assert k1 != k2


def test_cache_key_different_type():
    """Different prompt types produce different keys for same content."""
    k1 = cache_key("jd", "same text")
    k2 = cache_key("cv", "same text")
    assert k1 != k2


def test_set_and_get_cached(tmp_path):
    """Round-trip: store and retrieve."""
    with patch("src.llm.cache.LLM_CACHE_DIR", tmp_path):
        with patch("src.llm.cache._dir_created", False):
            data = {"skills": ["Python", "Docker"]}
            set_cached("testkey", data)
            result = get_cached("testkey")
            assert result == data


def test_get_cached_missing(tmp_path):
    """Returns None for uncached key."""
    with patch("src.llm.cache.LLM_CACHE_DIR", tmp_path):
        with patch("src.llm.cache._dir_created", False):
            assert get_cached("nonexistent") is None
