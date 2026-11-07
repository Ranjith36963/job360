"""Learned per-host blocking (owner 2026-11-02, two browsers): read from events Job360 already stores, no table.

When Job360's own browser records ``blocked`` (``by: browser``, the key no caller can write) with reason
``captcha`` or ``bot_check`` on the same form host for ``LEARN_APPS`` different applications of ONE user, that
host is learned as blocked for that user: the next job whose apply link is on that exact host starts in the
user's own browser (``LEARNED_ROUTE``). A later confirmation page seen by Job360's browser while filling a job on
that host (a ``confirmed`` ``fill`` row in ``browser_route_attempts``, written by the browser gate) clears it:
only blocks newer than the last such success count. Blocks older than ``WINDOW`` do not count (sites change).

Per user, never global (rule #12): every query is scoped by ``user_id``. Hosts are compared exactly, as
``routing.site_of`` names them (``guard.canonical_host``), never by substring. Read-only (M3).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from src.services.browser_agent import routing
from src.services.browser_agent.route_book import RETENTION

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

LEARN_REASONS = frozenset({"captcha", "bot_check"})
LEARN_APPS = 2
WINDOW = RETENTION
LEARNED_ROUTE = routing.Route("own_browser", "learned_bot_check")


def host_of(url_or_host: str) -> str:
    """The exact host a link (or a bare host) names, as ``routing.site_of`` gives it; "" when none."""
    return routing.site_of(url_or_host or "")[2]


def route_for(url: str, hosts: frozenset[str]) -> Optional[routing.Route]:
    """``LEARNED_ROUTE`` when the link's exact host is one of the user's learned ``hosts``, else None."""
    host = host_of(url)
    return LEARNED_ROUTE if host and host in hosts else None


def _when(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        at = value
    else:
        try:
            at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


async def blocked_hosts(db: JobDatabase, user_id: str, now: Optional[datetime] = None) -> frozenset[str]:
    """The hosts learned as blocked for ``user_id``: two queries, whatever the queue's size."""
    # recorded_at is TEXT written as a UTC isoformat: compare like with like.
    since = ((now or datetime.now(timezone.utc)) - WINDOW).astimezone(timezone.utc)
    cur = await db._db.execute(
        "SELECT e.application_id, e.payload, e.recorded_at FROM application_events e "
        "WHERE e.user_id = ? AND e.event_type = 'blocked' AND e.recorded_at >= ? "
        "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) "
        "ORDER BY e.id DESC LIMIT 1000",
        (user_id, since.isoformat()),
    )
    blocks: list[tuple[str, int, datetime]] = []
    for row in await cur.fetchall():
        d = dict(row)
        raw = d.get("payload")
        try:
            p = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            continue
        # Job360's browser wrote it (``by``), or an assistant recorded it for the cloud browser (a hand-off).
        if not isinstance(p, dict) or p.get("reason") not in LEARN_REASONS or (
            p.get("by") != "browser" and p.get("browser") != "job360"
        ):
            continue
        host, at = host_of(str(p.get("page_host") or "")), _when(d.get("recorded_at"))
        if host and at is not None and d.get("application_id") is not None:
            blocks.append((host, int(d["application_id"]), at))
    if not blocks:
        return frozenset()
    cur = await db._db.execute(
        "SELECT tenant_host, MAX(occurred_at) AS last_ok FROM browser_route_attempts WHERE user_id = ? "
        "AND browser = 'job360' AND task = 'fill' AND outcome = 'confirmed' GROUP BY tenant_host",
        (user_id,),
    )
    cleared = {str(dict(r)["tenant_host"]): _when(dict(r)["last_ok"]) for r in await cur.fetchall()}
    apps: dict[str, set[int]] = {}
    for host, app_id, at in blocks:
        last_ok = cleared.get(host)
        if last_ok is None or at > last_ok:
            apps.setdefault(host, set()).add(app_id)
    return frozenset(h for h, ids in apps.items() if len(ids) >= LEARN_APPS)
