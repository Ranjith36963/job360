"""Tests for the multi-provider LLM client pool."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.llm.client import ProviderPool, is_configured, parse_json_response
from src.llm.providers import ProviderConfig


# ── Helpers ───────────────────────────────────────────────────────────


def _make_provider(name: str = "test", api_key: str = "k") -> ProviderConfig:
    return ProviderConfig(
        name=name,
        base_url="https://test.example.com/v1",
        model="test-model",
        api_key_env=f"{name.upper()}_API_KEY",
        rpm=30,
        priority=1,
    )


def _mock_openai_response(content: str = '{"skills": ["Python"]}'):
    """Create a mock that looks like openai.ChatCompletion response."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ── is_configured ─────────────────────────────────────────────────────


def test_is_configured_no_keys():
    with patch("src.llm.client.get_configured_providers", return_value=[]):
        assert is_configured() is False


def test_is_configured_with_key():
    with patch(
        "src.llm.client.get_configured_providers",
        return_value=[_make_provider()],
    ):
        assert is_configured() is True


# ── parse_json_response ──────────────────────────────────────────────


def test_parse_json_plain():
    data = parse_json_response('{"a": 1}')
    assert data == {"a": 1}


def test_parse_json_fenced():
    raw = '```json\n{"a": 1}\n```'
    data = parse_json_response(raw)
    assert data == {"a": 1}


def test_parse_json_fenced_no_lang():
    raw = '```\n{"a": 1}\n```'
    data = parse_json_response(raw)
    assert data == {"a": 1}


def test_parse_json_invalid():
    with pytest.raises(json.JSONDecodeError):
        parse_json_response("not json at all")


# ── ProviderPool ─────────────────────────────────────────────────────


@patch("src.llm.client.get_configured_providers")
def test_pool_complete_success(mock_providers):
    mock_providers.return_value = [_make_provider("groq")]
    pool = ProviderPool()

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_openai_response("result")

    with patch.object(pool, "_get_client", return_value=mock_client):
        result = pool.complete("test prompt")

    assert result == "result"
    mock_client.chat.completions.create.assert_called_once()


@patch("src.llm.client.get_configured_providers")
def test_pool_rotates_on_error(mock_providers):
    p1 = _make_provider("provider_a")
    p1.priority = 1
    p2 = _make_provider("provider_b")
    p2.priority = 2
    mock_providers.return_value = [p1, p2]

    pool = ProviderPool()

    fail_client = MagicMock()
    fail_client.chat.completions.create.side_effect = Exception("connection error")

    ok_client = MagicMock()
    ok_client.chat.completions.create.return_value = _mock_openai_response("ok")

    def _get_client(provider):
        return fail_client if provider.name == "provider_a" else ok_client

    with patch.object(pool, "_get_client", side_effect=_get_client):
        result = pool.complete("test")

    assert result == "ok"


@patch("src.llm.client.get_configured_providers")
def test_pool_cooldown_on_rate_limit(mock_providers):
    mock_providers.return_value = [_make_provider("groq"), _make_provider("cerebras")]
    pool = ProviderPool()

    rate_client = MagicMock()
    rate_client.chat.completions.create.side_effect = Exception("429 rate limit")

    ok_client = MagicMock()
    ok_client.chat.completions.create.return_value = _mock_openai_response("ok")

    call_count = {"n": 0}

    def _get_client(provider):
        call_count["n"] += 1
        return rate_client if provider.name == "groq" else ok_client

    with patch.object(pool, "_get_client", side_effect=_get_client):
        result = pool.complete("test")

    assert result == "ok"
    # Groq should be in cooldown now.
    assert "groq" in pool._cooldowns


@patch("src.llm.client.get_configured_providers")
def test_pool_returns_none_when_empty(mock_providers):
    mock_providers.return_value = []
    pool = ProviderPool()
    assert pool.complete("test") is None


@patch("src.llm.client.get_configured_providers")
def test_pool_status(mock_providers):
    mock_providers.return_value = [_make_provider("groq")]
    pool = ProviderPool()
    status = pool.status()
    assert "configured" in status
    assert "groq" in status["configured"]
    assert "cooldowns" in status


@patch("src.llm.client.get_configured_providers")
def test_pool_prefer_provider(mock_providers):
    p1 = _make_provider("groq")
    p1.priority = 1
    p2 = _make_provider("gemini")
    p2.priority = 3
    mock_providers.return_value = [p1, p2]

    pool = ProviderPool()

    used_providers = []

    def _get_client(provider):
        used_providers.append(provider.name)
        client = MagicMock()
        client.chat.completions.create.return_value = _mock_openai_response("ok")
        return client

    with patch.object(pool, "_get_client", side_effect=_get_client):
        result = pool.complete("test", prefer="gemini")

    assert result == "ok"
    # Gemini should be tried first when preferred.
    assert used_providers[0] == "gemini"
