import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.aijobs")


class AIJobsSource(BaseJobSource):
    name = "aijobs"

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

            if not any(kw in text for kw in self.relevance_keywords):
                continue
            if not _is_uk_or_remote(location):
                continue

            date_found = item.get("date") or datetime.now(timezone.utc).isoformat()
            apply_url = item.get("url", "")

            jobs.append(Job(
                title=title,
                company=item.get("company", ""),
                location=location,
                description=description[:5000],
                apply_url=apply_url,
                source=self.name,
                date_found=date_found,
            ))

        logger.info(f"AIJobs: found {len(jobs)} relevant jobs")
        return jobs
