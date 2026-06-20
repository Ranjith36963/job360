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
from typing import Optional

logger = logging.getLogger("job360.services.rescore")

# FIX 4 — per-user asyncio.Lock dict so two concurrent re-scores for the
# SAME user serialise; different users still run in parallel.
_user_rescore_locks: dict = {}


def score_catalog_row(scorer, row: dict):
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
    job.id = row.get("id")  # type: ignore[attr-defined]
    return scorer.score(job)


async def rescore_user_feed(
    user_id: str,
    db_path: Optional[str] = None,
) -> dict:
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
            from src.services.skill_matcher import JobScorer  # noqa: PLC0415

            search_config = generate_search_config(profile)
            # Engine 2 switch (ENGINE2_ENABLED) OR the legacy ENRICHMENT_ENABLED flag.
            if ENGINE2_ENABLED or ENRICHMENT_ENABLED:
                enrichment_lookup_dict = await _build_enrichment_lookup(db._conn)
            else:
                enrichment_lookup_dict = {}
            scorer = JobScorer(
                search_config,
                user_preferences=profile.preferences,
                enrichment_lookup=lambda j: enrichment_lookup_dict.get(getattr(j, "id", None)),
            )

            # 5. Load catalog rows + existing feed job ids
            rows = await db.get_catalog_jobs_for_rescore()

            cur = await db._conn.execute(
                "SELECT job_id FROM user_feed WHERE user_id = ?",
                (user_id,),
            )
            existing_feed_ids: set = {r[0] for r in await cur.fetchall()}

            # recency bucket — try to import from main; replicate tiny logic on cycle
            try:
                from src.main import _recency_bucket  # noqa: PLC0415
            except Exception:  # noqa: BLE001
                from datetime import datetime, timezone  # noqa: PLC0415

                def _recency_bucket(date_found: Optional[str]) -> str:  # type: ignore[misc]
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

            feed = FeedService(db._conn)
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
            if matcher_on:
                from src.services.llm_matcher import clear_user_verdicts  # noqa: PLC0415
                from src.models import Job as _Job  # noqa: PLC0415
                await clear_user_verdicts(db._conn, user_id)
                shortlist_jobs = []
            else:
                _Job = None  # type: ignore[assignment,misc]  # unused branch
                shortlist_jobs = None  # type: ignore[assignment]  # unused branch

            for row in rows:
                jid = row.get("id")
                if jid is None:
                    continue
                # FIX 3 — per-row error guard: one bad row must not abort the
                # whole re-score (mirrors run_search's per-job guard in main.py).
                try:
                    breakdown = score_catalog_row(scorer, row)
                    ms = breakdown.match_score

                    if ms > 0 or jid in existing_feed_ids:
                        await feed.upsert_feed_row(
                            user_id=user_id,
                            job_id=jid,
                            score=int(ms),
                            bucket=_recency_bucket(row.get("date_found")),
                            profile_version=version,
                        )
                        rescored += 1

                    # Collect shortlist for LLM judge (FIX 1: only when matcher_on)
                    if matcher_on and ms >= MATCHER_THRESHOLD:
                        job = _Job(  # type: ignore[misc]
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
                        job.id = jid  # type: ignore[attr-defined]
                        job.match_score = int(ms)
                        shortlist_jobs.append(job)  # type: ignore[union-attr]
                except Exception as exc:  # noqa: BLE001
                    logger.warning("rescore: skipping job %s: %s", jid, exc)
                    continue

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
