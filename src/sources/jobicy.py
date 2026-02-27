import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource

logger = logging.getLogger("job360.sources.jobicy")


class JobicySource(BaseJobSource):
    name = "jobicy"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        params = {
            "count": "50",
            "tag": "ai,machine-learning,python,data-science",
        }
        data = await self._get_json(
            "https://jobicy.com/api/v2/remote-jobs", params=params
        )
        if not data or "jobs" not in data:
            return []
        for item in data["jobs"]:
            jobs.append(Job(
                title=item.get("jobTitle", ""),
                company=item.get("companyName", ""),
                location=item.get("jobGeo", ""),
                salary_min=item.get("annualSalaryMin"),
                salary_max=item.get("annualSalaryMax"),
                description=item.get("jobExcerpt", ""),
                apply_url=item.get("url", ""),
                source=self.name,
                date_found=datetime.now(timezone.utc).isoformat(),
            ))
        logger.info(f"Jobicy: found {len(jobs)} jobs")
        return jobs
