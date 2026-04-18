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
            now_iso = datetime.now(timezone.utc).isoformat()
            raw_pub = item.get("pubDate") or item.get("createdAt")
            posted_at = raw_pub if raw_pub else None
            confidence = "high" if raw_pub else "low"
            jobs.append(Job(
                title=item.get("title", ""),
                company=item.get("companyName", ""),
                location=location,
                salary_min=item.get("minSalary"),
                salary_max=item.get("maxSalary"),
                description=item.get("excerpt", ""),
                apply_url=item.get("applicationUrl", item.get("url", "")),
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_pub,
            ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Himalayas: found %s relevant jobs", len(jobs))
        return jobs
