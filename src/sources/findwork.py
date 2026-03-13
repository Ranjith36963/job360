"""Findwork — tech/design job aggregator with free API.

Free API key (register at https://findwork.dev).
Aggregates from HN, RemoteOK, WWR, Dribbble, and more.
URL: https://findwork.dev
"""

import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.filters.skill_matcher import get_search_queries, get_search_locations

logger = logging.getLogger("job360.sources.findwork")


class FindworkSource(BaseJobSource):
    name = "findwork"

    def __init__(self, session: aiohttp.ClientSession, api_key: str = ""):
        super().__init__(session)
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.info("Findwork: no API key, skipping")
            return []
        jobs = []
        headers = {"Authorization": f"Token {self._api_key}"}
        queries = get_search_queries(limit=3)
        locations = get_search_locations()[:2] or [""]

        for query in queries:
            params = {
                "search": query,
                "sort_by": "date",
            }
            if locations and locations[0].lower() != "remote":
                params["location"] = locations[0]

            data = await self._get_json(
                "https://findwork.dev/api/jobs/",
                params=params,
                headers=headers,
            )
            if not data or "results" not in data:
                continue
            for item in data["results"]:
                title = item.get("role", "")
                company = item.get("company_name", "")
                url = item.get("url", "")
                if not url:
                    continue
                location = item.get("location", "")
                if item.get("remote"):
                    location = f"{location}, Remote" if location else "Remote"
                desc = item.get("text", "") or item.get("description", "")
                # Strip HTML
                import re
                clean_desc = re.sub(r"<[^>]+>", " ", desc)[:500] if desc else ""
                keywords = item.get("keywords", [])
                date_found = item.get("date_posted", "") or datetime.now(timezone.utc).isoformat()
                jobs.append(Job(
                    title=title,
                    company=company,
                    location=location or "Unknown",
                    description=f"{clean_desc} {' '.join(keywords)}".strip(),
                    apply_url=url,
                    source=self.name,
                    date_found=date_found,
                ))
        logger.info(f"Findwork: found {len(jobs)} jobs")
        return jobs
