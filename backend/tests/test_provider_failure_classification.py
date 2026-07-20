"""A permanently-misconfigured LLM provider must be distinguishable from a blip.

Why this exists
---------------
`openai` was never installed in production. The import raised, the provider
chain caught it with `except Exception`, logged
    "openai failed, trying next provider: No module named 'openai'"
at WARNING, and fell through to a free tier. The documented PRIMARY provider
never ran a single time, for months, and nothing said so — because that line
looked exactly like a transient 429 from a provider having a bad minute.

The fix does NOT change control flow. Falling through to the next provider is
correct resilience and stays. What changes is that a failure which will recur on
EVERY call until a human intervenes is logged at ERROR, so it is visible to
Sentry and to anyone scanning logs, instead of hiding among retries.

The classifier is deliberately CONSERVATIVE: anything unrecognised counts as
transient. Missing a permanent failure costs one log level; crying wolf on every
transient error would make the ERROR signal worthless — which is the exact
failure mode being fixed.
"""

import pytest

from src.services.profile.llm_provider import _is_permanent_provider_failure


class _StatusError(Exception):
    """Stand-in for a provider SDK error carrying an HTTP status."""

    def __init__(self, status_code: int, msg: str = "") -> None:
        super().__init__(msg or f"status {status_code}")
        self.status_code = status_code


@pytest.mark.parametrize(
    "exc,why",
    [
        (ModuleNotFoundError("No module named 'openai'"), "the actual incident"),
        (ImportError("cannot import name 'AsyncOpenAI' from 'openai'"), "SDK moved a symbol"),
        (_StatusError(401), "bad or rotated API key"),
        (_StatusError(403), "key lacks permission for the model"),
        (_StatusError(404), "model name typo'd or deprecated"),
        (Exception("Incorrect API key provided: sk-xxx"), "provider text, no status attr"),
        (Exception("The model `gpt-4o-mini-typo` does not exist"), "model name wrong"),
    ],
)
def test_permanent_failures_are_recognised(exc: BaseException, why: str) -> None:
    """These recur on every call until someone changes config — must be loud."""
    assert _is_permanent_provider_failure(exc) is True, (
        f"should be classified PERMANENT ({why}); logging it beside transient "
        "429s is how the openai outage stayed invisible for months"
    )


@pytest.mark.parametrize(
    "exc,why",
    [
        (_StatusError(429), "rate limited — the classic transient"),
        (_StatusError(500), "provider server error"),
        (_StatusError(503), "provider temporarily unavailable"),
        (TimeoutError("request timed out"), "network timeout"),
        (Exception("connection reset by peer"), "transport blip"),
        (Exception("something entirely unfamiliar"), "unknown — must NOT be permanent"),
    ],
)
def test_transient_failures_are_not_flagged_permanent(exc: BaseException, why: str) -> None:
    """Over-reporting would make the ERROR signal meaningless.

    The last case is the important one: an unrecognised error defaults to
    transient. A classifier that guessed 'permanent' whenever it was unsure
    would fire on every bad minute and be tuned out within a week.
    """
    assert _is_permanent_provider_failure(exc) is False, (
        f"should be classified TRANSIENT ({why}); a false ERROR here trains "
        "people to ignore the signal that matters"
    )
