import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.keywords import JOB_TITLES, RELEVANCE_KEYWORDS

logger = logging.getLogger("job360.sources.indeed")


class JobSpySource(BaseJobSource):
    name = "indeed"

    def __init__(self, session: aiohttp.ClientSession, sites: list[str] | None = None):
        super().__init__(session)
        self._sites = sites or ["indeed", "glassdoor"]

    async def fetch_jobs(self) -> list[Job]:
        try:
            from jobspy import scrape_jobs
        except ImportError:
            logger.warning("python-jobspy not installed, skipping Indeed/Glassdoor")
            return []

        jobs = []
        queries = JOB_TITLES[:8]
        for query in queries:
            try:
                df = await asyncio.to_thread(
                    scrape_jobs,
                    site_name=self._sites,
                    search_term=query,
                    location="London, UK",
                    country_indeed="UK",
                    results_wanted=50,
                    hours_old=168,
                )
            except Exception as e:
                logger.warning(f"JobSpy scrape failed for '{query}': {e}")
                continue
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                title = str(row.get("title", ""))
                desc = str(row.get("description", ""))
                text = f"{title} {desc}".lower()
                if not any(kw in text for kw in RELEVANCE_KEYWORDS):
                    continue
                site = str(row.get("site", "indeed")).lower()
                source_name = site if site in ("indeed", "glassdoor") else "indeed"
                date_posted = row.get("date_posted")
                if hasattr(date_posted, "isoformat"):
                    date_found = date_posted.isoformat()
                else:
                    date_found = str(date_posted) if date_posted else datetime.now(timezone.utc).isoformat()
                salary_min = row.get("min_amount")
                salary_max = row.get("max_amount")
                try:
                    salary_min = float(salary_min) if salary_min and str(salary_min) != "nan" else None
                except (ValueError, TypeError):
                    salary_min = None
                try:
                    salary_max = float(salary_max) if salary_max and str(salary_max) != "nan" else None
                except (ValueError, TypeError):
                    salary_max = None
                location = str(row.get("location", ""))
                if str(row.get("is_remote", "")).lower() == "true" and "remote" not in location.lower():
                    location = f"{location}, Remote".strip(", ")
                jobs.append(Job(
                    title=title,
                    company=str(row.get("company", "")),
                    location=location,
                    description=desc[:5000],
                    apply_url=str(row.get("job_url", "")),
                    source=source_name,
                    date_found=date_found,
                    salary_min=salary_min,
                    salary_max=salary_max,
                ))
        logger.info(f"JobSpy: found {len(jobs)} relevant jobs from {', '.join(self._sites)}")
        return jobs
