import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.filters.skill_matcher import get_search_tags, get_search_locations

logger = logging.getLogger("job360.sources.jobicy")


class JobicySource(BaseJobSource):
    name = "jobicy"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        tags = get_search_tags()
        # Derive geo from profile locations
        locations = get_search_locations()
        geo = ""
        for loc in locations:
            loc_lower = loc.lower().replace(" ", "-")
            if loc_lower in ("uk", "united-kingdom", "remote"):
                geo = "united-kingdom"
                break
            elif len(loc_lower) > 3:
                geo = loc_lower
                break
        params = {
            "count": "50",
            "tag": tags,
        }
        if geo:
            params["geo"] = geo
        data = await self._get_json(
            "https://jobicy.com/api/v2/remote-jobs", params=params
        )
        if not data or "jobs" not in data:
            return []
        for item in data["jobs"]:
            date_found = item.get("pubDate") or datetime.now(timezone.utc).isoformat()
            jobs.append(Job(
                title=item.get("jobTitle", ""),
                company=item.get("companyName", ""),
                location=item.get("jobGeo", ""),
                salary_min=item.get("annualSalaryMin"),
                salary_max=item.get("annualSalaryMax"),
                description=item.get("jobExcerpt", ""),
                apply_url=item.get("url", ""),
                source=self.name,
                date_found=date_found,
            ))
        logger.info(f"Jobicy: found {len(jobs)} jobs")
        return jobs
