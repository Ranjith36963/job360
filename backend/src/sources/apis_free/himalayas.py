import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.himalayas")


class HimalayasSource(BaseJobSource):
    name = "himalayas"
    category = "free_json"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        params = {"limit": "50"}
        data = await self._get_json(
            "https://himalayas.app/jobs/api", params=params
        )
        if not data or "jobs" not in data:
            return []
        for item in data["jobs"]:
            loc_restrictions = item.get("locationRestrictions", [])
            location = ", ".join(loc_restrictions) if isinstance(loc_restrictions, list) else str(loc_restrictions)
            date_found = item.get("pubDate") or item.get("createdAt") or datetime.now(timezone.utc).isoformat()
            jobs.append(Job(
                title=item.get("title", ""),
                company=item.get("companyName", ""),
                location=location,
                salary_min=item.get("minSalary"),
                salary_max=item.get("maxSalary"),
                description=item.get("excerpt", ""),
                apply_url=item.get("applicationUrl", item.get("url", "")),
                source=self.name,
                date_found=date_found,
            ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Himalayas: found %s relevant jobs", len(jobs))
        return jobs
