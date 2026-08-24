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
import os
import time
from datetime import datetime, timezone
from datetime import time as _time
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

if TYPE_CHECKING:
    from src.services.profile.models import UserProfile

from src.core.settings import ENRICHMENT_THRESHOLD, SITE_BASE_URL
from src.models import Job
from src.repositories import pg
from src.services.delivery.decision_card import build_decision_card
from src.services.delivery.email_body import (
    render_digest_subject,
    render_digest_text,
)
from src.services.feed import FeedService
from src.services.job_enrichment import ENRICHMENT_ENABLED, _build_enrichment_lookup
from src.services.job_signals import signal_backed_lookup
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
    enrichment_lookup_dict: Optional[dict[int, Any]] = None,
    suppress_notifications: bool = False,
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
    enrichment_lookup_dict : optional
        Pre-built ``{job_id: JobEnrichment}`` map (see
        ``_build_enrichment_lookup``). When None (every call site except the
        F1 refresh fan-out), this function builds its own — unchanged
        behaviour. A caller fanning this out over many job ids in one tick
        (``refresh_catalog``) should build the map ONCE and pass it here:
        the table is the whole shared catalog's enrichment (~6.5k rows
        measured in prod, 2026-08-15), so rebuilding it per job turns an
        O(1) query into an O(n) one for no reason — the map does not change
        mid-tick (enrichment itself runs as a separate, later ARQ task).
    suppress_notifications : optional
        When True, score + feed-write proceeds normally but the
        score>=threshold ledger-insert + ``send_notification`` enqueue is
        skipped. Default False — every existing caller/test keeps today's
        behaviour. ``refresh_catalog`` passes True (see its docstring for
        why): this is the first production path that can fan a single
        catalog refresh out into a same-tick notification burst for one
        user, and that is a deliberate product decision to make explicitly,
        not something to inherit by accident from a kwarg default.

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
        users = await _load_users(db)
        targets = [(u["id"], _filter_profile_for(u["id"]), 80) for u in users]

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
    #
    # F1 — a caller fanning this out over many job ids in one tick can hand
    # us an already-built map (see the `enrichment_lookup_dict` param docs
    # above) so the ~6.5k-row query only runs once per refresh, not once per
    # job. Falls back to building it here — unchanged for every other caller.
    if enrichment_lookup_dict is None:
        enrichment_lookup_dict = await _build_enrichment_lookup(db)
    # `Job.id` is a declared field now (models.py) and _job_from_row populates
    # it from the row, so this lookup actually finds enrichment instead of
    # always calling get(None). Reads `job.id` directly rather than through
    # getattr — the getattr dance existed only because the field was previously
    # stapled on at runtime and might genuinely be absent.
    # Wrapped so the deterministic detectors fill what the LLM left 'unknown'
    # (job_signals.signal_backed_lookup explains why this is the only seam that
    # can see both the enrichment record and the job's own title/location).
    enrichment_lookup_fn = signal_backed_lookup(
        lambda job: enrichment_lookup_dict.get(job.id)
    )

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

        if score >= threshold and not suppress_notifications:
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
        return {"sent": 0, "queued": 0, "failed": 0}

    title = f"{job_row['title']} @ {job_row['company']}"
    body = f"Job360 match: {job_row['title']}\n{job_row['apply_url']}"

    # Test hook: ctx['dispatcher'] short-circuits the real Apprise path.
    # In production, we import lazily to dodge Apprise's ~30MB dep chain
    # per CLAUDE.md rule #11.
    dispatcher_fn = ctx.get("dispatcher")
    if dispatcher_fn is None:
        from src.services.channels.dispatcher import dispatch as real_dispatch

        dispatcher_fn = real_dispatch

    # `job_id` and `match_score` are NOT optional decoration here. Without
    # job_id, `dispatcher._queue_digest` returns immediately without writing a
    # row -- so a `daily` rule produced NO digest AND no send, while the code
    # below still counted it. (CodeRabbit, PR #352.)
    results = await dispatcher_fn(
        db, user_id=user_id, title=title, body=body,
        job_id=job_id, match_score=job_row.get("match_score"),
    )

    sent = 0
    queued = 0
    failed = 0
    for result in results:
        channel_key = result.channel_type or f"channel:{result.channel_id}"
        # Ensure a ledger row exists (idempotent per UNIQUE constraint).
        await _record_ledger_if_new(db, user_id=user_id, job_id=job_id, channel=channel_key)
        # QUEUED IS NOT SENT. `dispatch()` returns ok=True for a digest it merely
        # enqueued, so counting `ok` alone marked a notification delivered that
        # no user will receive until the digest drains -- and then wrote
        # `notified_at`, which suppresses it from ever being re-notified. A
        # notification recorded as delivered but never delivered is worse than
        # one that failed loudly: the failure is invisible in every table that
        # would show it. (CodeRabbit, PR #352.)
        if result.queued_digest:
            queued += 1
        elif result.ok:
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

    # #318 — make delivery OBSERVABLE. `user_feed.notified_at` was NULL on all
    # 24,597 live rows because the only writers (`mark_notified`,
    # `list_pending_notifications`) had no production callers at all — the
    # re-notify dedup runs off `notification_ledger` instead, so the column was
    # orphaned rather than load-bearing. Without this, fixing delivery would
    # still leave the issue's headline symptom ("notified_at is NULL on every
    # feed row") looking untouched.
    if sent:
        try:
            await FeedService(db).mark_notified_for_jobs(user_id, [job_id])
        except Exception as exc:  # noqa: BLE001 — telemetry never fails a send
            _log.warning("mark_notified failed user=%s job=%s: %s", user_id, job_id, exc)

    return {"sent": sent, "queued": queued, "failed": failed}


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


def _filter_profile_for(user_id: str) -> FilterProfile:
    """Build the funnel's first gate from the user's ACTUAL stored profile.

    Until 2026-08-09 the caller passed a bare ``FilterProfile()`` — every field
    at its default — so all three prefilter stages took their "no preference
    declared, pass everything" branch. The cheap gate that exists to spare the
    expensive stages filtered nothing, for every user, since it was written.

    Rule #29 is preserved by construction: each stage already passes everything
    when its side is empty, so a user who has stated no locations, no workplace
    and no level is filtered exactly as much as before (not at all).

    Both omissions below were MEASURED against 2,000 live jobs, not guessed.

    SKILLS ARE NOT WIRED. ``skill_overlap_ok`` drops any job whose
    title+description mentions none of the user's skills. Dry-run on the real
    catalogue with the owner's real 80-skill profile: it keeps 40% and the
    combined gate keeps 28.8% — it would silently delete SEVEN JOBS IN TEN
    before anything scored them. That is a product decision with evidence
    attached, not a wiring detail.

    THE EXPERIENCE LEVEL IS THE **TYPED** ONE ONLY, never
    ``resolve_experience_level``. That helper falls back to
    ``experience_level_inferred``, a value read off the CV's dated job titles
    rather than stated by anyone. Feeding it to a HARD GATE dropped 24.5% of the
    catalogue for an owner whose typed level is empty — every senior+ role
    removed on the strength of a guess he never made. Rule #29 draws exactly
    this line: an unstated preference means "don't care", so a soft score may
    lean on an inference but a gate that deletes jobs may not.
    """
    profile = _user_profile_for(user_id)
    if profile is None:
        return FilterProfile()
    prefs = profile.preferences
    return FilterProfile(
        preferred_locations=list(prefs.preferred_locations or []),
        work_arrangement=prefs.work_arrangement or "",
        experience_level=prefs.experience_level or "",
    )


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
        # H1 — carry the row id onto the Job. The enrichment lookup is keyed on
        # it (`enrichment_lookup_dict.get(getattr(job, "id", None))`), so an
        # unset id meant `get(None)`, which ALWAYS misses — silently scoring
        # every enrichment dimension (seniority / salary / visa / workplace) as
        # 0 for every job ingested through the worker. Nothing raised: dict.get
        # on a missing key returns None, and the scorer treats None as "no
        # enrichment", which is indistinguishable from "this job genuinely has
        # none".
        #
        # This is the documented dim-scoring-id bug, in the worker path.
        # enrich_job_task already does it right (`job.id = row["id"]`); this
        # call site was simply never given the same treatment.
        #
        # Scope note: with ENRICHMENT_ENABLED off the lookup dict is empty, so
        # every get() misses either way and behaviour is unchanged. With it on,
        # the dimensions finally contribute what they were always meant to.
        id=job_row["id"],
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
        SELECT id, staleness_state, consecutive_misses, last_seen_at, first_seen_at
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


async def _backfill_thin_descriptions(db: pg.Connection, budget: int) -> dict[str, int]:
    """BACKFILL phase (2026-08-07) — runs FIRST inside ``enrichment_sweep``,
    before missing-coverage and repair, so a job whose real text just landed
    is eligible for enrichment in THIS SAME tick rather than the next one.

    WHY THIS EXISTS. Sources fetch job-detail pages under strict PER-RUN
    budgets (``_MAX_DETAIL_FETCHES`` in workday.py / smartrecruiters.py) so a
    single run never blows the 240s ATS timeout. A job stored past that
    budget keeps an empty/short description forever — nothing re-fetches it
    unless it happens to reappear in a later run's listing. Coverage of every
    enriched field (workplace/seniority/visa) tracks description length
    almost perfectly: measured in prod, 1,311 active jobs (30% of the
    catalog) carry under 200 chars of description.

    Selects the ``budget`` highest-value ACTIVE jobs (same
    COALESCE(llm_fit_score, score) value ordering as the other phases) whose
    ``description`` is under ``MIN_DESCRIPTION_CHARS`` AND whose source is on
    the ``ALLOWED_BACKFILL_SOURCES`` capability list (LinkedIn and
    structurally-textless sources are OUT — pushed into the SQL itself,
    not filtered in Python after the fact, so a 50/tick budget is never
    burned on rows that were never going to be fetched) AND whose
    ``description_backfill_attempts`` is under ``MAX_BACKFILL_ATTEMPTS``.

    TERMINAL STATE — real counter (migration 0029), not a padded description.
    An earlier version of this padded a still-thin ``description`` with
    trailing spaces past the 200-char floor purely to stop it being
    reselected. That was REJECTED in review: coverage.py's skill-text
    predicate counts ANY description over 200 chars as real coverage with no
    whitespace check, so padding manufactured a FALSE "we understand this
    job" signal — fed to the LLM judge, the keyword scorer, and rendered to
    users, on top of it. ``description`` is now written ONLY with genuinely
    fetched (whitespace-stripped) text; ``description_backfill_attempts`` is
    the thing that stops a hopeless row from being retried forever, and it
    increments on EVERY attempt regardless of outcome — success, partial
    improvement, no improvement, or an exception. A row that clears
    ``MIN_DESCRIPTION_CHARS`` drops out of selection naturally (the length
    predicate already excludes it); a row that never does stops being
    selected once it hits ``MAX_BACKFILL_ATTEMPTS``, full stop.

    Per-row try/except (rule: one poison row must never abort the sweep) —
    the file already documents an incident where a single bad row aborted an
    entire run.

    Returns ``{"attempted", "filled", "still_thin", "skipped_by_capability",
    "errors"}`` — merged into ``enrichment_sweep``'s return dict.
    ``skipped_by_capability`` stays 0 in normal operation now that the SQL
    itself excludes disallowed sources; kept as a defensive counter (belt-
    and-braces) in case a row's source ever slips past the IN-clause.
    """
    stats = {"attempted": 0, "filled": 0, "still_thin": 0, "skipped_by_capability": 0, "errors": 0}
    if budget <= 0:
        return stats

    from src.services.description_backfill import (  # noqa: PLC0415
        ALLOWED_BACKFILL_SOURCES,
        MAX_BACKFILL_ATTEMPTS,
        MIN_DESCRIPTION_CHARS,
        fetch_description,
    )

    allowed_sources = sorted(ALLOWED_BACKFILL_SOURCES)
    if not allowed_sources:
        return stats
    source_placeholders = ",".join("?" for _ in allowed_sources)

    cur = await db.execute(
        f"""
        SELECT j.id, j.title, j.company, j.location, j.description,
               j.apply_url, j.source
        FROM jobs j
        JOIN user_feed f ON f.job_id = j.id AND f.status = 'active'
        WHERE length(coalesce(j.description, '')) < ?
          AND coalesce(j.description_backfill_attempts, 0) < ?
          AND j.source IN ({source_placeholders})
        GROUP BY j.id, j.title, j.company, j.location, j.description,
                 j.apply_url, j.source
        ORDER BY max(COALESCE(f.llm_fit_score, f.score)) DESC
        LIMIT ?
        """,  # noqa: S608 — source_placeholders is "?"*len(allowed_sources), no user input
        (MIN_DESCRIPTION_CHARS, MAX_BACKFILL_ATTEMPTS, *allowed_sources, budget),
    )
    rows = [dict(r) for r in await cur.fetchall()]

    # Defensive belt-and-braces (see docstring) — should never fire given the
    # SQL IN-clause above, but a row landing here with a disallowed source
    # must still never be fetched.
    allowed_rows = []
    for row in rows:
        if row.get("source") in ALLOWED_BACKFILL_SOURCES:
            allowed_rows.append(row)
        else:
            stats["skipped_by_capability"] += 1
    if not allowed_rows:
        return stats

    import aiohttp  # noqa: PLC0415 — only needed when there is real fetching to do

    devitjobs_cache: dict[str, dict[str, str]] = {}
    async with aiohttp.ClientSession() as session:
        for row in allowed_rows:
            stats["attempted"] += 1
            fetch_raised = False
            new_text: Optional[str] = None
            try:
                new_text = await fetch_description(row, session, devitjobs_cache=devitjobs_cache)
            except Exception:  # noqa: BLE001 — one poison row must never abort the sweep
                fetch_raised = True
                stats["errors"] += 1
                _log.warning(
                    "description_backfill_fetch_failed",
                    extra={"event": "description_backfill_fetch_failed", "job_id": row.get("id")},
                    exc_info=True,
                )

            try:
                existing = row.get("description") or ""
                # .strip() on BOTH sides — a fetch that returns whitespace-only
                # text (or HTML that tag-strips down to nothing) must NOT be
                # mistaken for real content, and must not be compared unfairly
                # against padded legacy rows either. Pins the exact bug this
                # phase was rejected for once already (see module docstring).
                stripped_new = (new_text or "").strip()
                stripped_existing = existing.strip()

                if stripped_new and len(stripped_new) >= MIN_DESCRIPTION_CHARS:
                    await db.execute(
                        "UPDATE jobs SET description = ?, "
                        "description_backfill_attempts = coalesce(description_backfill_attempts, 0) + 1 "
                        "WHERE id = ?",
                        (stripped_new, row["id"]),
                    )
                    stats["filled"] += 1
                elif stripped_new and len(stripped_new) > len(stripped_existing):
                    # Genuine improvement, still short — real text, written
                    # verbatim; the attempt still counts against the cap.
                    await db.execute(
                        "UPDATE jobs SET description = ?, "
                        "description_backfill_attempts = coalesce(description_backfill_attempts, 0) + 1 "
                        "WHERE id = ?",
                        (stripped_new, row["id"]),
                    )
                    stats["still_thin"] += 1
                else:
                    # No improvement (including a fetch that raised) — bump
                    # the REAL attempt counter only. description is NEVER
                    # modified except with real fetched text.
                    await db.execute(
                        "UPDATE jobs SET "
                        "description_backfill_attempts = coalesce(description_backfill_attempts, 0) + 1 "
                        "WHERE id = ?",
                        (row["id"],),
                    )
                    if not fetch_raised:
                        stats["still_thin"] += 1
                await db.commit()
            except Exception:  # noqa: BLE001 — one poison row must never abort the sweep
                _log.warning(
                    "description_backfill_write_failed",
                    extra={"event": "description_backfill_write_failed", "job_id": row.get("id")},
                    exc_info=True,
                )
                stats["errors"] += 1

    return stats


@_logged_task
async def enrichment_sweep(ctx: dict[str, Any]) -> dict[str, Any]:
    """ARQ periodic task: self-heal enrichment coverage of the candidate pool.

    WHY THIS EXISTS (goal: understand JOB → 100%, 2026-08-05). Enrichment is
    per-JOB and shared (rule #17), but it only ever ran inside pipeline runs
    with small per-run budgets — measured in prod: 24/7,309 jobs enriched
    (0.3%), so the salary/visa/seniority/workplace preference dimensions
    effectively never fired for anyone. Filling coverage depended on manual
    sweeps from a laptop — exactly the kind of hands-on ops a production
    system must not need. This cron makes coverage converge BY ITSELF.

    A BACKFILL phase (``_backfill_thin_descriptions``, 2026-08-07) now runs
    FIRST: it goes back for real description text on jobs a source's per-run
    detail-fetch budget skipped over, so the enrichment phases below actually
    have something to extract from. See that function's docstring.

    Each tick: pick the ``ENRICHMENT_SWEEP_PER_TICK`` (default 100, 0
    disables) highest-value ACTIVE candidate jobs still missing enrichment —
    value = the best COALESCE(llm_fit_score, score) any user's feed gives the
    job — and enrich them via the standard ``enrich_batch`` path (idempotent,
    ``skip_existing``, semaphore 3). Cost: the free-tier extraction chain the
    pipeline already uses; bounded per tick; zero per-user LLM spend.

    Gated exactly like every other E2 call site: ``ENGINE2_ENABLED`` OR the
    legacy ``ENRICHMENT_ENABLED`` flag — both off ⇒ the MISSING-COVERAGE and
    REPAIR phases below are a pure no-op (rule #18).

    The BACKFILL phase is DELIBERATELY OUTSIDE that gate (2026-08-07 manager
    review). E2 only controls the LLM enrichment call — but description text
    is also what Engine 1's keyword scorer matches against and what the
    Engine 4 LLM judge reads, so a keyword-only deployment (E2 off) would
    otherwise never get its text fixed. Backfill is governed ONLY by its own
    ``DESCRIPTION_BACKFILL_PER_TICK`` budget (0 disables it), and it makes no
    LLM call of its own regardless.
    """
    import os as _os  # noqa: PLC0415

    from src.core.settings import DESCRIPTION_BACKFILL_PER_TICK, ENGINE2_ENABLED  # noqa: PLC0415
    from src.services.job_enrichment import enrich_batch  # noqa: PLC0415

    db: pg.Connection = ctx["db"]
    db.row_factory = pg.Row

    backfill_stats = await _backfill_thin_descriptions(db, DESCRIPTION_BACKFILL_PER_TICK)

    if not (ENGINE2_ENABLED or ENRICHMENT_ENABLED):
        return {"enriched": 0, "reason": "e2_off", **backfill_stats}

    budget = int(_os.getenv("ENRICHMENT_SWEEP_PER_TICK", "100"))
    if budget <= 0:
        return {"enriched": 0, "reason": "disabled", **backfill_stats}

    cur = await db.execute(
        """
        SELECT j.id, j.title, j.company, j.location, j.description,
               j.apply_url, j.source, j.date_found
        FROM jobs j
        JOIN user_feed f ON f.job_id = j.id AND f.status = 'active'
        LEFT JOIN job_enrichment e ON e.job_id = j.id
        WHERE e.job_id IS NULL
        GROUP BY j.id, j.title, j.company, j.location, j.description,
                 j.apply_url, j.source, j.date_found
        ORDER BY max(COALESCE(f.llm_fit_score, f.score)) DESC
        LIMIT ?
        """,
        (budget,),
    )
    rows = [dict(r) for r in await cur.fetchall()]

    # REPAIR phase (2026-08-06, found by the Pillar-2 simulation): enrichment
    # that ran against an EMPTY description extracted mostly 'unknown' (prod:
    # visa 94% / seniority 62% / workplace 61% unknown), and skip_existing
    # froze those hollow rows forever — even after the description-backfill
    # fixes delivered the text. Spend whatever budget the missing-coverage
    # phase left on re-enriching unknown-heavy rows whose job NOW has real
    # text. save_enrichment upserts, so the repair overwrites in place.
    repair_budget = budget - len(rows)
    repair_rows: list[dict[str, Any]] = []
    if repair_budget > 0:
        cur = await db.execute(
            """
            SELECT j.id, j.title, j.company, j.location, j.description,
                   j.apply_url, j.source, j.date_found
            FROM jobs j
            JOIN job_enrichment e ON e.job_id = j.id
            JOIN user_feed f ON f.job_id = j.id AND f.status = 'active'
            WHERE e.visa_sponsorship = 'unknown'
              AND e.seniority = 'unknown'
              AND e.workplace_type = 'unknown'
              AND length(coalesce(j.description, '')) > 200
            GROUP BY j.id, j.title, j.company, j.location, j.description,
                     j.apply_url, j.source, j.date_found
            ORDER BY max(COALESCE(f.llm_fit_score, f.score)) DESC
            LIMIT ?
            """,
            (repair_budget,),
        )
        repair_rows = [dict(r) for r in await cur.fetchall()]

    if not rows and not repair_rows:
        return {"enriched": 0, "reason": "coverage_complete", **backfill_stats}

    def _mk_job(r: dict[str, Any]) -> Job:
        job = Job(
            title=r["title"] or "", company=r["company"] or "",
            apply_url=r["apply_url"] or "", source=r["source"] or "",
            date_found=r["date_found"] or "", location=r["location"] or "",
            description=r["description"] or "",
        )
        job.id = r["id"]
        return job

    enriched = repaired = 0
    if rows:
        results = await enrich_batch(
            [_mk_job(r) for r in rows], semaphore_limit=3, conn=db, skip_existing=True
        )
        enriched = len([x for x in (results or []) if x])
    if repair_rows:
        results = await enrich_batch(
            [_mk_job(r) for r in repair_rows], semaphore_limit=3, conn=db,
            skip_existing=False,  # deliberate: overwrite the hollow rows
        )
        repaired = len([x for x in (results or []) if x])
    return {"enriched": enriched, "repaired": repaired, **backfill_stats}


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

    What the email CONTAINS (rebuilt 2026-08-24): the body is no longer a list
    of links. Jobs are joined against ``user_feed`` (score + the judge's
    verdict and reason — all per-user, none of it on the shared catalog) and
    ``job_enrichment`` (salary, parsed by the SAME function the dashboard uses),
    turned into ``DecisionCard``s and rendered by ``services/delivery``. The
    card module is the one definition of what a job says, so the email and the
    screen cannot drift. See docs/plans/2026-08-24-email-webhook-only-delivery.md.

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
    #
    # 2026-08-24 — this used to select four columns from `jobs` alone, which is
    # why the digest could only ever say "Title @ Company — url". The score, the
    # judge's verdict and the reason it wrote are PER-USER: they live on
    # `user_feed`, not on the shared catalog. Without this join the email
    # structurally cannot say what the dashboard says, no matter how the body is
    # formatted. The join is INNER on purpose — a queued job with no feed row
    # for this user is not something we can score or explain, so we do not send
    # it.
    rows: list[dict[str, Any]] = []
    _job_ids = {r["job_id"] for r in pending}
    if _job_ids:
        _placeholders = ",".join("?" for _ in _job_ids)
        cur = await db.execute(
            "SELECT j.id AS job_id, j.title, j.company, j.location, e.salary AS enr_salary, "  # noqa: S608 — placeholders only
            "       f.score, f.llm_fit_score, f.llm_verdict, f.llm_reason "
            "FROM jobs j "
            "JOIN user_feed f ON f.job_id = j.id AND f.user_id = ? "
            "LEFT JOIN job_enrichment e ON e.job_id = j.id "
            f"WHERE j.id IN ({_placeholders}) "
            "ORDER BY COALESCE(f.llm_fit_score, f.score) DESC",
            (user_id, *tuple(_job_ids)),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    jobs_count = len(rows)
    if jobs_count == 0:
        # Jobs were purged — drop the orphaned queue rows so we don't loop forever.
        await _mark_digest_rows_sent(db, user_id)
        return {"sent": 0, "failed": 0, "jobs_count": 0}

    # ONE definition of what a job says, shared with the dashboard. The email
    # must not re-derive a score or re-word a reason — see
    # src/services/delivery/decision_card.py for why that is load-bearing.
    cards = [build_decision_card(r, site_base_url=SITE_BASE_URL) for r in rows]
    deliverable_ids = {int(r["job_id"]) for r in rows}

    # Drain queue rows we can never deliver. The join above is INNER, so a
    # queued job whose user_feed row has since gone (purged, or the user's
    # verdicts were cleared) produces no card — and without this it would also
    # never be marked sent, leaving a row at sent=0 that notification_tick
    # re-enqueues every five minutes forever. Same class of infinite loop the
    # `jobs_count == 0` branch above already guards; this is the partial case,
    # which the old catalog-only query could not produce and this one can.
    _undeliverable = {int(r["job_id"]) for r in pending} - deliverable_ids
    if _undeliverable:
        _ph = ",".join("?" for _ in _undeliverable)
        _now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        await db.execute(
            "UPDATE user_notification_digests SET sent=1, sent_at=? "  # noqa: S608 — placeholders only
            f"WHERE user_id=? AND sent=0 AND job_id IN ({_ph})",
            (_now, user_id, *tuple(_undeliverable)),
        )
        await db.commit()
        _log.info(
            "send_bundle dropped %d queued job(s) with no user_feed row (user=%s)",
            len(_undeliverable),
            user_id,
        )
    digest_title = render_digest_subject(shown=jobs_count, considered=jobs_count)
    # `considered` equals `shown` here because this queue only ever holds jobs
    # that already cleared the score threshold — by the time a row reaches
    # user_notification_digests the filtering has happened upstream and the
    # rejected count is no longer in scope. Wiring the true funnel count through
    # is Phase 5; stating a number we cannot source would be worse than omitting
    # the line, so render_digest_text prints no "dropped" line at all when the
    # two are equal.
    digest_body = render_digest_text(cards, considered=jobs_count, dropped_reasons=[])

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
        # Only mark queue rows for jobs that actually made it into the email.
        # `deliverable_ids` replaces the old `job_details` dict: a job now has to
        # survive the user_feed join to be sent, so "was it in the email" and
        # "does the catalog still have it" are no longer the same question.
        uniq_jids = [j for j in dict.fromkeys(jids) if j in deliverable_ids]
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
            # #318 — same observability wiring as send_notification: stamp
            # user_feed.notified_at for the jobs this bundle actually carried.
            try:
                await FeedService(db).mark_notified_for_jobs(user_id, uniq_jids)
            except Exception as exc:  # noqa: BLE001 — telemetry never fails a send
                _log.warning("mark_notified failed user=%s: %s", user_id, exc)
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


# F1 — cap on how many freshly-fetched job ids one `refresh_catalog` tick
# will fan out through `score_and_ingest`. Named constant (not inlined) so
# the number is greppable and documented in one place, not re-derived at
# every call site.
#
# Why 1000, not unbounded: `refresh_catalog` already ran, unfanned, since
# 2026-07-27 — nothing bounded its output before, so a source outage/replay
# that suddenly returns a huge batch must not turn one cron tick into an
# unbounded per-user scoring loop (N ids x every active user x JobScorer).
# Why 1000, not a tighter number: measured live 2026-08-15, one normal
# catalog-refill tick inserts ~280-284 new jobs — 1000 is ~3.5x that, real
# headroom for a bigger-than-usual day without being unbounded.
MAX_REFRESH_INGEST_IDS = 1000

# ---------- Issue #271 — durable re-scoring ----------------------------
#
# Until 2026-08-12 a profile save fired ``rescore_user_feed`` with
# ``asyncio.create_task`` IN THE WEB PROCESS: no queue entry, no retry, no
# completion record (``grep -c rescore src/workers/tasks.py`` was literally 0).
# ``main`` auto-deploys on every merge, so a deploy alone killed anything in
# flight. Measured in prod 2026-08-11: 9,708 ``user_feed`` rows on a
# profile_version older than their user's current one, ALL pointing at jobs
# still in the catalog (0 orphans) — reachable work that simply never ran.

# Retry backoff for a failed re-score, in seconds: try 2 waits 30s, try 3 waits
# 60s, and so on. ``WorkerSettings.max_tries = 5`` is the ceiling, so a
# permanently-poisoned job gives up rather than looping on the single
# (``max_jobs = 1``) worker slot forever.
RESCORE_RETRY_DEFER_SECONDS = 30


@_logged_task
async def rescore_user_feed_task(ctx: dict[str, Any], user_id: str) -> dict[str, Any]:
    """ARQ task: re-score ONE user's feed against their current profile.

    Deliberately thin — the scoring logic stays in ``services/rescore.py`` so
    the queued path and the in-process fallback can never diverge.

    Uses its OWN database connection (``rescore_user_feed`` opens a
    ``JobDatabase``), not ``ctx['db']``: the re-score is long and the worker
    shares one psycopg connection across tasks, so borrowing it would risk the
    "another operation in progress" hazard that ``max_jobs = 1`` exists to
    avoid.

    On failure it asks ARQ to retry with a backoff instead of swallowing the
    error. That is the whole point of moving this onto the queue: a transient DB
    blip used to strand a user's feed on an old profile_version forever, with
    nobody to notice.
    """
    from src.services import rescore as _rescore  # noqa: PLC0415 — heavy (rule #16)

    try:
        result = await _rescore.rescore_user_feed(user_id)
    except Exception as exc:
        job_try = int(ctx.get("job_try") or 1)
        _log.error(
            "rescore_task_failed",
            extra={
                "event": "rescore_task_failed",
                "user_id": user_id,
                "job_try": job_try,
                "error": str(exc),
            },
            exc_info=True,
        )
        try:
            from arq.worker import Retry  # noqa: PLC0415
        except Exception:  # noqa: BLE001 — arq absent in a minimal env
            raise exc from None
        raise Retry(defer=RESCORE_RETRY_DEFER_SECONDS * job_try) from exc

    _log.info(
        "rescore_task_done",
        extra={
            "event": "rescore_task_done",
            "user_id": user_id,
            "rescored": result.get("rescored"),
            "version": result.get("version"),
        },
    )
    return result


# Backfill defaults. Small on purpose: this exists to drain a 9,708-row debt
# without becoming the thing that takes production down.
BACKFILL_BATCH_SIZE = 25
BACKFILL_THROTTLE_SECONDS = 2.0


async def _stale_feed_users(
    db: pg.Connection, *, after: str, limit: int
) -> list[dict[str, Any]]:
    """Users whose ``user_feed`` rows are behind their current profile version.

    Keyset pagination on ``user_id`` (``user_id > after``) rather than
    OFFSET: the set shrinks underneath us as re-scores land, and OFFSET over a
    shrinking set silently SKIPS rows. It also guarantees the backfill loop
    terminates — a plain "re-query the stale set" loop would return the same
    batch forever, because a user stays stale until their queued job actually
    runs.

    Returns dicts with ``user_id``, ``current_version`` and ``stale_rows``.
    Returns ``[]`` if the profile-version table is missing (pre-migration DB)
    rather than aborting the sweep.
    """
    try:
        cur = await db.execute(
            """
            SELECT f.user_id AS user_id,
                   MAX(v.current_version) AS current_version,
                   COUNT(*) AS stale_rows
              FROM user_feed f
              JOIN (SELECT user_id, MAX(id) AS current_version
                      FROM user_profile_versions
                     GROUP BY user_id) v
                ON v.user_id = f.user_id
             WHERE COALESCE(f.profile_version, -1) <> v.current_version
               AND f.user_id > ?
             GROUP BY f.user_id
             ORDER BY f.user_id
             LIMIT ?
            """,
            (after, limit),
        )
        return [dict(r) for r in await cur.fetchall()]
    except Exception as exc:  # noqa: BLE001 — missing table on a legacy DB
        _log.warning(
            "rescore_backfill_query_failed",
            extra={"event": "rescore_backfill_query_failed", "error": str(exc)},
            exc_info=True,
        )
        return []


@_logged_task
async def rescore_backfill(
    ctx: dict[str, Any],
    *,
    batch_size: int = BACKFILL_BATCH_SIZE,
    max_users: int = 0,
    throttle_seconds: float = BACKFILL_THROTTLE_SECONDS,
) -> dict[str, Any]:
    """Drain the stale-feed debt: queue ONE re-score per affected user.

    RUN IT DELIBERATELY. It is registered in ``WorkerSettings.functions`` so it
    can be enqueued on purpose, and it is deliberately NOT on a cron — nothing
    this expensive should fire on boot.

    Four properties, each one a scar:

    * **Batched + throttled.** It walks users ``batch_size`` at a time and
      sleeps ``throttle_seconds`` between batches. Never one giant transaction.
    * **Does not monopolise the event loop.** The sleep between batches is a
      real ``await``, so anything else on the loop gets CPU while this runs.
      Pinned by ``tests/test_rescore_on_the_queue.py`` with a competing
      coroutine and a DB stub that yields nowhere else — delete the await and
      that test fails. This repo's recorded lesson is exactly this: "a
      correctness fix with an operational cost is still a regression".
    * **The heavy work is not done here.** Scoring the whole catalog for one
      user is a queued ``rescore_user_feed_task``, so each unit is bounded,
      retried and observable on its own. This function only decides WHO is owed
      a re-score.
    * **Resumable, not restartable.** The job id is
      ``rescore-backfill:<user>:<current_version>``; ARQ refuses a second job
      with an id it already knows, so a run that dies half-way can simply be
      re-run — users already re-scored have dropped out of the stale query, and
      users already queued are deduped by id. A NEW profile change produces a
      new version, hence a new id, and is still allowed through.

    Args:
        batch_size: users selected per DB round-trip.
        max_users: hard ceiling for one run; 0 = no ceiling.
        throttle_seconds: pause between batches.

    Returns ``{"enqueued", "batches", "skipped_no_queue"}``.
    """
    import asyncio as _asyncio  # noqa: PLC0415

    db: pg.Connection = ctx["db"]
    db.row_factory = pg.Row
    enqueue = ctx.get("enqueue")

    enqueued = 0
    batches = 0
    skipped_no_queue = 0
    after = ""

    while True:
        remaining = batch_size
        if max_users:
            remaining = min(batch_size, max_users - enqueued)
            if remaining <= 0:
                break

        rows = await _stale_feed_users(db, after=after, limit=remaining)
        if not rows:
            break
        batches += 1

        for row in rows:
            user_id = str(row["user_id"])
            after = user_id
            version = row["current_version"]
            if enqueue is None:
                skipped_no_queue += 1
                continue
            result = enqueue(
                "rescore_user_feed_task",
                user_id,
                _job_id=f"rescore-backfill:{user_id}:{version}",
            )
            if hasattr(result, "__await__"):
                await result
            enqueued += 1

        # THE YIELD. Everything above is synchronous once the batch is loaded,
        # so this is what keeps the worker's other tasks (and its Redis
        # heartbeat) alive while a long backfill drains.
        await _asyncio.sleep(throttle_seconds)

    _log.info(
        "rescore_backfill_done",
        extra={
            "event": "rescore_backfill_done",
            "enqueued": enqueued,
            "batches": batches,
            "skipped_no_queue": skipped_no_queue,
        },
    )
    return {
        "enqueued": enqueued,
        "batches": batches,
        "skipped_no_queue": skipped_no_queue,
    }


def _refresh_catalog_notifies() -> bool:
    """True when the nightly catalog refill is allowed to notify. Default False.

    #318 turned the old hardcoded ``suppress_notifications=True`` into this
    switch. The default is unchanged — the nightly fan-out stays silent — but
    the owner can now flip ``REFRESH_CATALOG_NOTIFY=1`` and restart the worker
    instead of needing a code change to make a product decision.

    Read at call time, not import time, so a restart is enough.

    Safe to enable ONLY because new users are seeded onto ``notify_mode='daily'``
    (``services/notifications/defaults.py``). In 'daily' mode dispatch() routes
    matches into ``user_notification_digests`` and ``send_bundle`` mails ONE
    bundle per user. Flipping this on while anyone sits on 'instant' is the
    blast-radius scenario this function's docstring warns about: ~280 new jobs
    per tick would become ~280 separate emails, per user, every night.
    """
    return os.getenv("REFRESH_CATALOG_NOTIFY", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


async def refresh_catalog(ctx: dict[str, Any]) -> dict[str, Any]:
    """ARQ periodic task: refill the SHARED job catalog on a schedule, then
    fan the freshly-fetched jobs out into every user's OWN feed.

    WHY THIS EXISTS. Nothing fetched jobs on a timer — `run_search` ran only
    when a human clicked Search. Meanwhile `purge_old_jobs` deletes anything
    older than 30 days, so the catalog drained. Measured in prod 2026-07-27:
    `run_log` had 11 rows in 25 days; 5,827 jobs fetched all-time vs 112
    visible, and most users saw an empty feed. Every instrument stayed GREEN
    throughout — 200s, no errors, no alerts — because all of them detect
    PRESENCE (did something break) and none detect ABSENCE (did something
    stop). This task is the missing production loop.

    F1 (2026-08-15) — fixing THAT gap was still not enough. This cron
    refilled the SHARED `jobs` catalog (rule #10: no user_id on that table)
    but nothing ever re-scored any user's OWN `user_feed` against the new
    rows, so a healthy catalog cron produced zero felt improvement: measured
    live, the owner's newest `user_feed` row was 19 days old while the
    catalog had 280 new jobs that same morning. `score_and_ingest` already
    did everything needed (per-user prefilter, per-user JobScorer, upsert
    into `user_feed`) — it just had ZERO production callers. This is that
    wiring: after the shared fetch, walk every job inserted THIS tick
    through `score_and_ingest`, capped by `MAX_REFRESH_INGEST_IDS`.

    COST SAFETY — structural, not a promise. `run_search` passes
    ``user_id=None``, and in ``main.run_search`` every per-user stage is
    gated behind ``if user_id is not None``: the ``user_feed`` write and,
    critically, ``_run_matcher_stage``, which makes up to
    ``MATCHER_MAX_JOBS`` PAID LLM calls *per user per run*. With no user
    attached those stages are unreachable during the fetch. The fan-out below
    is a SEPARATE, pure-local-CPU path — `score_and_ingest` prefilters, runs
    `JobScorer` (keyword + optional multi-dim), and upserts `user_feed`; it
    never calls the paid judge, in-process or otherwise (guard:
    ``test_refresh_catalog_fanout_never_reaches_the_paid_judge``).

    ONE HONEST EXCEPTION, stated because "never calls an LLM" would be a lie
    the next reader would rely on: `score_and_ingest` ENQUEUES
    ``enrich_job_task`` — a real LLM extraction — once per job when the first
    user clears ``ENRICHMENT_THRESHOLD`` (default 10, i.e. nearly every job).
    It is unreachable today only because ``ENGINE2_ENABLED`` defaults False
    (settings.py:256, gated as ENGINE2_ENABLED OR the legacy
    ENRICHMENT_ENABLED — rule #18, so BOTH names must be checked). That is a
    FLAG, not a guarantee: switching it on turns this nightly fan-out into
    ~280 LLM extractions per tick. Enrichment is genuinely wanted (it fills
    the salary/seniority/visa shelves the dim scorers read), so this is not
    blocked here — but whoever flips that flag should cap or batch this path
    first, and should know they are flipping it for a cron, not just for
    on-demand searches.

    So this cron costs worker CPU (already running 24/7) plus keyed-API quota
    for the fetch, plus bounded local scoring — and no per-user judge spend.

    NOTIFICATIONS — still suppressed by default, but now a SWITCH
    (``suppress_notifications=not _refresh_catalog_notifies()``, env
    ``REFRESH_CATALOG_NOTIFY``, default off). `score_and_ingest` queues an
    instant notification at score >= 80; wiring it into a nightly cron makes
    that reachable for EVERY user, EVERY tick, all at once — a materially
    different shape of risk than a user's own on-demand search enqueuing their
    own single result. Turning per-job pushes on for an unattended 04:00 cron
    is a real product decision (bundle daily? cap per tick? per-user opt-in?)
    that deserves its own review; making it an env var means that decision no
    longer costs a code change and a deploy.

    **The old "blast radius is zero" note is now STALE — do not rely on it.**
    It said `notification_rules` and `user_channels` were both empty. That was
    true on 2026-08-15 and was still true on 2026-08-19 (0 rows, 11 users),
    but #318 now SEEDS both at signup, so from here on users really do have a
    rulebook and a channel. What keeps the flip safe is no longer emptiness —
    it is that seeded users default to ``notify_mode='daily'``, so dispatch()
    bundles their matches into ``user_notification_digests`` and `send_bundle`
    mails one digest instead of ~280 separate messages.

    Feed rows and scores are written normally either way; the ledger row and
    the outbound send are BOTH skipped (:219-220), which is the correct
    pairing — a ledger row with no send would be treated as already-notified
    by the if-new dedup and would permanently swallow that job's notification.
    ``no_notify=True`` on `run_search` itself is unchanged: the shared
    catalog refill still belongs to nobody.

    Returns the run stats so a failure is visible in the ARQ log rather than
    silent.
    """
    import logging  # noqa: PLC0415

    from src.main import run_search  # noqa: PLC0415 — lazy (rule #11): heavy import
    from src.services.profile.keyword_generator import generate_search_config  # noqa: PLC0415
    from src.services.profile.models import SearchConfig  # noqa: PLC0415
    from src.services.profile.storage import list_profile_user_ids, load_profile  # noqa: PLC0415

    # The shared catalog serves EVERYONE, so fetch with the UNION of every
    # user's search config. Passing user_id=None alone was not enough — found
    # live 2026-07-30: run_search then fell back to DEFAULT_TENANT_ID, which
    # has no profile row in prod, and aborted with sources_queried=0. Combined
    # with the read-only-disk crash it masked, this cron had never fetched a
    # single job. Cost safety is unchanged: user_id stays None, so the
    # per-user stages (user_feed write, the paid LLM judge) remain
    # structurally unreachable.
    union = SearchConfig()
    profiles_used = 0
    for uid in list_profile_user_ids():
        profile = load_profile(uid)
        if not profile or not profile.is_complete:
            continue
        cfg = generate_search_config(profile)
        profiles_used += 1
        for attr in (
            "job_titles",
            # Must be unioned too: if it were left empty the sources would fall
            # back to the union of raw `job_titles` (BaseJobSource.search_titles)
            # and the catalog refill would send the junk queries again.
            "search_titles",
            "primary_skills",
            "secondary_skills",
            "tertiary_skills",
            "relevance_keywords",
            "locations",
            "visa_keywords",
            "search_queries",
        ):
            merged = getattr(union, attr)
            for item in getattr(cfg, attr):
                if item not in merged:
                    merged.append(item)
        union.core_domain_words |= cfg.core_domain_words
        union.supporting_role_words |= cfg.supporting_role_words
        # Deliberately NOT unioned: negative_title_keywords. One user's "not
        # for me" (e.g. an engineer excluding "sales") must not hide jobs from
        # a user who wants exactly those — exclusions are personal, and the
        # shared catalog must over-collect, not under-collect.

    if profiles_used == 0:
        # Say it loudly rather than abort quietly inside run_search — an empty
        # union means an empty fetch, which is this cron failing its one job.
        logging.getLogger(__name__).warning(
            "catalog refresh skipped: no complete user profiles found — nothing to fetch for"
        )
        return {"sources_queried": 0, "total_found": 0, "new_jobs": 0, "profiles_used": 0}

    logging.getLogger(__name__).info(
        "catalog refresh: union of %s profile(s) — %s titles, %s keywords",
        profiles_used,
        len(union.job_titles),
        len(union.relevance_keywords),
    )
    # Captured BEFORE the fetch runs, not after: run_search can take minutes
    # (multiple sources, retries), so timestamping post-hoc would miss any
    # job whose date_found was stamped while the fetch was still mid-flight.
    started = datetime.now(timezone.utc).isoformat()
    stats = await run_search(user_id=None, no_notify=True, search_config=union)
    logging.getLogger(__name__).info(
        "catalog refresh: sources=%s found=%s new=%s",
        stats.get("sources_queried"),
        stats.get("total_found"),
        stats.get("new_jobs"),
    )

    # F1 — fan the freshly-fetched jobs out into every user's own feed. This
    # is a SEPARATE connection from the one run_search opened and already
    # closed internally: `ctx['db']` is the worker's own long-lived
    # connection (populated by `worker_startup`; absent in the two
    # user_id=None cost-safety tests above, which pass ctx={} and exercise
    # only the fetch half — the `.get` below makes that a clean no-op rather
    # than a KeyError).
    scored_jobs = 0
    ingested_rows = 0
    db: Optional[pg.Connection] = ctx.get("db")
    if db is not None:
        db.row_factory = pg.Row
        cur = await db.execute(
            "SELECT id FROM jobs WHERE date_found >= ? ORDER BY id LIMIT ?",
            (started, MAX_REFRESH_INGEST_IDS),
        )
        new_ids = [row["id"] for row in await cur.fetchall()]

        # A capped fan-out LOSES jobs, it does not defer them: the next tick
        # computes a fresh `started`, so anything past the LIMIT is never
        # scored into a feed at all. Measured load is ~280 new jobs/tick
        # against a cap of 1000, so this should not fire — which is exactly
        # why it must be loud if it ever does, rather than quietly trimming
        # a busy day's catalog. Sentry picks this up via the logging handler.
        if len(new_ids) >= MAX_REFRESH_INGEST_IDS:
            cur = await db.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE date_found >= ?", (started,)
            )
            row = await cur.fetchone()
            total_new = int(row["n"]) if row else len(new_ids)
            _log.error(
                "FEED FAN-OUT CAPPED: %s new jobs this tick, only %s ingested — "
                "%s will never reach any user feed. Raise MAX_REFRESH_INGEST_IDS "
                "or make the fan-out resumable.",
                total_new,
                len(new_ids),
                max(0, total_new - len(new_ids)),
            )

        if new_ids:
            # Build the enrichment lookup ONCE for the whole tick instead of
            # once per job — see `score_and_ingest`'s `enrichment_lookup_dict`
            # param docstring for why that's safe. Measured live 2026-08-15:
            # job_enrichment holds ~6.5k rows; at ~280 jobs/tick, rebuilding
            # per job was ~40s of avoidable worker-loop time each run.
            enrichment_lookup_dict = await _build_enrichment_lookup(db)
            for job_id in new_ids:
                result = await score_and_ingest(
                    ctx,
                    job_id,
                    enrichment_lookup_dict=enrichment_lookup_dict,
                    # See the NOTIFICATIONS section of this function's
                    # docstring. Still suppressed by default, but it is now a
                    # PARAMETER rather than a hardcode: the owner can turn the
                    # nightly path on with an env var and a restart, instead of
                    # needing a code change and a deploy to make the decision.
                    suppress_notifications=not _refresh_catalog_notifies(),
                )
                scored_jobs += 1
                ingested_rows += result.get("ingested", 0)

        logging.getLogger(__name__).info(
            "catalog refresh: fanned out %s/%s new job id(s) into user feeds "
            "(%s user_feed row(s) written)",
            scored_jobs,
            len(new_ids),
            ingested_rows,
        )

    return {
        "sources_queried": stats.get("sources_queried", 0),
        "total_found": stats.get("total_found", 0),
        "new_jobs": stats.get("new_jobs", 0),
        "scored_jobs": scored_jobs,
        "ingested_rows": ingested_rows,
    }
