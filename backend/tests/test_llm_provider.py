"""Tests for LLM provider pool."""
import logging
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_llm_extract_no_keys_raises():
    """Should raise RuntimeError when no API keys are configured."""
    with patch("src.services.profile.llm_provider.GEMINI_API_KEY", ""), \
         patch("src.services.profile.llm_provider.GROQ_API_KEY", ""), \
         patch("src.services.profile.llm_provider.CEREBRAS_API_KEY", ""):
        from src.services.profile.llm_provider import llm_extract
        with pytest.raises(RuntimeError, match="No LLM API key configured"):
            await llm_extract("test prompt")


@pytest.mark.asyncio
async def test_llm_extract_gemini_success():
    """Should call Gemini when key is available and return parsed JSON."""
    mock_result = {"skills": ["Python", "Docker"]}
    with patch("src.services.profile.llm_provider.GEMINI_API_KEY", "fake-key"), \
         patch("src.services.profile.llm_provider._call_gemini", new_callable=AsyncMock, return_value=mock_result):
        from src.services.profile.llm_provider import llm_extract
        result = await llm_extract("test prompt")
    assert result == {"skills": ["Python", "Docker"]}


@pytest.mark.asyncio
async def test_llm_extract_gemini_fails_groq_succeeds():
    """Should fallback to Groq when Gemini fails."""
    mock_result = {"skills": ["Nursing", "Patient Care"]}
    with patch("src.services.profile.llm_provider.GEMINI_API_KEY", "fake-key"), \
         patch("src.services.profile.llm_provider.GROQ_API_KEY", "fake-key"), \
         patch("src.services.profile.llm_provider._call_gemini", new_callable=AsyncMock, side_effect=Exception("Gemini down")), \
         patch("src.services.profile.llm_provider._call_groq", new_callable=AsyncMock, return_value=mock_result):
        from src.services.profile.llm_provider import llm_extract
        result = await llm_extract("test prompt")
    assert result == {"skills": ["Nursing", "Patient Care"]}


@pytest.mark.asyncio
async def test_llm_extract_all_fail_raises():
    """Should raise RuntimeError when all providers fail."""
    with patch("src.services.profile.llm_provider.GEMINI_API_KEY", "fake-key"), \
         patch("src.services.profile.llm_provider.GROQ_API_KEY", "fake-key"), \
         patch("src.services.profile.llm_provider.CEREBRAS_API_KEY", ""), \
         patch("src.services.profile.llm_provider._call_gemini", new_callable=AsyncMock, side_effect=Exception("down")), \
         patch("src.services.profile.llm_provider._call_groq", new_callable=AsyncMock, side_effect=Exception("also down")):
        from src.services.profile.llm_provider import llm_extract
        with pytest.raises(RuntimeError, match="All LLM providers failed"):
            await llm_extract("test prompt")


@pytest.mark.asyncio
async def test_llm_extract_cerebras_success():
    """Should call Cerebras when Gemini and Groq keys are unset."""
    mock_result = {"skills": ["Nursing", "Patient Care"]}
    with patch("src.services.profile.llm_provider.GEMINI_API_KEY", ""), \
         patch("src.services.profile.llm_provider.GROQ_API_KEY", ""), \
         patch("src.services.profile.llm_provider.CEREBRAS_API_KEY", "fake-key"), \
         patch("src.services.profile.llm_provider._call_cerebras", new_callable=AsyncMock, return_value=mock_result):
        from src.services.profile.llm_provider import llm_extract
        result = await llm_extract("test prompt")
    assert result == mock_result


@pytest.mark.asyncio
async def test_llm_extract_fast_prefers_cerebras():
    """llm_extract_fast should try Cerebras FIRST (fastest for bulk scoring)."""
    mock_result = {"score": 85}
    with patch("src.services.profile.llm_provider.GEMINI_API_KEY", "fake-key"), \
         patch("src.services.profile.llm_provider.GROQ_API_KEY", "fake-key"), \
         patch("src.services.profile.llm_provider.CEREBRAS_API_KEY", "fake-key"), \
         patch("src.services.profile.llm_provider._call_cerebras", new_callable=AsyncMock, return_value=mock_result) as cerebras_mock, \
         patch("src.services.profile.llm_provider._call_gemini", new_callable=AsyncMock) as gemini_mock, \
         patch("src.services.profile.llm_provider._call_groq", new_callable=AsyncMock) as groq_mock:
        from src.services.profile.llm_provider import llm_extract_fast
        result = await llm_extract_fast("score this")
    assert result == mock_result
    cerebras_mock.assert_called_once()
    gemini_mock.assert_not_called()
    groq_mock.assert_not_called()


@pytest.mark.asyncio
async def test_llm_extract_fast_falls_back_to_groq():
    """llm_extract_fast should fall back to Groq when Cerebras fails."""
    mock_result = {"score": 72}
    with patch("src.services.profile.llm_provider.CEREBRAS_API_KEY", "fake"), \
         patch("src.services.profile.llm_provider.GROQ_API_KEY", "fake"), \
         patch("src.services.profile.llm_provider.GEMINI_API_KEY", ""), \
         patch("src.services.profile.llm_provider._call_cerebras", new_callable=AsyncMock, side_effect=Exception("cerebras down")), \
         patch("src.services.profile.llm_provider._call_groq", new_callable=AsyncMock, return_value=mock_result):
        from src.services.profile.llm_provider import llm_extract_fast
        result = await llm_extract_fast("score this")
    assert result == mock_result


@pytest.mark.asyncio
async def test_llm_extract_fast_all_keys_missing_raises():
    """llm_extract_fast should raise when no keys configured."""
    with patch("src.services.profile.llm_provider.CEREBRAS_API_KEY", ""), \
         patch("src.services.profile.llm_provider.GROQ_API_KEY", ""), \
         patch("src.services.profile.llm_provider.GEMINI_API_KEY", ""):
        from src.services.profile.llm_provider import llm_extract_fast
        with pytest.raises(RuntimeError, match="No LLM API key configured"):
            await llm_extract_fast("test")


# ---------------------------------------------------------------------------
# Task 3 — structured LLM call logging tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_gemini_logs_structured_info_on_success(caplog):
    """_call_gemini emits a structured 'llm_call' INFO record with latency+outcome."""
    import google.generativeai as genai

    mock_response = MagicMock()
    mock_response.text = '{"skills": ["Python"]}'
    mock_response.usage_metadata = MagicMock(total_token_count=42)

    mock_model = MagicMock()
    mock_model.generate_content_async = AsyncMock(return_value=mock_response)

    with patch("src.services.profile.llm_provider.GEMINI_API_KEY", "fake"), \
         patch.object(genai, "configure"), \
         patch.object(genai, "GenerativeModel", return_value=mock_model), \
         caplog.at_level(logging.INFO, logger="job360.profile.llm_provider"):
        from src.services.profile.llm_provider import _call_gemini
        await _call_gemini("prompt", "")

    records = [r for r in caplog.records if r.getMessage() == "llm_call"]
    assert len(records) == 1
    rec = records[0]
    assert rec.provider == "gemini"  # type: ignore[attr-defined]
    assert rec.outcome == "ok"  # type: ignore[attr-defined]
    assert isinstance(rec.latency_ms, int)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_call_groq_logs_structured_warning_on_error(caplog):
    """_call_groq emits a structured 'llm_call_error' WARNING when the API raises."""
    with patch("src.services.profile.llm_provider.GROQ_API_KEY", "fake"), \
         caplog.at_level(logging.WARNING, logger="job360.profile.llm_provider"):
        from groq import AsyncGroq
        with patch.object(AsyncGroq, "__init__", return_value=None), \
             patch.object(AsyncGroq, "chat", new_callable=MagicMock) as mock_chat:
            mock_chat.completions = MagicMock()
            mock_chat.completions.create = AsyncMock(side_effect=RuntimeError("quota"))
            from src.services.profile.llm_provider import _call_groq
            with pytest.raises(RuntimeError, match="quota"):
                await _call_groq("prompt", "")

    records = [r for r in caplog.records if r.getMessage() == "llm_call_error"]
    assert len(records) == 1
    rec = records[0]
    assert rec.provider == "groq"  # type: ignore[attr-defined]
    assert rec.outcome == "error"  # type: ignore[attr-defined]
    assert rec.error_type == "RuntimeError"  # type: ignore[attr-defined]
    assert not hasattr(rec, "error"), "raw error string must not appear (key leak risk)"


@pytest.mark.asyncio
async def test_call_cerebras_logs_total_tokens_on_success(caplog):
    """_call_cerebras log record includes total_tokens when API returns usage."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content='{"x": 1}'))]
    mock_resp.usage = MagicMock(total_tokens=99)

    with patch("src.services.profile.llm_provider.CEREBRAS_API_KEY", "fake"), \
         caplog.at_level(logging.INFO, logger="job360.profile.llm_provider"):
        from cerebras.cloud.sdk import AsyncCerebras
        with patch.object(AsyncCerebras, "__init__", return_value=None), \
             patch.object(AsyncCerebras, "chat", new_callable=MagicMock) as mock_chat:
            mock_chat.completions = MagicMock()
            mock_chat.completions.create = AsyncMock(return_value=mock_resp)
            from src.services.profile.llm_provider import _call_cerebras
            await _call_cerebras("prompt", "")

    records = [r for r in caplog.records if r.getMessage() == "llm_call"]
    assert len(records) == 1
    assert records[0].total_tokens == 99  # type: ignore[attr-defined]
