import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.findwork")


class FindworkSource(BaseJobSource):
    name = "findwork"

    def __init__(self, session: aiohttp.ClientSession, api_key: str = "", search_config=None):
        super().__init__(session, search_config=search_config)
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.info("Findwork: no FINDWORK_API_KEY, skipping")
            return []

        jobs = []
        headers = {"Authorization": f"Token {self._api_key}"}
        search_term = self.search_queries[0] if self.search_queries else self.job_titles[0] if self.job_titles else "software engineer"
        params = {
            "search": search_term,
            "location": "united kingdom",
        }
        data = await self._get_json(
            "https://findwork.dev/api/jobs/",
            params=params,
            headers=headers,
        )
        if not data or "results" not in data:
            return []

        for item in data["results"]:
            title = item.get("role", "")
            description = item.get("text", "")
            location = item.get("location", "")
            text = f"{title} {description}".lower()

            if not self._relevance_match(text):
                continue
            if not _is_uk_or_remote(location):
                continue

            date_found = item.get("date_posted") or datetime.now(timezone.utc).isoformat()
            apply_url = item.get("url", "")

            jobs.append(Job(
                title=title,
                company=item.get("company_name", ""),
                location=location,
                description=description[:5000],
                apply_url=apply_url,
                source=self.name,
                date_found=date_found,
            ))

        logger.info(f"Findwork: found {len(jobs)} relevant jobs")
        return jobs
