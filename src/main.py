import argparse
import asyncio
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from src.config.settings import (
    REED_API_KEY, ADZUNA_APP_ID, ADZUNA_APP_KEY, JSEARCH_API_KEY,
    JOOBLE_API_KEY, SERPAPI_KEY,
    DB_PATH, EXPORTS_DIR, REPORTS_DIR, REQUEST_TIMEOUT, MIN_MATCH_SCORE,
)
from src.utils.logger import setup_logging
from src.models import Job
from src.storage.database import JobDatabase
from src.storage.csv_export import export_to_csv
from src.filters.skill_matcher import score_job, check_visa_flag, detect_experience_level, salary_in_range
from src.filters.deduplicator import deduplicate
from src.notifications.report_generator import generate_markdown_report
from src.notifications.base import get_configured_channels
from src.notifications.email_notify import send_email
from src.notifications.slack_notify import send_slack
from src.notifications.discord_notify import send_discord

from src.sources.reed import ReedSource
from src.sources.adzuna import AdzunaSource
from src.sources.jsearch import JSearchSource
from src.sources.arbeitnow import ArbeitnowSource
from src.sources.remoteok import RemoteOKSource
from src.sources.jobicy import JobicySource
from src.sources.himalayas import HimalayasSource
from src.sources.greenhouse import GreenhouseSource
from src.sources.lever import LeverSource
from src.sources.workable import WorkableSource
from src.sources.ashby import AshbySource
from src.sources.findajob import FindAJobSource
from src.sources.remotive import RemotiveSource
from src.sources.jooble import JoobleSource
from src.sources.linkedin import LinkedInSource
from src.sources.smartrecruiters import SmartRecruitersSource
from src.sources.pinpoint import PinpointSource
from src.sources.recruitee import RecruiteeSource
from src.sources.indeed import JobSpySource
from src.sources.workday import WorkdaySource
from src.sources.google_jobs import GoogleJobsSource
from src.sources.devitjobs import DevITJobsSource
from src.sources.landingjobs import LandingJobsSource

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
    "findajob": FindAJobSource,
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
}


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


def _build_sources(session: aiohttp.ClientSession, source_filter: str | None = None) -> list:
    """Build source instances, optionally filtered to a single source."""
    all_sources = [
        # Group A: Keyed APIs
        ReedSource(session, api_key=REED_API_KEY),
        AdzunaSource(session, app_id=ADZUNA_APP_ID, app_key=ADZUNA_APP_KEY),
        JSearchSource(session, api_key=JSEARCH_API_KEY),
        # Group B: Free APIs
        ArbeitnowSource(session),
        RemoteOKSource(session),
        JobicySource(session),
        HimalayasSource(session),
        # Group C: ATS boards
        GreenhouseSource(session),
        LeverSource(session),
        WorkableSource(session),
        AshbySource(session),
        # Group D: Government
        FindAJobSource(session),
        # Group E: New free APIs
        RemotiveSource(session),
        JoobleSource(session, api_key=JOOBLE_API_KEY),
        LinkedInSource(session),
        # Group F: New ATS boards
        SmartRecruitersSource(session),
        PinpointSource(session),
        RecruiteeSource(session),
        # Group G: Scraper-based
        JobSpySource(session),
        # Group H: Workday ATS
        WorkdaySource(session),
        # Group I: Real-time data sources
        GoogleJobsSource(session, api_key=SERPAPI_KEY),
        DevITJobsSource(session),
        LandingJobsSource(session),
    ]
    if source_filter:
        return [s for s in all_sources if s.name == source_filter]
    return all_sources


async def run_search(
    db_path: str | None = None,
    source_filter: str | None = None,
    dry_run: bool = False,
    log_level: str | None = None,
    no_notify: bool = False,
    launch_dashboard: bool = False,
) -> dict:
    setup_logging(log_level)
    logger.info("=" * 60)
    logger.info("Job360 - Starting job search run")
    if source_filter:
        logger.info(f"  Source filter: {source_filter}")
    if dry_run:
        logger.info("  Mode: DRY RUN (no DB writes, no notifications)")
    logger.info("=" * 60)

    # Init database
    path = db_path or str(DB_PATH)
    db = JobDatabase(path)
    await db.init_db()

    # Auto-purge old jobs (>30 days)
    purged = await db.purge_old_jobs(days=30)
    if purged:
        logger.info(f"Purged {purged} jobs older than 30 days")

    # Create session
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Build sources
        sources = _build_sources(session, source_filter)

        if not sources:
            logger.error(f"No sources matched filter: {source_filter}")
            await db.close()
            return {"total_found": 0, "new_jobs": 0, "sources_queried": 0, "per_source": {}}

        # Fetch from all sources concurrently
        all_jobs: list[Job] = []
        per_source: dict[str, int] = {}
        source_count = 0

        async def _fetch_source(source):
            try:
                return await asyncio.wait_for(source.fetch_jobs(), timeout=120)
            except asyncio.TimeoutError:
                logger.warning(f"Source {source.name} timed out")
                return []
            except Exception as e:
                logger.error(f"Source {source.name} failed: {e}")
                return []

        results = await asyncio.gather(*[_fetch_source(s) for s in sources])

        for source, jobs in zip(sources, results):
            source_count += 1
            per_source[source.name] = len(jobs)
            all_jobs.extend(jobs)
            if jobs:
                logger.info(f"  {source.name}: {len(jobs)} jobs")
            else:
                logger.info(f"  {source.name}: 0 jobs")

        logger.info(f"Total raw jobs: {len(all_jobs)}")

        # Score all jobs
        for job in all_jobs:
            job.match_score = score_job(job)
            job.visa_flag = check_visa_flag(job)
            job.experience_level = detect_experience_level(job.title)

        # Deduplicate
        unique_jobs = deduplicate(all_jobs)
        logger.info(f"After dedup: {len(unique_jobs)} unique jobs")

        # Filter by minimum score
        unique_jobs = [j for j in unique_jobs if j.match_score >= MIN_MATCH_SCORE]
        logger.info(f"After score filter (>={MIN_MATCH_SCORE}): {len(unique_jobs)} jobs")

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
            await db.close()
            logger.info("Job360 dry run complete")
            return stats

        # Filter new jobs (not seen in DB)
        new_jobs: list[Job] = []
        for job in unique_jobs:
            if not await db.is_job_seen(job.normalized_key()):
                await db.insert_job(job)
                new_jobs.append(job)

        new_jobs.sort(key=lambda j: (j.match_score, salary_in_range(j)), reverse=True)
        logger.info(f"New jobs: {len(new_jobs)}")

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
            await export_to_csv(new_jobs, csv_path)
            logger.info(f"CSV exported: {csv_path}")

            # Markdown report
            REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            md_report = generate_markdown_report(new_jobs, stats)
            md_path = REPORTS_DIR / f"report_{ts}.md"
            md_path.write_text(md_report, encoding="utf-8")
            logger.info(f"Report saved: {md_path}")

            # Notifications via channel abstraction
            if not no_notify:
                for channel in get_configured_channels():
                    try:
                        await channel.send(new_jobs, stats, csv_path=csv_path)
                    except Exception as e:
                        logger.error(f"{channel.name} notification failed: {e}")

            # Print time-bucketed summary to console
            _print_bucketed_summary(new_jobs, "Results")
        else:
            logger.info("No new jobs to report")
            print("\nJob360: No new jobs found this run.\n")

        # Log run
        await db.log_run(stats)

    await db.close()
    logger.info("Job360 run complete")

    # Launch dashboard if requested
    if launch_dashboard:
        logger.info("Launching Streamlit dashboard...")
        subprocess.Popen([sys.executable, "-m", "streamlit", "run", "src/dashboard.py"])

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
    print(f"\n{'='*60}")
    print(f"Job360 {label}: {len(jobs)} jobs found")
    print(f"  24h: {counts['last_24h']} | 24-48h: {counts['24_48h']} | "
          f"48-72h: {counts['48_72h']} | 3-7d: {counts['3_7d']}")
    print(f"{'='*60}")
    for idx in range(4):
        bucket_list = bucketed.get(idx, [])
        if bucket_list:
            label_name = BUCKETS[idx][0]
            print(f"\n  {BUCKETS[idx][1]} {label_name} ({len(bucket_list)} jobs):")
            for i, j in enumerate(bucket_list, 1):
                visa = " [VISA]" if j.get("visa_flag") else ""
                salary = ""
                if j.get("salary_min") and j.get("salary_max"):
                    salary = f" | {int(j['salary_min']):,}-{int(j['salary_max']):,}"
                posted = f" | {_format_date(j.get('date_found', ''))}"
                src = f" [{j.get('source', '')}]"
                print(f"    {i}. [{j['match_score']}] {j['title']} @ {j['company']}{salary}{visa}{posted}{src}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job360 Pipeline")
    parser.add_argument("--no-email", action="store_true", help="Skip notifications")
    parser.add_argument("--dashboard", action="store_true", help="Launch dashboard after run")
    args = parser.parse_args()
    asyncio.run(run_search(no_notify=args.no_email, launch_dashboard=args.dashboard))
