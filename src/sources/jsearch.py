import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.filters.skill_matcher import get_search_queries, get_search_locations

logger = logging.getLogger("job360.sources.jsearch")


class JSearchSource(BaseJobSource):
    name = "jsearch"

    def __init__(self, session: aiohttp.ClientSession, api_key: str = ""):
        super().__init__(session)
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.info("JSearch: no API key, skipping")
            return []
        jobs = []
        headers = {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
        }
        # Build queries from profile: "<title> <location>" (limited to 3 for free tier)
        titles = get_search_queries(limit=3)
        locations = get_search_locations()[:2] or ["Remote"]
        jsearch_queries = [f"{t} {locations[0]}" for t in titles] if locations else titles
        for query in jsearch_queries[:3]:
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
                continue
            for item in data["data"]:
                location_parts = [
                    item.get("job_city", ""),
                    item.get("job_country", ""),
                ]
                location = ", ".join(p for p in location_parts if p)
                date_found = item.get("job_posted_at_datetime_utc") or datetime.now(timezone.utc).isoformat()
                jobs.append(Job(
                    title=item.get("job_title", ""),
                    company=item.get("employer_name", ""),
                    location=location,
                    salary_min=item.get("job_min_salary"),
                    salary_max=item.get("job_max_salary"),
                    description=item.get("job_description", ""),
                    apply_url=item.get("job_apply_link", ""),
                    source=self.name,
                    date_found=date_found,
                ))
        logger.info(f"JSearch: found {len(jobs)} jobs")
        return jobs
