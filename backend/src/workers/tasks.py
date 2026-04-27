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

from src.core.settings import ENRICHMENT_THRESHOLD
from src.models import Job
from src.services.feed import FeedService
from src.services.job_enrichment import ENRICHMENT_ENABLED, _build_enrichment_lookup
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
    # B10 — track whether we've fanned out an enrich_job_task for this job.
    # Enrichment is shared catalog (CLAUDE.md rule #17): one enqueue per job,
    # not one per user. We fire once the FIRST user crosses ENRICHMENT_THRESHOLD.
    enrichment_enqueued = False

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

    # Pillar 2 Batch 2.9 — enrichment lookup is the same for every user
    # (job_enrichment is shared catalog per CLAUDE.md rule #10). Build once,
    # share across the per-user scorer cache. Empty dict ⇒ no rows ⇒
    # multi-dim contributes 0 (legacy 4-component path preserved).
    enrichment_lookup_dict = await _build_enrichment_lookup(db)
    enrichment_lookup_fn = lambda job: enrichment_lookup_dict.get(getattr(job, "id", None))  # noqa: E731

    def _scorer_for(user_id: str) -> JobScorer:
        if user_id not in user_scorers:
            profile = _user_profile_for(user_id)
            prefs = profile.preferences if profile is not None else None
            user_scorers[user_id] = JobScorer(
                _search_config_for(user_id),
                user_preferences=prefs,
                enrichment_lookup=enrichment_lookup_fn,
            )
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

        # B10 — fan out enrichment for catalog-quality jobs. Mirror the
        # CLI path's threshold-gated enrich_batch invocation (Agent-Enrichment),
        # but as ARQ enqueue (one task per job, not blocking the worker tick).
        # Default-off via ENRICHMENT_ENABLED (CLAUDE.md rule #18).
        if ENRICHMENT_ENABLED and not enrichment_enqueued and score >= ENRICHMENT_THRESHOLD:
            enqueue = ctx.get("enqueue")
            if enqueue is not None:
                result = enqueue("enrich_job_task", job_id)
                if hasattr(result, "__await__"):
                    await result
            enrichment_enqueued = True

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


def _user_profile_for(user_id: str):
    """Load the user's full ``UserProfile`` from the user_profiles table.

    Returns ``None`` on any failure — no row, schema drift, JSON decode
    error — so callers can skip multi-dim wiring and stay on the legacy
    4-component path (CLAUDE.md rule #19).
    """
    try:
        from src.services.profile.storage import load_profile

        profile = load_profile(user_id)
        if profile and profile.is_complete:
            return profile
    except Exception:  # noqa: BLE001, S110 — defensive, multi-dim is opt-in
        pass
    return None


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


# ---------- Step-3 B-14 — nightly ghost sweep periodic task ------------


async def nightly_ghost_sweep(ctx: dict) -> dict:
    """ARQ periodic task: advance ghost detection state for stale jobs.

    Evaluates every non-expired job in the DB using the pure-function state
    machine in :mod:`src.services.ghost_detection` and writes the new
    ``staleness_state`` back to the ``jobs`` table when it changes.

    Transitions driven by ``evaluate_job_state()``:
      - active → possibly_stale: ≥2 misses + ≥12 h absence
      - possibly_stale → likely_stale: ≥3 misses + ≥24 h absence
      - likely_stale stays until a direct URL check (CONFIRMED_EXPIRED) —
        that step is out of scope for this periodic sweep.

    ``CONFIRMED_EXPIRED`` rows are skipped (sticky per ghost_detection design).

    Returns ``{"evaluated": N, "transitioned": M}`` so the ARQ dashboard can
    surface sweep health without reading the DB.
    """
    from src.services.ghost_detection import StalenessState, evaluate_job_state  # noqa: PLC0415 — lazy

    db: aiosqlite.Connection = ctx["db"]
    db.row_factory = aiosqlite.Row

    # Load all non-expired jobs (CONFIRMED_EXPIRED is sticky; skip it).
    cursor = await db.execute(
        """
        SELECT id, staleness_state, consecutive_misses, last_seen_at
        FROM jobs
        WHERE staleness_state != 'confirmed_expired'
        """
    )
    rows = [dict(r) for r in await cursor.fetchall()]

    evaluated = 0
    transitioned = 0
    for row in rows:
        evaluated += 1
        new_state = evaluate_job_state(row)
        current = row.get("staleness_state") or StalenessState.ACTIVE.value
        if new_state.value != current:
            await db.execute(
                "UPDATE jobs SET staleness_state = ? WHERE id = ?",
                (new_state.value, row["id"]),
            )
            transitioned += 1

    await db.commit()
    return {"evaluated": evaluated, "transitioned": transitioned}


# ---------- Pillar 2 Batch 2.5 — job enrichment task -------------------
#
# Queued post-ingest (after ``score_and_ingest``) via the ARQ fan-out hook in
# ``ctx['enqueue']``. Idempotent: a second call on a ``job_id`` that already
# has a ``job_enrichment`` row is a no-op. Tests inject a mock
# ``llm_extract_validated`` through ``ctx['llm_extract_validated']`` so
# the LLM provider chain is never touched during pytest (CLAUDE.md rule #4).


async def send_daily_digest(ctx: dict, user_id: str, channel: str) -> dict[str, int]:
    """ARQ periodic task: send queued digest notifications for (user_id, channel).

    Step-3 B-04 — daily digest sender.

    Workflow:
      1. Fetch all pending rows from ``user_notification_digests`` for the
         (user_id, channel) pair.
      2. Load job details for every queued job_id.
      3. Format a combined digest message and send via the channel's Apprise
         credentials (through ``send_notification`` test hook if present in ctx).
      4. Mark all pending rows as sent via the DB helper.

    This task is registered in ``WorkerSettings.cron_jobs`` and is designed
    to be enqueued once per user per channel per day by a scheduler — the
    caller controls frequency; the task is idempotent (no-op if no pending rows).

    Returns ``{'sent': 0|1, 'jobs_count': N}``.
    """
    db: aiosqlite.Connection = ctx["db"]
    db.row_factory = aiosqlite.Row

    # 1. Get pending digest rows
    try:
        cur = await db.execute(
            "SELECT * FROM user_notification_digests " "WHERE user_id = ? AND channel = ? AND sent = 0",
            (user_id, channel),
        )
        digest_rows = [dict(r) for r in await cur.fetchall()]
    except Exception:  # noqa: BLE001
        return {"sent": 0, "jobs_count": 0}

    if not digest_rows:
        return {"sent": 0, "jobs_count": 0}

    # 2. Fetch job details
    job_ids = list({r["job_id"] for r in digest_rows})
    job_details: list[dict] = []
    for jid in job_ids:
        cur = await db.execute("SELECT title, company, apply_url FROM jobs WHERE id = ?", (jid,))
        row = await cur.fetchone()
        if row:
            job_details.append(
                {"job_id": jid, "title": row["title"], "company": row["company"], "apply_url": row["apply_url"]}
            )

    # 3. Build digest message and dispatch
    jobs_count = len(job_details)
    if jobs_count == 0:
        # Jobs may have been purged — mark digests sent to avoid re-processing.
        await _mark_digest_rows_sent(db, user_id, channel)
        return {"sent": 0, "jobs_count": 0}

    digest_title = f"Job360 Daily Digest — {jobs_count} new match{'es' if jobs_count > 1 else ''}"
    lines = [f"• {jd['title']} @ {jd['company']} — {jd['apply_url']}" for jd in job_details]
    digest_body = "\n".join(lines)

    # Re-use the send_notification dispatcher hook when available (for tests).
    dispatcher_fn = ctx.get("dispatcher")
    if dispatcher_fn is None:
        from src.services.channels.dispatcher import dispatch as real_dispatch

        dispatcher_fn = real_dispatch

    results = await dispatcher_fn(db, user_id=user_id, title=digest_title, body=digest_body)
    any_sent = any(r.ok and not r.skipped and not r.queued_digest for r in results)

    # 4. Mark rows as sent regardless of dispatch outcome (avoid infinite retry floods).
    await _mark_digest_rows_sent(db, user_id, channel)

    return {"sent": int(any_sent), "jobs_count": jobs_count}


async def _mark_digest_rows_sent(db: aiosqlite.Connection, user_id: str, channel: str) -> None:
    """Helper: flip sent=1 on all pending digest rows for (user_id, channel)."""
    import logging
    from datetime import datetime, timezone

    _log = logging.getLogger(__name__)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        await db.execute(
            "UPDATE user_notification_digests SET sent=1, sent_at=? " "WHERE user_id=? AND channel=? AND sent=0",
            (now, user_id, channel),
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        _log.debug("Could not mark digests sent for user %s channel %s: %s", user_id, channel, exc)


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
