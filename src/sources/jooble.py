"""Jooble — global job aggregator with free API.

Free API key (register at https://jooble.org/api/about).
Covers 70+ countries, millions of job listings.
URL: https://jooble.org/api/about
"""

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.filters.skill_matcher import get_search_queries, get_search_locations

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
        seen_urls: set[str] = set()
        queries = get_search_queries(limit=3)
        locations = get_search_locations()[:2] or [""]

        for query in queries:
            for loc in locations:
                body = {
                    "keywords": query,
                    "location": loc,
                    "page": "1",
                }
                data = await self._post_json(
                    f"https://jooble.org/api/{self._api_key}",
                    body=body,
                )
                if not data or "jobs" not in data:
                    continue
                for item in data["jobs"]:
                    url = item.get("link", "")
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
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
                        location=item.get("location", loc),
                        description=item.get("snippet", ""),
                        apply_url=url,
                        source=self.name,
                        date_found=date_found,
                        salary_min=salary_min,
                        salary_max=salary_max,
                    ))
                await asyncio.sleep(1)
        logger.info(f"Jooble: found {len(jobs)} jobs")
        return jobs
