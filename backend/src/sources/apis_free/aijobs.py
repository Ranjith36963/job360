import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.aijobs")


class AIJobsSource(BaseJobSource):
    name = "aijobs"
    category = "free_json"

    async def fetch_jobs(self) -> list[Job]:
        data = await self._get_json("https://aijobs.net/api/list-jobs/")
        if not data or not isinstance(data, list):
            return []

        jobs = []
        for item in data:
            title = item.get("title", "")
            description = item.get("description", "")
            location = item.get("location", "")
            text = f"{title} {description}".lower()

            if not _is_uk_or_remote(location):
                continue

            now_iso = datetime.now(timezone.utc).isoformat()
            raw_date = item.get("date")
            posted_at = raw_date if raw_date else None
            confidence = "high" if raw_date else "low"
            apply_url = item.get("url", "")

            jobs.append(Job(
                title=title,
                company=item.get("company", ""),
                location=location,
                description=description[:5000],
                apply_url=apply_url,
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_date,
            ))

        logger.info("AIJobs: found %s relevant jobs", len(jobs))
        return jobs
