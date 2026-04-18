import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.jobicy")


class JobicySource(BaseJobSource):
    name = "jobicy"
    category = "free_json"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        params = {
            "count": "50",
            "geo": "uk",
            "industry": "data-science",
            "tag": "ai",
        }
        data = await self._get_json(
            "https://jobicy.com/api/v2/remote-jobs", params=params
        )
        if not data or "jobs" not in data:
            return []
        for item in data["jobs"]:
            title = item.get("jobTitle", "")
            description = item.get("jobExcerpt", "")
            now_iso = datetime.now(timezone.utc).isoformat()
            raw_pub = item.get("pubDate")
            posted_at = raw_pub if raw_pub else None
            confidence = "high" if raw_pub else "low"
            jobs.append(Job(
                title=title,
                company=item.get("companyName", ""),
                location=item.get("jobGeo", ""),
                salary_min=item.get("annualSalaryMin"),
                salary_max=item.get("annualSalaryMax"),
                description=description,
                apply_url=item.get("url", ""),
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_pub,
            ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Jobicy: found %s relevant jobs", len(jobs))
        return jobs
