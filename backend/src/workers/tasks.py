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
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import aiosqlite

from src.models import Job
from src.services.feed import FeedService
from src.services.prefilter import FilterProfile, passes_prefilter
from src.services.profile.models import SearchConfig
from src.services.skill_matcher import JobScorer


def idempotency_key(user_id: str, job_id: int, channel: str) -> str:
    """Stable hash for (user, job, channel) — blueprint §1 dedup key."""
    raw = f"{user_id}:{job_id}:{channel}".encode()
    return hashlib.sha1(raw, usedforsecurity=False).hexdigest()


async def _load_users(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    """Fetch all active users with their filter profile.

    Batch 2 stores the profile fields inline on a future user_profiles table;
    until that lands, the function accepts a fixture-provided path via
    ``ctx['users_loader']`` in tests.
    """
    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT id FROM users WHERE deleted_at IS NULL")
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
    # JobScorer. Batch 3.5.2: each user scores against THEIR OWN SearchConfig
    # loaded from the user_profiles table. The cache is local to this call
    # so two concurrent worker invocations never share scorer state.
    scorer_fn: Optional[Callable[[str, Job], int]] = ctx.get("scorer")
    user_scorers: dict[str, JobScorer] = {}

    def _scorer_for(user_id: str) -> JobScorer:
        if user_id not in user_scorers:
            user_scorers[user_id] = JobScorer(_search_config_for(user_id))
        return user_scorers[user_id]

    for user_id, profile, threshold in targets:
        if not passes_prefilter(profile, job):
            continue
        if scorer_fn is not None:
            score = int(scorer_fn(user_id, job))
        else:
            # Step-1 B4: JobScorer.score() now returns a ScoreBreakdown —
            # unpack match_score before comparing against the threshold.
            score = int(_scorer_for(user_id).score(job).match_score)
        bucket = _bucket_for_row(job_row)
        await feed.upsert_feed_row(user_id=user_id, job_id=job_id, score=score, bucket=bucket)
        ingested += 1

        if score >= threshold:
            await _record_ledger_if_new(db, user_id=user_id, job_id=job_id, channel="instant")
            enqueue = ctx.get("enqueue")
            if enqueue is not None:
                result = enqueue("send_notification", user_id, job_id, "instant")
                # Accept both sync and async enqueue hooks (tests prefer sync).
                if hasattr(result, "__await__"):
                    await result
            queued += 1

    return {"ingested": ingested, "notifications_queued": queued}


async def _record_ledger_if_new(db: aiosqlite.Connection, *, user_id: str, job_id: int, channel: str) -> bool:
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


async def mark_ledger_sent(db: aiosqlite.Connection, *, user_id: str, job_id: int, channel: str) -> None:
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
    cur = await db.execute("SELECT title, company, apply_url FROM jobs WHERE id = ?", (job_id,))
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
        await _record_ledger_if_new(db, user_id=user_id, job_id=job_id, channel=channel_key)
        if result.ok:
            await mark_ledger_sent(db, user_id=user_id, job_id=job_id, channel=channel_key)
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


async def mark_ledger_sent_task(ctx: dict, user_id: str, job_id: int, channel: str) -> None:
    """ARQ ctx wrapper around :func:`mark_ledger_sent`."""
    await mark_ledger_sent(ctx["db"], user_id=user_id, job_id=job_id, channel=channel)


async def mark_ledger_failed_task(ctx: dict, user_id: str, job_id: int, channel: str, error: str) -> None:
    """ARQ ctx wrapper around :func:`mark_ledger_failed`."""
    await mark_ledger_failed(
        ctx["db"],
        user_id=user_id,
        job_id=job_id,
        channel=channel,
        error=error,
    )


# ---------- helpers ----------------------------------------------------


def _search_config_for(user_id: str) -> SearchConfig:
    """Build the user's SearchConfig from their stored profile, else defaults.

    Reads from the user_profiles table (Batch 3.5.2). On any failure —
    no row, schema drift, JSON decode — falls back to
    ``SearchConfig.from_defaults()`` so the worker never crashes on a
    bad row.
    """
    try:
        from src.services.profile.keyword_generator import generate_search_config
        from src.services.profile.storage import load_profile

        profile = load_profile(user_id)
        if profile and profile.is_complete:
            return generate_search_config(profile)
    except Exception:  # noqa: BLE001, S110 — fall back silently to defaults if profile load fails
        pass
    return SearchConfig.from_defaults()


def _default_search_config() -> SearchConfig:
    """Back-compat shim for pre-Batch-3.5.2 callers / existing tests.

    New code should call ``_search_config_for(user_id)`` directly. This
    function now reads the DEFAULT_TENANT_ID row from user_profiles and
    is therefore functionally equivalent to the single-file era from a
    CLI perspective.
    """
    from src.core.tenancy import DEFAULT_TENANT_ID

    return _search_config_for(DEFAULT_TENANT_ID)


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


# ---------- Pillar 2 Batch 2.5 — job enrichment task -------------------
#
# Queued post-ingest (after ``score_and_ingest``) via the ARQ fan-out hook in
# ``ctx['enqueue']``. Idempotent: a second call on a ``job_id`` that already
# has a ``job_enrichment`` row is a no-op. Tests inject a mock
# ``llm_extract_validated`` through ``ctx['llm_extract_validated']`` so
# the LLM provider chain is never touched during pytest (CLAUDE.md rule #4).


async def enrich_job_task(ctx: dict, job_id: int) -> dict[str, bool | str]:
    """Produce a :class:`JobEnrichment` row for ``job_id``.

    Skips work if the row already exists (idempotence). The LLM call itself
    is injected via ``ctx['llm_extract_validated']`` for tests; prod paths
    use the real :func:`llm_extract_validated`.

    Returns a summary dict with ``enriched: bool`` and an optional
    ``reason`` — the task never raises so ARQ doesn't retry on our account.
    """
    from src.services.job_enrichment import (
        enrich_job,
        has_enrichment,
        save_enrichment,
    )

    db: aiosqlite.Connection = ctx["db"]

    if await has_enrichment(db, job_id):
        return {"enriched": False, "reason": "already_enriched"}

    db.row_factory = aiosqlite.Row
    cur = await db.execute(
        "SELECT id, title, company, location, description FROM jobs WHERE id = ?",
        (job_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return {"enriched": False, "reason": "job_not_found"}

    job = Job(
        title=row["title"] or "",
        company=row["company"] or "",
        apply_url="",
        source="",
        date_found="",
        location=row["location"] or "",
        description=row["description"] or "",
    )
    job.id = row["id"]

    try:
        enrichment = await enrich_job(
            job,
            llm_extract_validated_fn=ctx.get("llm_extract_validated"),
        )
    except Exception as exc:  # noqa: BLE001 — defensive top-level
        return {"enriched": False, "reason": f"llm_error: {exc}"}

    await save_enrichment(db, job_id, enrichment)
    return {"enriched": True}
