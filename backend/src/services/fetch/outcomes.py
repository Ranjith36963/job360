"""The closed URL-fetch outcome enum — single source of truth.

Spec R3 (docs/plans/2026-09-04-url-fetch/spec.md): a value exists only when
the user's next action differs. Both the backend's Pydantic ``Literal`` (on
``FetchUrlResponse.outcome`` in ``src/api/routes/bring.py``) and the
frontend's copy map (``frontend/src/lib/url-fetch-messages.ts``) are pinned to
``OUTCOMES`` by frozen tests (items 37, 40) — this is the ONE place the eight
values and their default server-side sentences are declared.
"""
from __future__ import annotations

OK = "ok"
SSRF_DENIED = "ssrf_denied"
INVALID_URL = "invalid_url"
UNREACHABLE = "unreachable"
BLOCKED = "blocked"
TIMEOUT = "timeout"
TOO_LARGE = "too_large"
UNSUPPORTED_CONTENT = "unsupported_content"

# Order matches spec R3's table. Closed — adding a ninth value means adding a
# message here AND a frontend copy-map entry (test 40 fails the build until
# a distinct message is added for it).
OUTCOMES: tuple[str, ...] = (
    OK,
    SSRF_DENIED,
    INVALID_URL,
    UNREACHABLE,
    BLOCKED,
    TIMEOUT,
    TOO_LARGE,
    UNSUPPORTED_CONTENT,
)

# Server-authored, one plain sentence per outcome (R2). The frontend owns its
# OWN copy (plan.md: url-fetch-messages.ts) rather than trusting the wire —
# these are the fallback/API-contract sentences, and every one must be
# non-empty (frozen test 37).
MESSAGES: dict[str, str] = {
    OK: "Filled from the link — check it before you submit.",
    SSRF_DENIED: "That link points somewhere we won't fetch. Use a different link.",
    INVALID_URL: "That doesn't look like a web link. Fix it and try again.",
    UNREACHABLE: "We couldn't reach that link. Check it, or paste the ad instead.",
    BLOCKED: "That site refused the fetch — paste the ad text below instead.",
    TIMEOUT: "That took too long to load — paste the ad text below instead, or retry.",
    TOO_LARGE: "That page is too large for us to read — paste the ad text below instead.",
    UNSUPPORTED_CONTENT: "That link isn't a web page — paste the ad text below instead.",
}
