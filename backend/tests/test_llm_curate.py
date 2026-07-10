"""LLM-curation passes: semantic dedup of education/roles + adjacent-skill
suggestions. All LLM calls are mocked (offline, rule #4)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.services.profile import llm_curate


@pytest.mark.asyncio
async def test_llm_merge_duplicates_collapses_same_entry_differently_worded():
    """The LLM recognises two phrasings of the SAME degree and returns ONE clean
    title. This is the reasoning the deterministic/fuzzy passes couldn't do
    safely (different wording, same qualification)."""
    async def fake(prompt, system=""):
        return {"items": ["MSc Artificial Intelligence & Robotics — University of Hertfordshire"]}

    items = [
        "Master of Science – Artificial Intelligence and Robotics — University of Hertfordshire",
        "Master's degree, Artificial Intelligence and Robotics - University of Hertfordshire",
    ]
    with patch("src.services.profile.llm_provider.llm_extract", new=fake):
        out = await llm_curate.llm_merge_duplicates(items, kind="education")
    assert out == ["MSc Artificial Intelligence & Robotics — University of Hertfordshire"]


@pytest.mark.asyncio
async def test_llm_merge_duplicates_noop_on_empty_or_single():
    """0 or 1 item → return as-is WITHOUT calling the LLM (cost guard)."""
    called = False

    async def fake(prompt, system=""):
        nonlocal called
        called = True
        return {"items": []}

    with patch("src.services.profile.llm_provider.llm_extract", new=fake):
        assert await llm_curate.llm_merge_duplicates([], "roles") == []
        assert await llm_curate.llm_merge_duplicates(["One role"], "roles") == ["One role"]
    assert called is False


@pytest.mark.asyncio
async def test_llm_merge_duplicates_graceful_on_llm_failure():
    """If the LLM errors, keep the ORIGINAL list — never lose data."""
    async def boom(prompt, system=""):
        raise RuntimeError("no key")

    items = ["A", "B"]
    with patch("src.services.profile.llm_provider.llm_extract", new=boom):
        assert await llm_curate.llm_merge_duplicates(items, "roles") == items


@pytest.mark.asyncio
async def test_llm_suggest_adjacent_skills_excludes_existing_and_returns_new():
    """Suggestions are skills ADJACENT to the user's, and never ones they already
    have (case-insensitive)."""
    async def fake(prompt, system=""):
        return {"suggestions": ["TensorFlow", "Keras", "JAX", "PyTorch"]}  # PyTorch already held

    with patch("src.services.profile.llm_provider.llm_extract", new=fake):
        out = await llm_curate.llm_suggest_adjacent_skills(["PyTorch", "Terraform"])
    assert "TensorFlow" in out and "Keras" in out
    assert "PyTorch" not in out          # already held → filtered out


@pytest.mark.asyncio
async def test_llm_suggest_adjacent_skills_empty_input_skips_llm():
    called = False

    async def fake(prompt, system=""):
        nonlocal called
        called = True
        return {"suggestions": ["X"]}

    with patch("src.services.profile.llm_provider.llm_extract", new=fake):
        assert await llm_curate.llm_suggest_adjacent_skills([]) == []
    assert called is False
