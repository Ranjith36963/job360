import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.core.settings import USER_AGENT

logger = logging.getLogger("job360.sources.remoteok")


class RemoteOKSource(BaseJobSource):
    name = "remoteok"
    category = "free_json"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        headers = {"User-Agent": USER_AGENT}
        data = await self._get_json("https://remoteok.com/api", headers=headers)
        if not data or not isinstance(data, list):
            return []
        # Skip first element (metadata/legal notice)
        for item in data[1:]:
            if not isinstance(item, dict):
                continue
            now_iso = datetime.now(timezone.utc).isoformat()
            raw_date = item.get("date")
            posted_at = raw_date if raw_date else None
            confidence = "high" if raw_date else "low"
            jobs.append(Job(
                title=item.get("position", ""),
                company=item.get("company", ""),
                location="Remote",
                salary_min=item.get("salary_min"),
                salary_max=item.get("salary_max"),
                description=item.get("description", ""),
                apply_url=item.get("url", ""),
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_date,
            ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("RemoteOK: found %s relevant jobs", len(jobs))
        return jobs
