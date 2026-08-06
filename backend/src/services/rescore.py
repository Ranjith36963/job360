"""Shared scoring helpers for catalog-row rescoring.

Provides:
- ``score_catalog_row``: pure computation helper — reconstructs a Job from a
  catalog row dict and returns a ScoreBreakdown from scorer.score(job).
- ``rescore_user_feed``: async orchestrator — re-scores the whole catalog
  against the user's current profile, stamps profile_version, clears and
  (if MATCHER_ENABLED) re-runs LLM verdicts.

Both helpers are lazy-import-first (CLAUDE.md rule #16).
"""
from __future__ import annotations

import logging
from typing import Any, Optional, cast

from src.repositories.db_retry import with_write_retry

logger = logging.getLogger("job360.services.rescore")

# FIX 4 — per-user asyncio.Lock dict so two concurrent re-scores for the
# SAME user serialise; different users still run in parallel.
_user_rescore_locks: dict[str, Any] = {}


def score_catalog_row(scorer: Any, row: dict[str, Any]) -> Any:
    """Score one stored catalog row against a prepared JobScorer.

    Reconstructs a Job exactly like api/routes/jobs.py does, then calls
    ``scorer.score(job)`` and returns the resulting ``ScoreBreakdown``.

    Args:
        scorer: a prepared ``JobScorer`` instance (already initialised with
            the user's ``SearchConfig``, optional ``user_preferences``, and
            optional ``enrichment_lookup``).
        row: a plain dict with the catalog columns produced by
            ``JobDatabase.get_catalog_jobs_for_rescore`` (title, company,
            apply_url, source, date_found, location, description,
            salary_min, salary_max, posted_at, date_confidence).

    Returns:
        A ``ScoreBreakdown`` namedtuple/dataclass as returned by
        ``JobScorer.score()``.
    """
    from src.models import Job  # lazy per rule #16

    job = Job(
        title=row.get("title", "") or "",
        company=row.get("company", "") or "",
        apply_url=row.get("apply_url", "") or "",
        source=row.get("source", "") or "",
        date_found=row.get("date_found", "") or "",
        location=row.get("location", "") or "",
        description=row.get("description", "") or "",
        salary_min=row.get("salary_min"),
        salary_max=row.get("salary_max"),
        posted_at=row.get("posted_at"),
        date_confidence=row.get("date_confidence") or "low",
    )
    # FIX 5 — set job.id so the enrichment_lookup keyed on job.id can find
    # the enrichment row (project dim-scoring-id-bug: job.id was unset here,
    # causing enrichment dims to always score 0).
    job.id = row.get("id")
    return scorer.score(job)


async def _build_user_scorer(db: Any, profile: Any) -> Any:
    """Build the ``JobScorer`` for ``profile`` exactly as run_search does.

    Shared by ``rescore_user_feed`` and ``backfill_feed_from_catalog`` so the two
    paths can never drift apart in how they score (CLAUDE.md rules #19/#20: pass
    BOTH user_preferences and enrichment_lookup, or neither).
    """
    from src.core.settings import ENGINE2_ENABLED  # noqa: PLC0415
    from src.services.job_enrichment import (  # noqa: PLC0415
        ENRICHMENT_ENABLED,
        _build_enrichment_lookup,
    )
    from src.services.profile.keyword_generator import generate_search_config  # noqa: PLC0415
    from src.services.skill_matcher import JobScorer  # noqa: PLC0415

    search_config = generate_search_config(profile)
    enrichment_lookup_dict: dict[Any, Any]
    if ENGINE2_ENABLED or ENRICHMENT_ENABLED:
        enrichment_lookup_dict = await _build_enrichment_lookup(db._db)
    else:
        enrichment_lookup_dict = {}
    return JobScorer(
        search_config,
        user_preferences=profile.preferences,
        enrichment_lookup=lambda j: enrichment_lookup_dict.get(
            cast(int, getattr(j, "id", None))
        ),
    )


async def _load_preference_signals(db: Any, user_id: str) -> dict[str, set[str]]:
    """Learned-preference weighting (v1 2026-08-05, v2 same day): what the
    user's OWN actions teach us — BOTH directions.

    Positive: companies and title tokens of liked/applied jobs.
    Negative (v2): title tokens that RECUR across rejected jobs. One
    rejection teaches nothing general (the exact job is already excluded);
    a token appearing in >=2 rejected titles is a PATTERN the user keeps
    saying no to. Tokens also present in the liked vocabulary are immune —
    an ambivalent signal must never sink jobs the user demonstrably wants.

    Used as SELECTION weighting only — deterministic, explainable (no
    model), affects candidate MEMBERSHIP, never the stored score, so the
    dashboard ranking stays the raw scorer output.
    """
    import re as _re  # noqa: PLC0415

    cur = await db._db.execute(
        "SELECT j.company, j.title, a.action FROM user_actions a "
        "JOIN jobs j ON j.id = a.job_id WHERE a.user_id = ? AND a.action IN (?, ?, ?)",
        (user_id, "liked", "applied", "not_interested"),
    )
    companies: set[str] = set()
    tokens: set[str] = set()
    rejected_counts: dict[str, int] = {}
    for row in await cur.fetchall():
        comp, title, action = (row[0] or ""), (row[1] or ""), (row[2] or "")
        title_words = {w for w in _re.findall(r"\w+", title.lower()) if len(w) > 2}
        if action == "not_interested":
            for w in title_words:
                rejected_counts[w] = rejected_counts.get(w, 0) + 1
            continue
        if comp.strip():
            companies.add(comp.strip().lower())
        tokens |= title_words
    # v2 — a rejection PATTERN needs recurrence (>=2 rejected titles) and no
    # positive counter-evidence (liked vocabulary wins ties).
    rejected_tokens = {
        w for w, n in rejected_counts.items() if n >= 2 and w not in tokens
    }
    return {"companies": companies, "tokens": tokens, "rejected_tokens": rejected_tokens}


def _preference_boost(row: dict[str, Any], signals: dict[str, set[str]]) -> int:
    """Selection-priority weighting from learned preferences.

    +8 same company as a liked/applied job; +4 when >=2 title tokens overlap
    the liked vocabulary; −6 when >=1 title token matches a recurring
    rejection pattern (v2). The penalty is deliberately smaller than the
    keyword scale so it re-orders the shelf's margin — it does not blacklist
    (explicit rejection of the exact job already does that)."""
    import re as _re  # noqa: PLC0415

    boost = 0
    if (row.get("company") or "").strip().lower() in signals["companies"]:
        boost += 8
    title_tokens = set(_re.findall(r"\w+", (row.get("title") or "").lower()))
    if len(title_tokens & signals["tokens"]) >= 2:
        boost += 4
    if title_tokens & signals.get("rejected_tokens", set()):
        boost -= 6
    return boost


async def _load_action_sets(db: Any, user_id: str) -> tuple[set[Any], set[Any]]:
    """The user-feedback loop's read side (2026-08-05).

    Returns ``(protected, rejected)`` job-id sets from ``user_actions``:
      * ``protected`` — liked/applied jobs: user investment that must survive
        any re-selection (same principle as the LLM-verdict guard).
      * ``rejected`` — not_interested jobs: an explicit user NO that selection
        must honour; before this, the reject button recorded a row the
        candidate layer completely ignored — a dead lever.
    """
    cur = await db._db.execute(
        "SELECT job_id, action FROM user_actions WHERE user_id = ?",
        (user_id,),
    )
    protected: set[Any] = set()
    rejected: set[Any] = set()
    for row in await cur.fetchall():
        jid, action = row[0], row[1]
        if action == "not_interested":
            rejected.add(jid)
        elif action in ("liked", "applied"):
            protected.add(jid)
    return protected, rejected


async def backfill_feed_from_catalog(
    user_id: str,
    db: Any,
    *,
    profile: Any = None,
    version: Any = None,
) -> int:
    """Score the SHARED catalog for ``user_id`` and upsert the matches into their feed.

    Why this exists
    ---------------
    ``run_search`` only ever wrote the jobs *that run itself fetched* into
    ``user_feed``. The ``jobs`` table is a shared catalog (rule #10) that other
    users' runs keep filling — but none of those jobs were ever offered to this
    user unless their own crawl happened to re-fetch the identical posting. Two
    accounts with the SAME CV could therefore end up with wildly different feeds
    (observed: 37 jobs vs 4), purely from how many times each had searched.
    This makes a search a READ over the catalog, not just a crawl of whatever
    the sources returned in that minute.

    Deliberately NOT ``rescore_user_feed``: that helper clears and re-runs the
    LLM judge verdicts, which would re-pay the per-job LLM cost on *every*
    search. This one only scores + upserts, so it is safe to call every run.
    Existing verdicts are left untouched.

    Args:
        user_id: the user whose feed to fill.
        db: an already-open ``JobDatabase`` (reuses the caller's connection).
        profile: optional pre-loaded profile; loaded if omitted.
        version: optional profile-version stamp; resolved if omitted.

    Returns:
        Number of feed rows written/updated. ``0`` when there's no usable profile.
    """
    from src.core.settings import MIN_STORE_SCORE  # noqa: PLC0415
    from src.services.feed import FeedService  # noqa: PLC0415
    from src.services.profile.storage import (  # noqa: PLC0415
        current_profile_version_id,
        load_profile,
    )

    if profile is None:
        profile = load_profile(user_id)
    if not profile or not profile.is_complete:
        return 0
    if version is None:
        version = current_profile_version_id(user_id)

    from src.services.skill_matcher import SCORER_VERSION as _SCORER_VERSION  # noqa: PLC0415

    scorer = await _build_user_scorer(db, profile)
    rows = await db.get_catalog_jobs_for_rescore()

    # recency bucket — imported lazily to dodge the main.py import cycle
    # User-Level candidate bound (funnel Stage-1). Read at call time so tests
    # (and a runtime env change) can adjust it without re-importing.
    import src.core.settings as _settings  # noqa: PLC0415
    from src.main import _recency_bucket  # noqa: PLC0415

    cap = int(getattr(_settings, "FEED_CANDIDATE_CAP", 0) or 0)

    # Phase 1 — score the whole catalog (unchanged cost: this loop always
    # scored every row; it just also WROTE every row that beat the floor).
    # `title_strong` feeds the gem-rescue lane (Phase 5): iteration-1 audit
    # found 17 buried gems, ALL title-perfect jobs sunk by thin/no description
    # text ("Senior Data Engineer", desc=0c, kw=26).
    scored: list[tuple[int, Any, dict[str, Any]]] = []
    title_strong: list[tuple[int, Any, dict[str, Any]]] = []
    from src.services.llm_matcher import TITLE_RESCUE_MIN  # noqa: PLC0415

    for row in rows:
        jid = row.get("id")
        if jid is None:
            continue
        # Per-row guard: one malformed catalog row must never abort the backfill
        # (mirrors run_search's per-job guard).
        try:
            breakdown = score_catalog_row(scorer, row)
            ms = int(breakdown.match_score or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("catalog backfill: skipping job %s: %s", jid, exc)
            continue
        if int(getattr(breakdown, "title_score", 0) or 0) >= TITLE_RESCUE_MIN:
            title_strong.append((ms, jid, row))
        if ms < MIN_STORE_SCORE:
            continue
        scored.append((ms, jid, row))

    # The user-feedback loop: explicit actions steer the shelf.
    protected_ids, rejected_ids = await _load_action_sets(db, user_id)
    if rejected_ids:
        scored = [t for t in scored if t[1] not in rejected_ids]

    # Phase 2 — candidate selection: keep only the user's top-`cap` jobs.
    # cap=0 disables (legacy flood). Selection priority = score + learned-
    # preference boost (v1: liked/applied companies + title vocabulary), so
    # jobs resembling what the user chose win the shelf's marginal slots.
    # The boost affects MEMBERSHIP only — stored scores stay raw.
    if cap > 0 and len(scored) > cap:
        signals = await _load_preference_signals(db, user_id)
        if signals["companies"] or signals["tokens"] or signals["rejected_tokens"]:
            scored.sort(
                key=lambda t: t[0] + _preference_boost(t[2], signals), reverse=True
            )
        else:
            scored.sort(key=lambda t: t[0], reverse=True)
        selected = scored[:cap]
    else:
        selected = scored

    feed = FeedService(db._db)
    written = 0
    for ms, jid, row in selected:
        try:
            await with_write_retry(
                lambda jid=jid, ms=ms, row=row: feed.upsert_feed_row(  # type: ignore[misc]
                    user_id=user_id,
                    job_id=jid,
                    score=ms,
                    bucket=_recency_bucket(row.get("date_found")),
                    profile_version=version,
                    scorer_version=_SCORER_VERSION,
                )
            )
            written += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("catalog backfill: skipping job %s: %s", jid, exc)
            continue

    # Phase 3 — membership sync: evict active rows outside the selection,
    # reactivate re-selected stale rows. Only when the cap is on.
    evicted = 0
    if cap > 0 and selected:
        try:
            # Liked/applied rows join the kept set (never staled, reactivated
            # if stale); not_interested rows are force-evicted past every guard.
            evicted = await with_write_retry(
                lambda: feed.apply_candidate_selection(
                    user_id,
                    {jid for _, jid, _ in selected} | protected_ids,
                    force_evict_ids=rejected_ids,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("catalog backfill: selection sync failed: %s", exc)

    # Phase 4 — CANDIDATE enrichment (funnel Stage-2, 2026-08-05). E2 used to
    # enrich only the top-20 of jobs a run itself fetched, so prod coverage sat
    # at 24/7,309 jobs (0.3%) and the salary/visa/seniority/workplace
    # preference dimensions effectively never fired. Enrichment is per-JOB and
    # shared (rule #17: no user_id), so enriching this user's candidates
    # benefits every user and is cached forever. Budget: the best-scoring
    # ENRICHMENT_MAX_JOBS selected candidates still missing enrichment, per
    # backfill — coverage of the candidate set grows with every search.
    # Gated exactly like run_search's stage (ENGINE2_ENABLED OR legacy flag).
    from src.core.settings import ENGINE2_ENABLED  # noqa: PLC0415
    from src.services.job_enrichment import ENRICHMENT_ENABLED  # noqa: PLC0415

    if (ENGINE2_ENABLED or ENRICHMENT_ENABLED) and selected:
        try:
            from src.core.settings import ENRICHMENT_MAX_JOBS  # noqa: PLC0415
            from src.models import Job as _EJob  # noqa: PLC0415
            from src.services.job_enrichment import (  # noqa: PLC0415
                enrich_batch,
                has_enrichment,
            )

            ranked = sorted(selected, key=lambda t: t[0], reverse=True)
            to_enrich: list[Any] = []
            for _ms, jid, row in ranked:
                if len(to_enrich) >= ENRICHMENT_MAX_JOBS:
                    break
                if await has_enrichment(db._db, jid):
                    continue
                ejob = _EJob(
                    title=row.get("title", "") or "",
                    company=row.get("company", "") or "",
                    apply_url=row.get("apply_url", "") or "",
                    source=row.get("source", "") or "",
                    date_found=row.get("date_found", "") or "",
                    location=row.get("location", "") or "",
                    description=row.get("description", "") or "",
                )
                ejob.id = jid
                to_enrich.append(ejob)
            if to_enrich:
                await enrich_batch(to_enrich, semaphore_limit=3, conn=db._db)
                logger.info(
                    "catalog backfill: enriched %s candidate jobs for user %s",
                    len(to_enrich), user_id,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("catalog backfill: candidate enrichment failed: %s", exc)

    # Phase 5 — the GEM-RESCUE lane (2026-08-06, iteration-1 audit). Title-
    # perfect jobs with thin/no descriptions lose the 40 skill points and sink
    # below the shelf (or get evicted): 17/100 buried-sample gems, all this
    # class. Any title-strong row without a verdict gets judged regardless of
    # its total (capped per run); a judged-strong row is reactivated so the
    # COALESCE ranking surfaces it, and the verdict guard makes it
    # eviction-proof from then on. Gated like every E4 call site.
    from src.core.settings import ENGINE4_ENABLED  # noqa: PLC0415
    from src.services.llm_matcher import (  # noqa: PLC0415
        MATCHER_ENABLED,
        MATCHER_RESCUE_MAX,
        RESCUE_REACTIVATE_MIN,
    )

    if (ENGINE4_ENABLED or MATCHER_ENABLED) and title_strong and MATCHER_RESCUE_MAX > 0:
        try:
            from src.models import Job as _RJob  # noqa: PLC0415
            from src.services.llm_matcher import (  # noqa: PLC0415
                has_verdict,
                match_batch,
                profile_to_matcher_text,
            )

            rescue: list[Any] = []
            for ms, jid, row in sorted(title_strong, key=lambda t: t[0], reverse=True):
                if len(rescue) >= MATCHER_RESCUE_MAX:
                    break
                if await has_verdict(db._db, user_id, jid):
                    continue
                rjob = _RJob(
                    title=row.get("title", "") or "",
                    company=row.get("company", "") or "",
                    apply_url=row.get("apply_url", "") or "",
                    source=row.get("source", "") or "",
                    date_found=row.get("date_found", "") or "",
                    location=row.get("location", "") or "",
                    description=row.get("description", "") or "",
                    salary_min=row.get("salary_min"),
                    salary_max=row.get("salary_max"),
                )
                rjob.id = jid
                rjob.match_score = ms
                rescue.append(rjob)
            if rescue:
                await match_batch(
                    rescue,
                    user_id=user_id,
                    profile_text=profile_to_matcher_text(profile),
                    conn=db._db,
                    semaphore_limit=3,
                )
                # Reactivate rescued rows whose verdict clears the bar — an
                # evicted gem returns to the shelf, ranked by its fit score.
                from datetime import datetime, timezone  # noqa: PLC0415

                now = datetime.now(timezone.utc).isoformat()
                cur = await db._db.execute(
                    "UPDATE user_feed SET status = 'active', updated_at = ? "
                    "WHERE user_id = ? AND status = 'stale' AND llm_fit_score >= ?",
                    (now, user_id, RESCUE_REACTIVATE_MIN),
                )
                await db._db.commit()
                logger.info(
                    "catalog backfill: gem-rescue judged %s title-strong jobs "
                    "for user %s (reactivated %s)",
                    len(rescue), user_id, cur.rowcount or 0,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("catalog backfill: gem-rescue failed: %s", exc)

    logger.info(
        "catalog backfill: user %s matched %s of %s catalog jobs "
        "(floor %s, cap %s, evicted %s)",
        user_id,
        written,
        len(rows),
        MIN_STORE_SCORE,
        cap or "off",
        evicted,
    )
    return written


async def rescore_user_feed(
    user_id: str,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    """Re-score the whole catalog against the user's current profile.

    Steps:
    1. Load profile; bail early if missing or incomplete.
    2. Acquire per-user lock (FIX 4) to serialise concurrent re-scores.
    3. Open DB (same pattern as run_search), close in finally.
    4. Build scorer exactly like run_search (mirrors CLAUDE.md rules #19/#20).
    5. Load all catalog rows; for each: score and upsert into user_feed.
       Per-row errors are caught so one bad row can't abort the whole run (FIX 3).
    6. If MATCHER_ENABLED: clear existing verdicts, collect shortlist
       (score >= MATCHER_THRESHOLD), and call _run_matcher_stage. (FIX 1 —
       entire verdict path is skipped when the flag is off.)
    7. Return {"rescored": N, "version": version_id}.

    Heavy imports stay local (CLAUDE.md rule #16).
    """
    # 1. Profile check — lazy imports to avoid import cycles with main.py
    from src.services.profile.storage import (  # noqa: PLC0415
        current_profile_version_id,
        load_profile,
    )

    profile = load_profile(user_id)
    if not profile or not profile.is_complete:
        return {"rescored": 0, "reason": "no_profile"}

    # FIX 4 — acquire per-user lock before opening the DB.
    import asyncio as _asyncio  # noqa: PLC0415

    lock = _user_rescore_locks.setdefault(user_id, _asyncio.Lock())
    async with lock:
        # 2. Open the DB the same way run_search does
        from src.core.settings import DB_PATH  # noqa: PLC0415
        from src.repositories.database import JobDatabase  # noqa: PLC0415

        db = JobDatabase(db_path or str(DB_PATH))
        await db.init_db()
        try:
            # 3. Profile version stamp
            version = current_profile_version_id(user_id)

            # 4. Build scorer exactly like run_search (rules #19 / #20)
            from src.core.settings import ENGINE2_ENABLED  # noqa: PLC0415
            from src.services.job_enrichment import (  # noqa: PLC0415
                ENRICHMENT_ENABLED,
                _build_enrichment_lookup,
            )
            from src.services.profile.keyword_generator import generate_search_config  # noqa: PLC0415
            from src.services.skill_matcher import (  # noqa: PLC0415
                SCORER_VERSION as _SCORER_VERSION,
            )
            from src.services.skill_matcher import JobScorer  # noqa: PLC0415

            search_config = generate_search_config(profile)
            # Engine 2 switch (ENGINE2_ENABLED) OR the legacy ENRICHMENT_ENABLED flag.
            # `db._db` is the connection property that narrows the Optional
            # `_conn` in one place (see JobDatabase._db docstring).
            enrichment_lookup_dict: dict[Any, Any]
            if ENGINE2_ENABLED or ENRICHMENT_ENABLED:
                enrichment_lookup_dict = await _build_enrichment_lookup(db._db)
            else:
                enrichment_lookup_dict = {}
            scorer = JobScorer(
                search_config,
                user_preferences=profile.preferences,
                # cast: the lookup is keyed by job id; `getattr` widens to
                # Any|None and a missing id simply misses the dict (unchanged
                # runtime behaviour — `cast` is a no-op).
                enrichment_lookup=lambda j: enrichment_lookup_dict.get(
                    cast(int, getattr(j, "id", None))
                ),
            )

            # 5. Load catalog rows + existing feed job ids
            rows = await db.get_catalog_jobs_for_rescore()

            cur = await db._db.execute(
                "SELECT job_id FROM user_feed WHERE user_id = ?",
                (user_id,),
            )
            existing_feed_ids: set[Any] = {r[0] for r in await cur.fetchall()}

            # recency bucket — try to import from main; replicate tiny logic on cycle
            try:
                from src.main import _recency_bucket  # noqa: PLC0415
            except Exception:  # noqa: BLE001
                from datetime import datetime, timezone  # noqa: PLC0415

                def _recency_bucket(date_found: Optional[str]) -> str:
                    try:
                        from src.utils.time_buckets import parse_date_safe  # noqa: PLC0415
                        dt = parse_date_safe(date_found or "")
                    except Exception:  # noqa: BLE001
                        dt = None
                    if dt is None:
                        return "older"
                    hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
                    for label, limit in (("24h", 24), ("48h", 48), ("3d", 72), ("5d", 120), ("7d", 168)):
                        if hours <= limit:
                            return label
                    return "older"

            from src.services.feed import FeedService  # noqa: PLC0415

            feed = FeedService(db._db)
            rescored = 0

            # FIX 1 — read MATCHER_ENABLED lazily, ONLY to decide whether to
            # run the verdict path.  The import is inside the try-block but
            # the clear / shortlist-build / re-judge are fully gated on the
            # flag, matching how main.py::_run_matcher_stage returns early.
            from src.core.settings import ENGINE4_ENABLED  # noqa: PLC0415
            from src.services.llm_matcher import (  # noqa: PLC0415
                MATCHER_ENABLED,
                MATCHER_THRESHOLD,
            )

            # Engine 4 switch (ENGINE4_ENABLED) OR the legacy MATCHER_ENABLED flag.
            matcher_on = ENGINE4_ENABLED or MATCHER_ENABLED

            # FIX 1 — only import Job and build the shortlist when the judge
            # will actually run.  When matcher_on is False, shortlist_jobs is
            # never allocated and clear_user_verdicts is never called.
            shortlist_jobs: Optional[list[Any]]
            if matcher_on:
                from src.models import Job as _Job  # noqa: PLC0415
                from src.services.llm_matcher import clear_user_verdicts  # noqa: PLC0415
                await clear_user_verdicts(db._db, user_id)
                shortlist_jobs = []
            else:
                _Job = None  # type: ignore[assignment,misc]  # unused branch
                shortlist_jobs = None

            # User-Level candidate bound (funnel Stage-1) — same selection the
            # backfill applies, so a profile change re-selects the shelf.
            import src.core.settings as _settings  # noqa: PLC0415

            cap = int(getattr(_settings, "FEED_CANDIDATE_CAP", 0) or 0)

            # Phase 1 — score every catalog row (FIX 3 per-row guard kept:
            # one bad row must not abort the whole re-score).
            scored_rows: list[tuple[int, Any, dict[str, Any]]] = []
            for row in rows:
                jid = row.get("id")
                if jid is None:
                    continue
                try:
                    breakdown = score_catalog_row(scorer, row)
                    ms = int(breakdown.match_score or 0)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("rescore: skipping job %s: %s", jid, exc)
                    continue
                # Legacy write condition: a positive score, or an existing feed
                # row that must be re-stamped under the new profile version.
                if ms > 0 or jid in existing_feed_ids:
                    scored_rows.append((ms, jid, row))

            # The user-feedback loop: rejected jobs never re-enter selection
            # (and never reach the paid judge); liked/applied are protected.
            protected_ids, rejected_ids = await _load_action_sets(db, user_id)
            if rejected_ids:
                scored_rows = [t for t in scored_rows if t[1] not in rejected_ids]

            # Phase 2 — selection: top-`cap` by score + learned-preference
            # boost (same rule as backfill, so the two paths never disagree
            # about shelf membership). cap=0 = legacy (all).
            if cap > 0 and len(scored_rows) > cap:
                signals = await _load_preference_signals(db, user_id)
                if signals["companies"] or signals["tokens"] or signals["rejected_tokens"]:
                    scored_rows.sort(
                        key=lambda t: t[0] + _preference_boost(t[2], signals),
                        reverse=True,
                    )
                else:
                    scored_rows.sort(key=lambda t: t[0], reverse=True)
                selected_rows = scored_rows[:cap]
            else:
                selected_rows = scored_rows

            for ms, jid, row in selected_rows:
                try:
                    # Finding #11: retry on 'database is locked' so a transient
                    # lock under concurrent writes doesn't drop this scored row.
                    await with_write_retry(
                        # Bind loop vars as defaults so the closure captures
                        # THIS iteration's values (B023). ignore[misc]: a
                        # default-arg lambda is not a bare zero-arg Callable.
                        lambda jid=jid, ms=ms, row=row: feed.upsert_feed_row(  # type: ignore[misc]
                            user_id=user_id,
                            job_id=jid,
                            score=int(ms),
                            bucket=_recency_bucket(row.get("date_found")),
                            profile_version=version,
                            scorer_version=_SCORER_VERSION,
                        )
                    )
                    rescored += 1

                    # Collect shortlist for LLM judge (FIX 1: only when
                    # matcher_on) — built from SELECTED rows only, so the judge
                    # never spends on a job outside the candidate set.
                    if matcher_on and ms >= MATCHER_THRESHOLD:
                        job = _Job(
                            title=row.get("title", "") or "",
                            company=row.get("company", "") or "",
                            apply_url=row.get("apply_url", "") or "",
                            source=row.get("source", "") or "",
                            date_found=row.get("date_found", "") or "",
                            location=row.get("location", "") or "",
                            description=row.get("description", "") or "",
                            salary_min=row.get("salary_min"),
                            salary_max=row.get("salary_max"),
                            posted_at=row.get("posted_at"),
                            date_confidence=row.get("date_confidence") or "low",
                        )
                        job.id = jid
                        job.match_score = int(ms)
                        shortlist_jobs.append(job)  # type: ignore[union-attr]
                except Exception as exc:  # noqa: BLE001
                    logger.warning("rescore: skipping job %s: %s", jid, exc)
                    continue

            # Phase 3 — membership sync (evict out-of-selection, reactivate
            # re-selected). Only when the cap is on.
            if cap > 0 and selected_rows:
                try:
                    evicted = await with_write_retry(
                        lambda: feed.apply_candidate_selection(
                            user_id,
                            {jid for _, jid, _ in selected_rows} | protected_ids,
                            force_evict_ids=rejected_ids,
                        )
                    )
                    if evicted:
                        logger.info(
                            "rescore: user %s evicted %s out-of-selection rows",
                            user_id, evicted,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("rescore: selection sync failed: %s", exc)

            # 6. LLM re-judge (FIX 1: entire block gated on matcher_on)
            if matcher_on and shortlist_jobs:
                try:
                    from src.main import _run_matcher_stage  # noqa: PLC0415
                    await _run_matcher_stage(db, user_id=user_id, jobs=shortlist_jobs)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("rescore: matcher stage failed (continuing): %s", exc)

            logger.info(
                "rescore: user %s rescored %s jobs against profile version %s",
                user_id,
                rescored,
                version,
            )
            return {"rescored": rescored, "version": version}

        finally:
            await db.close()
