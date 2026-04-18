import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.arbeitnow")


class ArbeitnowSource(BaseJobSource):
    name = "arbeitnow"
    category = "free_json"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        data = await self._get_json("https://www.arbeitnow.com/api/job-board-api")
        if not data or "data" not in data:
            return []
        for item in data["data"]:
            now_iso = datetime.now(timezone.utc).isoformat()
            raw_created = item.get("created_at")
            posted_at = raw_created if raw_created else None
            confidence = "high" if raw_created else "low"
            jobs.append(Job(
                title=item.get("title", ""),
                company=item.get("company_name", ""),
                location=item.get("location", ""),
                description=item.get("description", ""),
                apply_url=item.get("url", ""),
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_created,
            ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Arbeitnow: found %s relevant jobs", len(jobs))
        return jobs
