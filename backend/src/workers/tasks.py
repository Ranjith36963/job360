"""Worker task functions.

These are plain ``async def`` — they can be called directly from tests
(with a stub ``ctx``) or registered as ARQ functions (see
``workers/settings.py``). The tasks touch only:
  * ``ctx['db']`` — an open pg.Connection
  * ``ctx['enqueue']`` — async callable(function_name, *args) used for fan-out
    (in tests, a ``list.append``-style stub; in prod, ``ctx['redis'].enqueue_job``)
"""

from __future__ import annotations

import functools
import hashlib
import time
from datetime import datetime, timezone
from datetime import time as _time
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

if TYPE_CHECKING:
    from src.services.profile.models import UserProfile

from src.core.settings import ENRICHMENT_THRESHOLD
from src.models import Job
from src.repositories import pg
from src.services.feed import FeedService
from src.services.job_enrichment import ENRICHMENT_ENABLED, _build_enrichment_lookup
from src.services.prefilter import FilterProfile, passes_prefilter
from src.services.profile.models import SearchConfig
from src.services.skill_matcher import JobScorer
from src.utils.logger import get_logger

_log = get_logger("worker")  # "job360.worker" → data/logs/


_TaskFn = Callable[..., Awaitable[Any]]


def _logged_task(fn: _TaskFn) -> _TaskFn:
    """Wrap an ARQ task with start / done(+duration) / error(+traceback) logging.

    Gap H — the background worker layer ran completely dark. Every task now
    emits a structured line so "why didn't my notification fire / cron run?" is
    answerable. ``functools.wraps`` keeps ``__name__`` so ARQ's registry and the
    direct-call test usage are unaffected.

    Typed as ``Callable[..., Awaitable[Any]] -> Callable[..., Awaitable[Any]]``
    rather than via a signature-preserving ``ParamSpec`` — ``ParamSpec`` needs
    Python 3.10's ``typing.ParamSpec`` at runtime (this module still runs on
    3.9) or a new ``typing_extensions`` dependency, neither of which this
    annotation-only pass may introduce. The loosened signature is why every
    decorated task below keeps its own explicit parameter/return annotations.
    """

    @functools.wraps(fn)
    async def _wrapped(ctx: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        _log.info("task_start", extra={"event": "task_start", "task": fn.__name__})
        try:
            result = await fn(ctx, *args, **kwargs)
            _log.info(
                "task_done",
                extra={
                    "event": "task_done",
                    "task": fn.__name__,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
            return result
        except Exception as e:
            _log.error(
                "task_error",
                extra={"event": "task_error", "task": fn.__name__, "error": str(e)},
                exc_info=True,
            )
            raise

    return _wrapped


def idempotency_key(user_id: str, job_id: int, channel: str) -> str:
    """Stable hash for (user, job, channel) — blueprint §1 dedup key."""
    raw = f"{user_id}:{job_id}:{channel}".encode()
    return hashlib.sha1(raw, usedforsecurity=False).hexdigest()


async def _load_users(db: pg.Connection) -> list[dict[str, Any]]:
    """Fetch all active users with their filter profile.

    Batch 2 stores the profile fields inline on a future user_profiles table;
    until that lands, the function accepts a fixture-provided path via
    ``ctx['users_loader']`` in tests.
    """
    db.row_factory = pg.Row
    cur = await db.execute("SELECT id FROM users WHERE deleted_at IS NULL")
    return [dict(r) for r in await cur.fetchall()]


@_logged_task
async def score_and_ingest(
    ctx: dict[str, Any],
    job_id: int,
    *,
    users_override: Optional[list[tuple[str, FilterProfile, int]]] = None,
) -> dict[str, int]:
    """Pre-filter + score + upsert feed rows for every active user.

    Parameters
    ----------
    ctx : dict
        Worker context. Must contain ``'db'`` (pg.Connection).
    job_id : int
        Row id in the ``jobs`` table.
    users_override : optional
        Test hook — list of (user_id, FilterProfile, instant_threshold).
        When None, the task loads users from the DB.

    Returns ``{'ingested': N, 'notifications_queued': M}``.
    """
    db: pg.Connection = ctx["db"]
    db.row_factory = pg.Row
    cur = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    job_row = await cur.fetchone()
    if job_row is None:
        return {"ingested": 0, "notifications_queued": 0}

    job = _job_from_row(job_row)

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
    # getattr(job, "id", None) -> Optional[int] at runtime (Job has no
    # declared `id` field; see the enrich_job_task note below on the same
    # pattern), but mypy can only see `Any | None`, hence the ignore. This is
    # the documented call pattern from job_enrichment._build_enrichment_lookup's
    # own docstring — dict.get(None) safely returns None, not a crash.
    enrichment_lookup_fn = lambda job: enrichment_lookup_dict.get(getattr(job, "id", None))  # type: ignore[arg-type]  # noqa: E731

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
        # Engine 2 switch (ENGINE2_ENABLED) OR the legacy ENRICHMENT_ENABLED flag.
        from src.core.settings import ENGINE2_ENABLED  # noqa: PLC0415

        if (ENGINE2_ENABLED or ENRICHMENT_ENABLED) and not enrichment_enqueued and score >= ENRICHMENT_THRESHOLD:
            enqueue = ctx.get("enqueue")
            if enqueue is not None:
                result = enqueue("enrich_job_task", job_id)
                if hasattr(result, "__await__"):
                    await result
            enrichment_enqueued = True

    return {"ingested": ingested, "notifications_queued": queued}


async def _record_ledger_if_new(db: pg.Connection, *, user_id: str, job_id: int, channel: str) -> bool:
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
    except pg.IntegrityError:
        await db.rollback()
        return False


async def mark_ledger_sent(db: pg.Connection, *, user_id: str, job_id: int, channel: str) -> None:
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
    db: pg.Connection,
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
# is `ctx: dict` with 'db' (pg.Connection) and optionally 'enqueue'.


@_logged_task
async def send_notification(
    ctx: dict[str, Any],
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
        Must contain ``'db'`` (pg.Connection). Optionally:
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
    db: pg.Connection = ctx["db"]
    db.row_factory = pg.Row

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


@_logged_task
async def mark_ledger_sent_task(ctx: dict[str, Any], user_id: str, job_id: int, channel: str) -> None:
    """ARQ ctx wrapper around :func:`mark_ledger_sent`."""
    await mark_ledger_sent(ctx["db"], user_id=user_id, job_id=job_id, channel=channel)


@_logged_task
async def mark_ledger_failed_task(ctx: dict[str, Any], user_id: str, job_id: int, channel: str, error: str) -> None:
    """ARQ ctx wrapper around :func:`mark_ledger_failed`."""
    await mark_ledger_failed(
        ctx["db"],
        user_id=user_id,
        job_id=job_id,
        channel=channel,
        error=error,
    )


# ---------- helpers ----------------------------------------------------


def _user_profile_for(user_id: str) -> Optional[UserProfile]:
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


def _job_from_row(job_row: Any) -> Job:
    """Build a ``Job`` from a ``jobs`` row.

    ``date_found`` is normalised to an ISO **string**. That is not cosmetic:
    ``Job.date_found`` is typed ``str`` (models.py:23) and
    ``skill_matcher._recency_score`` calls ``datetime.fromisoformat(date_found)``
    on it. Passing the ``datetime`` that ``_parse_dt`` returns raised TypeError
    there, which that function's own ``except (ValueError, TypeError): return 0``
    swallowed — silently zeroing the recency component of EVERY job scored
    through ``score_and_ingest``. Nothing errored; the score was just wrong.

    Postgres hands back a ``datetime`` for this column, so the conversion has to
    happen somewhere; doing it here keeps every Job in the codebase the same
    shape. Pinned by tests/test_recency_date_type.py.
    """
    return Job(
        title=job_row["title"],
        company=job_row["company"],
        apply_url=job_row["apply_url"],
        source=job_row["source"],
        date_found=_parse_dt(job_row["date_found"]).isoformat(),
        location=job_row["location"] or "",
        description=job_row["description"] or "",
        match_score=job_row["match_score"],
    )


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _bucket_for_row(row: pg.Row) -> str:
    """Compute time bucket from the 5-column date model (Batch 1) with fallbacks."""
    # `x in row` (dict.__contains__) is equivalent to `x in row.keys()` here —
    # Row doesn't override __contains__, only keys() — and is typed in the
    # stdlib stubs, avoiding a no-untyped-call on Row.keys() (pg.py is
    # unannotated and out of scope for this file-scoped pass).
    first_seen_raw = row["first_seen_at"] if "first_seen_at" in row else None
    if not first_seen_raw:
        first_seen_raw = row["first_seen"] if "first_seen" in row else None
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


@_logged_task
async def nightly_ghost_sweep(ctx: dict[str, Any]) -> dict[str, int]:
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

    db: pg.Connection = ctx["db"]
    db.row_factory = pg.Row

    # Load all non-expired jobs (CONFIRMED_EXPIRED is sticky; skip it).
    cursor = await db.execute(
        """
        SELECT id, staleness_state, consecutive_misses, last_seen_at
        FROM jobs
        WHERE staleness_state IS NULL OR staleness_state != 'confirmed_expired'
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


MAX_BUNDLE_RETRIES = 5


@_logged_task
async def send_bundle(ctx: dict[str, Any], user_id: str) -> dict[str, int]:
    """ARQ task: send all queued digest notifications for user_id across all channels.

    Called by notification_tick when a bundle is due for the user.
    Sends with force=True to bypass mode/quiet-hours gate (bundle already decided).

    Per-channel outcome handling (blueprint §6 / rules #23/#24):
      * success  → mark that channel's queue rows ``sent``, write ledger ``sent``.
      * failure  → leave the queue rows (so the next tick retries), write ledger
        ``failed`` (increments retry_count). Once retry_count reaches
        ``MAX_BUNDLE_RETRIES`` the ledger row flips to ``dlq`` and the queue rows
        are dropped so a permanently-bad channel cannot wedge the queue.
      * skipped  → leave the queue rows untouched (rule disabled mid-flight etc.).
    ``last_sent_at`` is stamped only when at least one channel actually sent.

    Returns {'sent': int, 'failed': int, 'jobs_count': int}.
    """
    db: pg.Connection = ctx["db"]
    db.row_factory = pg.Row

    try:
        cur = await db.execute(
            "SELECT channel, job_id FROM user_notification_digests "
            "WHERE user_id = ? AND sent = 0",
            (user_id,),
        )
        pending = [dict(r) for r in await cur.fetchall()]
    except Exception:  # noqa: BLE001
        return {"sent": 0, "failed": 0, "jobs_count": 0}

    if not pending:
        return {"sent": 0, "failed": 0, "jobs_count": 0}

    by_channel: dict[str, list[int]] = {}
    for r in pending:
        by_channel.setdefault(r["channel"], []).append(r["job_id"])

    # Fetch job details for every queued job_id (deduped). N7 — one batched
    # `WHERE id IN (...)` instead of a per-job_id SELECT in a loop (this cron
    # runs every 5 min forever). Placeholders are generated from the id count
    # only — no user input in the SQL text.
    job_details: dict[int, dict[str, Any]] = {}
    _job_ids = {r["job_id"] for r in pending}
    if _job_ids:
        _placeholders = ",".join("?" for _ in _job_ids)
        cur = await db.execute(
            f"SELECT id, title, company, apply_url FROM jobs WHERE id IN ({_placeholders})",  # noqa: S608 — placeholders only
            tuple(_job_ids),
        )
        for row in await cur.fetchall():
            job_details[row["id"]] = {
                "title": row["title"],
                "company": row["company"],
                "apply_url": row["apply_url"],
            }

    jobs_count = len(job_details)
    if jobs_count == 0:
        # Jobs were purged — drop the orphaned queue rows so we don't loop forever.
        await _mark_digest_rows_sent(db, user_id)
        return {"sent": 0, "failed": 0, "jobs_count": 0}

    digest_title = f"Job360 Digest — {jobs_count} new match{'es' if jobs_count > 1 else ''}"
    lines = [f"• {jd['title']} @ {jd['company']} — {jd['apply_url']}" for jd in job_details.values()]
    digest_body = "\n".join(lines)

    dispatcher_fn = ctx.get("dispatcher")
    if dispatcher_fn is None:
        from src.services.channels.dispatcher import dispatch as real_dispatch

        dispatcher_fn = real_dispatch

    # dispatch with force=True to bypass mode/quiet-hours gate
    results = await dispatcher_fn(db, user_id=user_id, title=digest_title, body=digest_body, force=True)

    # Map each channel_type to ('sent'|'failed'|'skipped', error).
    # Named `res` (not `r`) deliberately — `r` is already bound above (the
    # `for r in pending:` loop, `r: dict[str, Any]`) and Python doesn't scope
    # for-loop variables to the loop, so reusing `r` here made mypy infer this
    # loop's element as the earlier dict type, producing 8 false-positive
    # attr-defined errors on ChannelSendResult attributes. Pure rename, same
    # values, same control flow.
    status_by_channel: dict[str, tuple[str, str]] = {}
    for res in results:
        if res.ok and not res.skipped and not getattr(res, "queued_digest", False):
            status_by_channel[res.channel_type] = ("sent", "")
        elif res.skipped:
            status_by_channel[res.channel_type] = ("skipped", res.error or "")
        else:
            status_by_channel[res.channel_type] = ("failed", res.error or "delivery failed")

    sent = failed = 0
    any_sent = False
    for channel, jids in by_channel.items():
        uniq_jids = [j for j in dict.fromkeys(jids) if j in job_details]
        if not uniq_jids:
            continue
        st, err = status_by_channel.get(channel, ("failed", "channel not dispatched"))
        if st == "skipped":
            continue  # leave the queue rows untouched
        for jid in uniq_jids:
            await _record_ledger_if_new(db, user_id=user_id, job_id=jid, channel=channel)
        if st == "sent":
            for jid in uniq_jids:
                await mark_ledger_sent(db, user_id=user_id, job_id=jid, channel=channel)
            await db.execute(
                "UPDATE user_notification_digests SET sent=1, "
                "sent_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                "WHERE user_id=? AND channel=? AND sent=0",
                (user_id, channel),
            )
            await db.commit()
            sent += 1
            any_sent = True
        else:  # failed → leave rows for retry, ledger failed, DLQ after the cap
            for jid in uniq_jids:
                await mark_ledger_failed(db, user_id=user_id, job_id=jid, channel=channel, error=err)
            await db.execute(
                "UPDATE notification_ledger SET status='dlq' "
                "WHERE user_id=? AND channel=? AND retry_count >= ?",
                (user_id, channel, MAX_BUNDLE_RETRIES),
            )
            await db.execute(
                "DELETE FROM user_notification_digests WHERE user_id=? AND channel=? AND sent=0 "
                "AND job_id IN (SELECT job_id FROM notification_ledger "
                "  WHERE user_id=? AND channel=? AND status='dlq')",
                (user_id, channel, user_id, channel),
            )
            await db.commit()
            failed += 1

    if any_sent:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            await db.execute(
                "UPDATE notification_rules SET last_sent_at = ? WHERE user_id = ?",
                (now, user_id),
            )
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            import logging as _logging

            _logging.getLogger(__name__).debug(
                "Could not update last_sent_at for user %s: %s", user_id, exc
            )

    return {"sent": sent, "failed": failed, "jobs_count": jobs_count}


async def _mark_digest_rows_sent(db: pg.Connection, user_id: str) -> None:
    """Flip sent=1 on all pending digest rows for user_id (all channels)."""
    import logging
    _log = logging.getLogger(__name__)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        await db.execute(
            "UPDATE user_notification_digests SET sent=1, sent_at=? "
            "WHERE user_id=? AND sent=0",
            (now, user_id),
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        _log.debug("Could not mark digests sent for user %s: %s", user_id, exc)


def _bundle_due(rule: dict[str, Any], *, now_utc: datetime, user_tz: str = "UTC") -> bool:
    """Return True when it is time to send a bundle for this rule.

    - notify_mode == 'instant': never bundle here (always immediate; the
      quiet-hours flush for stranded instant matches lives in
      ``notification_tick``, not this pure helper).
    - notify_mode == 'daily': true when the current tick is at/after the
      user-local send time AND no bundle has been sent yet today (user-local
      date). This is a "window, not exact minute" check — the cron only ticks
      every 5 min, so an exact ``HH:MM`` compare missed any send-time whose
      minute wasn't a tick multiple (finding N1). It also self-heals a missed
      tick (restart/outage): the next tick at/after the send time still fires.
    - notify_mode == 'every_n_hours': true when last_sent_at is None OR
      now_utc - last_sent_at >= interval_hours

    Always returns False on parse errors.
    """
    from datetime import timedelta
    mode = rule.get("notify_mode", "instant")
    if mode == "instant":
        return False
    if mode == "daily":
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(user_tz)
            now_local = now_utc.astimezone(tz)
            send_time_str = rule.get("daily_send_time", "08:00")
            h, m = map(int, send_time_str.split(":"))
            target_local = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
            # Not yet at today's send time → not due.
            if now_local < target_local:
                return False
            # At/after the send time: due only if we haven't already sent a
            # bundle today (user-local date). Fires once on the first tick
            # at/after the send time; never twice the same day.
            last_sent_str = rule.get("last_sent_at")
            if not last_sent_str:
                return True
            last_sent_local = datetime.fromisoformat(
                last_sent_str.replace("Z", "+00:00")
            ).astimezone(tz)
            return last_sent_local.date() < now_local.date()
        except Exception:  # noqa: BLE001
            return False
    if mode == "every_n_hours":
        interval = int(rule.get("interval_hours", 6))
        last_sent_str = rule.get("last_sent_at")
        if last_sent_str is None:
            return True
        try:
            last_sent = datetime.fromisoformat(last_sent_str.replace("Z", "+00:00"))
            return (now_utc - last_sent) >= timedelta(hours=interval)
        except Exception:  # noqa: BLE001
            return True
    return False


def _in_quiet_window(rule: dict[str, Any], now_utc: datetime, user_tz: str) -> bool:
    """True when ``now_utc`` falls inside the rule's quiet-hours window.

    ``quiet_hours_start`` / ``quiet_hours_end`` are HH:MM strings in the user's
    local timezone. Supports wraparound windows that cross midnight
    (e.g. 23:00–07:00). Uses stdlib ``zoneinfo`` (no pytz). Returns False when
    no window is configured or on any parse error (default: allow dispatch).
    """
    qs = rule.get("quiet_hours_start")
    qe = rule.get("quiet_hours_end")
    if not qs or not qe:
        return False
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(user_tz)
        now_local = now_utc.astimezone(tz).time().replace(second=0, microsecond=0)
        # Explicit 2-tuple unpack (not `_time(*map(int, ...))`) — mypy can't
        # verify a starred `map[int]` iterator fills exactly (hour, minute)
        # and not e.g. the 5th `tzinfo` slot, so the star-unpack call was
        # flagged as an arg-type mismatch. Same values, same call, no `*`.
        qs_h, qs_m = (int(p) for p in qs.split(":"))
        qe_h, qe_m = (int(p) for p in qe.split(":"))
        start = _time(qs_h, qs_m)
        end = _time(qe_h, qe_m)
        if start <= end:
            return start <= now_local < end
        return now_local >= start or now_local < end
    except Exception:  # noqa: BLE001
        return False


async def _has_pending_digests(db: pg.Connection, user_id: str) -> bool:
    """True when the user has unsent ``user_notification_digests`` rows."""
    try:
        cur = await db.execute(
            "SELECT 1 FROM user_notification_digests WHERE user_id = ? AND sent = 0 LIMIT 1",
            (user_id,),
        )
        return await cur.fetchone() is not None
    except Exception:  # noqa: BLE001 — table missing on legacy DB
        return False


@_logged_task
async def notification_tick(ctx: dict[str, Any]) -> dict[str, int]:
    """ARQ cron task: check all users with notification rules, enqueue send_bundle when due.

    Runs every 5 minutes (configured in WorkerSettings.get_cron_jobs).
    For each user with an enabled rule, checks _bundle_due(); if true, enqueues send_bundle.
    """
    db: pg.Connection = ctx["db"]
    db.row_factory = pg.Row
    now_utc = datetime.now(timezone.utc)

    try:
        # N7 — one LEFT JOIN instead of a per-rule `SELECT timezone FROM users`.
        # This cron fires every 5 minutes, so the old shape cost 1 + N queries
        # per tick and grew linearly with the number of users who have
        # notifications enabled. LEFT (not INNER) so a rule whose user row is
        # missing still gets evaluated, exactly as before — it just falls back
        # to UTC, which is what the old per-row `except` did.
        cur = await db.execute(
            "SELECT r.*, u.timezone AS _user_timezone "
            "FROM notification_rules r "
            "LEFT JOIN users u ON u.id = r.user_id "
            "WHERE r.enabled = 1"
        )
        rules = [dict(r) for r in await cur.fetchall()]
    except Exception:  # noqa: BLE001
        return {"checked": 0, "enqueued": 0}

    enqueued = 0
    for rule in rules:
        user_id = rule["user_id"]
        user_tz = rule.get("_user_timezone") or "UTC"

        due = _bundle_due(rule, now_utc=now_utc, user_tz=user_tz)
        # SI2 — quiet-hours flush. An instant-mode user's matches are queued
        # into user_notification_digests while inside quiet hours (dispatcher
        # gate 3+4). Instant mode never bundles, so once quiet hours end nothing
        # would drain those rows. Flush them here: outside the quiet window AND
        # there are pending rows → enqueue send_bundle (drains with force=True).
        if not due and rule.get("notify_mode", "instant") == "instant":
            if not _in_quiet_window(rule, now_utc, user_tz) and await _has_pending_digests(db, user_id):
                due = True

        if due:
            enqueue = ctx.get("enqueue")
            if enqueue is not None:
                result = enqueue("send_bundle", user_id)
                if hasattr(result, "__await__"):
                    await result
            enqueued += 1

    return {"checked": len(rules), "enqueued": enqueued}


@_logged_task
async def enrich_job_task(ctx: dict[str, Any], job_id: int) -> dict[str, bool | str]:
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

    db: pg.Connection = ctx["db"]

    if await has_enrichment(db, job_id):
        return {"enriched": False, "reason": "already_enriched"}

    db.row_factory = pg.Row
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
    # FINDING (not fixed — models.py is out of scope for this file-scoped
    # pass): `Job` (src/models.py) has no declared `id` field, so this is a
    # dynamic attribute stapled onto a plain (non-slotted) @dataclass — it
    # works at runtime but mypy can't see it. This is the SAME pattern
    # job_enrichment._build_enrichment_lookup's docstring documents as the
    # standard call convention (`getattr(job, 'id', None)`), and here it's
    # set correctly *before* the enrich_job call that reads it — unlike the
    # previously-identified "dim scoring id bug" (job.id unset at scoring
    # time in main.py/jobs.py), this call site looks correct, just untyped.
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
