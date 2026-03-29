"""Multi-provider LLM client with round-robin rotation and automatic failover.

Distributes requests across all configured free LLM providers (Groq, Cerebras,
Gemini, DeepSeek, OpenRouter, SambaNova) to avoid rate limits.  All providers
use the OpenAI-compatible API via the ``openai`` Python library.

Usage::

    from src.llm.client import is_configured, llm_complete, allm_complete

    if is_configured():
        text = llm_complete("Extract skills from: ...")
        # or async:
        text = await allm_complete("Extract skills from: ...")
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Optional

from src.llm.providers import (
    CV_PREFERRED_MODEL,
    CV_PREFERRED_PROVIDER,
    ProviderConfig,
    get_configured_providers,
)

logger = logging.getLogger("job360.llm.client")

# Cooldown duration when a provider returns 429 (seconds).
_COOLDOWN_SECONDS = 60


class ProviderPool:
    """Round-robin LLM client across multiple free providers.

    - Distributes calls evenly (round-robin)
    - On 429: cools down that provider for 60 s, tries next
    - On error: skips to next provider
    - Lazy-creates one ``openai.OpenAI`` client per provider
    """

    def __init__(self) -> None:
        self._providers = get_configured_providers()
        self._clients: dict[str, object] = {}  # name → openai.OpenAI
        self._index = 0
        self._cooldowns: dict[str, float] = {}  # name → cooldown-until timestamp
        self._call_counts: dict[str, int] = {}
        self._failures: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self, provider: ProviderConfig):
        """Lazy-create an OpenAI client for *provider*."""
        if provider.name not in self._clients:
            try:
                import openai
            except ImportError:
                logger.warning("openai package not installed — pip install openai")
                return None
            self._clients[provider.name] = openai.OpenAI(
                api_key=provider.api_key,
                base_url=provider.base_url,
                default_headers=provider.headers or None,
                timeout=30.0,
            )
        return self._clients[provider.name]

    def _is_available(self, name: str) -> bool:
        """Return True if *name* is NOT in cooldown."""
        until = self._cooldowns.get(name, 0)
        if time.time() >= until:
            self._cooldowns.pop(name, None)
            return True
        return False

    def _next_available(self) -> Optional[ProviderConfig]:
        """Get the next available provider via round-robin."""
        n = len(self._providers)
        for _ in range(n):
            p = self._providers[self._index % n]
            self._index += 1
            if self._is_available(p.name):
                return p
        return None  # all cooling down

    def _find_provider(self, name: str) -> Optional[ProviderConfig]:
        """Find a specific provider by name."""
        for p in self._providers:
            if p.name == name and self._is_available(p.name):
                return p
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def complete(
        self,
        prompt: str,
        max_tokens: int = 2000,
        prefer: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> Optional[str]:
        """Send *prompt* to an LLM, rotating across providers.

        Parameters
        ----------
        prefer : str, optional
            Try this provider first (e.g. ``"gemini"`` for CV parsing).
        model_override : str, optional
            Override the provider's default model (e.g. ``"gemini-2.5-flash"``).
        """
        if not self._providers:
            return None

        # If a preferred provider is requested and available, try it first.
        first: Optional[ProviderConfig] = None
        if prefer:
            first = self._find_provider(prefer)

        max_attempts = len(self._providers) * 2
        attempted: set[str] = set()

        providers_to_try = []
        if first:
            providers_to_try.append(first)
            attempted.add(first.name)

        for attempt in range(max_attempts):
            if not providers_to_try:
                p = self._next_available()
                if p is None:
                    logger.warning("All LLM providers in cooldown")
                    return None
                if p.name in attempted:
                    continue
                providers_to_try.append(p)

            provider = providers_to_try.pop(0)
            attempted.add(provider.name)
            client = self._get_client(provider)
            if client is None:
                continue

            model = model_override if model_override else provider.model
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=0.1,
                )
                result = response.choices[0].message.content
                logger.debug(f"LLM response from {provider.name} ({model})")
                self._call_counts[provider.name] = self._call_counts.get(provider.name, 0) + 1
                logger.debug("Pool call_counts: %s", self._call_counts)
                return result
            except Exception as exc:
                exc_str = str(exc).lower()
                self._failures[provider.name] = self._failures.get(provider.name, 0) + 1
                if "429" in exc_str or "rate" in exc_str:
                    self._cooldowns[provider.name] = time.time() + _COOLDOWN_SECONDS
                    logger.info(
                        f"Rate-limited by {provider.name}, cooling down {_COOLDOWN_SECONDS}s"
                    )
                else:
                    logger.warning(f"LLM error from {provider.name}: {exc}")
                continue

        return None

    def status(self) -> dict:
        """Return pool status for logging."""
        now = time.time()
        return {
            "configured": [p.name for p in self._providers],
            "cooldowns": {
                name: round(until - now, 1)
                for name, until in self._cooldowns.items()
                if until > now
            },
            "call_counts": dict(self._call_counts),
            "failures": dict(self._failures),
        }


# ── Module-level singleton + convenience functions ────────────────────

_pool: Optional[ProviderPool] = None


def _get_pool() -> ProviderPool:
    global _pool
    if _pool is None:
        _pool = ProviderPool()
    return _pool


def is_configured() -> bool:
    """Return True if at least one LLM provider has an API key set."""
    return len(get_configured_providers()) > 0


def llm_complete(
    prompt: str,
    max_tokens: int = 2000,
    prefer: Optional[str] = None,
    model_override: Optional[str] = None,
) -> Optional[str]:
    """Synchronous LLM completion via the provider pool."""
    return _get_pool().complete(prompt, max_tokens, prefer, model_override)


async def allm_complete(
    prompt: str,
    max_tokens: int = 2000,
    prefer: Optional[str] = None,
    model_override: Optional[str] = None,
) -> Optional[str]:
    """Async LLM completion — runs sync client in a thread."""
    return await asyncio.to_thread(
        llm_complete, prompt, max_tokens, prefer, model_override
    )


def parse_json_response(raw: str) -> dict:
    """Parse JSON from an LLM response, handling markdown code fences."""
    text = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
    return json.loads(text)


def pool_status() -> dict:
    """Return current pool status (configured providers, cooldowns)."""
    return _get_pool().status()
