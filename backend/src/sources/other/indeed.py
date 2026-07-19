import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.indeed")


class JobSpySource(BaseJobSource):
    name = "indeed"
    category = "other"

    def __init__(self, session: aiohttp.ClientSession, sites: list[str] | None = None, search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        # Glassdoor disabled by default 2026-06-11: Glassdoor fronts the
        # findPopularLocationAjax.htm location lookup with an anti-bot 403
        # ("Security | Glassdoor") for every term, so jobspy logged
        # "Glassdoor: location not parsed" at ERROR per query (6 lines/run)
        # and produced zero glassdoor rows. The block is term-independent —
        # no location format fixes it. Pass sites=["indeed", "glassdoor"]
        # explicitly to retry if the lookup ever opens up again.
        self._sites = sites or ["indeed"]

    async def fetch_jobs(self) -> list[Job]:
        try:
            from jobspy import scrape_jobs
        except ImportError:
            logger.warning("python-jobspy not installed, skipping Indeed/Glassdoor")
            return []

        jobs = []
        queries = self.job_titles[:8]
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
                logger.warning("JobSpy scrape failed for '%s': %s", query, e)
                continue
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                title = str(row.get("title", ""))
                desc = str(row.get("description", ""))
                site = str(row.get("site", "indeed")).lower()
                source_name = site if site in ("indeed", "glassdoor") else "indeed"
                now_iso = datetime.now(timezone.utc).isoformat()
                date_posted = row.get("date_posted")
                if hasattr(date_posted, "isoformat"):
                    posted_at = date_posted.isoformat()
                    raw_date = posted_at
                    confidence = "high"
                elif date_posted:
                    posted_at = str(date_posted)
                    raw_date = posted_at
                    confidence = "high"
                else:
                    posted_at = None
                    raw_date = None
                    confidence = "low"
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
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_date,
                    salary_min=salary_min,
                    salary_max=salary_max,
                ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("JobSpy: found %s relevant jobs from %s", len(jobs), ", ".join(self._sites))
        return jobs
