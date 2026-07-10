"""In-memory sliding-window rate limiter for auth surfaces.

Phase −2 post-review fix #3. Provides a soft rate limit on the two SMTP-
amplifying endpoints (``POST /api/auth/verify-email/request`` and
``POST /api/auth/password-reset/request``) so an authenticated attacker
or impatient user can't burn through the daily SMTP quota by spamming
resend.

**Scope.** In-memory only — not race-safe across processes. Acceptable
for Phase −2 (single dev worker). Production deployment (Phase 3 of
LAUNCH_PLAN.md) replaces this with a Redis-backed implementation that
the ARQ worker can share state with. The public API
(``check_and_record``) is stable so the swap is a one-import change.

**Trade-off.** A restart wipes the buckets. That's fine — the attack
this defends against is a runaway client in a tight loop, not a long-
running distributed brute-force. Restart-driven reset is acceptable.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Deque, Dict

_LOCK = Lock()
_BUCKETS: Dict[str, Deque[datetime]] = {}
# Separate store for login brute-force lockout. Unlike _BUCKETS (which records
# *every* request), this counts only FAILED logins and is cleared on success —
# so a legitimate user who mistypes a few times then logs in is never locked.
_FAILURES: Dict[str, Deque[datetime]] = {}


def check_and_record(
    key: str,
    *,
    max_in_window: int = 1,
    window_seconds: int = 60,
) -> bool:
    """Return True if the request is allowed (and record it), False if limited.

    Args:
        key: A stable identifier for the rate-limit bucket. Examples:
            ``"verify-email:<user_id>"``, ``"password-reset:<ip_hash>"``.
            Distinct keys are independent buckets.
        max_in_window: Cap on requests within ``window_seconds``. Default 1.
        window_seconds: Window size in seconds. Default 60.

    Returns:
        True if under the limit (request was recorded). False if at or
        above the limit (request was NOT recorded — caller should treat
        as denied).

    Note:
        The bucket auto-prunes expired entries on each call. Memory is
        bounded by the number of distinct keys observed in the last
        ``window_seconds`` — for our two endpoints with per-user keys
        that's O(active users).
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=window_seconds)
    with _LOCK:
        bucket = _BUCKETS.setdefault(key, deque())
        # Drop entries that have fallen out of the window.
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= max_in_window:
            return False
        bucket.append(now)
        return True


def record_failure(key: str, *, now: datetime | None = None) -> int:
    """Record one failed login for ``key`` and return the failures now tracked.

    Args:
        key: Stable identifier for the account, e.g. ``"login:<email>"``.
        now: Injectable clock for tests; defaults to UTC now.

    Note:
        Append-only here; the window is pruned in :func:`is_locked`, which the
        login route always calls first, so the bucket stays bounded in practice.
    """
    now = now or datetime.now(timezone.utc)
    with _LOCK:
        bucket = _FAILURES.setdefault(key, deque())
        bucket.append(now)
        return len(bucket)


def is_locked(
    key: str,
    *,
    max_failures: int = 5,
    window_seconds: int = 900,
    now: datetime | None = None,
) -> bool:
    """Return True if ``key`` has >= ``max_failures`` failures inside the window.

    Prunes failures older than ``window_seconds`` on each call, so the lock
    naturally lifts once the burst ages out (sliding window).

    Note:
        Keying on the email protects the *account* from password-guessing. The
        trade-off is a nuisance lock-out DoS (an attacker can keep a victim's
        account locked). Acceptable for the in-memory phase; the Phase-3 Redis
        swap can add an IP dimension. See module docstring.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=window_seconds)
    with _LOCK:
        bucket = _FAILURES.get(key)
        if not bucket:
            return False
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if not bucket:
            _FAILURES.pop(key, None)
            return False
        return len(bucket) >= max_failures


def clear_failures(key: str) -> None:
    """Drop all recorded failures for ``key`` — call on a successful login."""
    with _LOCK:
        _FAILURES.pop(key, None)


def reset_for_tests() -> None:
    """Clear all buckets — call between tests to prevent cross-pollination.

    Production code MUST NOT call this. Tests should call it in setup or
    use the fixture pattern that wraps it in monkeypatch.
    """
    with _LOCK:
        _BUCKETS.clear()
        _FAILURES.clear()
