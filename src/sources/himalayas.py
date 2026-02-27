import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.keywords import RELEVANCE_KEYWORDS

logger = logging.getLogger("job360.sources.himalayas")


class HimalayasSource(BaseJobSource):
    name = "himalayas"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        params = {"limit": "50"}
        data = await self._get_json(
            "https://himalayas.app/jobs/api", params=params
        )
        if not data or "jobs" not in data:
            return []
        for item in data["jobs"]:
            categories = " ".join(item.get("categories", [])) if isinstance(item.get("categories"), list) else ""
            text = f"{item.get('title', '')} {item.get('excerpt', '')} {categories}".lower()
            if not any(kw in text for kw in RELEVANCE_KEYWORDS):
                continue
            loc_restrictions = item.get("locationRestrictions", [])
            location = ", ".join(loc_restrictions) if isinstance(loc_restrictions, list) else str(loc_restrictions)
            jobs.append(Job(
                title=item.get("title", ""),
                company=item.get("companyName", ""),
                location=location,
                salary_min=item.get("minSalary"),
                salary_max=item.get("maxSalary"),
                description=item.get("excerpt", ""),
                apply_url=item.get("applicationUrl", item.get("url", "")),
                source=self.name,
                date_found=datetime.now(timezone.utc).isoformat(),
            ))
        logger.info(f"Himalayas: found {len(jobs)} relevant jobs")
        return jobs
