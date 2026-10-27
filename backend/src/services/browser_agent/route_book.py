"""The route book: what each visit of Job360's browser met, one row per attempt (routes PR C1, record only).

``record`` books one attempt in ``browser_route_attempts`` (migration 0092): the site from
``routing.site_of`` (kind, key, the host it ran on), the task, the browser and the outcome. Every value is
checked against its closed set first; the URL itself, its path and query are never stored (security M1), and
the host never reaches a log line, Sentry or PostHog: a log names only ``site_kind`` and ``outcome`` (H2).
Each write also prunes that user's rows older than ``RETENTION`` in the same transaction.

Nothing routes on these rows yet (no routing change in C1). Own-browser outcomes are not booked here: they stay
in the queue rows, for display only, so an assistant-reported value can never steer a route.
"""
from __future__ import annotations

import logging
import re
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.services.browser_agent import routing
from src.services.browser_agent.outcome import OUTCOMES

_log = logging.getLogger("job360.browser.route_book")

BROWSERS = ("job360", "own_browser", "job360_server")
# A probe's row is booked "pending" before it runs (PR F1); every other row carries a real outcome.
BOOK_OUTCOMES = OUTCOMES + ("pending",)
RETENTION = timedelta(days=30)
MAX_SITE_KEY = 260
# A host name as canonical_host gives it (lower-case ASCII, IDNA applied, at most 253): anything else is no host.
_HOST = re.compile(r"^[a-z0-9.-]{1,253}$")

# Outcomes booked once per (user, session, application, site_key, task): a page that works is one fact a
# session, not one per look. Every other outcome is booked each time it happens.
ONCE_PER_SESSION = frozenset({"ok", "confirmed"})
_SEEN_MAX = 10_000
_seen: OrderedDict[tuple[Any, ...], None] = OrderedDict()


class RouteBookValueError(ValueError):
    """A value outside its closed set: a caller bug, raised before anything is written."""


def _closed(name: str, value: object, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise RouteBookValueError(f"{name} must be one of {', '.join(allowed)}")
    return value


async def record(
    db: Any, user_id: str, url: str, task: str, browser: str, outcome: str, *, probe: bool = False,
    now: Optional[datetime] = None,
) -> bool:
    """Book one attempt for ``user_id``. Raises ``RouteBookValueError`` for a value outside its closed set.
    A link with no host name books nothing. A failed write is logged (site_kind and outcome only) and returns
    False: booking never breaks a browser action. ``now`` is the clock (tests pass the fixture's)."""
    _closed("task", task, routing.LEARN_TASKS)
    _closed("browser", browser, BROWSERS)
    _closed("outcome", outcome, BOOK_OUTCOMES)
    if not isinstance(user_id, str) or not user_id:
        raise RouteBookValueError("user_id is required")
    site_kind, site_key, tenant_host = routing.site_of(url)
    if site_kind == routing.UNNAMED:
        return False  # a host that cannot be named is not a site (#1325): nothing to book
    _closed("site_kind", site_kind, routing.SITE_KINDS)
    if not _HOST.match(tenant_host):
        return False  # no host (or none that is a host name): nothing to book
    if len(site_key) > MAX_SITE_KEY:
        raise RouteBookValueError("site_key is too long")
    at = now or datetime.now(timezone.utc)
    try:
        async with db._db.transaction():
            await db._db.execute(
                "DELETE FROM browser_route_attempts WHERE user_id = ? AND occurred_at < ?",
                (user_id, at - RETENTION),
            )
            await db._db.execute(
                "INSERT INTO browser_route_attempts "
                "(user_id, site_kind, site_key, tenant_host, task, browser, outcome, probe, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, site_kind, site_key, tenant_host, task, browser, outcome, bool(probe), at),
            )
    except Exception as exc:  # noqa: BLE001 - booking never breaks a browser action
        _log.warning(
            "route attempt not booked",
            extra={"event": "route_attempt_failed", "site_kind": site_kind, "outcome": outcome,
                   "error": type(exc).__name__},
        )
        return False
    return True


def first_in_session(user_id: str, session: str, application_id: Optional[int], url: str, task: str,
                     outcome: str) -> bool:
    """True when this attempt should be booked: always for a failure, once per session for ``ONCE_PER_SESSION``.
    Bounded in memory (the oldest keys fall out first)."""
    if outcome not in ONCE_PER_SESSION:
        return True
    key = (user_id, session, application_id, routing.site_of(url)[1], task, outcome)
    if key in _seen:
        return False
    _seen[key] = None
    while len(_seen) > _SEEN_MAX:
        _seen.popitem(last=False)
    return True
