"""A provider we know is dead must not be called again.

FOUND IN PRODUCTION-SHAPED VERIFICATION. With an expired GROQ_API_KEY in the
fallback chain, one CV upload took **584 seconds** — 9.7 minutes of a user
watching a spinner. A profile extraction makes 8 LLM calls, and every single one
paid a full doomed round-trip to Groq before failing over.

The infuriating part: the code already KNEW. It logged "LLM provider 'groq'
looks PERMANENTLY misconfigured" and then called it again on the next request,
and the next, forever. Detection without action is not a safeguard.

The distinction that matters is permanent vs transient:
  * expired key / missing package / unknown model -> fails on EVERY call until a
    human intervenes. Stop calling it.
  * 429 rate limit -> recovers on its own. Keep calling it (``_attempt`` backs
    off). Marking these dead would disable a healthy provider for the whole
    process, which is a far worse bug than the one being fixed.
"""

from __future__ import annotations

import pytest

from src.services.profile import llm_provider as lp


@pytest.fixture(autouse=True)
def _clean_slate():
    """The dead set is module-level, so leaking it between tests would make
    results depend on execution order."""
    lp.reset_dead_providers()
    yield
    lp.reset_dead_providers()


class _AuthError(Exception):
    """Shaped like a real SDK auth error: carries a 401 status_code."""

    status_code = 401

    def __str__(self) -> str:
        return "Error code: 401 - Invalid API Key (expired_api_key)"


def test_an_expired_key_is_tried_once_then_skipped(monkeypatch):
    """THE 584-SECOND BUG. A dead provider must cost one round-trip, not one per
    call for the lifetime of the process."""
    calls: list[str] = []

    async def dead(prompt, system):
        calls.append("groq")
        raise _AuthError()

    async def good(prompt, system):
        return {"ok": True}

    monkeypatch.setattr(lp, "GROQ_API_KEY", "expired")
    monkeypatch.setattr(lp, "OPENAI_API_KEY", "")
    monkeypatch.setattr(lp, "GEMINI_API_KEY", "")
    monkeypatch.setattr(lp, "CEREBRAS_API_KEY", "live")
    monkeypatch.setattr(lp, "_call_groq", dead)
    monkeypatch.setattr(lp, "_call_cerebras", good)

    import asyncio

    async def run_eight():
        return [await lp.llm_extract("p") for _ in range(8)]

    results = asyncio.run(run_eight())

    assert all(r == {"ok": True} for r in results), "the healthy provider must still serve"
    assert len(calls) == 1, (
        f"the dead provider was called {len(calls)} times across 8 extractions — "
        "this is exactly the waste that turned one upload into 9.7 minutes"
    )
    assert "groq" in lp._DEAD_PROVIDERS


def test_a_rate_limited_provider_is_not_marked_dead(monkeypatch):
    """The dangerous inverse. A 429 recovers on its own; disabling that provider
    for the whole process would take a healthy paid provider offline after one
    busy minute — worse than the bug being fixed."""

    class _RateLimitError(Exception):
        status_code = 429

        def __str__(self) -> str:
            return "429 Too Many Requests: rate limit exceeded"

    async def limited(prompt, system):
        raise _RateLimitError()

    async def good(prompt, system):
        return {"ok": True}

    monkeypatch.setattr(lp, "OPENAI_API_KEY", "live")
    monkeypatch.setattr(lp, "GEMINI_API_KEY", "")
    monkeypatch.setattr(lp, "GROQ_API_KEY", "")
    monkeypatch.setattr(lp, "CEREBRAS_API_KEY", "live")
    monkeypatch.setattr(lp, "_call_openai", limited)
    monkeypatch.setattr(lp, "_call_cerebras", good)
    monkeypatch.setattr(lp, "_LLM_RL_RETRIES", 0)

    import asyncio

    asyncio.run(lp.llm_extract("p"))
    assert "openai" not in lp._DEAD_PROVIDERS, (
        "a transient 429 disabled a healthy provider for the whole process"
    )


def test_the_error_still_names_every_provider_when_all_are_dead(monkeypatch):
    """When everything is dead the caller must get a real diagnosis, not an
    empty message — the skip path has to report itself."""

    async def dead(prompt, system):
        raise _AuthError()

    monkeypatch.setattr(lp, "OPENAI_API_KEY", "")
    monkeypatch.setattr(lp, "GEMINI_API_KEY", "")
    monkeypatch.setattr(lp, "CEREBRAS_API_KEY", "")
    monkeypatch.setattr(lp, "GROQ_API_KEY", "expired")
    monkeypatch.setattr(lp, "_call_groq", dead)

    import asyncio

    with pytest.raises(lp.LLMAllProvidersFailed):
        asyncio.run(lp.llm_extract("p"))

    # Second call: groq is now skipped, but the error must still explain why.
    with pytest.raises(lp.LLMAllProvidersFailed) as ei:
        asyncio.run(lp.llm_extract("p"))
    assert "groq" in str(ei.value)
    assert "skipped" in str(ei.value), "a skipped provider must say so in the error"
