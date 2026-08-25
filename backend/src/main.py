import argparse
import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import aiohttp

from src.core.settings import (
    ADZUNA_APP_ID,
    ADZUNA_APP_KEY,
    CAREERJET_AFFID,
    DB_PATH,
    DFE_APPRENTICESHIPS_API_KEY,
    ENRICHMENT_MAX_JOBS,
    ENRICHMENT_MIN_SCORE,
    EXPORTS_DIR,
    FINDWORK_API_KEY,
    JOOBLE_API_KEY,
    JSEARCH_API_KEY,
    MIN_MATCH_SCORE,
    MIN_STORE_SCORE,
    REED_API_KEY,
    REPORTS_DIR,
    REQUEST_TIMEOUT,
    SEMANTIC_ENABLED,
    SERPAPI_KEY,
)
from src.core.tenancy import DEFAULT_TENANT_ID
from src.models import Job
from src.repositories import pg
from src.repositories.csv_export import export_to_csv
from src.repositories.database import JobDatabase
from src.services.circuit_breaker import BreakerState
from src.services.circuit_breaker import default_registry as default_breaker_registry
from src.services.deduplicator import deduplicate
from src.services.domain_classifier import (
    classify_user_domain,
    source_matches_user_domains,
)
from src.services.job_enrichment import (
    ENRICHMENT_ENABLED,
    _build_enrichment_lookup,
    enrich_batch,
)
from src.services.metrics_exporter import export_notification_metrics, export_pipeline_metrics
from src.services.notifications.report_generator import generate_markdown_report
from src.services.profile.keyword_generator import generate_search_config
from src.services.profile.models import SearchConfig, UserProfile
from src.services.profile.storage import current_profile_version_id, load_profile
from src.services.scheduler import TieredScheduler
from src.services.skill_matcher import (
    SCORER_VERSION,
    JobScorer,
    detect_experience_level,
    salary_in_range,
)
from src.services.uk_gate import check_uk
from src.sources.apis_free.arbeitnow import ArbeitnowSource
from src.sources.apis_free.devitjobs import DevITJobsSource
from src.sources.apis_free.himalayas import HimalayasSource
from src.sources.apis_free.hn_jobs import HNJobsSource
from src.sources.apis_free.jobicy import JobicySource
from src.sources.apis_free.landingjobs import LandingJobsSource
from src.sources.apis_free.remoteok import RemoteOKSource
from src.sources.apis_free.remotive import RemotiveSource

# Batch 3 additions
from src.sources.apis_free.teaching_vacancies import TeachingVacanciesSource
from src.sources.apis_keyed.adzuna import AdzunaSource
from src.sources.apis_keyed.careerjet import CareerjetSource
from src.sources.apis_keyed.findwork import FindworkSource
from src.sources.apis_keyed.google_jobs import GoogleJobsSource
from src.sources.apis_keyed.gov_apprenticeships import GovApprenticeshipsSource
from src.sources.apis_keyed.jooble import JoobleSource
from src.sources.apis_keyed.jsearch import JSearchSource
from src.sources.apis_keyed.reed import ReedSource
from src.sources.ats.ashby import AshbySource
from src.sources.ats.greenhouse import GreenhouseSource
from src.sources.ats.lever import LeverSource
from src.sources.ats.personio import PersonioSource
from src.sources.ats.pinpoint import PinpointSource
from src.sources.ats.recruitee import RecruiteeSource
from src.sources.ats.smartrecruiters import SmartRecruitersSource
from src.sources.ats.successfactors import SuccessFactorsSource
from src.sources.ats.workable import WorkableSource
from src.sources.ats.workday import WorkdaySource
from src.sources.base import BaseJobSource
from src.sources.feeds.nhs_jobs import NHSJobsSource
from src.sources.feeds.realworkfromanywhere import RealWorkFromAnywhereSource
from src.sources.feeds.uni_jobs import UniJobsSource
from src.sources.feeds.weworkremotely import WeWorkRemotelySource
from src.sources.other.hackernews import HackerNewsSource
from src.sources.other.indeed import JobSpySource
from src.sources.other.nofluffjobs import NoFluffJobsSource
from src.sources.other.themuse import TheMuseSource
from src.sources.scrapers.aijobs_ai import AIJobsAISource
from src.sources.scrapers.bcs_jobs import BCSJobsSource
from src.sources.scrapers.climatebase import ClimatebaseSource
from src.sources.scrapers.eightykhours import EightyKHoursSource
from src.sources.scrapers.linkedin import LinkedInSource
from src.utils.logger import set_run_uuid, setup_logging
from src.utils.loop_guard import cpu_bound
from src.utils.telemetry import source_timer

logger = logging.getLogger("job360.main")

# Source name → class mapping for --source filter
SOURCE_REGISTRY = {
    "reed": ReedSource,
    "adzuna": AdzunaSource,
    "jsearch": JSearchSource,
    "arbeitnow": ArbeitnowSource,
    "remoteok": RemoteOKSource,
    "jobicy": JobicySource,
    "himalayas": HimalayasSource,
    "greenhouse": GreenhouseSource,
    "lever": LeverSource,
    "workable": WorkableSource,
    "ashby": AshbySource,
    "remotive": RemotiveSource,
    "jooble": JoobleSource,
    "linkedin": LinkedInSource,
    "smartrecruiters": SmartRecruitersSource,
    "pinpoint": PinpointSource,
    "recruitee": RecruiteeSource,
    "indeed": JobSpySource,
    "glassdoor": JobSpySource,
    "workday": WorkdaySource,
    "google_jobs": GoogleJobsSource,
    "devitjobs": DevITJobsSource,
    "landingjobs": LandingJobsSource,
    "themuse": TheMuseSource,
    "hackernews": HackerNewsSource,
    "careerjet": CareerjetSource,
    "findwork": FindworkSource,
    "gov_apprenticeships": GovApprenticeshipsSource,
    "nofluffjobs": NoFluffJobsSource,
    # Phase 4: New free sources
    "hn_jobs": HNJobsSource,
    "nhs_jobs": NHSJobsSource,
    "personio": PersonioSource,
    "weworkremotely": WeWorkRemotelySource,
    "realworkfromanywhere": RealWorkFromAnywhereSource,
    "climatebase": ClimatebaseSource,
    "eightykhours": EightyKHoursSource,
    "bcs_jobs": BCSJobsSource,
    "uni_jobs": UniJobsSource,
    "successfactors": SuccessFactorsSource,
    "aijobs_ai": AIJobsAISource,
    # Batch 3 additions
    "teaching_vacancies": TeachingVacanciesSource,
}

# Number of unique source instances created by _build_sources().
# 40 not 41 because "indeed" and "glassdoor" both map to JobSpySource (one instance).
# 4 dead sources removed in the 2026-06 M6 rotation: jobtensor, comeet,
# gov_apprenticeships, aijobs_global — all upstream-dead. gov_apprenticeships
# was restored 2026-06-16 against the DfE Display Advert API v2 (keyed).
# 6 more removed 2026-08-10 after live checks (registry 47 -> 41): aijobs
# (aijobs.net/api/list-jobs/ 404), jobs_ac_uk (all 4 feed URLs 404), biospace
# (all 3 feed URLs 404), rippling (ats.rippling.com board API gone), nhs_jobs_xml
# (feed now serves HTML, retired — the separate nhs_jobs source is still ALIVE),
# workanywhere (HTTP 429 bot-checkpoint on every path).
# Used by test_main.py::test_source_instance_count_matches_build to catch drift.
# Update this when adding/removing sources.
SOURCE_INSTANCE_COUNT = 40


async def _ghost_detection_pass(
    db: JobDatabase,
    sources: list[BaseJobSource],
    results: list[list[Job] | BaseException | None],
    history: dict[str, list[int]],
    completeness_threshold: float = 0.7,
) -> dict[str, int]:
    """Per-source absence sweep with scrape-completeness gate.

    For each (source, result) pair from the main asyncio.gather:
      * Skip if the scrape failed (exception or None).
      * Skip if result count < `completeness_threshold` × rolling-7d average
        (pillar_3_batch_1.md §3 Step 1 — don't treat a rate-limited scrape
        as ghost evidence).
      * Otherwise: call update_last_seen for every observed job key, then
        mark_missed_for_source for the rest.

    Returns {source_name: missed_count} for observability.
    """
    missed_by_source: dict[str, int] = {}

    async def _warn_sweep_skipped(source_name: str, reason: str, detail: str) -> None:
        """Name the cost of a skipped absence sweep, not just the skip.

        PROD 2026-08-13: adzuna, linkedin and workday raise on EVERY run
        (run_log.per_source_errors; workday's duration is pinned at the 240s
        timeout). Skipping them is correct — a failed scrape must never be read
        as "the jobs disappeared" — but the skip used to be a bare `continue`
        with no log at all for the failure branch. So their listings stopped
        ageing and were served as live indefinitely: 140 rows sat in
        `staleness_state='active'` with `last_seen_at` 3-8 days old, and
        because `consecutive_misses` never reached 2 the nightly ghost sweep
        legitimately reported `transitioned: 0`. Every instrument said fine.
        """
        # `-1` IS NOT A COUNT. When the count itself failed this logged
        # "-1 live job(s)" and swallowed the exception whole, so a reader could
        # not tell a source-FETCH failure from a second failure in the DATABASE,
        # and had no stack trace for either. A log line that exists to name a
        # cost must not quietly invent the number. Say so, and record why.
        # (CodeRabbit, PR #387.)
        frozen: int | str
        try:
            frozen = await db.count_unexpired_jobs_for_source(source_name)
        except Exception:  # noqa: BLE001 — observability must never break a run
            frozen = "an unknown number of"
            logger.warning(
                "  %s: could not count this source's live jobs either — the "
                "absence-sweep warning below has no number behind it",
                source_name,
                exc_info=True,
            )
        logger.warning(
            "  %s: absence sweep SKIPPED (%s: %s) — %s live job(s) of this source "
            "stopped ageing and are still served as active",
            source_name,
            reason,
            detail,
            frozen,
        )

    for source, result in zip(sources, results):
        if isinstance(result, BaseException) or result is None:
            await _warn_sweep_skipped(
                source.name,
                "fetch failed",
                type(result).__name__ if isinstance(result, BaseException) else "no result",
            )
            continue
        past = history.get(source.name, [])
        rolling_avg = (sum(past) / len(past)) if past else 0.0
        if rolling_avg > 0 and len(result) < completeness_threshold * rolling_avg:
            await _warn_sweep_skipped(
                source.name,
                "incomplete scrape",
                f"{len(result)} results < {completeness_threshold * 100:.0f}% of 7-day avg {rolling_avg:.1f}",
            )
            continue
        seen: set[tuple[str, str]] = {job.normalized_key() for job in result}
        for key in seen:
            await db.update_last_seen(key)
        missed = await db.mark_missed_for_source(source.name, seen)
        missed_by_source[source.name] = missed
        if missed:
            logger.info(
                "  %s: marked %s existing job(s) as missed this cycle",
                source.name,
                missed,
            )
    return missed_by_source


def _format_date(date_str: str) -> str:
    """Parse date_found into a short 'Posted: 28 Feb 2026' format."""
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return f"Posted: {dt.strftime('%d %b %Y')}"
        except (ValueError, AttributeError):
            continue
    # Fallback: try to extract a date-like substring
    if date_str and len(date_str) >= 10:
        return f"Posted: {date_str[:10]}"
    return "Posted: N/A"


def _build_sources(
    session: aiohttp.ClientSession,
    source_filter: str | None = None,
    search_config: SearchConfig | None = None,
    user_profile: UserProfile | None = None,
) -> list[BaseJobSource]:
    """Build source instances, optionally filtered to a single source or by
    the user's classified professional domain(s).

    Pillar 2 Batch 2.4 — a user's profile is classified into a set of domains
    (tech / healthcare / academia / education / climate). A source is
    included if any of:
      * `user_profile` is None → graceful fallback, include everything,
      * `source_filter` is set → only the single requested source,
      * the source is tagged `"general"`,
      * the source's `DOMAINS` overlaps the user's domains.
    """
    sc = search_config  # short alias
    all_sources = [
        # Group A: Keyed APIs
        ReedSource(session, api_key=REED_API_KEY, search_config=sc),
        AdzunaSource(session, app_id=ADZUNA_APP_ID, app_key=ADZUNA_APP_KEY, search_config=sc),
        JSearchSource(session, api_key=JSEARCH_API_KEY, search_config=sc),
        # Group B: Free APIs
        ArbeitnowSource(session, search_config=sc),
        RemoteOKSource(session, search_config=sc),
        JobicySource(session, search_config=sc),
        HimalayasSource(session, search_config=sc),
        # Group C: ATS boards
        GreenhouseSource(session, search_config=sc),
        LeverSource(session, search_config=sc),
        WorkableSource(session, search_config=sc),
        AshbySource(session, search_config=sc),
        # Group E: New free APIs
        RemotiveSource(session, search_config=sc),
        JoobleSource(session, api_key=JOOBLE_API_KEY, search_config=sc),
        LinkedInSource(session, search_config=sc),
        # Group F: New ATS boards
        SmartRecruitersSource(session, search_config=sc),
        PinpointSource(session, search_config=sc),
        RecruiteeSource(session, search_config=sc),
        # Group G: Scraper-based
        JobSpySource(session, search_config=sc),
        # Group H: Workday ATS
        WorkdaySource(session, search_config=sc),
        # Group I: Real-time data sources
        GoogleJobsSource(session, api_key=SERPAPI_KEY, search_config=sc),
        DevITJobsSource(session, search_config=sc),
        LandingJobsSource(session, search_config=sc),
        # Group J: New free/keyed sources
        TheMuseSource(session, search_config=sc),
        HackerNewsSource(session, search_config=sc),
        CareerjetSource(session, affid=CAREERJET_AFFID, search_config=sc),
        FindworkSource(session, api_key=FINDWORK_API_KEY, search_config=sc),
        GovApprenticeshipsSource(session, api_key=DFE_APPRENTICESHIPS_API_KEY, search_config=sc),
        NoFluffJobsSource(session, search_config=sc),
        # Group K: Phase 4 new free sources
        HNJobsSource(session, search_config=sc),
        NHSJobsSource(session, search_config=sc),
        PersonioSource(session, search_config=sc),
        WeWorkRemotelySource(session, search_config=sc),
        RealWorkFromAnywhereSource(session, search_config=sc),
        ClimatebaseSource(session, search_config=sc),
        EightyKHoursSource(session, search_config=sc),
        BCSJobsSource(session, search_config=sc),
        UniJobsSource(session, search_config=sc),
        SuccessFactorsSource(session, search_config=sc),
        AIJobsAISource(session, search_config=sc),
        # Group L: Batch 3 additions
        TeachingVacanciesSource(session, search_config=sc),
    ]
    if source_filter:
        # Special case: glassdoor shares JobSpySource with indeed
        if source_filter == "glassdoor":
            source_filter = "indeed"
        return [s for s in all_sources if s.name == source_filter]

    # Batch 2.4 — domain-aware filtering. A None profile returns empty domains
    # which source_matches_user_domains interprets as "include everything".
    user_domains = classify_user_domain(user_profile)
    if not user_domains:
        return all_sources
    return [s for s in all_sources if source_matches_user_domains(s.DOMAINS, user_domains)]


def _recency_bucket(date_found: str | None) -> str:
    """Coarse recency bucket for a user_feed row (mirrors the dashboard's buckets)."""
    from src.utils.time_buckets import parse_date_safe

    dt = parse_date_safe(date_found or "")
    if dt is None:
        return "older"
    hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    for label, limit in (("24h", 24), ("48h", 48), ("3d", 72), ("5d", 120), ("7d", 168)):
        if hours <= limit:
            return label
    return "older"


async def _run_matcher_stage(db: JobDatabase, *, user_id: str, jobs: list[Job]) -> None:
    """Funnel -> judge: LLM-match the top shortlisted jobs for THIS user.

    Gated on MATCHER_ENABLED (default off — rule #18 analog: OFF must be a
    byte-identical no-op). Any failure is logged and swallowed: the judge
    upgrades scores, it never blocks a run. Lazy imports keep the default
    path import-free.
    """
    try:
        from src.core.settings import ENGINE4_ENABLED  # noqa: PLC0415
        from src.services.llm_matcher import (  # noqa: PLC0415 — lazy by design
            MATCHER_ENABLED,
            MATCHER_MAX_JOBS,
            MATCHER_THRESHOLD,
            MATCHER_WINDOW,
            match_batch,
            profile_to_matcher_text,
        )

        # Engine 4 switch (ENGINE4_ENABLED) OR the legacy MATCHER_ENABLED flag.
        if not (ENGINE4_ENABLED or MATCHER_ENABLED):
            return
        from src.services.profile.storage import load_profile  # noqa: PLC0415

        profile = load_profile(user_id)
        if profile is None:
            logger.info("matcher: no profile for user %s — skipping", user_id)
            return

        # THE VERIFIED WINDOW (2026-08-06, ground-truth audit finding). The old
        # shortlist came only from `jobs` — the postings THIS run fetched — so
        # the backfilled catalog rows that actually fill the dashboard were
        # never judged: 28 of the measured top-100 were unjudged junk sitting
        # above judged rows. The judge now targets what the user WILL SEE — the
        # top MATCHER_WINDOW feed rows by display rank — judging any without a
        # verdict. Verdicts cache per (user, job), so only new window entrants
        # cost anything after the first pass. MATCHER_WINDOW=0 falls back to
        # the legacy fetched-only shortlist.
        assert db._conn is not None  # set by init_db() before this stage runs
        shortlist: list[Job] = []
        if MATCHER_WINDOW > 0:
            cur = await db._conn.execute(
                """
                SELECT j.id, j.title, j.company, j.location, j.description,
                       j.apply_url, j.source, j.date_found, j.salary_min,
                       j.salary_max, f.score
                FROM (
                    SELECT job_id, score, llm_fit_score FROM user_feed
                    WHERE user_id = ? AND status = 'active' AND score >= ?
                    ORDER BY COALESCE(llm_fit_score, score) DESC
                    LIMIT ?
                ) f
                JOIN jobs j ON j.id = f.job_id
                WHERE f.llm_fit_score IS NULL
                """,
                (user_id, MATCHER_THRESHOLD, MATCHER_WINDOW),
            )
            def _row_to_job(r: Any) -> Job:
                j = Job(
                    title=r[1] or "", company=r[2] or "", apply_url=r[5] or "",
                    source=r[6] or "", date_found=r[7] or "",
                    location=r[3] or "", description=r[4] or "",
                    salary_min=r[8], salary_max=r[9],
                )
                j.id = r[0]
                j.match_score = int(r[10] or 0)
                return j

            for r in await cur.fetchall():
                shortlist.append(_row_to_job(r))

            # THE DEEP BENCH — judge a slice BELOW the window each run. The
            # iteration-2 audit still found 6 good jobs (fit 70-85) stranded
            # at ranks 100-800: not thin-text (the rescue lane's case) and not
            # visible (the window's case), just ordinary mid-pack keyword
            # scores. Walking a fixed-size slice deeper on every search
            # verifies the whole shelf over time, and the `llm_fit_score IS
            # NULL` filter means a judged job is never paid for twice.
            from src.services.llm_matcher import MATCHER_DEEP_BENCH  # noqa: PLC0415

            if MATCHER_DEEP_BENCH > 0:
                cur = await db._conn.execute(
                    """
                    SELECT j.id, j.title, j.company, j.location, j.description,
                           j.apply_url, j.source, j.date_found, j.salary_min,
                           j.salary_max, f.score
                    FROM (
                        SELECT job_id, score, llm_fit_score FROM user_feed
                        WHERE user_id = ? AND status = 'active'
                          AND score >= ? AND llm_fit_score IS NULL
                        ORDER BY score DESC
                        OFFSET ? LIMIT ?
                    ) f
                    JOIN jobs j ON j.id = f.job_id
                    """,
                    (user_id, MATCHER_THRESHOLD, MATCHER_WINDOW, MATCHER_DEEP_BENCH),
                )
                deep = [_row_to_job(r) for r in await cur.fetchall()]
                if deep:
                    logger.info(
                        "matcher: deep bench adds %s jobs below the window for user %s",
                        len(deep), user_id,
                    )
                    shortlist.extend(deep)
        else:
            shortlist = sorted(
                (
                    j
                    for j in jobs
                    if getattr(j, "id", None) is not None
                    and j.match_score is not None
                    and j.match_score >= MATCHER_THRESHOLD
                ),
                key=lambda j: j.match_score,
                reverse=True,
            )[:MATCHER_MAX_JOBS]
        if not shortlist:
            return
        t0 = time.perf_counter()
        logger.info(
            "matcher: judging %s shortlisted jobs for user %s",
            len(shortlist),
            user_id,
        )
        assert db._conn is not None  # set by init_db() before this stage runs (see run_search)
        results = await match_batch(
            shortlist,
            user_id=user_id,
            profile_text=profile_to_matcher_text(profile),
            conn=db._conn,
            semaphore_limit=3,
        )
        verdicts = [r for r in results if r is not None]
        judged = len(verdicts)
        fits = [v.fit_score for v in verdicts]
        logger.info(
            "matcher: judged %s/%s jobs in %.1fs for user %s (fit min/avg/max = %s/%s/%s)",
            judged,
            len(shortlist),
            time.perf_counter() - t0,
            user_id,
            min(fits) if fits else 0,
            round(sum(fits) / len(fits), 1) if fits else 0,
            max(fits) if fits else 0,
        )
    except Exception as e:  # noqa: BLE001 — judge failure must never kill the run
        logger.warning("matcher stage failed (run continues): %s", e)


async def _enqueue_notifications(
    conn: pg.Connection,
    user_id: str,
    written: list[tuple[int, int]],
    enqueue: Callable[..., Any],
) -> int:
    """SI1 — enqueue ``send_notification`` for feed rows above the user's
    score_threshold, so a per-user search actually produces notifications.

    ``written`` is a list of ``(job_id, score)``. Pre-filters on the enabled
    ``notification_rules`` row's ``score_threshold`` (dispatch() still applies
    the definitive score/quiet-hours/mode gate) so we don't flood the queue with
    rows the dispatcher would skip. Accepts sync OR async enqueue hooks. Never
    raises into the pipeline — a notification failure must not fail the run.
    Returns the number enqueued.
    """
    try:
        cur = await conn.execute(
            "SELECT score_threshold FROM notification_rules WHERE user_id = ? AND enabled = 1",
            (user_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return 0  # no enabled rule → nothing to notify
        threshold = int(row[0]) if row[0] is not None else 0
        enqueued = 0
        for job_id, score in written:
            if score >= threshold:
                res = enqueue("send_notification", user_id, job_id, "instant")
                if hasattr(res, "__await__"):
                    await res
                enqueued += 1
        logger.info(
            "SI1: enqueued %s notification(s) for user %s (threshold %s)",
            enqueued, user_id, threshold,
        )
        return enqueued
    except Exception as e:  # notifications must never break the run
        logger.warning("notification enqueue failed for user %s: %s", user_id, e)
        return 0


def _encode_and_upsert(
    vix: Any,
    encode_job: Any,
    job: Job,
    enrichment: Any,
    job_id: int,
) -> None:
    """Embed one job and push it into the vector index — the SYNCHRONOUS pair.

    Split out purely so ``run_search`` can hand it to ``asyncio.to_thread``.
    ``encode_job`` runs the sentence-transformer (~7.5 s per batch in prod) and
    ``vix.upsert`` writes to Chroma; both block. Keeping them together in one
    thread hop avoids paying the hand-off twice per job.

    Typed ``Any`` deliberately: the heavy modules are lazy-imported inside
    ``run_search`` (rules #11/#16), so their real types are not importable here.
    """
    vec = encode_job(job, enrichment)
    vix.upsert(
        job_id=job_id,
        vector=vec,
        metadata={"title": job.title, "company": job.company},
    )


async def _embed_backfill_budget(db: Any, conn: Any, budget: int) -> int:
    """Embed up to ``budget`` EXISTING catalog jobs that have no vector yet.

    Convergence backfill (2026-08-05): jobs inserted before semantic was on
    would otherwise stay vectorless until purged. Same encode path as new-job
    embedding (enrichment-aware, worker-thread hop). Per-job failures are
    logged and skipped — one bad row never stops the sweep. Returns embedded
    count. Callers gate on SEMANTIC_ENABLED.
    """
    # PREFLIGHT — say it ONCE, loudly, before the loop.
    #
    # The caller has already checked SEMANTIC_ENABLED, so reaching here means
    # semantic is switched ON. If the stack is not installed in this process,
    # every single job below would raise, be caught by the per-job handler, and
    # log an identical "job <id> failed" warning that names neither the cause
    # nor the fix. That is exactly what the worker has been doing: its image is
    # built from Dockerfile.worker with a plain `pip install .`, so the daily
    # refresh_catalog cron ingests jobs it can never embed. Coverage sat at 687
    # of 9,977 with no error anywhere — the failure was real and invisible.
    from src.services.embeddings import (  # noqa: PLC0415 — lazy (rule #16)
        MODEL_NAME,
        encode_job,
        semantic_stack_installed,  # noqa: PLC0415
    )
    from src.services.job_enrichment import load_enrichment  # noqa: PLC0415
    from src.services.pg_vector_index import (  # noqa: PLC0415
        PgVectorIndex,
        vector_column_available,
    )


    try:
        vix = PgVectorIndex()
    except Exception as e:  # noqa: BLE001
        logger.warning("embed backfill: VectorIndex init failed: %s", e)
        return 0

    sql = """
        SELECT j.id, j.title, j.company, j.location, j.description,
               j.apply_url, j.source, j.date_found
        FROM jobs j LEFT JOIN job_embeddings e ON e.job_id = j.id
        WHERE e.job_id IS NULL OR e.embedding IS NULL
        ORDER BY j.id
        LIMIT ?
        """
    # Migration 0027 only adds `embedding` where pgvector exists, and naming a
    # missing column is a hard error inside run_search — so ask first. With the
    # column present, a legacy audit row WITHOUT a vector (the Chroma-era rows)
    # must count as "needs embedding": that desync is what this change removes.
    if not vector_column_available(str(getattr(db, "_path", "")) or None):
        sql = """
        SELECT j.id, j.title, j.company, j.location, j.description,
               j.apply_url, j.source, j.date_found
        FROM jobs j LEFT JOIN job_embeddings e ON e.job_id = j.id
        WHERE e.job_id IS NULL
        ORDER BY j.id
        LIMIT ?
        """
    cur = await conn.execute(
        sql,
        (budget,),
    )
    rows = await cur.fetchall()
    embedded = 0
    for r in rows:
        try:
            job = Job(
                title=r[1] or "", company=r[2] or "", apply_url=r[5] or "",
                source=r[6] or "", date_found=r[7] or "",
                location=r[3] or "", description=r[4] or "",
            )
            job.id = r[0]
            try:
                enrichment = await load_enrichment(conn, r[0])
            except Exception:  # noqa: BLE001
                enrichment = None
            await asyncio.to_thread(
                _encode_and_upsert, vix, encode_job, job, enrichment, r[0]
            )
            await conn.execute(
                "INSERT INTO job_embeddings(job_id, model_version) VALUES (?, ?) "
                "ON CONFLICT(job_id) DO UPDATE SET model_version = EXCLUDED.model_version",
                (r[0], MODEL_NAME),
            )
            embedded += 1
        except Exception as e:  # noqa: BLE001
            # ONE missing package must not read as N unrelated job failures.
            #
            # This is what the worker has actually been doing. Its image is
            # built from Dockerfile.worker with a plain `pip install .` (no
            # `[semantic]` extra) while the API image installs torch +
            # `.[semantic]` — confirmed in the live Railway build log. Because
            # sentence_transformers is imported lazily (rule #16), the module
            # import SUCCEEDS and nothing complains at startup; the failure only
            # surfaced here, per job, as an identical "job <id> failed" warning
            # naming neither the cause nor the remedy. Semantic coverage sat at
            # 687 of 9,977 with every instrument green.
            #
            # Detected HERE rather than as an upfront probe on purpose: callers
            # may inject their own encoder (the backfill tests patch
            # `encode_job`), and a probe for the real package would wrongly stop
            # a run that never needed it. Failing first, then escalating, can
            # only fire when embedding genuinely could not happen.
            # BOTH halves are required. Testing only "is the stack absent?"
            # escalates on a failure that has nothing to do with it — CI caught
            # exactly that: a test poisons ONE row with RuntimeError("encoder
            # blew up") and expects the sweep to skip it and carry on, but CI
            # also has no sentence-transformers installed, so the sweep
            # abandoned instead. The message check is what makes this specific:
            # it is the marker `embeddings._load_encoder` raises when the import
            # fails, so an unrelated encoder error can never be mistaken for a
            # missing package.
            if "sentence-transformers is not installed" in str(
                e
            ) and not semantic_stack_installed():
                logger.error(
                    "SEMANTIC_ENABLED is on but sentence-transformers is not "
                    "installed in this process — abandoning the embed backfill "
                    "after 0 successes. Semantic search cannot improve while "
                    "this is true. Install the extra for THIS service "
                    "(pip install '.[semantic]'), or turn SEMANTIC_ENABLED off "
                    "so the gap is intentional rather than silent. Underlying "
                    "error: %s",
                    e,
                )
                break
            logger.warning("embed backfill: job %s failed: %s", r[0], e)
    await db.commit()
    if embedded:
        logger.info("embed backfill: %s existing jobs embedded this run", embedded)
    return embedded


@cpu_bound
def _score_dedup_and_filter(all_jobs: list[Job], scorer: JobScorer) -> list[Job]:
    """Score every job, extract deadlines, dedup, and score-filter — the whole
    SYNCHRONOUS, CPU-heavy stage of the pipeline in one place.

    Invoked from ``run_search`` via ``asyncio.to_thread`` so it runs OFF the
    event loop. Inline (the old code) this same block blocked the single backend
    process for ~a minute on a few thousand raw jobs — per-job regex scoring
    plus an O(n^2) TF-IDF/fuzzy dedup — during which the ``/search/{id}/status``
    polls could not be answered, so the UI reported "Lost contact with the
    server while searching" even though nothing had crashed. Threading it keeps
    the loop free to service those polls. **No scoring behaviour changes** — the
    exact same functions run, just not on the loop.

    Mutates each Job in ``all_jobs`` in place (scores/deadline) and returns the
    deduplicated, score-filtered subset. Contains no ``await`` — safe to thread.
    """
    for job in all_jobs:
        breakdown = scorer.score(job)
        job.match_score = breakdown.match_score
        job.role = breakdown.title_score
        job.skill = breakdown.skill_score
        job.location_score = breakdown.location_score
        job.recency = breakdown.recency_score
        job.seniority_score = breakdown.seniority_score
        job.visa_flag = scorer.check_visa_flag(job)
        job.experience_level = detect_experience_level(job.title)

    # Deadline extraction — fill any job lacking a structured deadline from its
    # description text. Lazy-imported to keep the top-level import surface small.
    from src.services.deadline import extract_deadline  # noqa: PLC0415

    for job in all_jobs:
        if job.deadline is None and job.description:
            result = extract_deadline(job.description)
            if result is not None:
                job.deadline, job.deadline_source = result

    unique_jobs = deduplicate(all_jobs)
    logger.info("After dedup: %s unique jobs", len(unique_jobs))
    # Storage floor, NOT the display floor. This used to drop everything under
    # MIN_MATCH_SCORE (30) *before* the job was ever persisted, which meant:
    #   - a thin extracted profile scored most jobs low -> they were destroyed,
    #     so the user was permanently stuck with a handful of results;
    #   - recency decays up to 10 pts as a posting ages, so a job that scored 35
    #     one week scored 25 the next and silently vanished.
    # Both are unrecoverable data loss for a *ranked feed* product, where ranking
    # already handles precision. Store broadly above a spam cut; the dashboard
    # and CLI still default to min_score=MIN_MATCH_SCORE at READ time.
    kept = [j for j in unique_jobs if (j.match_score or 0) >= MIN_STORE_SCORE]
    logger.info(
        "After store floor (>=%s): %s jobs kept, %s dropped as spam "
        "(display floor is %s, applied at read time)",
        MIN_STORE_SCORE,
        len(kept),
        len(unique_jobs) - len(kept),
        MIN_MATCH_SCORE,
    )
    return kept


async def run_search(
    db_path: str | None = None,
    source_filter: str | None = None,
    dry_run: bool = False,
    log_level: str | None = None,
    no_notify: bool = False,
    user_id: str | None = None,
    enqueue: Callable[..., Any] | None = None,
    search_config: SearchConfig | None = None,
) -> dict[str, Any]:
    # SI1 — ``enqueue`` is the notification fan-out hook. The per-user feed-write
    # path historically wrote user_feed but NEVER triggered a notification, so no
    # user ever got a job-match notification (the whole score_threshold
    # gate was dead). When a caller (the ARQ worker / an API path with Redis)
    # passes an ``enqueue(function_name, *args)`` callable, run_search enqueues
    # ``send_notification`` for above-threshold feed rows after writing them.
    # Defaults to None = no-op, so the CLI / any caller without a worker+Redis is
    # byte-identical to before. dispatch() still applies the fine-grained
    # score/quiet-hours/mode gating; run_search only pre-filters on the rule's
    # score_threshold to avoid flooding the queue with rows dispatch() would skip.
    setup_logging(log_level)
    # Step-1 S1 — set the per-run correlation id BEFORE the first log line so
    # every subsequent record carries it via the contextvar formatter.
    run_uuid = str(uuid.uuid4())
    set_run_uuid(run_uuid)
    run_started_at = time.perf_counter()
    # Backlog #9 — zero the process-wide engine telemetry so the counters we
    # persist to run_log reflect THIS run, not a cumulative total. (Note: the
    # telemetry singletons are process-global, so tightly-overlapping concurrent
    # runs can still mix — acceptable for now; a per-run context is future work.)
    from src.utils.telemetry import reset_for_testing as _reset_run_telemetry  # noqa: PLC0415

    _reset_run_telemetry()
    logger.info("=" * 60)
    logger.info("Job360 - Starting job search run")
    if source_filter:
        logger.info("  Source filter: %s", source_filter)
    if dry_run:
        logger.info("  Mode: DRY RUN (no DB writes, no notifications)")

    # Load user profile for dynamic keywords.
    # When the HTTP API passes a logged-in `user_id`, score against THAT user's
    # profile; otherwise fall back to the single-tenant CLI path
    # (DEFAULT_TENANT_ID). See docs/product/plans/batch-3.5.2-plan.md Deliverable E.
    # Without this, the web "New Search" ran profile-less (E2E_TEST_REPORT #1).
    #
    # A caller may instead hand us a ready-made `search_config` — the shared
    # catalog refresh does: it fetches for EVERYONE (union of all users'
    # configs), so no single profile is "the" profile, and the worker container
    # has no legacy user_profile.json to fall back to. Found live 2026-07-30:
    # the 04:00 cron loaded DEFAULT_TENANT_ID, got nothing, and aborted with
    # sources_queried=0 — the second of two independent reasons the catalog
    # cron had never once fetched a job.
    profile = None if search_config is not None else load_profile(user_id or DEFAULT_TENANT_ID)
    if search_config is None and (not profile or not profile.is_complete):
        logger.error("=" * 60)
        logger.error("No user profile found. Job360 requires a CV or preferences.")
        logger.error("")
        logger.error("Get started with one of:")
        logger.error("  python -m src.cli setup-profile --cv path/to/cv.pdf")
        logger.error("  Or use the frontend at http://localhost:3000/profile")
        logger.error("")
        logger.error("Without a profile, no hardcoded AI/ML defaults are used —")
        logger.error("scoring would return zero matches for every job.")
        logger.error("=" * 60)
        return {
            "total_found": 0,
            "new_jobs": 0,
            "sources_queried": 0,
            "per_source": {},
            "error": "no_profile",
        }

    if search_config is None:
        search_config = generate_search_config(profile)
        logger.info("  Using dynamic keywords from user profile")
    else:
        logger.info(
            "  Using caller-provided search config (%s titles, %s keywords)",
            len(search_config.job_titles),
            len(search_config.relevance_keywords),
        )
    logger.info("=" * 60)

    # Init database
    path = db_path or str(DB_PATH)
    db = JobDatabase(path)
    await db.init_db()

    try:
        # Narrow db._conn from `Connection | None` to `Connection` once, up front:
        # init_db() above always opens a connection (never leaves it None) and
        # nothing in this function resets it before db.close() in the `finally`
        # below, so this assert can never actually fail — it exists purely so
        # mypy can track the non-Optional type through the rest of this function.
        assert db._conn is not None, "init_db() always opens a connection"
        conn = db._conn

        # Auto-purge old jobs (>30 days)
        purged = await db.purge_old_jobs(days=30)
        if purged:
            logger.info("Purged %s jobs older than 30 days", purged)

        # Pillar 2 Batch 2.9 — wire user_preferences + enrichment_lookup so the
        # multi-dim path (seniority/salary/visa/workplace) activates when both
        # a profile and at least one job_enrichment row exist. When
        # ENRICHMENT_ENABLED is false we pass an empty dict so the lookup
        # callable always returns None and the multi-dim contribution is 0
        # (CLAUDE.md rule #19 — legacy callers see legacy behaviour).
        # Engine 2 switch (ENGINE2_ENABLED) OR the legacy ENRICHMENT_ENABLED flag.
        from src.core.settings import ENGINE2_ENABLED  # noqa: PLC0415

        if ENGINE2_ENABLED or ENRICHMENT_ENABLED:
            enrichment_lookup_dict = await _build_enrichment_lookup(conn)
        else:
            enrichment_lookup_dict = {}
        scorer = JobScorer(
            search_config,
            user_preferences=profile.preferences if profile else None,
            # job.id is set dynamically at runtime (src/main.py ~line 745) but is NOT a
            # declared field on the Job dataclass (src/models.py) — a known Pillar-2
            # scoring issue (see project memory: dim-scoring id bug). getattr(...) makes
            # the runtime safe; the ignore below is only for the resulting dict.get() key
            # type. Not fixed here: models.py is out of scope and Pillar 2 is hands-off.
            enrichment_lookup=lambda job: enrichment_lookup_dict.get(getattr(job, "id", None)),  # type: ignore[arg-type]
        )

        # Create session
        connector = aiohttp.TCPConnector(limit=30, limit_per_host=5)
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            # Build sources
            sources = _build_sources(
                session,
                source_filter,
                search_config=search_config,
                user_profile=profile,
            )

            if not sources:
                logger.error("No sources matched filter: %s", source_filter)
                return {"total_found": 0, "new_jobs": 0, "sources_queried": 0, "per_source": {}}

            # Fetch via TieredScheduler (Batch 3.5 Deliverable E):
            #   * One-shot CLI runs pass force=True so every source dispatches
            #     exactly once — the tier intervals only matter for the
            #     long-lived daemon path (not in scope for this batch).
            #   * The scheduler consults the breaker registry BEFORE
            #     dispatch; OPEN-state sources are skipped without
            #     invoking fetch_jobs. Success / failure is auto-recorded
            #     into each breaker by the scheduler, replacing the
            #     post-hoc loop we had in Batch 3.
            #   * Ghost-detection still needs an (all-sources-aligned)
            #     results list — we reconstruct it from the scheduler's
            #     return value, filling None for skipped sources (which
            #     _ghost_detection_pass already treats as skip-sweep).
            all_jobs: list[Job] = []
            per_source: dict[str, int] = {}
            source_count = 0
            failed_sources: list[str] = []

            registry = default_breaker_registry()
            pre_states = {s.name: registry.get(s.name).state for s in sources}

            # Step-1 S2 — wrap each source's fetch_jobs in a per-source timer.
            # `source_timer` populates `t.duration_ms` after the call returns;
            # we record the wall-clock duration AND any raised exception into
            # two dicts that we persist to run_log at end-of-run.
            per_source_duration: dict[str, float] = {}
            per_source_errors: dict[str, int] = {}

            def _instrument(src: BaseJobSource) -> None:
                original_fetch = src.fetch_jobs

                async def _timed_fetch() -> list[Job]:
                    started_ns = time.perf_counter_ns()
                    try:
                        with source_timer(src.name):
                            return await original_fetch()
                    except BaseException:
                        per_source_errors[src.name] = per_source_errors.get(src.name, 0) + 1
                        raise
                    finally:
                        # Store SECONDS, not milliseconds.
                        #
                        # This column was written in ms while `total_duration`
                        # (below) and the field's own un-suffixed name are
                        # SECONDS, so every consumer read ms as seconds. Found by
                        # the deep journey walk 2026-08-05: the source-health
                        # endpoint reported `workday=212391s` — 59 HOURS — for a
                        # search that finishes in ~5 minutes. 212391 ms is 212 s,
                        # which is the real number. Verified corrupt in prod too,
                        # so any monitoring keyed on per-source latency was acting
                        # on garbage. Now consistent with total_duration.
                        elapsed_s = max(0.0, (time.perf_counter_ns() - started_ns) / 1_000_000_000)
                        per_source_duration[src.name] = round(elapsed_s, 2)

                src.fetch_jobs = _timed_fetch  # type: ignore[method-assign]

            for s in sources:
                _instrument(s)

            scheduler = TieredScheduler(sources, registry)
            paired = await scheduler.tick(force=True)

            results_by_name: dict[str, list[Job] | BaseException | None] = {
                name: None for name in (s.name for s in sources)
            }
            for src, result in paired:
                results_by_name[src.name] = result

            for source in sources:
                source_count += 1
                result = results_by_name.get(source.name)
                if isinstance(result, BaseException):
                    per_source[source.name] = 0
                    failed_sources.append(source.name)
                    logger.warning("  %s: FAILED (%s)", source.name, type(result).__name__)
                elif result is None:
                    # Skipped by breaker (OPEN) or never dispatched
                    per_source[source.name] = 0
                    breaker_state = registry.get(source.name).state
                    if breaker_state == BreakerState.OPEN:
                        logger.info("  %s: skipped (breaker OPEN)", source.name)
                    else:
                        failed_sources.append(source.name)
                        logger.warning("  %s: FAILED", source.name)
                elif result:
                    per_source[source.name] = len(result)
                    all_jobs.extend(result)
                    logger.info("  %s: %s jobs", source.name, len(result))
                else:
                    per_source[source.name] = 0
                    logger.info("  %s: 0 jobs", source.name)

            if failed_sources:
                logger.warning("Failed sources (%s): %s", len(failed_sources), ", ".join(failed_sources))

            # Surface newly-opened breakers (post-scheduler state diff)
            newly_opened = [
                s.name
                for s in sources
                if pre_states.get(s.name) != BreakerState.OPEN and registry.get(s.name).state == BreakerState.OPEN
            ]
            if newly_opened:
                logger.warning(
                    "Circuit breaker OPEN for source(s) after consecutive failures: %s",
                    ", ".join(newly_opened),
                )

            # Ghost-detection input expects a results list aligned with sources
            results = [results_by_name.get(s.name) for s in sources]

            try:
                history = await db.get_last_source_counts(7)
            except Exception as e:
                logger.warning("Source history fetch skipped: %s", e)
                history = {}

            # Ghost detection: update last_seen for observed jobs; mark absent jobs as missed.
            # Per pillar_3_batch_1.md §3 Step 1, skip the absence sweep for any source whose
            # current result count is below 70% of its 7-day rolling average — a rate-limited
            # or blocked scrape must NEVER be interpreted as jobs disappearing.
            try:
                await _ghost_detection_pass(db, sources, results, history)
            except Exception as e:
                logger.warning("Ghost-detection pass skipped: %s", e)

            logger.info("Total raw jobs: %s", len(all_jobs))

            # H1: attach each job's catalog id BEFORE scoring, so the multi-dim
            # enrichment lookup (keyed on job.id, rules #19/#20) actually finds a
            # RETURNING job's enrichment from a prior run. Previously score() ran
            # here with job.id still None, so seniority/salary/visa/workplace dims
            # silently scored 0; and if that dims-blind score missed
            # ENRICHMENT_THRESHOLD, the job never reached the post-enrich re-score
            # pass — so its persisted match_score stayed wrong forever. One query
            # loads the whole (bounded, <=30-day) catalog's normalized-key → id map;
            # brand-new jobs aren't in it and correctly stay id=None (they have no
            # enrichment yet — scoring them dims-blind is right).
            _catalog_ids: dict[tuple[str, str], int] = {}
            _cur = await conn.execute(
                "SELECT normalized_company, normalized_title, id FROM jobs"
            )
            for _row in await _cur.fetchall():
                _catalog_ids[(_row[0], _row[1])] = _row[2]
            for job in all_jobs:
                job.id = _catalog_ids.get(job.normalized_key())

            # Score → deadline-extract → dedup → score-filter. This whole stage is
            # synchronous and CPU-heavy (per-job regex scoring across skill-synonym
            # aliases + an O(n^2) TF-IDF/fuzzy dedup over every raw job). Run it in a
            # WORKER THREAD via asyncio.to_thread so it never blocks the event loop —
            # inline it froze this single backend process for ~a minute on a few
            # thousand raw jobs, starving the /search/{id}/status polls, which the UI
            # surfaced as "Lost contact with the server while searching" (the search
            # had not crashed — the loop was just busy). Scores are unchanged.
            unique_jobs = await asyncio.to_thread(_score_dedup_and_filter, all_jobs, scorer)

            if dry_run:
                # Dry run: show results without DB writes or notifications
                unique_jobs.sort(key=lambda j: (j.match_score, salary_in_range(j)), reverse=True)
                stats = {
                    "total_found": len(all_jobs),
                    "new_jobs": len(unique_jobs),
                    "sources_queried": source_count,
                    "per_source": per_source,
                }
                _print_bucketed_summary(unique_jobs, "DRY RUN")
                logger.info("Job360 dry run complete")
                return stats

            # Insert new jobs (INSERT OR IGNORE returns rowcount=1 for actual inserts)
            new_jobs: list[Job] = []
            # ONE POISON ROW MUST NOT KILL THE WHOLE SEARCH.
            #
            # 2026-07-27: both of a real user's searches aborted here. A
            # HackerNews "Who's hiring" comment had been stored as title AND
            # company, so (normalized_company, normalized_title) exceeded
            # Postgres's 2,704-byte btree index limit and insert_job raised
            # ProgramLimitExceeded. With no guard, that single row took down the
            # entire run: his feed then received ZERO new rows for seven days
            # while the catalog grew by ~2,800 jobs. He pressed Search twice,
            # got a failure twice, and had no idea why.
            #
            # The rescore and backfill paths were given per-row guards after an
            # earlier incident; this loop never was. Skipping one unstorable job
            # costs that job; aborting costs every job AND the user's feed.
            skipped = 0
            # THE UK GATE — one door, checked once, for every source.
            #
            # Job360 is a UK-market product: a job in Warsaw or Indianapolis
            # reaching a user's feed is a product fault, not a ranking
            # preference. The only prior defence was the scorer's -15
            # foreign-location penalty, which admits the job and then argues
            # about its rank; measured 2026-08-07, 156 clearly foreign jobs
            # were live in prod. Each of the 46 sources applied its own
            # `_is_uk_or_remote` check (or none), so the leak was structural.
            # This is the single chokepoint every job passes through.
            gate_blocked: dict[str, int] = {}
            for job in unique_jobs:
                verdict = check_uk(
                    job.location, job.source,
                    description=job.description or "", title=job.title or "",
                )
                if not verdict.allowed:
                    gate_blocked[verdict.reason] = gate_blocked.get(verdict.reason, 0) + 1
                    continue
                try:
                    if await db.insert_job(job):
                        new_jobs.append(job)
                except Exception as exc:  # noqa: BLE001 — never sink the whole run
                    skipped += 1
                    logger.error(
                        "insert failed for %r @ %r (%s: %s) — skipping this job "
                        "and continuing the run",
                        (job.title or "")[:80], (job.company or "")[:60],
                        type(exc).__name__, str(exc)[:200],
                    )
            if gate_blocked:
                # Log the reasons, never a bare total: tightening or loosening
                # the gate must be an evidence-based change, not a guess.
                logger.info(
                    "UK gate blocked %d job(s) before storage: %s",
                    sum(gate_blocked.values()),
                    ", ".join(f"{k}={v}" for k, v in sorted(gate_blocked.items())),
                )
            if skipped:
                logger.warning("%d job(s) could not be stored this run", skipped)
            await db.commit()

            new_jobs.sort(key=lambda j: (j.match_score, salary_in_range(j)), reverse=True)
            logger.info("New jobs: %s", len(new_jobs))

            # Re-resolve the DB id for the jobs we just inserted this run (brand-new
            # jobs had no catalog id during the pre-scoring H1 pass above). Returning
            # jobs already carry their id from that earlier pass; re-setting it here
            # is a harmless no-op. enrich_batch persists keyed on ``job.id``, so this
            # MUST cover the just-inserted rows before enrichment runs.
            for job in unique_jobs:
                cur = await conn.execute(
                    "SELECT id FROM jobs WHERE normalized_company = ? AND normalized_title = ?",
                    job.normalized_key(),
                )
                r = await cur.fetchone()
                job.id = r[0] if r is not None else None

            # Step-1 B7 — gate LLM enrichment by score. No-op when the flag is
            # OFF (CLAUDE.md rule #18). B7-2 fix: this runs AFTER insert so the
            # jobs carry their DB id; enrich_batch persists to job_enrichment,
            # which the scorer's enrichment_lookup applies on subsequent runs.
            # Only high-scored jobs go to the LLM, fanned out via a bounded
            # semaphore so a slow provider can't block the pipeline.
            # Engine 2 switch (ENGINE2_ENABLED) OR the legacy ENRICHMENT_ENABLED flag.
            from src.core.settings import ENGINE2_ENABLED  # noqa: PLC0415

            if ENGINE2_ENABLED or ENRICHMENT_ENABLED:
                # BUDGET, not a threshold. Measured in prod 2026-07-28: the highest
                # match_score in the entire 3,342-row feed was 58, against a
                # threshold of 60 — so this stage had NEVER run, and job_enrichment
                # held 0 rows. It could not have run: the gate sat above the
                # maximum the scorer can currently produce.
                #
                # A threshold is a claim about the score distribution. That
                # distribution moved when the CV-copy merge was removed from
                # merge_cv_and_preferences (scores fell to normal), and the gate
                # was never re-tuned. Worse, the failure is SILENT: zero rows
                # selected logs nothing, raises nothing, and looks exactly like a
                # feature that was never built.
                #
                # "Best N per run" makes no claim about the distribution at all. It
                # works whether the top score is 58 or 95, self-adjusts as scoring
                # changes, and doubles as a hard cost ceiling — the same pattern
                # MATCHER_MAX_JOBS already uses for the LLM judge (engine 4), which
                # is why THAT engine was running (80 verdicts in prod) while this
                # one was dark.
                eligible = [
                    j
                    for j in unique_jobs
                    if getattr(j, "id", None) is not None
                    and j.match_score is not None
                    and j.match_score >= ENRICHMENT_MIN_SCORE
                ]
                eligible.sort(key=lambda j: j.match_score or 0, reverse=True)
                high_scored = eligible[:ENRICHMENT_MAX_JOBS]
                if not high_scored and unique_jobs:
                    # Never fail silently again: if the budget selected nothing,
                    # say why. This log is the alarm the old threshold lacked —
                    # a stage that selects 0 rows otherwise looks identical to a
                    # stage that was never built.
                    logger.warning(
                        "Enrichment selected 0 jobs — %s scored, best was %s, floor is %s",
                        len(unique_jobs),
                        max((j.match_score or 0) for j in unique_jobs),
                        ENRICHMENT_MIN_SCORE,
                    )
                if high_scored:
                    logger.info(
                        "Enriching top %s of %s eligible jobs (budget=%s, floor=%s, best score=%s)",
                        len(high_scored),
                        len(eligible),
                        ENRICHMENT_MAX_JOBS,
                        ENRICHMENT_MIN_SCORE,
                        high_scored[0].match_score,
                    )
                    # Concurrency 3 (not 10): free-tier LLMs cap at ~30 requests/min
                    # (Cerebras) and small token/min budgets (Groq). A burst of 10
                    # concurrent × retries 429s every provider at once. 3 keeps the
                    # batch under the per-minute limits while still parallelising.
                    await enrich_batch(high_scored, semaphore_limit=3, conn=conn)
                    await db.commit()

                    # Engine 2 dim-scoring fix: the first score() (above) ran
                    # before these jobs had DB ids or enrichment rows, so the
                    # enrichment dims (seniority/salary/visa/workplace) scored 0.
                    # Now they have both — re-score with a rebuilt lookup so the
                    # dims fold into match_score, and persist so the feed write
                    # below + the catalog reflect the dim-inclusive score.
                    fresh_lookup = await _build_enrichment_lookup(conn)
                    dim_scorer = JobScorer(
                        search_config,
                        user_preferences=profile.preferences if profile else None,
                        # See note above on job.id (dynamic attr, Pillar-2 hands-off).
                        enrichment_lookup=lambda job: fresh_lookup.get(getattr(job, "id", None)),  # type: ignore[arg-type]
                    )
                    for job in high_scored:
                        bd = dim_scorer.score(job)
                        job.match_score = bd.match_score
                        job.role = bd.title_score
                        job.skill = bd.skill_score
                        job.location_score = bd.location_score
                        job.recency = bd.recency_score
                        job.seniority_score = bd.seniority_score
                        await db.update_job_scores(job)
                    await db.commit()

            # Per-user feed: write THIS user's matched jobs into user_feed so the
            # dashboard shows only their jobs. The shared `jobs` table is the
            # universal catalog/cache; user_feed is the isolated per-user view
            # (blueprint §3). Write ALL `unique_jobs` (not just new inserts) — a
            # job already in the catalog from another user's search still belongs
            # in THIS user's feed. Only runs on the per-user HTTP path (user_id set).
            if user_id is not None and unique_jobs:
                from src.services.feed import FeedService  # noqa: PLC0415 — avoid import cycle at module load

                feed = FeedService(conn)
                feed_written = 0
                feed_profile_version = current_profile_version_id(user_id)
                _written_for_notify: list[tuple[int, int]] = []  # (job_id, score)
                for job in unique_jobs:
                    try:
                        # N7 — reuse the id already resolved in the scoring loop
                        # above (job.id set from the same `SELECT id FROM jobs`);
                        # re-querying it here doubled the point-queries per run.
                        # (job.id is a dynamically-set attribute, not a declared
                        # dataclass field — see note earlier in this function.)
                        if job.id is None:
                            continue
                        _score = int(job.match_score or 0)
                        await feed.upsert_feed_row(
                            user_id=user_id,
                            job_id=job.id,
                            score=_score,
                            bucket=_recency_bucket(job.date_found),
                            profile_version=feed_profile_version,
                            scorer_version=SCORER_VERSION,
                        )
                        feed_written += 1
                        _written_for_notify.append((job.id, _score))
                    except Exception as e:  # never let a feed write fail the whole run
                        logger.warning("user_feed write failed for %r: %s", job.title, e)
                logger.info("Wrote %s jobs to user_feed for user %s", feed_written, user_id)

                # SI1 — trigger notifications for the rows just written. Only when
                # a caller supplied an ``enqueue`` hook (worker/Redis present) and
                # notifications aren't suppressed.
                if enqueue is not None and not no_notify and _written_for_notify:
                    await _enqueue_notifications(
                        conn, user_id, _written_for_notify, enqueue
                    )

            # Catalog backfill — make a search a READ over the shared catalog,
            # not just a crawl. The block above only feeds jobs THIS run fetched;
            # the shared `jobs` table is filled by every user's runs, and none of
            # those were ever offered to this user unless their own crawl happened
            # to re-fetch the same posting. That is why two accounts with the same
            # CV diverged (37 jobs vs 4) purely on how often each had searched.
            #
            # Runs even when `unique_jobs` is empty — a run that fetched nothing
            # (dead source, exhausted API quota) is exactly when the user most
            # needs the catalog. Never allowed to fail the run.
            if user_id is not None:
                try:
                    from src.services.rescore import (  # noqa: PLC0415 — import cycle
                        backfill_feed_from_catalog,
                    )

                    await backfill_feed_from_catalog(user_id, db)
                except Exception as e:  # noqa: BLE001 — best-effort enrichment of the feed
                    logger.warning("catalog backfill failed for user %s: %s", user_id, e)

            # Funnel -> judge (LLM matcher). Per-user, post-feed-write so the
            # verdict UPDATE always finds its user_feed row. Default OFF.
            if user_id is not None and unique_jobs:
                await _run_matcher_stage(db, user_id=user_id, jobs=unique_jobs)

            # Step-1 B8 — vector index upsert for newly-inserted jobs.
            # Gated on SEMANTIC_ENABLED (CLAUDE.md rule #18 — opt-in default OFF).
            # Heavy deps (sentence_transformers, chromadb) are imported lazily
            # INSIDE this if-block per CLAUDE.md rule #16 — top-level import would
            # pay sentence-transformers' 2s startup cost on every CLI invocation.
            if SEMANTIC_ENABLED and new_jobs:
                from src.services.embeddings import MODEL_NAME, encode_job  # noqa: PLC0415 — lazy by design (rule #16)
                from src.services.job_enrichment import load_enrichment  # noqa: PLC0415
                from src.services.pg_vector_index import PgVectorIndex  # noqa: PLC0415

                try:
                    vix = PgVectorIndex()
                except Exception as e:
                    logger.warning("VectorIndex init failed; skipping vector upsert: %s", e)
                    vix = None

                if vix is not None:
                    for j in new_jobs:
                        try:
                            # Look up the persisted row id (insert_job returned bool only).
                            row_cursor = await conn.execute(
                                "SELECT id FROM jobs WHERE normalized_company = ? AND normalized_title = ?",
                                j.normalized_key(),
                            )
                            row = await row_cursor.fetchone()
                            if row is None:
                                continue
                            job_id = row[0]
                            try:
                                enrichment = await load_enrichment(conn, job_id)
                            except Exception:
                                enrichment = None
                            # WORKER THREAD — encode_job() runs the sentence
                            # transformer, and vix.upsert() writes to Chroma;
                            # both are synchronous and CPU-bound. Inline, this
                            # loop blocked the event loop once per new job
                            # (prod: ~7.5s per encode batch), so a run that
                            # inserted enough jobs starved the 3-second
                            # /api/search/{id}/status polls for over a minute
                            # and the dashboard reported "Lost contact with the
                            # server while searching". Same fix as the scoring
                            # hand-off at the top of this function.
                            await asyncio.to_thread(
                                _encode_and_upsert, vix, encode_job, j, enrichment, job_id
                            )
                            await conn.execute(
                                "INSERT INTO job_embeddings(job_id, model_version) VALUES (?, ?) "
                                "ON CONFLICT(job_id) DO UPDATE SET model_version = EXCLUDED.model_version",
                                (job_id, MODEL_NAME),
                            )
                        except Exception as e:
                            logger.warning("vector upsert failed for job %r: %s", j.title, e)
                    await db.commit()

            # Convergence backfill (2026-08-05, goal: JOB understanding → 100%).
            # New jobs embed above; EXISTING catalog jobs that predate semantic
            # (prod: 284/7,309 embedded) would otherwise stay vectorless until
            # the 30-day purge cycled them out. Each run therefore also embeds
            # a budget of missing-embedding jobs — same pattern as candidate
            # enrichment: coverage converges through ordinary searches, no
            # manual sweep on the prod box. Gated on SEMANTIC_ENABLED (rule #18).
            if SEMANTIC_ENABLED:
                from src.core.settings import EMBED_BACKFILL_PER_RUN  # noqa: PLC0415
                if EMBED_BACKFILL_PER_RUN > 0:
                    await _embed_backfill_budget(db, conn, EMBED_BACKFILL_PER_RUN)

            # Stats
            stats = {
                "total_found": len(all_jobs),
                "new_jobs": len(new_jobs),
                "sources_queried": source_count,
                "per_source": per_source,
            }

            # Generate outputs — file side-outputs only, so non-fatal by design.
            # The run's REAL products (jobs, user_feed, run_log) live in
            # Postgres above/below this block. In a container whose package dir
            # is read-only (the worker installs into site-packages), these
            # writes raise OSError; letting that kill the run would throw away
            # the entire fetch AND skip the run_log write — which is exactly
            # what starved the catalog for three nights before issue #170.
            if new_jobs:
                try:
                    # CSV
                    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
                    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                    csv_path = str(EXPORTS_DIR / f"jobs_{ts}.csv")
                    await asyncio.to_thread(export_to_csv, new_jobs, csv_path)
                    logger.info("CSV exported: %s", csv_path)

                    # Markdown report
                    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
                    md_report = generate_markdown_report(new_jobs, stats)
                    md_path = REPORTS_DIR / f"report_{ts}.md"
                    await asyncio.to_thread(md_path.write_text, md_report, encoding="utf-8")
                    logger.info("Report saved: %s", md_path)
                except OSError as exc:
                    logger.warning(
                        "CSV/report export skipped (%s) — run continues; DB is the source of truth",
                        exc,
                    )


                # Print time-bucketed summary to console
                _print_bucketed_summary(new_jobs, "Results")
            else:
                logger.info("No new jobs to report")
                logger.info("Job360: No new jobs found this run.")

            # Log run — Step-1 S1+S2: persist run_uuid + per-source timing/errors
            # + total wall-clock duration into the run_log row.
            total_duration = time.perf_counter() - run_started_at
            # Backlog #9 — persist the LLM judge telemetry (judged/skipped/avg/spread)
            # into run_log so it survives the process, not just the in-memory singleton.
            from src.utils.telemetry import matcher_telemetry  # noqa: PLC0415

            await db.log_run(
                stats,
                run_uuid=run_uuid,
                per_source_errors=per_source_errors,
                per_source_duration=per_source_duration,
                total_duration=total_duration,
                user_id=user_id,
                matcher_stats=matcher_telemetry().as_dict(),
            )

            # Zero-result alarm. Source rot is SILENT by construction: a 404
            # makes _get_json return None, a renamed XML tag makes the parse
            # loop never execute — the source returns [], nothing raises, and
            # the circuit breaker never trips. nhs_jobs and successfactors sat
            # at zero for months this way. logger.error() is picked up by
            # Sentry via its logging integration, so this needs no new channel.
            # Only REGRESSIONS are reported (a source that once worked and
            # stopped) — flagging permanently-zero sources every run is how an
            # alert becomes noise and stops being read.
            try:
                dead = await db.get_silently_dead_sources(hours=48)
                if dead:
                    logger.error(
                        "SOURCE ROT: %d source(s) returned ZERO jobs for 48h "
                        "despite working before — %s",
                        len(dead),
                        ", ".join(
                            f"{name} (peak {peak})"
                            for name, peak in sorted(
                                dead.items(), key=lambda kv: -kv[1]
                            )
                        ),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("zero-result alarm check failed: %s", exc)

            # Step-5 — export metrics snapshots after every run (non-fatal).
            try:
                await export_pipeline_metrics(path)
                await export_notification_metrics(path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("metrics export failed: %s", exc)

        logger.info("Job360 run complete")
    finally:
        await db.close()

    return stats


def _print_bucketed_summary(jobs: list[Job], label: str = "Results") -> None:
    """Print a time-bucketed summary of jobs to the console."""
    from src.utils.time_buckets import BUCKETS, bucket_jobs, bucket_summary_counts

    job_dicts = [
        {
            "title": j.title,
            "company": j.company,
            "location": j.location,
            "match_score": j.match_score,
            "visa_flag": j.visa_flag,
            "salary_min": j.salary_min,
            "salary_max": j.salary_max,
            "date_found": j.date_found,
            "apply_url": j.apply_url,
            "source": j.source,
        }
        for j in jobs
    ]
    bucketed = bucket_jobs(job_dicts, min_score=0)
    counts = bucket_summary_counts(bucketed)
    logger.info("=" * 60)
    logger.info("Job360 %s: %s jobs found", label, len(jobs))
    logger.info(
        "  24h: %s | 24-48h: %s | 48-72h: %s | 3-7d: %s",
        counts["last_24h"],
        counts["24_48h"],
        counts["48_72h"],
        counts["3_7d"],
    )
    logger.info("=" * 60)
    for idx in range(4):
        bucket_list = bucketed.get(idx, [])
        if bucket_list:
            label_name = BUCKETS[idx][0]
            logger.info("  %s %s (%s jobs):", BUCKETS[idx][1], label_name, len(bucket_list))
            for i, j in enumerate(bucket_list, 1):
                visa = " [VISA]" if j.get("visa_flag") else ""
                salary = ""
                if j.get("salary_min") and j.get("salary_max"):
                    salary = f" | {int(j['salary_min']):,}-{int(j['salary_max']):,}"
                posted = f" | {_format_date(j.get('date_found', ''))}"
                src = f" [{j.get('source', '')}]"
                logger.info(
                    "    %s. [%s] %s @ %s%s%s%s%s",
                    i,
                    j["match_score"],
                    j["title"],
                    j["company"],
                    salary,
                    visa,
                    posted,
                    src,
                )
    logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job360 Pipeline")
    parser.add_argument("--no-email", action="store_true", help="Skip notifications")
    args = parser.parse_args()
    asyncio.run(run_search(no_notify=args.no_email))
