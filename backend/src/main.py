import argparse
import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

import aiohttp

from src.core.settings import (
    ADZUNA_APP_ID,
    ADZUNA_APP_KEY,
    CAREERJET_AFFID,
    DB_PATH,
    DFE_APPRENTICESHIPS_API_KEY,
    ENRICHMENT_THRESHOLD,
    EXPORTS_DIR,
    FINDWORK_API_KEY,
    JOOBLE_API_KEY,
    JSEARCH_API_KEY,
    MIN_MATCH_SCORE,
    REED_API_KEY,
    REPORTS_DIR,
    REQUEST_TIMEOUT,
    SEMANTIC_ENABLED,
    SERPAPI_KEY,
)
from src.core.tenancy import DEFAULT_TENANT_ID
from src.models import Job
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
from src.services.profile.storage import current_profile_version_id, load_profile
from src.services.scheduler import TieredScheduler
from src.services.skill_matcher import JobScorer, detect_experience_level, salary_in_range
from src.sources.apis_free.aijobs import AIJobsSource
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
from src.sources.ats.rippling import RipplingSource
from src.sources.ats.smartrecruiters import SmartRecruitersSource
from src.sources.ats.successfactors import SuccessFactorsSource
from src.sources.ats.workable import WorkableSource
from src.sources.ats.workday import WorkdaySource
from src.sources.feeds.biospace import BioSpaceSource
from src.sources.feeds.jobs_ac_uk import JobsAcUkSource
from src.sources.feeds.nhs_jobs import NHSJobsSource
from src.sources.feeds.nhs_jobs_xml import NHSJobsXMLSource
from src.sources.feeds.realworkfromanywhere import RealWorkFromAnywhereSource
from src.sources.feeds.uni_jobs import UniJobsSource
from src.sources.feeds.weworkremotely import WeWorkRemotelySource
from src.sources.feeds.workanywhere import WorkAnywhereSource
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
    "aijobs": AIJobsSource,
    "themuse": TheMuseSource,
    "hackernews": HackerNewsSource,
    "careerjet": CareerjetSource,
    "findwork": FindworkSource,
    "gov_apprenticeships": GovApprenticeshipsSource,
    "nofluffjobs": NoFluffJobsSource,
    # Phase 4: New free sources
    "hn_jobs": HNJobsSource,
    "jobs_ac_uk": JobsAcUkSource,
    "nhs_jobs": NHSJobsSource,
    "personio": PersonioSource,
    "workanywhere": WorkAnywhereSource,
    "weworkremotely": WeWorkRemotelySource,
    "realworkfromanywhere": RealWorkFromAnywhereSource,
    "biospace": BioSpaceSource,
    "climatebase": ClimatebaseSource,
    "eightykhours": EightyKHoursSource,
    "bcs_jobs": BCSJobsSource,
    "uni_jobs": UniJobsSource,
    "successfactors": SuccessFactorsSource,
    "aijobs_ai": AIJobsAISource,
    # Batch 3 additions
    "teaching_vacancies": TeachingVacanciesSource,
    "nhs_jobs_xml": NHSJobsXMLSource,
    "rippling": RipplingSource,
}

# Number of unique source instances created by _build_sources().
# 46 not 47 because "indeed" and "glassdoor" both map to JobSpySource (one instance).
# 4 dead sources removed in the 2026-06 M6 rotation: jobtensor, comeet,
# gov_apprenticeships, aijobs_global — all upstream-dead. gov_apprenticeships
# was restored 2026-06-16 against the DfE Display Advert API v2 (keyed).
# Used by test_main.py::test_source_instance_count_matches_build to catch drift.
# Update this when adding/removing sources.
SOURCE_INSTANCE_COUNT = 46


async def _ghost_detection_pass(
    db,
    sources,
    results,
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
    for source, result in zip(sources, results):
        if isinstance(result, BaseException) or result is None:
            continue
        past = history.get(source.name, [])
        rolling_avg = (sum(past) / len(past)) if past else 0.0
        if rolling_avg > 0 and len(result) < completeness_threshold * rolling_avg:
            logger.warning(
                "  %s: result count (%s) below %.0f%% of 7-day avg (%.1f) — " "skipping absence sweep",
                source.name,
                len(result),
                completeness_threshold * 100,
                rolling_avg,
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
    session: aiohttp.ClientSession, source_filter: str | None = None, search_config=None, user_profile=None
) -> list:
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
        AIJobsSource(session, search_config=sc),
        TheMuseSource(session, search_config=sc),
        HackerNewsSource(session, search_config=sc),
        CareerjetSource(session, affid=CAREERJET_AFFID, search_config=sc),
        FindworkSource(session, api_key=FINDWORK_API_KEY, search_config=sc),
        GovApprenticeshipsSource(session, api_key=DFE_APPRENTICESHIPS_API_KEY, search_config=sc),
        NoFluffJobsSource(session, search_config=sc),
        # Group K: Phase 4 new free sources
        HNJobsSource(session, search_config=sc),
        JobsAcUkSource(session, search_config=sc),
        NHSJobsSource(session, search_config=sc),
        PersonioSource(session, search_config=sc),
        WorkAnywhereSource(session, search_config=sc),
        WeWorkRemotelySource(session, search_config=sc),
        RealWorkFromAnywhereSource(session, search_config=sc),
        BioSpaceSource(session, search_config=sc),
        ClimatebaseSource(session, search_config=sc),
        EightyKHoursSource(session, search_config=sc),
        BCSJobsSource(session, search_config=sc),
        UniJobsSource(session, search_config=sc),
        SuccessFactorsSource(session, search_config=sc),
        AIJobsAISource(session, search_config=sc),
        # Group L: Batch 3 additions
        TeachingVacanciesSource(session, search_config=sc),
        NHSJobsXMLSource(session, search_config=sc),
        RipplingSource(session, search_config=sc),
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


async def _run_matcher_stage(db, *, user_id: str, jobs: list) -> None:
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


async def run_search(
    db_path: str | None = None,
    source_filter: str | None = None,
    dry_run: bool = False,
    log_level: str | None = None,
    no_notify: bool = False,
    user_id: str | None = None,
) -> dict:
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
    # (DEFAULT_TENANT_ID). See docs/plans/batch-3.5.2-plan.md Deliverable E.
    # Without this, the web "New Search" ran profile-less (E2E_TEST_REPORT #1).
    profile = load_profile(user_id or DEFAULT_TENANT_ID)
    if not profile or not profile.is_complete:
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

    search_config = generate_search_config(profile)
    logger.info("  Using dynamic keywords from user profile")
    logger.info("=" * 60)

    # Init database
    path = db_path or str(DB_PATH)
    db = JobDatabase(path)
    await db.init_db()

    try:
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
            enrichment_lookup_dict = await _build_enrichment_lookup(db._conn)
        else:
            enrichment_lookup_dict = {}
        scorer = JobScorer(
            search_config,
            user_preferences=profile.preferences if profile else None,
            enrichment_lookup=lambda job: enrichment_lookup_dict.get(getattr(job, "id", None)),
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
            per_source_duration: dict[str, int] = {}
            per_source_errors: dict[str, int] = {}

            def _instrument(src):
                original_fetch = src.fetch_jobs

                async def _timed_fetch():
                    started_ns = time.perf_counter_ns()
                    try:
                        with source_timer(src.name):
                            return await original_fetch()
                    except BaseException:
                        per_source_errors[src.name] = per_source_errors.get(src.name, 0) + 1
                        raise
                    finally:
                        elapsed_ms = max(0, (time.perf_counter_ns() - started_ns) // 1_000_000)
                        per_source_duration[src.name] = int(elapsed_ms)

                src.fetch_jobs = _timed_fetch  # type: ignore[method-assign]

            for s in sources:
                _instrument(s)

            scheduler = TieredScheduler(sources, registry)
            paired = await scheduler.tick(force=True)

            results_by_name: dict = {name: None for name in (s.name for s in sources)}
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

            # Score all jobs using the user's profile (scorer always exists — guarded at start).
            # Step-1 B4: JobScorer.score() now returns a ScoreBreakdown — surface
            # the scalar match_score on the Job so the MIN_MATCH_SCORE filter still works.
            # Step-1.5 S1.1-C: capture every dim component so the breakdown survives
            # the round-trip to the API. Names map ScoreBreakdown → JobResponse:
            # title_score → role; recency_score → recency; *_score fields kept
            # as-is. Engine doesn't currently produce experience/credentials/
            # semantic/penalty — those columns persist as 0 until later batches.
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

            # Deadline extraction — fill in any job that didn't get a structured
            # deadline (e.g. from JSON-LD validThrough) from its description text.
            # Runs after scoring, before dedup, so every raw job is covered.
            # Lazy-imported to keep the top-level import surface small.
            from src.services.deadline import extract_deadline  # noqa: PLC0415

            for job in all_jobs:
                if job.deadline is None and job.description:
                    result = extract_deadline(job.description)
                    if result is not None:
                        job.deadline, job.deadline_source = result

            # Deduplicate
            unique_jobs = deduplicate(all_jobs)
            logger.info("After dedup: %s unique jobs", len(unique_jobs))

            # Filter by minimum score
            unique_jobs = [j for j in unique_jobs if j.match_score >= MIN_MATCH_SCORE]
            logger.info("After score filter (>=%s): %s jobs", MIN_MATCH_SCORE, len(unique_jobs))

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
            for job in unique_jobs:
                if await db.insert_job(job):
                    new_jobs.append(job)
            await db.commit()

            new_jobs.sort(key=lambda j: (j.match_score, salary_in_range(j)), reverse=True)
            logger.info("New jobs: %s", len(new_jobs))

            # Attach each job's DB id (the PK assigned by INSERT, resolved via
            # normalized key). The Job dataclass carries no id until now, and
            # enrich_batch persists keyed on ``job.id`` — so this MUST happen
            # before enrichment or every result is silently dropped.
            for job in unique_jobs:
                cur = await db._conn.execute(
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
                high_scored = [
                    j
                    for j in unique_jobs
                    if getattr(j, "id", None) is not None
                    and j.match_score is not None
                    and j.match_score >= ENRICHMENT_THRESHOLD
                ]
                if high_scored:
                    logger.info(
                        "Enriching %s jobs with match_score >= %s",
                        len(high_scored),
                        ENRICHMENT_THRESHOLD,
                    )
                    # Concurrency 3 (not 10): free-tier LLMs cap at ~30 requests/min
                    # (Cerebras) and small token/min budgets (Groq). A burst of 10
                    # concurrent × retries 429s every provider at once. 3 keeps the
                    # batch under the per-minute limits while still parallelising.
                    await enrich_batch(high_scored, semaphore_limit=3, conn=db._conn)
                    await db.commit()

                    # Engine 2 dim-scoring fix: the first score() (above) ran
                    # before these jobs had DB ids or enrichment rows, so the
                    # enrichment dims (seniority/salary/visa/workplace) scored 0.
                    # Now they have both — re-score with a rebuilt lookup so the
                    # dims fold into match_score, and persist so the feed write
                    # below + the catalog reflect the dim-inclusive score.
                    fresh_lookup = await _build_enrichment_lookup(db._conn)
                    dim_scorer = JobScorer(
                        search_config,
                        user_preferences=profile.preferences if profile else None,
                        enrichment_lookup=lambda job: fresh_lookup.get(getattr(job, "id", None)),
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

                feed = FeedService(db._conn)
                feed_written = 0
                feed_profile_version = current_profile_version_id(user_id)
                for job in unique_jobs:
                    try:
                        cur = await db._conn.execute(
                            "SELECT id FROM jobs WHERE normalized_company = ? AND normalized_title = ?",
                            job.normalized_key(),
                        )
                        r = await cur.fetchone()
                        if r is None:
                            continue
                        await feed.upsert_feed_row(
                            user_id=user_id,
                            job_id=r[0],
                            score=int(job.match_score or 0),
                            bucket=_recency_bucket(job.date_found),
                            profile_version=feed_profile_version,
                        )
                        feed_written += 1
                    except Exception as e:  # never let a feed write fail the whole run
                        logger.warning("user_feed write failed for %r: %s", job.title, e)
                logger.info("Wrote %s jobs to user_feed for user %s", feed_written, user_id)

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
                from src.services.vector_index import VectorIndex  # noqa: PLC0415

                try:
                    vix = VectorIndex()
                except Exception as e:
                    logger.warning("VectorIndex init failed; skipping vector upsert: %s", e)
                    vix = None

                if vix is not None:
                    for j in new_jobs:
                        try:
                            # Look up the persisted row id (insert_job returned bool only).
                            row_cursor = await db._conn.execute(
                                "SELECT id FROM jobs WHERE normalized_company = ? AND normalized_title = ?",
                                j.normalized_key(),
                            )
                            row = await row_cursor.fetchone()
                            if row is None:
                                continue
                            job_id = row[0]
                            try:
                                enrichment = await load_enrichment(db._conn, job_id)
                            except Exception:
                                enrichment = None
                            vec = encode_job(j, enrichment)
                            vix.upsert(
                                job_id=job_id,
                                vector=vec,
                                metadata={"title": j.title, "company": j.company},
                            )
                            await db._conn.execute(
                                "INSERT INTO job_embeddings(job_id, model_version) VALUES (?, ?) "
                                "ON CONFLICT(job_id) DO UPDATE SET model_version = EXCLUDED.model_version",
                                (job_id, MODEL_NAME),
                            )
                        except Exception as e:
                            logger.warning("vector upsert failed for job %r: %s", j.title, e)
                    await db.commit()

            # Stats
            stats = {
                "total_found": len(all_jobs),
                "new_jobs": len(new_jobs),
                "sources_queried": source_count,
                "per_source": per_source,
            }

            # Generate outputs
            if new_jobs:
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


def _print_bucketed_summary(jobs: list, label: str = "Results"):
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
