"""A public LinkedIn job page, read by Job360's server with no sign-in (routes PR D, rule S6).

The owner's decision (2026-10-26): Job360's server may read public LinkedIn job pages; the user's signed-in
LinkedIn stays in their own browser, and nothing of it ever reaches this server. So this module only ever asks
for LinkedIn's signed-out ``jobPosting`` fragment, by the job's number: never the caller's link itself, never a
cookie (``fetch_url`` keeps none), never a redirect (``follow_redirects=False``), always through the SSRF guard
in ``services/fetch``. Chromium never loads LinkedIn: this is the ``browser`` tool's ``open`` for a job page.

One strike: any 3xx (LinkedIn's authwall and login pages are redirects), 999, 401 or 403 books ``bot_block``
(``browser="job360_server"``, ``site_key="linkedin"``, ``task="read"``) in the route book, and every read for
that user is refused for ``BLOCK_FOR`` with reason ``linkedin_guest_blocked``. The refusal reads the newest such
row; the route book keeps 30 days, so the 7-day window is always covered. Only transport facts decide (S0): the
status code, never the page text. The text that comes back is the site's, fenced like every page the browser
tool returns.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlsplit

from src.services.browser_agent import route_book, routing
from src.services.fetch.guard import canonical_host
from src.services.untrusted import UNTRUSTED, wrap_untrusted

BLOCK_FOR = timedelta(days=7)
JOB_HOST = "www.linkedin.com"
GUEST_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"
# /jobs/view/<digits>, optionally behind a slug that ends in "-<digits>" (routing._LINKEDIN_JOB_READ's shape).
_JOB_PATH = re.compile(r"^/jobs/view/(?:[^/]*-)?(\d{1,20})/?$")
STRIKE_STATUSES = frozenset({401, 403, 999})
MAX_TEXT = 20_000

NOT_A_JOB_PAGE = (
    "Only a public LinkedIn job page (linkedin.com/jobs/view/<number>) is read by Job360's server. Every other "
    "LinkedIn page opens only in the user's own browser (their choice): leave it in the own-browser queue."
)
BLOCKED = (
    "LinkedIn blocked Job360's signed-out read, so LinkedIn job pages wait for the user's own browser until {date}."
)
NOT_LOADED = "The LinkedIn job page did not load ({outcome}). Read it in the user's own browser."


def job_id(url: str) -> Optional[str]:
    """The job's number from ``https://www.linkedin.com/jobs/view/<digits>`` (a slug ending in the number is
    allowed, the query is ignored); None for anything else."""
    text = (url or "").strip()
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or port not in (None, 443) or canonical_host(text) != JOB_HOST:
        return None
    m = _JOB_PATH.match(parts.path)
    return m.group(1) if m else None


async def blocked_until(db: Any, user_id: str, now: datetime) -> Optional[datetime]:
    """When this user's 7-day stop ends, from their newest server ``bot_block`` on LinkedIn; None when none
    is running."""
    cur = await db._db.execute(
        "SELECT occurred_at FROM browser_route_attempts WHERE user_id = ? AND site_key = 'linkedin' "
        "AND task = 'read' AND browser = 'job360_server' AND outcome = 'bot_block' "
        "ORDER BY occurred_at DESC LIMIT 1",
        (user_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    until = dict(row)["occurred_at"] + BLOCK_FOR
    return until if until > now else None


def _blocked(until: datetime, *, recorded: bool) -> dict[str, Any]:
    route = routing.Route("own_browser", "linkedin_guest_blocked")
    return {
        "status": "refused", "reason": route.reason, "detail": BLOCKED.format(date=f"{until.day} {until:%b}"),
        "until": until.isoformat(), "bot_block_recorded": recorded, "browser": routing.label(route, date=until),
    }


async def read_job(db: Any, user_id: str, url: str, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Read one public LinkedIn job page for ``user_id``. ``{"status": "ok", "text": <fenced>}`` on a 200;
    ``refused`` for any other link (before any fetch) and while a strike runs (no request at all); a strike
    books ``bot_block`` and starts the stop. ``now`` is the clock (tests pass the fixture's)."""
    from src.services.fetch import extract, fetcher  # noqa: PLC0415 - aiohttp stays lazy (rule #16)

    ident = job_id(url)
    if ident is None:
        return {"status": "refused", "reason": "linkedin_own_browser", "detail": NOT_A_JOB_PAGE}
    at = now or datetime.now(timezone.utc)
    until = await blocked_until(db, user_id, at)
    if until is not None:
        return _blocked(until, recorded=False)
    result = await fetcher.fetch_url(GUEST_URL.format(id=ident), follow_redirects=False)
    if 300 <= result.status < 400 or result.status in STRIKE_STATUSES:
        recorded = await route_book.record(db, user_id, url, "read", "job360_server", "bot_block", now=at)
        return _blocked(at + BLOCK_FOR, recorded=recorded)
    if result.outcome != "ok":
        return {"status": "error", "reason": "load_failed", "detail": NOT_LOADED.format(outcome=result.outcome)}
    text = extract.strip_html_to_text(result.html, max_len=MAX_TEXT)
    return {
        "status": "ok", "url": f"https://{JOB_HOST}/jobs/view/{ident}/", "note": UNTRUSTED,
        "text": wrap_untrusted(text), "browser": routing.label(routing.Route("job360", "linkedin_guest_read")),
    }
