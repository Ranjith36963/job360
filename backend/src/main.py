import argparse
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from src.core.settings import (
    REED_API_KEY, ADZUNA_APP_ID, ADZUNA_APP_KEY, JSEARCH_API_KEY,
    JOOBLE_API_KEY, SERPAPI_KEY, CAREERJET_AFFID, FINDWORK_API_KEY,
    DB_PATH, EXPORTS_DIR, REPORTS_DIR, REQUEST_TIMEOUT, MIN_MATCH_SCORE,
)
from src.utils.logger import setup_logging
from src.models import Job
from src.repositories.database import JobDatabase
from src.repositories.csv_export import export_to_csv
from src.services.skill_matcher import check_visa_flag, detect_experience_level, salary_in_range, JobScorer
from src.services.deduplicator import deduplicate
from src.services.profile.storage import load_profile
from src.services.profile.keyword_generator import generate_search_config
from src.services.notifications.report_generator import generate_markdown_report
from src.services.notifications.base import get_configured_channels
from src.services.circuit_breaker import default_registry as default_breaker_registry
from src.services.circuit_breaker import BreakerState
from src.services.scheduler import TieredScheduler

from src.sources.apis_keyed.reed import ReedSource
from src.sources.apis_keyed.adzuna import AdzunaSource
from src.sources.apis_keyed.jsearch import JSearchSource
from src.sources.apis_free.arbeitnow import ArbeitnowSource
from src.sources.apis_free.remoteok import RemoteOKSource
from src.sources.apis_free.jobicy import JobicySource
from src.sources.apis_free.himalayas import HimalayasSource
from src.sources.ats.greenhouse import GreenhouseSource
from src.sources.ats.lever import LeverSource
from src.sources.ats.workable import WorkableSource
from src.sources.ats.ashby import AshbySource
from src.sources.apis_free.remotive import RemotiveSource
from src.sources.apis_keyed.jooble import JoobleSource
from src.sources.scrapers.linkedin import LinkedInSource
from src.sources.ats.smartrecruiters import SmartRecruitersSource
from src.sources.ats.pinpoint import PinpointSource
from src.sources.ats.recruitee import RecruiteeSource
from src.sources.other.indeed import JobSpySource
from src.sources.ats.workday import WorkdaySource
from src.sources.apis_keyed.google_jobs import GoogleJobsSource
from src.sources.apis_free.devitjobs import DevITJobsSource
from src.sources.apis_free.landingjobs import LandingJobsSource
from src.sources.apis_free.aijobs import AIJobsSource
from src.sources.other.themuse import TheMuseSource
from src.sources.other.hackernews import HackerNewsSource
from src.sources.apis_keyed.careerjet import CareerjetSource
from src.sources.apis_keyed.findwork import FindworkSource
from src.sources.other.nofluffjobs import NoFluffJobsSource
from src.sources.apis_free.hn_jobs import HNJobsSource
from src.sources.feeds.jobs_ac_uk import JobsAcUkSource
from src.sources.feeds.nhs_jobs import NHSJobsSource
from src.sources.ats.personio import PersonioSource
from src.sources.feeds.workanywhere import WorkAnywhereSource
from src.sources.feeds.weworkremotely import WeWorkRemotelySource
from src.sources.feeds.realworkfromanywhere import RealWorkFromAnywhereSource
from src.sources.feeds.biospace import BioSpaceSource
from src.sources.scrapers.jobtensor import JobTensorSource
from src.sources.scrapers.climatebase import ClimatebaseSource
from src.sources.scrapers.eightykhours import EightyKHoursSource
from src.sources.scrapers.bcs_jobs import BCSJobsSource
from src.sources.feeds.uni_jobs import UniJobsSource
from src.sources.ats.successfactors import SuccessFactorsSource
from src.sources.scrapers.aijobs_global import AIJobsGlobalSource
from src.sources.scrapers.aijobs_ai import AIJobsAISource
# Batch 3 additions
from src.sources.apis_free.teaching_vacancies import TeachingVacanciesSource
from src.sources.apis_free.gov_apprenticeships import GovApprenticeshipsSource
from src.sources.feeds.nhs_jobs_xml import NHSJobsXMLSource
from src.sources.ats.rippling import RipplingSource
from src.sources.ats.comeet import ComeetSource

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
    "jobtensor": JobTensorSource,
    "climatebase": ClimatebaseSource,
    "eightykhours": EightyKHoursSource,
    "bcs_jobs": BCSJobsSource,
    "uni_jobs": UniJobsSource,
    "successfactors": SuccessFactorsSource,
    "aijobs_global": AIJobsGlobalSource,
    "aijobs_ai": AIJobsAISource,
    # Batch 3 additions
    "teaching_vacancies": TeachingVacanciesSource,
    "gov_apprenticeships": GovApprenticeshipsSource,
    "nhs_jobs_xml": NHSJobsXMLSource,
    "rippling": RipplingSource,
    "comeet": ComeetSource,
}

# Number of unique source instances created by _build_sources().
# 49 not 50 because "indeed" and "glassdoor" both map to JobSpySource (one instance).
# Used by test_main.py::test_source_instance_count_matches_build to catch drift.
# Update this when adding/removing sources.
SOURCE_INSTANCE_COUNT = 49


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
                "  %s: result count (%s) below %.0f%% of 7-day avg (%.1f) — "
                "skipping absence sweep",
                source.name, len(result),
                completeness_threshold * 100, rolling_avg,
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
                source.name, missed,
            )
    return missed_by_source


def _format_date(date_str: str) -> str:
    """Parse date_found into a short 'Posted: 28 Feb 2026' format."""
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return f"Posted: {dt.strftime('%d %b %Y')}"
        except (ValueError, AttributeError):
            continue
    # Fallback: try to extract a date-like substring
    if date_str and len(date_str) >= 10:
        return f"Posted: {date_str[:10]}"
    return "Posted: N/A"


def _build_sources(session: aiohttp.ClientSession, source_filter: str | None = None,
                    search_config=None) -> list:
    """Build source instances, optionally filtered to a single source."""
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
        JobTensorSource(session, search_config=sc),
        ClimatebaseSource(session, search_config=sc),
        EightyKHoursSource(session, search_config=sc),
        BCSJobsSource(session, search_config=sc),
        UniJobsSource(session, search_config=sc),
        SuccessFactorsSource(session, search_config=sc),
        AIJobsGlobalSource(session, search_config=sc),
        AIJobsAISource(session, search_config=sc),
        # Group L: Batch 3 additions
        TeachingVacanciesSource(session, search_config=sc),
        GovApprenticeshipsSource(session, search_config=sc),
        NHSJobsXMLSource(session, search_config=sc),
        RipplingSource(session, search_config=sc),
        ComeetSource(session, search_config=sc),
    ]
    if source_filter:
        # Special case: glassdoor shares JobSpySource with indeed
        if source_filter == "glassdoor":
            source_filter = "indeed"
        return [s for s in all_sources if s.name == source_filter]
    return all_sources


async def run_search(
    db_path: str | None = None,
    source_filter: str | None = None,
    dry_run: bool = False,
    log_level: str | None = None,
    no_notify: bool = False,
) -> dict:
    setup_logging(log_level)
    logger.info("=" * 60)
    logger.info("Job360 - Starting job search run")
    if source_filter:
        logger.info("  Source filter: %s", source_filter)
    if dry_run:
        logger.info("  Mode: DRY RUN (no DB writes, no notifications)")

    # Load user profile for dynamic keywords
    profile = load_profile()
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
    scorer = JobScorer(search_config)
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

        # Create session
        connector = aiohttp.TCPConnector(limit=30, limit_per_host=5)
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            # Build sources
            sources = _build_sources(session, source_filter, search_config=search_config)

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
                        logger.info(
                            "  %s: skipped (breaker OPEN)", source.name
                        )
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
                s.name for s in sources
                if pre_states.get(s.name) != BreakerState.OPEN
                and registry.get(s.name).state == BreakerState.OPEN
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

            # Score all jobs using the user's profile (scorer always exists — guarded at start)
            for job in all_jobs:
                job.match_score = scorer.score(job)
                job.visa_flag = scorer.check_visa_flag(job)
                job.experience_level = detect_experience_level(job.title)

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

                # Notifications via channel abstraction
                if not no_notify:
                    for channel in get_configured_channels():
                        try:
                            await channel.send(new_jobs, stats, csv_path=csv_path)
                        except Exception as e:
                            logger.error("%s notification failed: %s", channel.name, e)

                # Print time-bucketed summary to console
                _print_bucketed_summary(new_jobs, "Results")
            else:
                logger.info("No new jobs to report")
                logger.info("Job360: No new jobs found this run.")

            # Log run
            await db.log_run(stats)

        logger.info("Job360 run complete")
    finally:
        await db.close()

    return stats


def _print_bucketed_summary(jobs: list, label: str = "Results"):
    """Print a time-bucketed summary of jobs to the console."""
    from src.utils.time_buckets import bucket_jobs, bucket_summary_counts, BUCKETS
    job_dicts = [
        {
            "title": j.title, "company": j.company, "location": j.location,
            "match_score": j.match_score, "visa_flag": j.visa_flag,
            "salary_min": j.salary_min, "salary_max": j.salary_max,
            "date_found": j.date_found, "apply_url": j.apply_url, "source": j.source,
        }
        for j in jobs
    ]
    bucketed = bucket_jobs(job_dicts, min_score=0)
    counts = bucket_summary_counts(bucketed)
    logger.info("=" * 60)
    logger.info("Job360 %s: %s jobs found", label, len(jobs))
    logger.info("  24h: %s | 24-48h: %s | 48-72h: %s | 3-7d: %s",
                counts['last_24h'], counts['24_48h'], counts['48_72h'], counts['3_7d'])
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
                    salary = " | %s-%s" % (f"{int(j['salary_min']):,}", f"{int(j['salary_max']):,}")
                posted = " | %s" % _format_date(j.get('date_found', ''))
                src = " [%s]" % j.get('source', '')
                logger.info("    %s. [%s] %s @ %s%s%s%s%s",
                            i, j['match_score'], j['title'], j['company'],
                            salary, visa, posted, src)
    logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job360 Pipeline")
    parser.add_argument("--no-email", action="store_true", help="Skip notifications")
    args = parser.parse_args()
    asyncio.run(run_search(no_notify=args.no_email))
