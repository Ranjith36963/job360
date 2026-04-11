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
            date_found = item.get("pubDate") or datetime.now(timezone.utc).isoformat()
            jobs.append(Job(
                title=title,
                company=item.get("companyName", ""),
                location=item.get("jobGeo", ""),
                salary_min=item.get("annualSalaryMin"),
                salary_max=item.get("annualSalaryMax"),
                description=description,
                apply_url=item.get("url", ""),
                source=self.name,
                date_found=date_found,
            ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Jobicy: found %s relevant jobs", len(jobs))
        return jobs
