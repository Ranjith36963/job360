import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.keywords import RELEVANCE_KEYWORDS
from src.config.settings import USER_AGENT

logger = logging.getLogger("job360.sources.remoteok")


class RemoteOKSource(BaseJobSource):
    name = "remoteok"

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
            tags = " ".join(item.get("tags", [])) if isinstance(item.get("tags"), list) else ""
            text = f"{item.get('position', '')} {item.get('description', '')} {tags}".lower()
            if not any(kw in text for kw in RELEVANCE_KEYWORDS):
                continue
            date_found = item.get("date") or datetime.now(timezone.utc).isoformat()
            jobs.append(Job(
                title=item.get("position", ""),
                company=item.get("company", ""),
                location="Remote",
                salary_min=item.get("salary_min"),
                salary_max=item.get("salary_max"),
                description=item.get("description", ""),
                apply_url=item.get("url", ""),
                source=self.name,
                date_found=date_found,
            ))
        logger.info(f"RemoteOK: found {len(jobs)} relevant jobs")
        return jobs
