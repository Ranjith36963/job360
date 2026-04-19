"""Multi-provider LLM pool for CV analysis. Gemini → Groq → Cerebras fallback. Zero cost."""

from __future__ import annotations

import json
import logging
from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError

from src.core.settings import GEMINI_API_KEY, GROQ_API_KEY, CEREBRAS_API_KEY

logger = logging.getLogger("job360.profile.llm_provider")

_S = TypeVar("_S", bound=BaseModel)


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


async def llm_extract_validated(
    prompt: str,
    schema_cls: Type[_S],
    system: str = "",
    max_retries: int = 2,
) -> _S:
    """Call ``llm_extract`` and validate the result against a Pydantic schema.

    Batch 1.1 (Pillar 1) — strict JSON-schema extraction with a
    self-correction retry loop.

    On ``pydantic.ValidationError`` we append the error text to the
    prompt and call the LLM again, up to ``max_retries`` additional
    attempts. This turns weak models (Groq llama-3.3-70b, Cerebras
    llama3.1-8b) into usable JSON emitters without paying Gemini for
    every extraction.

    Args:
        prompt: user prompt — must instruct the model to emit JSON
          matching ``schema_cls``.
        schema_cls: Pydantic v2 ``BaseModel`` subclass to validate with.
        system: optional system prompt (priority / style / persona).
        max_retries: number of *extra* attempts after the first fails
          validation. ``0`` means no retry.

    Returns:
        A validated ``schema_cls`` instance.

    Raises:
        RuntimeError: when all providers fail or retries are exhausted.
          The final message includes the last ``ValidationError`` for
          debugging.
    """
    attempt = 0
    current_prompt = prompt
    last_validation_error: ValidationError | None = None

    # attempts = 1 (first call) + max_retries (corrections)
    while attempt <= max_retries:
        raw = await llm_extract(current_prompt, system=system)
        try:
            return schema_cls.model_validate(raw)
        except ValidationError as ve:
            last_validation_error = ve
            attempt += 1
            if attempt > max_retries:
                break
            logger.warning(
                "LLM output failed %s validation (attempt %d/%d); retrying with correction",
                schema_cls.__name__,
                attempt,
                max_retries + 1,
            )
            # Review fix #6 — trim to first 5 validation errors so the
            # combined retry prompt stays well under a weak model's
            # context window. ValidationError.__str__() on a CV with
            # many nested list errors can exceed Cerebras llama3.1-8b's
            # 8K window and convert a recoverable validation failure
            # into a hard provider failure.
            first_errors = ve.errors()[:5]
            error_lines = "\n".join(
                f"- {err.get('loc')}: {err.get('msg')}" for err in first_errors
            )
            current_prompt = (
                f"{prompt}\n\n"
                f"Your previous response failed schema validation (showing first "
                f"{len(first_errors)} of {len(ve.errors())} errors):\n"
                f"{error_lines}\n"
                f"Emit JSON matching the schema exactly. Do not include prose. "
                f"Correct EVERY field flagged above."
            )

    raise RuntimeError(
        f"LLM output failed {schema_cls.__name__} validation after "
        f"{max_retries + 1} attempts: {last_validation_error}"
    )


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
