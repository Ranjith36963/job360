import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.jsearch")

class JSearchSource(BaseJobSource):
    name = "jsearch"
    category = "keyed_api"

    def __init__(self, session: aiohttp.ClientSession, api_key: str = "", search_config=None):
        super().__init__(session, search_config=search_config)
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.warning("JSearch: no API key, skipping")
            return []
        jobs = []
        consecutive_failures = 0
        headers = {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
        }
        queries = self.search_queries
        if not queries:
            logger.info("JSearch: no search queries in profile, skipping")
            return []
        for i, query in enumerate(queries):
            if i > 0:
                await asyncio.sleep(2.0)
            params = {
                "query": query,
                "page": "1",
                "num_pages": "1",
                "date_posted": "week",
            }
            data = await self._get_json(
                "https://jsearch.p.rapidapi.com/search",
                params=params,
                headers=headers,
            )
            if not data or "data" not in data:
                consecutive_failures += 1
                if consecutive_failures >= 2:
                    logger.warning("JSearch: %s consecutive failures, stopping early", consecutive_failures)
                    break
                continue
            consecutive_failures = 0
            for item in data["data"]:
                title = item.get("job_title", "")
                description = item.get("job_description", "")
                location_parts = [
                    item.get("job_city", ""),
                    item.get("job_country", ""),
                ]
                location = ", ".join(p for p in location_parts if p)

                if not _is_uk_or_remote(location):
                    continue

                date_found = item.get("job_posted_at_datetime_utc") or datetime.now(timezone.utc).isoformat()
                jobs.append(Job(
                    title=title,
                    company=item.get("employer_name", ""),
                    location=location,
                    salary_min=item.get("job_min_salary"),
                    salary_max=item.get("job_max_salary"),
                    description=description[:5000],
                    apply_url=item.get("job_apply_link", ""),
                    source=self.name,
                    date_found=date_found,
                ))
        logger.info("JSearch: found %s jobs", len(jobs))
        return jobs
