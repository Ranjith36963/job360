"""Tests for LLM provider pool."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_llm_extract_no_keys_raises():
    """Should raise RuntimeError when no API keys are configured."""
    with patch("src.profile.llm_provider.GEMINI_API_KEY", ""), \
         patch("src.profile.llm_provider.GROQ_API_KEY", ""), \
         patch("src.profile.llm_provider.CEREBRAS_API_KEY", ""):
        from src.profile.llm_provider import llm_extract
        with pytest.raises(RuntimeError, match="No LLM API key configured"):
            await llm_extract("test prompt")


@pytest.mark.asyncio
async def test_llm_extract_gemini_success():
    """Should call Gemini when key is available and return parsed JSON."""
    mock_result = {"skills": ["Python", "Docker"]}
    with patch("src.profile.llm_provider.GEMINI_API_KEY", "fake-key"), \
         patch("src.profile.llm_provider._call_gemini", new_callable=AsyncMock, return_value=mock_result):
        from src.profile.llm_provider import llm_extract
        result = await llm_extract("test prompt")
    assert result == {"skills": ["Python", "Docker"]}


@pytest.mark.asyncio
async def test_llm_extract_gemini_fails_groq_succeeds():
    """Should fallback to Groq when Gemini fails."""
    mock_result = {"skills": ["Nursing", "Patient Care"]}
    with patch("src.profile.llm_provider.GEMINI_API_KEY", "fake-key"), \
         patch("src.profile.llm_provider.GROQ_API_KEY", "fake-key"), \
         patch("src.profile.llm_provider._call_gemini", new_callable=AsyncMock, side_effect=Exception("Gemini down")), \
         patch("src.profile.llm_provider._call_groq", new_callable=AsyncMock, return_value=mock_result):
        from src.profile.llm_provider import llm_extract
        result = await llm_extract("test prompt")
    assert result == {"skills": ["Nursing", "Patient Care"]}


@pytest.mark.asyncio
async def test_llm_extract_all_fail_raises():
    """Should raise RuntimeError when all providers fail."""
    with patch("src.profile.llm_provider.GEMINI_API_KEY", "fake-key"), \
         patch("src.profile.llm_provider.GROQ_API_KEY", "fake-key"), \
         patch("src.profile.llm_provider.CEREBRAS_API_KEY", ""), \
         patch("src.profile.llm_provider._call_gemini", new_callable=AsyncMock, side_effect=Exception("down")), \
         patch("src.profile.llm_provider._call_groq", new_callable=AsyncMock, side_effect=Exception("also down")):
        from src.profile.llm_provider import llm_extract
        with pytest.raises(RuntimeError, match="All LLM providers failed"):
            await llm_extract("test prompt")


@pytest.mark.asyncio
async def test_llm_extract_cerebras_success():
    """Should call Cerebras when Gemini and Groq keys are unset."""
    mock_result = {"skills": ["Nursing", "Patient Care"]}
    with patch("src.profile.llm_provider.GEMINI_API_KEY", ""), \
         patch("src.profile.llm_provider.GROQ_API_KEY", ""), \
         patch("src.profile.llm_provider.CEREBRAS_API_KEY", "fake-key"), \
         patch("src.profile.llm_provider._call_cerebras", new_callable=AsyncMock, return_value=mock_result):
        from src.profile.llm_provider import llm_extract
        result = await llm_extract("test prompt")
    assert result == mock_result


@pytest.mark.asyncio
async def test_llm_extract_fast_prefers_cerebras():
    """llm_extract_fast should try Cerebras FIRST (fastest for bulk scoring)."""
    mock_result = {"score": 85}
    with patch("src.profile.llm_provider.GEMINI_API_KEY", "fake-key"), \
         patch("src.profile.llm_provider.GROQ_API_KEY", "fake-key"), \
         patch("src.profile.llm_provider.CEREBRAS_API_KEY", "fake-key"), \
         patch("src.profile.llm_provider._call_cerebras", new_callable=AsyncMock, return_value=mock_result) as cerebras_mock, \
         patch("src.profile.llm_provider._call_gemini", new_callable=AsyncMock) as gemini_mock, \
         patch("src.profile.llm_provider._call_groq", new_callable=AsyncMock) as groq_mock:
        from src.profile.llm_provider import llm_extract_fast
        result = await llm_extract_fast("score this")
    assert result == mock_result
    cerebras_mock.assert_called_once()
    gemini_mock.assert_not_called()
    groq_mock.assert_not_called()


@pytest.mark.asyncio
async def test_llm_extract_fast_falls_back_to_groq():
    """llm_extract_fast should fall back to Groq when Cerebras fails."""
    mock_result = {"score": 72}
    with patch("src.profile.llm_provider.CEREBRAS_API_KEY", "fake"), \
         patch("src.profile.llm_provider.GROQ_API_KEY", "fake"), \
         patch("src.profile.llm_provider.GEMINI_API_KEY", ""), \
         patch("src.profile.llm_provider._call_cerebras", new_callable=AsyncMock, side_effect=Exception("cerebras down")), \
         patch("src.profile.llm_provider._call_groq", new_callable=AsyncMock, return_value=mock_result):
        from src.profile.llm_provider import llm_extract_fast
        result = await llm_extract_fast("score this")
    assert result == mock_result


@pytest.mark.asyncio
async def test_llm_extract_fast_all_keys_missing_raises():
    """llm_extract_fast should raise when no keys configured."""
    with patch("src.profile.llm_provider.CEREBRAS_API_KEY", ""), \
         patch("src.profile.llm_provider.GROQ_API_KEY", ""), \
         patch("src.profile.llm_provider.GEMINI_API_KEY", ""):
        from src.profile.llm_provider import llm_extract_fast
        with pytest.raises(RuntimeError, match="No LLM API key configured"):
            await llm_extract_fast("test")
