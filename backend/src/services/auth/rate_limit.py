"""Sliding-window rate limiter for auth surfaces.

Phase −2 post-review fix #3. Provides a soft rate limit on the two SMTP-
amplifying endpoints (``POST /api/auth/verify-email/request`` and
``POST /api/auth/password-reset/request``) and the login brute-force
lockout, so an attacker or impatient user can't burn the SMTP quota or
grind an account's password.

Two backends, same public API
-----------------------------
* **In-memory (default).** Per-process ``deque`` sliding windows guarded by a
  lock. Fast, dependency-free, race-safe *within* one process. A restart wipes
  the buckets, which is fine for the runaway-client threat this defends.
* **Redis (opt-in, ``RATE_LIMIT_REDIS=true`` + ``REDIS_URL`` set).** The SAME
  windows, stored in Redis sorted sets so every API replica shares one counter
  (docs/fable/01). Without this, a multi-replica deploy gives an attacker N×
  the intended budget because each replica counts alone.

**Safety.** The Redis path is wrapped so ANY Redis error (down, timeout, bad
reply) falls back to the in-memory path — a Redis outage degrades to today's
behaviour, never failing auth open or closed. Default OFF keeps behaviour
byte-identical to the in-memory implementation (rules #18/#24 discipline).

**Atomicity.** The Redis prune→count→add sequence runs as a single Lua script
(``EVAL``), so two concurrent requests can't both read ``count == max-1`` and
both slip through. The in-memory path gets the same atomicity from ``_LOCK``.
"""
from __future__ import annotations

import logging
import os
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Optional

logger = logging.getLogger("job360.rate_limit")

_LOCK = Lock()
_BUCKETS: dict[str, deque[datetime]] = {}
# Separate store for login brute-force lockout. Unlike _BUCKETS (which records
# *every* request), this counts only FAILED logins and is cleared on success —
# so a legitimate user who mistypes a few times then logs in is never locked.
_FAILURES: dict[str, deque[datetime]] = {}

# TTL applied to a Redis failures set so an abandoned key self-expires even
# though record_failure() has no window argument. is_locked() still prunes by
# its own window on read; this is just garbage collection.
_REDIS_FAILURE_TTL_SECONDS = 86_400

# ── Redis backend ───────────────────────────────────────────────────────────

# Atomic sliding-window admit. KEYS[1]=set, ARGV: now, window, max, member.
# Returns 1 if the request is admitted (and recorded), 0 if limited.
_LUA_CHECK_AND_RECORD = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, tonumber(ARGV[1]) - tonumber(ARGV[2]))
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[3]) then
  return 0
end
redis.call('ZADD', KEYS[1], tonumber(ARGV[1]), ARGV[4])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return 1
"""

# Append one failure and return the current count. KEYS[1]=set, ARGV: now,
# ttl, member.
_LUA_RECORD_FAILURE = """
redis.call('ZADD', KEYS[1], tonumber(ARGV[1]), ARGV[3])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return redis.call('ZCARD', KEYS[1])
"""

# Prune the window and return the surviving count. KEYS[1]=set, ARGV: now, window.
_LUA_COUNT_IN_WINDOW = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, tonumber(ARGV[1]) - tonumber(ARGV[2]))
return redis.call('ZCARD', KEYS[1])
"""

_redis_singleton: Any = None
_redis_init_done = False


def _redis_client() -> Any:
    """Return a cached sync Redis client, or None when Redis is unavailable.

    None means "use the in-memory path" — the caller treats it as the normal
    single-process mode. Any import/connection problem returns None so a broken
    Redis never breaks auth.
    """
    global _redis_singleton, _redis_init_done
    # Read settings live so tests can flip the flag with monkeypatch.
    from src.core import settings

    if not getattr(settings, "RATE_LIMIT_REDIS", False):
        return None
    redis_url = os.getenv("REDIS_URL", "")
    if not redis_url:
        return None
    if _redis_init_done:
        return _redis_singleton
    _redis_init_done = True
    try:
        import redis  # noqa: PLC0415 — heavy/optional, lazy per rules #11/#16

        # redis-py ships py.typed but from_url() itself is unannotated.
        _redis_singleton = redis.from_url(
            redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            decode_responses=True,
        )
    except Exception as exc:  # pragma: no cover — import/URL guard
        logger.warning("rate_limit: Redis client init failed, using in-memory: %s", exc)
        _redis_singleton = None
    return _redis_singleton


def _reset_redis_for_tests() -> None:
    """Drop the cached client so the next call re-reads the flag/URL."""
    global _redis_singleton, _redis_init_done
    _redis_singleton = None
    _redis_init_done = False


# ── Public API (sync — Redis path is transparent) ───────────────────────────


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
        ``window_seconds``. When ``RATE_LIMIT_REDIS`` is on the state lives
        in Redis (shared across replicas); otherwise per-process.
    """
    client = _redis_client()
    if client is not None:
        try:
            now_ts = datetime.now(timezone.utc).timestamp()
            member = f"{now_ts}:{uuid.uuid4().hex}"
            allowed = client.eval(
                _LUA_CHECK_AND_RECORD,
                1,
                f"rl:req:{key}",
                now_ts,
                window_seconds,
                max_in_window,
                member,
            )
            return bool(allowed)
        except Exception as exc:
            logger.warning("rate_limit: Redis check_and_record failed, fallback: %s", exc)

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


def record_failure(key: str, *, now: Optional[datetime] = None) -> int:
    """Record one failed login for ``key`` and return the failures now tracked.

    Args:
        key: Stable identifier for the account, e.g. ``"login:<email>:<ip>"``.
        now: Injectable clock for tests; defaults to UTC now.

    Note:
        Append-only here; the window is pruned in :func:`is_locked`, which the
        login route always calls first, so the bucket stays bounded in practice.
    """
    now = now or datetime.now(timezone.utc)
    client = _redis_client()
    if client is not None:
        try:
            count = client.eval(
                _LUA_RECORD_FAILURE,
                1,
                f"rl:fail:{key}",
                now.timestamp(),
                _REDIS_FAILURE_TTL_SECONDS,
                f"{now.timestamp()}:{uuid.uuid4().hex}",
            )
            return int(count)
        except Exception as exc:
            logger.warning("rate_limit: Redis record_failure failed, fallback: %s", exc)

    with _LOCK:
        bucket = _FAILURES.setdefault(key, deque())
        bucket.append(now)
        return len(bucket)


def is_locked(
    key: str,
    *,
    max_failures: int = 5,
    window_seconds: int = 900,
    now: Optional[datetime] = None,
) -> bool:
    """Return True if ``key`` has >= ``max_failures`` failures inside the window.

    Prunes failures older than ``window_seconds`` on each call, so the lock
    naturally lifts once the burst ages out (sliding window).

    Note:
        Keying on email+IP (see the login route) protects the account without
        letting an email-only lockout become a nuisance-DoS on the victim.
    """
    now = now or datetime.now(timezone.utc)
    client = _redis_client()
    if client is not None:
        try:
            count = client.eval(
                _LUA_COUNT_IN_WINDOW,
                1,
                f"rl:fail:{key}",
                now.timestamp(),
                window_seconds,
            )
            return int(count) >= max_failures
        except Exception as exc:
            logger.warning("rate_limit: Redis is_locked failed, fallback: %s", exc)

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
    client = _redis_client()
    if client is not None:
        try:
            client.delete(f"rl:fail:{key}")
            return
        except Exception as exc:
            logger.warning("rate_limit: Redis clear_failures failed, fallback: %s", exc)

    with _LOCK:
        _FAILURES.pop(key, None)


# ── Login admission: check AND count in one atomic step ─────────────────────
#
# Gap-audit fix #6 review (P1). Checking is_locked() and calling
# record_failure() later, after an awaited DB read and an argon2 verify, let a
# concurrent burst from ONE address pass the check N times before any failure
# was recorded: 60 parallel wrong passwords were all 401 (no 429) and then
# tripped the per-email cap, locking the owner out from every address. Now the
# attempt is counted BEFORE anything is awaited (pre-increment), and only an
# attempt the email+IP bucket admitted is counted toward the per-email bucket,
# in the same step. A successful login refunds its own pre-count.

LOCK_EMAIL_IP = "email_ip"
LOCK_EMAIL = "email"

# KEYS[1]=email+IP failures set, KEYS[2]=per-email failures set.
# ARGV: now, window, ip_max, email_max (0 = no per-email cap), member, ttl.
# Returns 0 admitted (and counted), 1 refused by email+IP, 2 refused by email.
_LUA_ADMIT_LOGIN = """
local cutoff = tonumber(ARGV[1]) - tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, cutoff)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then
  return 1
end
local email_max = tonumber(ARGV[4])
if email_max > 0 then
  redis.call('ZREMRANGEBYSCORE', KEYS[2], 0, cutoff)
  if redis.call('ZCARD', KEYS[2]) >= email_max then
    return 2
  end
  redis.call('ZADD', KEYS[2], tonumber(ARGV[1]), ARGV[5])
  redis.call('EXPIRE', KEYS[2], tonumber(ARGV[6]))
end
redis.call('ZADD', KEYS[1], tonumber(ARGV[1]), ARGV[5])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[6]))
return 0
"""


@dataclass(frozen=True)
class LoginAdmission:
    """The verdict of :func:`admit_login_attempt`.

    ``lock`` names the bucket that refused (``LOCK_EMAIL_IP`` / ``LOCK_EMAIL``),
    None when admitted. ``token`` identifies this attempt's pre-count so
    :func:`refund_login_attempt` can remove exactly it on success.
    """

    admitted: bool
    lock: Optional[str] = None
    token: Any = None


def admit_login_attempt(
    ip_key: str,
    email_key: Optional[str],
    *,
    ip_max: int,
    email_max: int,
    window_seconds: int,
    now: Optional[datetime] = None,
) -> LoginAdmission:
    """Atomically decide one login attempt and, if admitted, count it.

    Refused when the email+IP bucket (``ip_key``) holds ``ip_max`` failures in
    the window, or when ``email_key`` is given, ``email_max > 0`` and the
    per-email bucket holds ``email_max``. Admitted attempts are added to both
    buckets at once; a refused attempt is added to neither, so an address that
    is locked out can never push the per-email bucket further. Call it before
    any ``await`` in the route; on a successful login call
    :func:`clear_failures` (email+IP) and :func:`refund_login_attempt`.
    """
    now = now or datetime.now(timezone.utc)
    use_email = email_key is not None and email_max > 0
    client = _redis_client()
    if client is not None:
        try:
            member = f"{now.timestamp()}:{uuid.uuid4().hex}"
            verdict = int(
                client.eval(
                    _LUA_ADMIT_LOGIN,
                    2,
                    f"rl:fail:{ip_key}",
                    f"rl:fail:{email_key or ip_key}",
                    now.timestamp(),
                    window_seconds,
                    ip_max,
                    email_max if use_email else 0,
                    member,
                    _REDIS_FAILURE_TTL_SECONDS,
                )
            )
            if verdict == 1:
                return LoginAdmission(False, LOCK_EMAIL_IP)
            if verdict == 2:
                return LoginAdmission(False, LOCK_EMAIL)
            return LoginAdmission(True, None, member)
        except Exception as exc:
            logger.warning("rate_limit: Redis admit_login_attempt failed, fallback: %s", exc)

    cutoff = now - timedelta(seconds=window_seconds)
    with _LOCK:
        ip_bucket = _FAILURES.setdefault(ip_key, deque())
        while ip_bucket and ip_bucket[0] < cutoff:
            ip_bucket.popleft()
        if len(ip_bucket) >= ip_max:
            return LoginAdmission(False, LOCK_EMAIL_IP)
        if email_key is not None and email_max > 0:
            email_bucket = _FAILURES.setdefault(email_key, deque())
            while email_bucket and email_bucket[0] < cutoff:
                email_bucket.popleft()
            if len(email_bucket) >= email_max:
                return LoginAdmission(False, LOCK_EMAIL)
            email_bucket.append(now)
        ip_bucket.append(now)
        return LoginAdmission(True, None, now)


def refund_login_attempt(key: str, admission: LoginAdmission) -> None:
    """Remove the one pre-count ``admission`` added to ``key`` (a successful
    login is not a failure). Other attempts' counts are untouched; a no-op when
    the entry has already aged out or the attempt was never admitted."""
    if not admission.admitted or admission.token is None:
        return
    if isinstance(admission.token, str):  # counted in Redis
        client = _redis_client()
        if client is not None:
            try:
                client.zrem(f"rl:fail:{key}", admission.token)
            except Exception as exc:
                logger.warning("rate_limit: Redis refund_login_attempt failed: %s", exc)
        return
    with _LOCK:
        bucket = _FAILURES.get(key)
        if bucket is None:
            return
        try:
            bucket.remove(admission.token)
        except ValueError:
            return
        if not bucket:
            _FAILURES.pop(key, None)


def reset_for_tests() -> None:
    """Clear all buckets — call between tests to prevent cross-pollination.

    Production code MUST NOT call this. Tests should call it in setup or
    use the fixture pattern that wraps it in monkeypatch.
    """
    _reset_redis_for_tests()
    with _LOCK:
        _BUCKETS.clear()
        _FAILURES.clear()
