import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from src.config.settings import (
    REED_API_KEY, ADZUNA_APP_ID, ADZUNA_APP_KEY, JSEARCH_API_KEY,
    DB_PATH, EXPORTS_DIR, REPORTS_DIR, REQUEST_TIMEOUT,
)
from src.utils.logger import setup_logging
from src.models import Job
from src.storage.database import JobDatabase
from src.storage.csv_export import export_to_csv
from src.filters.skill_matcher import score_job, check_visa_flag
from src.filters.deduplicator import deduplicate
from src.notifications.report_generator import generate_markdown_report
from src.notifications.email_notify import send_email

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

logger = logging.getLogger("job360.main")


async def run_search(db_path: str | None = None) -> dict:
    setup_logging()
    logger.info("=" * 60)
    logger.info("Job360 - Starting job search run")
    logger.info("=" * 60)

    # Init database
    path = db_path or str(DB_PATH)
    db = JobDatabase(path)
    await db.init_db()

    # Create session
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Build sources
        sources = [
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
        ]

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

        # Deduplicate
        unique_jobs = deduplicate(all_jobs)
        logger.info(f"After dedup: {len(unique_jobs)} unique jobs")

        # Filter new jobs (not seen in DB)
        new_jobs: list[Job] = []
        for job in unique_jobs:
            if not await db.is_job_seen(job.normalized_key()):
                await db.insert_job(job)
                new_jobs.append(job)

        new_jobs.sort(key=lambda j: j.match_score, reverse=True)
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
            md_path.write_text(md_report)
            logger.info(f"Report saved: {md_path}")

            # Email
            try:
                await send_email(new_jobs, stats, csv_path)
            except Exception as e:
                logger.error(f"Email failed: {e}")

            # Print top jobs to console
            print(f"\n{'='*60}")
            print(f"Job360 Results: {len(new_jobs)} new jobs found")
            print(f"{'='*60}")
            for i, job in enumerate(new_jobs[:10], 1):
                visa = " [VISA]" if job.visa_flag else ""
                salary = ""
                if job.salary_min and job.salary_max:
                    salary = f" | {int(job.salary_min):,}-{int(job.salary_max):,}"
                print(f"  {i}. [{job.match_score}] {job.title} @ {job.company} | {job.location}{salary}{visa}")
                print(f"     {job.apply_url}")
            print(f"{'='*60}\n")
        else:
            logger.info("No new jobs to report")
            print("\nJob360: No new jobs found this run.\n")

        # Log run
        await db.log_run(stats)

    await db.close()
    logger.info("Job360 run complete")
    return stats


if __name__ == "__main__":
    asyncio.run(run_search())
