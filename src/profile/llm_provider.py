"""Multi-provider LLM pool for CV analysis. Gemini → Groq → Cerebras fallback. Zero cost."""

from __future__ import annotations

import json
import logging
from typing import Any

from src.config.settings import GEMINI_API_KEY, GROQ_API_KEY, CEREBRAS_API_KEY

logger = logging.getLogger("job360.profile.llm_provider")


async def llm_extract(prompt: str, system: str = "") -> dict[str, Any]:
    """CV parsing — Gemini (best JSON) → Groq → Cerebras fallback.

    Provider priority optimised for JSON quality and daily quota.
    Raises RuntimeError if no provider is available or all fail.
    """
    errors = []

    if GEMINI_API_KEY:
        try:
            return await _call_gemini(prompt, system)
        except Exception as e:
            errors.append(f"Gemini: {e}")
            logger.warning("Gemini failed, trying next provider: %s", e)

    if GROQ_API_KEY:
        try:
            return await _call_groq(prompt, system)
        except Exception as e:
            errors.append(f"Groq: {e}")
            logger.warning("Groq failed, trying next provider: %s", e)

    if CEREBRAS_API_KEY:
        try:
            return await _call_cerebras(prompt, system)
        except Exception as e:
            errors.append(f"Cerebras: {e}")
            logger.warning("Cerebras failed: %s", e)

    if not (GEMINI_API_KEY or GROQ_API_KEY or CEREBRAS_API_KEY):
        raise RuntimeError(
            "No LLM API key configured. Set GEMINI_API_KEY, GROQ_API_KEY, or CEREBRAS_API_KEY in .env. "
            "All offer free tiers — see https://ai.google.dev, https://console.groq.com, https://cloud.cerebras.ai"
        )

    raise RuntimeError(f"All LLM providers failed: {'; '.join(errors)}")


# RESERVED: Used by the planned LLM-based bulk job scorer (not wired yet).
# Do NOT delete on a dead-code sweep — the Cerebras-first provider ordering
# is intentionally different from llm_extract() because bulk scoring needs
# low latency (Cerebras ~2000 tok/sec) more than daily quota headroom.
async def llm_extract_fast(prompt: str, system: str = "") -> dict[str, Any]:
    """Fast scoring — Cerebras (fastest ~2000 tok/sec) → Groq → Gemini fallback.

    Use this for bulk job scoring where latency matters more than daily quota.
    Raises RuntimeError if no provider is available or all fail.
    """
    errors = []

    if CEREBRAS_API_KEY:
        try:
            return await _call_cerebras(prompt, system)
        except Exception as e:
            errors.append(f"Cerebras: {e}")
            logger.warning("Cerebras failed, trying next provider: %s", e)

    if GROQ_API_KEY:
        try:
            return await _call_groq(prompt, system)
        except Exception as e:
            errors.append(f"Groq: {e}")
            logger.warning("Groq failed, trying next provider: %s", e)

    if GEMINI_API_KEY:
        try:
            return await _call_gemini(prompt, system)
        except Exception as e:
            errors.append(f"Gemini: {e}")
            logger.warning("Gemini failed: %s", e)

    if not (GEMINI_API_KEY or GROQ_API_KEY or CEREBRAS_API_KEY):
        raise RuntimeError(
            "No LLM API key configured. Set GEMINI_API_KEY, GROQ_API_KEY, or CEREBRAS_API_KEY in .env."
        )

    raise RuntimeError(f"All LLM providers failed: {'; '.join(errors)}")


async def _call_gemini(prompt: str, system: str) -> dict[str, Any]:
    """Call Google Gemini API (free tier: 15 RPM, 1M tokens/day)."""
    import google.generativeai as genai

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        "gemini-2.0-flash",
        system_instruction=system or None,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )
    response = await model.generate_content_async(prompt)
    return json.loads(response.text)


async def _call_groq(prompt: str, system: str) -> dict[str, Any]:
    """Call Groq API (free tier: 30 RPM, 14.4K tokens/day on llama3)."""
    from groq import AsyncGroq

    client = AsyncGroq(api_key=GROQ_API_KEY)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = await client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


async def _call_cerebras(prompt: str, system: str) -> dict[str, Any]:
    """Call Cerebras API (free tier: 30 RPM, fastest inference ~2000 tokens/sec).

    Uses llama3.1-8b — Cerebras's reliable free-tier model with JSON support.
    """
    from cerebras.cloud.sdk import AsyncCerebras

    client = AsyncCerebras(api_key=CEREBRAS_API_KEY)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = await client.chat.completions.create(
        model="llama3.1-8b",
        messages=messages,
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)
