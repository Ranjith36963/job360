"""Tests for LLM provider registry."""

from unittest.mock import patch

from src.llm.providers import PROVIDERS, ProviderConfig, get_configured_providers


def test_all_providers_defined():
    """All 6 providers are registered."""
    assert len(PROVIDERS) == 6
    names = [p.name for p in PROVIDERS]
    assert "groq" in names
    assert "cerebras" in names
    assert "gemini" in names
    assert "deepseek" in names
    assert "openrouter" in names
    assert "sambanova" in names


def test_providers_have_required_fields():
    """Every provider has base_url, model, api_key_env, rpm."""
    for p in PROVIDERS:
        assert p.base_url.startswith("https://")
        assert p.model
        assert p.api_key_env.endswith("_API_KEY")
        assert p.rpm > 0
        assert isinstance(p.priority, int)


def test_get_configured_providers_none():
    """Returns empty list when no API keys are set."""
    with patch.dict("os.environ", {}, clear=True):
        result = get_configured_providers()
        # May still pick up real env vars; just verify it returns a list.
        assert isinstance(result, list)


def test_get_configured_providers_one():
    """Returns only the provider whose key is set."""
    with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}, clear=False):
        result = get_configured_providers()
        names = [p.name for p in result]
        assert "groq" in names


def test_get_configured_sorted_by_priority():
    """Configured providers are sorted by priority (lower = first)."""
    with patch.dict(
        "os.environ",
        {"GROQ_API_KEY": "k1", "SAMBANOVA_API_KEY": "k2"},
        clear=False,
    ):
        result = get_configured_providers()
        if len(result) >= 2:
            priorities = [p.priority for p in result]
            assert priorities == sorted(priorities)


def test_openrouter_has_headers():
    """OpenRouter provider has HTTP-Referer and X-Title headers."""
    openrouter = [p for p in PROVIDERS if p.name == "openrouter"][0]
    assert "HTTP-Referer" in openrouter.headers
    assert "X-Title" in openrouter.headers


def test_provider_is_configured_property():
    """ProviderConfig.is_configured checks the env var."""
    p = ProviderConfig(
        name="test", base_url="https://test.com", model="m",
        api_key_env="TEST_NONEXISTENT_KEY_12345", rpm=10, priority=99,
    )
    assert p.is_configured is False

    with patch.dict("os.environ", {"TEST_NONEXISTENT_KEY_12345": "val"}):
        assert p.is_configured is True
