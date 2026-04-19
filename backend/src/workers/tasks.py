"""Worker task functions.

These are plain ``async def`` — they can be called directly from tests
(with a stub ``ctx``) or registered as ARQ functions (see
``workers/settings.py``). The tasks touch only:
  * ``ctx['db']`` — an open aiosqlite.Connection
  * ``ctx['enqueue']`` — async callable(function_name, *args) used for fan-out
    (in tests, a ``list.append``-style stub; in prod, ``ctx['redis'].enqueue_job``)
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import aiosqlite

from src.models import Job
from src.services.feed import FeedService
from src.services.prefilter import FilterProfile, passes_prefilter
from src.services.skill_matcher import JobScorer
from src.services.profile.models import SearchConfig


def idempotency_key(user_id: str, job_id: int, channel: str) -> str:
    """Stable hash for (user, job, channel) — blueprint §1 dedup key."""
    raw = f"{user_id}:{job_id}:{channel}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


async def _load_users(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    """Fetch all active users with their filter profile.

    Batch 2 stores the profile fields inline on a future user_profiles table;
    until that lands, the function accepts a fixture-provided path via
    ``ctx['users_loader']`` in tests.
    """
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
        "SELECT id FROM users WHERE deleted_at IS NULL"
    )
    return [dict(r) for r in await cur.fetchall()]


async def score_and_ingest(
    ctx: dict,
    job_id: int,
    *,
    users_override: Optional[list[tuple[str, FilterProfile, int]]] = None,
) -> dict[str, int]:
    """Pre-filter + score + upsert feed rows for every active user.

    Parameters
    ----------
    ctx : dict
        Worker context. Must contain ``'db'`` (aiosqlite.Connection).
    job_id : int
        Row id in the ``jobs`` table.
    users_override : optional
        Test hook — list of (user_id, FilterProfile, instant_threshold).
        When None, the task loads users from the DB.

    Returns ``{'ingested': N, 'notifications_queued': M}``.
    """
    db: aiosqlite.Connection = ctx["db"]
    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    job_row = await cur.fetchone()
    if job_row is None:
        return {"ingested": 0, "notifications_queued": 0}

    job = Job(
        title=job_row["title"],
        company=job_row["company"],
        apply_url=job_row["apply_url"],
        source=job_row["source"],
        date_found=_parse_dt(job_row["date_found"]),
        location=job_row["location"] or "",
        description=job_row["description"] or "",
        match_score=job_row["match_score"],
    )

    feed = FeedService(db)
    ingested = 0
    queued = 0

    if users_override is not None:
        targets = users_override
    else:
        # No per-user profile storage yet; fall back to "all users see all jobs".
        users = await _load_users(db)
        targets = [(u["id"], FilterProfile(), 80) for u in users]

    # Stage 4 of the 99% cascade (decisions doc D8): per-user scoring via
    # JobScorer. Pre-Batch-3 every user shares one SearchConfig (loaded from
    # user_profile.json when it exists, else defaults). Per-user
    # SearchConfig lands with the Batch 3 user_profiles table — the call
    # site here is correct today and lights up for real then.
    scorer_fn: Optional[Callable[[str, Job], int]] = ctx.get("scorer")
    default_scorer: Optional[JobScorer] = None
    if scorer_fn is None:
        default_scorer = JobScorer(_default_search_config())

    for user_id, profile, threshold in targets:
        if not passes_prefilter(profile, job):
            continue
        if scorer_fn is not None:
            score = int(scorer_fn(user_id, job))
        else:
            assert default_scorer is not None  # narrowing for type-checker
            score = int(default_scorer.score(job))
        bucket = _bucket_for_row(job_row)
        await feed.upsert_feed_row(
            user_id=user_id, job_id=job_id, score=score, bucket=bucket
        )
        ingested += 1

        if score >= threshold:
            await _record_ledger_if_new(
                db, user_id=user_id, job_id=job_id, channel="instant"
            )
            enqueue = ctx.get("enqueue")
            if enqueue is not None:
                result = enqueue("send_notification", user_id, job_id, "instant")
                # Accept both sync and async enqueue hooks (tests prefer sync).
                if hasattr(result, "__await__"):
                    await result
            queued += 1

    return {"ingested": ingested, "notifications_queued": queued}


async def _record_ledger_if_new(
    db: aiosqlite.Connection, *, user_id: str, job_id: int, channel: str
) -> bool:
    """Insert a ledger row in ``queued`` state. Idempotent per (user, job, channel).

    Returns True if a new row was created, False if it already existed.
    """
    try:
        await db.execute(
            """
            INSERT INTO notification_ledger(user_id, job_id, channel, status)
            VALUES (?, ?, ?, 'queued')
            """,
            (user_id, job_id, channel),
        )
        await db.commit()
        return True
    except aiosqlite.IntegrityError:
        await db.rollback()
        return False


async def mark_ledger_sent(
    db: aiosqlite.Connection, *, user_id: str, job_id: int, channel: str
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """
        UPDATE notification_ledger
           SET status = 'sent', sent_at = ?, error_message = NULL
         WHERE user_id = ? AND job_id = ? AND channel = ?
        """,
        (now, user_id, job_id, channel),
    )
    await db.commit()


async def mark_ledger_failed(
    db: aiosqlite.Connection,
    *,
    user_id: str,
    job_id: int,
    channel: str,
    error: str,
) -> None:
    await db.execute(
        """
        UPDATE notification_ledger
           SET status = 'failed',
               error_message = ?,
               retry_count = retry_count + 1
         WHERE user_id = ? AND job_id = ? AND channel = ?
        """,
        (error[:500], user_id, job_id, channel),
    )
    await db.commit()


# ---------- ARQ ctx-based fan-out tasks --------------------------------
#
# These are the top-level functions registered in
# src.workers.settings.WorkerSettings.functions. ARQ contract: first arg
# is `ctx: dict` with 'db' (aiosqlite.Connection) and optionally 'enqueue'.


async def send_notification(
    ctx: dict,
    user_id: str,
    job_id: int,
    urgency: str = "instant",
) -> dict[str, int]:
    """Dispatch a per-user notification across every enabled channel.

    Reads user_feed for job context, asks the dispatcher to fan out to
    every enabled channel for ``user_id``, and writes one
    ``notification_ledger`` row per channel — ``sent`` on success,
    ``failed`` (with error_message) on Apprise exception.

    Idempotency: each (user_id, job_id, channel) gets at most one ledger
    row per the UNIQUE(user_id, job_id, channel) constraint from
    migration 0004. A retry simply re-reads the row and flips its
    status; no duplicate inserts.

    Parameters
    ----------
    ctx : dict
        Must contain ``'db'`` (aiosqlite.Connection). Optionally:
        ``'dispatcher'`` — a test hook returning
        ``list[ChannelSendResult]``; when absent, uses the real
        ``services.channels.dispatcher.dispatch``.
    user_id : str
        Target user.
    job_id : int
        ``jobs.id`` primary key.
    urgency : str
        One of ``'instant' | 'digest'`` (for future routing; currently
        unused beyond audit).

    Returns ``{'sent': int, 'failed': int}``.
    """
    db: aiosqlite.Connection = ctx["db"]
    db.row_factory = aiosqlite.Row

    # Fetch job context for the notification body
    cur = await db.execute(
        "SELECT title, company, apply_url FROM jobs WHERE id = ?", (job_id,)
    )
    job_row = await cur.fetchone()
    if job_row is None:
        return {"sent": 0, "failed": 0}

    title = f"{job_row['title']} @ {job_row['company']}"
    body = f"Job360 match: {job_row['title']}\n{job_row['apply_url']}"

    # Test hook: ctx['dispatcher'] short-circuits the real Apprise path.
    # In production, we import lazily to dodge Apprise's ~30MB dep chain
    # per CLAUDE.md rule #11.
    dispatcher_fn = ctx.get("dispatcher")
    if dispatcher_fn is None:
        from src.services.channels.dispatcher import dispatch as real_dispatch
        dispatcher_fn = real_dispatch

    results = await dispatcher_fn(db, user_id=user_id, title=title, body=body)

    sent = 0
    failed = 0
    for result in results:
        channel_key = result.channel_type or f"channel:{result.channel_id}"
        # Ensure a ledger row exists (idempotent per UNIQUE constraint).
        await _record_ledger_if_new(
            db, user_id=user_id, job_id=job_id, channel=channel_key
        )
        if result.ok:
            await mark_ledger_sent(
                db, user_id=user_id, job_id=job_id, channel=channel_key
            )
            sent += 1
        else:
            await mark_ledger_failed(
                db,
                user_id=user_id,
                job_id=job_id,
                channel=channel_key,
                error=result.error or "unknown error",
            )
            failed += 1

    return {"sent": sent, "failed": failed}


async def mark_ledger_sent_task(
    ctx: dict, user_id: str, job_id: int, channel: str
) -> None:
    """ARQ ctx wrapper around :func:`mark_ledger_sent`."""
    await mark_ledger_sent(
        ctx["db"], user_id=user_id, job_id=job_id, channel=channel
    )


async def mark_ledger_failed_task(
    ctx: dict, user_id: str, job_id: int, channel: str, error: str
) -> None:
    """ARQ ctx wrapper around :func:`mark_ledger_failed`."""
    await mark_ledger_failed(
        ctx["db"],
        user_id=user_id,
        job_id=job_id,
        channel=channel,
        error=error,
    )


# ---------- helpers ----------------------------------------------------

def _default_search_config() -> SearchConfig:
    """Load the single shared SearchConfig for now.

    Pre-Batch-3 there is no ``user_profiles`` table. The legacy
    ``user_profile.json`` path is read by ``src.services.profile.storage``
    at the CLI boundary; if that file exists, the stored profile is used.
    If it does not, we fall back to ``SearchConfig.from_defaults()``.
    Batch 3 replaces this with a per-user config keyed by ``user_id``.
    """
    try:
        from src.services.profile.keyword_generator import generate_search_config
        from src.services.profile.storage import load_profile

        profile = load_profile()
        if profile and profile.is_complete:
            return generate_search_config(profile)
    except Exception:  # noqa: BLE001 — any profile load failure → defaults
        pass
    return SearchConfig.from_defaults()



def _parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _bucket_for_row(row: aiosqlite.Row) -> str:
    """Compute time bucket from the 5-column date model (Batch 1) with fallbacks."""
    first_seen_raw = row["first_seen_at"] if "first_seen_at" in row.keys() else None
    if not first_seen_raw:
        first_seen_raw = row["first_seen"] if "first_seen" in row.keys() else None
    if not first_seen_raw:
        return "3_7d"
    first_seen = _parse_dt(first_seen_raw)
    age_h = (datetime.now(timezone.utc) - first_seen).total_seconds() / 3600
    if age_h <= 24:
        return "24h"
    if age_h <= 48:
        return "24_48h"
    if age_h <= 72:
        return "48_72h"
    return "3_7d"
