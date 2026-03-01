import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.keywords import JOB_TITLES

logger = logging.getLogger("job360.sources.jooble")


class JoobleSource(BaseJobSource):
    name = "jooble"

    def __init__(self, session: aiohttp.ClientSession, api_key: str = ""):
        super().__init__(session)
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.info("Jooble: no API key, skipping")
            return []
        jobs = []
        seen_ids = set()
        queries = JOB_TITLES[:8]
        for query in queries:
            body = {
                "keywords": query,
                "location": "United Kingdom",
                "page": "1",
            }
            data = await self._post_json(
                f"https://jooble.org/api/{self._api_key}",
                body=body,
            )
            if not data or "jobs" not in data:
                continue
            for item in data["jobs"]:
                job_id = item.get("id", "")
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
                date_found = item.get("updated") or datetime.now(timezone.utc).isoformat()
                salary_text = item.get("salary", "")
                salary_min = None
                salary_max = None
                if salary_text and "-" in str(salary_text):
                    parts = str(salary_text).replace(",", "").replace("£", "").replace("$", "").split("-")
                    try:
                        salary_min = float("".join(c for c in parts[0] if c.isdigit() or c == "."))
                        salary_max = float("".join(c for c in parts[1] if c.isdigit() or c == "."))
                    except (ValueError, IndexError):
                        pass
                jobs.append(Job(
                    title=item.get("title", ""),
                    company=item.get("company", ""),
                    location=item.get("location", ""),
                    description=item.get("snippet", ""),
                    apply_url=item.get("link", ""),
                    source=self.name,
                    date_found=date_found,
                    salary_min=salary_min,
                    salary_max=salary_max,
                ))
            await asyncio.sleep(1)
        logger.info(f"Jooble: found {len(jobs)} jobs")
        return jobs
