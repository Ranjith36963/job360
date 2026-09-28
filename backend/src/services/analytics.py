"""Server-side PostHog capture — two funnel events only the backend can see.

Owner decision, 2026-09-28: `first_tool_call` (a user's first MCP tool call,
ANY tool — fired from the MCP server's own middleware) and `first_bring`
(first successful `bring_job`, web or MCP — both surfaces call the one route
function in `src/api/routes/bring.py`, so capturing there covers both without
duplication). The frontend already has its own PostHog client (`posthog-js`)
for events a browser can see (`signup_completed`, `connect_address_copied`);
these two cannot be seen there because the assistant, not the browser, makes
the call.

Off by default (`settings.ANALYTICS_BACKEND_ENABLED`). The frontend's consent
banner (fable/05 C3) stores its choice in the browser only — there is no
per-user consent flag in the database — so this is the owner's own opt-in for
every user, not a per-user check, until a stored consent flag exists.

Fire-and-forget by design (product rule: analytics must never slow down or
break a real request). The DB claim below (`mark_first_tool_call` /
`mark_first_bring`) is a single indexed `UPDATE ... RETURNING`, cheap enough
to await inline; only the network call to PostHog is deferred to a background
task, so a slow or unreachable PostHog can never add latency to a tool call or
a bring.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping

from src.core import settings
from src.utils.logger import get_logger

if TYPE_CHECKING:  # pragma: no cover — type-only
    from src.repositories import pg

logger = get_logger(__name__)


def capture_event(distinct_id: str, event: str, properties: Mapping[str, Any] | None = None) -> None:
    """Fire `event` to PostHog for `distinct_id`. Never raises, never blocks.

    A no-op unless both `ANALYTICS_BACKEND_ENABLED` and a project key are set.
    The actual HTTP POST runs as a background task — this function returns
    before any network I/O happens.
    """
    if not settings.ANALYTICS_BACKEND_ENABLED or not settings.POSTHOG_PROJECT_API_KEY:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover — called outside a running event loop
        logger.debug("analytics_no_event_loop", extra={"event": event})
        return
    task = loop.create_task(_send(distinct_id, event, dict(properties or {})))
    # Nothing awaits this task — that is the point (fire-and-forget). Keep a
    # reference only long enough that a fast test loop doesn't warn about a
    # task being garbage-collected before it runs; the task itself swallows
    # every error, so no result is ever read back.
    task.add_done_callback(lambda _t: None)


async def _send(distinct_id: str, event: str, properties: dict[str, Any]) -> None:
    import aiohttp  # heavy import stays local (rule #16)

    payload = {
        "api_key": settings.POSTHOG_PROJECT_API_KEY,
        "event": event,
        "distinct_id": distinct_id,
        "properties": properties,
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.post(f"{settings.POSTHOG_HOST}/capture/", json=payload):
                pass
    except Exception:  # noqa: BLE001 — analytics must never break the caller
        logger.debug("analytics_capture_failed", extra={"event": event})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def mark_first_tool_call(conn: pg.Connection, user_id: str) -> bool:
    """Atomically claim "this user's first MCP tool call ever".

    Safe to call on every tool invocation — an indexed `UPDATE ... WHERE
    first_tool_call_at IS NULL RETURNING id` only ever returns a row for the
    ONE call that wins the race, so the caller fires `first_tool_call` at
    most once per user, however many concurrent tool calls land.
    """
    cur = await conn.execute(
        "UPDATE users SET first_tool_call_at = ? WHERE id = ? AND first_tool_call_at IS NULL RETURNING id",
        (_now(), user_id),
    )
    row = await cur.fetchone()
    return row is not None


async def mark_first_bring(conn: pg.Connection, user_id: str) -> bool:
    """Atomically claim "this user's first successful bring_job ever"
    (web or MCP — both call the one `bring_job` route function). Same
    fire-once-not-twice guarantee as `mark_first_tool_call`."""
    cur = await conn.execute(
        "UPDATE users SET first_bring_at = ? WHERE id = ? AND first_bring_at IS NULL RETURNING id",
        (_now(), user_id),
    )
    row = await cur.fetchone()
    return row is not None
