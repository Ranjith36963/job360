import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.keywords import RELEVANCE_KEYWORDS

logger = logging.getLogger("job360.sources.arbeitnow")


class ArbeitnowSource(BaseJobSource):
    name = "arbeitnow"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        data = await self._get_json("https://www.arbeitnow.com/api/job-board-api")
        if not data or "data" not in data:
            return []
        for item in data["data"]:
            text = f"{item.get('title', '')} {item.get('description', '')} {' '.join(item.get('tags', []))}".lower()
            if not any(kw in text for kw in RELEVANCE_KEYWORDS):
                continue
            date_found = item.get("created_at") or datetime.now(timezone.utc).isoformat()
            jobs.append(Job(
                title=item.get("title", ""),
                company=item.get("company_name", ""),
                location=item.get("location", ""),
                description=item.get("description", ""),
                apply_url=item.get("url", ""),
                source=self.name,
                date_found=date_found,
            ))
        logger.info(f"Arbeitnow: found {len(jobs)} relevant jobs")
        return jobs
