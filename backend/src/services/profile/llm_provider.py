"""Multi-provider LLM pool for CV analysis. Gemini → Groq → Cerebras fallback. Zero cost."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time as _time
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from src.core.settings import (
    CEREBRAS_API_KEY,
    GEMINI_API_KEY,
    GROQ_API_KEY,
    OPENAI_API_KEY,
    OPENAI_MODEL,
)

logger = logging.getLogger("job360.profile.llm_provider")

# Decision #6 — deterministic extraction: temperature 0 so the same CV yields
# the same skills every run. Env-overridable.
_LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))

_S = TypeVar("_S", bound=BaseModel)


# ── Failure taxonomy + resilience knobs (env-overridable) ───────────────────
# Per-call hard timeout so a hung provider can't block the request (and, in the
# LinkedIn path, all 8 gathered calls). 60s is long enough for Gemini on a big
# CV, short enough to fail over.
_LLM_TIMEOUT_S = float(os.getenv("LLM_CALL_TIMEOUT_S", "60"))
# On a SHORT per-minute rate-limit, back off + retry the same provider. On a
# LONG / daily cap (minutes), don't wait — fail over to the next provider now.
_LLM_RL_RETRIES = int(os.getenv("LLM_RATE_LIMIT_RETRIES", "2"))
_LLM_RL_BASE_DELAY_S = float(os.getenv("LLM_RATE_LIMIT_BASE_DELAY_S", "2"))
_LLM_RL_MAX_WAIT_S = float(os.getenv("LLM_RATE_LIMIT_MAX_WAIT_S", "8"))


class LLMError(RuntimeError):
    """Base for LLM provider failures. Subclasses ``RuntimeError`` so existing
    callers that catch ``RuntimeError``/``Exception`` keep working unchanged."""


class LLMRateLimited(LLMError):  # noqa: N818 — reads better than ...Error
    """Every provider failed AND at least one was rate-limited / quota-exhausted.

    Callers MUST treat this as 'retry later', NOT as 'the user has no data'.
    Persisting an empty result on this error is the silent-data-loss bug — the
    correct response is to surface a degraded / try-again state.
    """


class LLMAllProvidersFailed(LLMError):  # noqa: N818 — reads better than ...Error
    """Every provider failed for non-rate-limit reasons (bad key, outage, bug)."""


_RATE_LIMIT_MARKERS = (
    "rate limit", "rate_limit", "ratelimit", "429", "too many requests",
    "quota", "resource_exhausted", "resource exhausted", "tokens per day",
)


def _is_rate_limit(exc: BaseException) -> bool:
    """True if the exception looks like a provider rate-limit / quota error."""
    status = (
        getattr(exc, "status_code", None)
        or getattr(exc, "status", None)
        or getattr(exc, "code", None)
    )
    if status in (429, "429"):
        return True
    msg = str(exc).lower()
    return any(m in msg for m in _RATE_LIMIT_MARKERS)


def _retry_after_seconds(exc: BaseException, default: float) -> float:
    """Best-effort parse of a provider's retry hint (e.g. 'try again in 24m3s'
    or a Retry-After value). Returns a LARGE number for long/daily caps so the
    caller fails over instead of blocking the request."""
    s = str(exc)
    m = re.search(r"try again in\s+([0-9.]+)\s*m", s)
    if m:
        return float(m.group(1)) * 60.0
    m = re.search(r"try again in\s+([0-9.]+)\s*s", s)
    if m:
        return float(m.group(1))
    m = re.search(r"retry[-_ ]?after['\"]?\s*[:=]?\s*([0-9.]+)", s, re.I)
    if m:
        return float(m.group(1))
    return default


async def _attempt(call, prompt: str, system: str, provider: str) -> dict[str, Any]:
    """Run ONE provider with a hard timeout + bounded backoff on SHORT rate-limits.

    - Hard timeout (``_LLM_TIMEOUT_S``) so a hung call can't block the request.
    - Short per-minute rate-limit → sleep the hinted (capped) delay and retry the
      same provider, up to ``_LLM_RL_RETRIES`` times.
    - Long/daily cap or any non-rate-limit error → raise immediately so the
      caller fails over to the next provider.
    """
    delay = _LLM_RL_BASE_DELAY_S
    for i in range(_LLM_RL_RETRIES + 1):
        try:
            return await asyncio.wait_for(call(prompt, system), timeout=_LLM_TIMEOUT_S)
        except asyncio.TimeoutError as e:
            raise TimeoutError(f"{provider} timed out after {_LLM_TIMEOUT_S}s") from e
        except Exception as e:  # noqa: BLE001
            if _is_rate_limit(e) and i < _LLM_RL_RETRIES:
                wait = _retry_after_seconds(e, delay)
                if wait <= _LLM_RL_MAX_WAIT_S:
                    logger.warning(
                        "%s short rate-limit; backing off %.1fs (retry %d/%d)",
                        provider, wait, i + 1, _LLM_RL_RETRIES,
                    )
                    await asyncio.sleep(wait)
                    delay *= 2
                    continue
            raise


async def llm_extract(prompt: str, system: str = "") -> dict[str, Any]:
    """CV parsing — OpenAI (paid, primary) → Gemini → Groq → Cerebras fallback.

    OpenAI ``gpt-4o-mini`` leads: paid quota removes the silent-empty / 429
    failures the free tiers hit, and temp-0 JSON mode is deterministic. The free
    tiers stay as zero-cost fallbacks. Each call has a hard timeout + smart 429
    backoff (see ``_attempt``).

    Raises:
        RuntimeError: no provider key configured.
        LLMRateLimited: all providers failed AND >=1 was rate-limited — retry
            later; callers MUST NOT persist an empty result on this.
        LLMAllProvidersFailed: all providers failed for non-rate-limit reasons.
    """
    errors: list[str] = []
    rate_limited = False

    for key, call, name in (
        (OPENAI_API_KEY, _call_openai, "openai"),
        (GEMINI_API_KEY, _call_gemini, "gemini"),
        (GROQ_API_KEY, _call_groq, "groq"),
        (CEREBRAS_API_KEY, _call_cerebras, "cerebras"),
    ):
        if not key:
            continue
        try:
            return await _attempt(call, prompt, system, name)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
            rate_limited = rate_limited or _is_rate_limit(e)
            logger.warning("%s failed, trying next provider: %s", name, e)

    if not (OPENAI_API_KEY or GEMINI_API_KEY or GROQ_API_KEY or CEREBRAS_API_KEY):
        raise RuntimeError(
            "No LLM API key configured. Set OPENAI_API_KEY (recommended), or a free tier "
            "GEMINI_API_KEY / GROQ_API_KEY / CEREBRAS_API_KEY in .env."
        )

    detail = "; ".join(errors)
    if rate_limited:
        raise LLMRateLimited(f"All LLM providers failed (rate-limited): {detail}")
    raise LLMAllProvidersFailed(f"All LLM providers failed: {detail}")


async def llm_extract_fast(prompt: str, system: str = "") -> dict[str, Any]:
    """Fast scoring — OpenAI → Cerebras (fastest ~2000 tok/sec) → Groq → Gemini.

    OpenAI leads for determinism + reliable quota; Cerebras stays second for its
    raw speed when the paid key is absent. Use this for bulk job scoring where
    latency matters more than daily quota. Same timeout + backoff + failure
    taxonomy as ``llm_extract``.

    Raises:
        RuntimeError: no provider key configured.
        LLMRateLimited / LLMAllProvidersFailed: as in ``llm_extract``.
    """
    errors: list[str] = []
    rate_limited = False

    for key, call, name in (
        (OPENAI_API_KEY, _call_openai, "openai"),
        (CEREBRAS_API_KEY, _call_cerebras, "cerebras"),
        (GROQ_API_KEY, _call_groq, "groq"),
        (GEMINI_API_KEY, _call_gemini, "gemini"),
    ):
        if not key:
            continue
        try:
            return await _attempt(call, prompt, system, name)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
            rate_limited = rate_limited or _is_rate_limit(e)
            logger.warning("%s failed, trying next provider: %s", name, e)

    if not (OPENAI_API_KEY or GEMINI_API_KEY or GROQ_API_KEY or CEREBRAS_API_KEY):
        raise RuntimeError(
            "No LLM API key configured. Set OPENAI_API_KEY (recommended) or a free tier key in .env."
        )

    detail = "; ".join(errors)
    if rate_limited:
        raise LLMRateLimited(f"All LLM providers failed (rate-limited): {detail}")
    raise LLMAllProvidersFailed(f"All LLM providers failed: {detail}")


async def llm_extract_validated(
    prompt: str,
    schema_cls: type[_S],
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


async def _call_openai(prompt: str, system: str) -> dict[str, Any]:
    """Call OpenAI (paid, PRIMARY). Deterministic (temp 0) JSON-mode extraction.

    Model is env-overridable via ``OPENAI_MODEL`` (default ``gpt-4o-mini`` —
    cheap, fast, strong JSON). Reliable paid quota removes the silent-empty /
    rate-limit failure the free tiers hit.
    """
    from openai import AsyncOpenAI

    _model = OPENAI_MODEL
    t0 = _time.monotonic()
    try:
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = await client.chat.completions.create(
            model=_model,
            messages=messages,
            temperature=_LLM_TEMPERATURE,
            response_format={"type": "json_object"},
        )
        latency_ms = round((_time.monotonic() - t0) * 1000)
        total_tokens = getattr(getattr(response, "usage", None), "total_tokens", None)
        logger.info(
            "llm_call",
            extra={"provider": "openai", "model": _model, "latency_ms": latency_ms,
                   "total_tokens": total_tokens, "outcome": "ok"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as exc:
        latency_ms = round((_time.monotonic() - t0) * 1000)
        logger.warning(
            "llm_call_error",
            extra={"provider": "openai", "model": _model, "latency_ms": latency_ms,
                   "outcome": "error", "error_type": type(exc).__name__},
        )
        raise


async def _call_gemini(prompt: str, system: str) -> dict[str, Any]:
    """Call Google Gemini API (free tier: 15 RPM, 1M tokens/day)."""
    import google.generativeai as genai

    _model = "gemini-2.0-flash"
    t0 = _time.monotonic()
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
            _model,
            system_instruction=system or None,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=_LLM_TEMPERATURE,
            ),
        )
        response = await model.generate_content_async(prompt)
        latency_ms = round((_time.monotonic() - t0) * 1000)
        total_tokens = getattr(getattr(response, "usage_metadata", None), "total_token_count", None)
        logger.info(
            "llm_call",
            extra={"provider": "gemini", "model": _model, "latency_ms": latency_ms,
                   "total_tokens": total_tokens, "outcome": "ok"},
        )
        return json.loads(response.text)
    except Exception as exc:
        latency_ms = round((_time.monotonic() - t0) * 1000)
        logger.warning(
            "llm_call_error",
            extra={"provider": "gemini", "model": _model, "latency_ms": latency_ms,
                   "outcome": "error", "error_type": type(exc).__name__},
        )
        raise


async def _call_groq(prompt: str, system: str) -> dict[str, Any]:
    """Call Groq API (free tier: 30 RPM, 14.4K tokens/day on llama3)."""
    from groq import AsyncGroq

    _model = "llama-3.3-70b-versatile"
    t0 = _time.monotonic()
    try:
        client = AsyncGroq(api_key=GROQ_API_KEY)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = await client.chat.completions.create(
            model=_model,
            messages=messages,
            temperature=_LLM_TEMPERATURE,
            response_format={"type": "json_object"},
        )
        latency_ms = round((_time.monotonic() - t0) * 1000)
        total_tokens = getattr(getattr(response, "usage", None), "total_tokens", None)
        logger.info(
            "llm_call",
            extra={"provider": "groq", "model": _model, "latency_ms": latency_ms,
                   "total_tokens": total_tokens, "outcome": "ok"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as exc:
        latency_ms = round((_time.monotonic() - t0) * 1000)
        logger.warning(
            "llm_call_error",
            extra={"provider": "groq", "model": _model, "latency_ms": latency_ms,
                   "outcome": "error", "error_type": type(exc).__name__},
        )
        raise


async def _call_cerebras(prompt: str, system: str) -> dict[str, Any]:
    """Call Cerebras API (free tier: 30 RPM, fastest inference ~2000 tokens/sec).

    Model is env-overridable via ``CEREBRAS_MODEL`` because available model IDs
    are account-specific — the old hardcoded ``llama3.1-8b`` 404s on accounts
    that don't have it (list yours with ``client.models.list()``). Default is
    ``gpt-oss-120b``, a capable JSON-emitting model on current free accounts.
    """
    from cerebras.cloud.sdk import AsyncCerebras

    _model = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")
    t0 = _time.monotonic()
    try:
        client = AsyncCerebras(api_key=CEREBRAS_API_KEY)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = await client.chat.completions.create(
            model=_model,
            messages=messages,
            temperature=_LLM_TEMPERATURE,
            response_format={"type": "json_object"},
        )
        latency_ms = round((_time.monotonic() - t0) * 1000)
        total_tokens = getattr(getattr(response, "usage", None), "total_tokens", None)
        logger.info(
            "llm_call",
            extra={"provider": "cerebras", "model": _model, "latency_ms": latency_ms,
                   "total_tokens": total_tokens, "outcome": "ok"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as exc:
        latency_ms = round((_time.monotonic() - t0) * 1000)
        logger.warning(
            "llm_call_error",
            extra={"provider": "cerebras", "model": _model, "latency_ms": latency_ms,
                   "outcome": "error", "error_type": type(exc).__name__},
        )
        raise
