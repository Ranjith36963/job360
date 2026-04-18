import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.findwork")


class FindworkSource(BaseJobSource):
    name = "findwork"
    category = "keyed_api"

    def __init__(self, session: aiohttp.ClientSession, api_key: str = "", search_config=None):
        super().__init__(session, search_config=search_config)
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.warning("Findwork: no FINDWORK_API_KEY, skipping")
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

            if not _is_uk_or_remote(location):
                continue

            now_iso = datetime.now(timezone.utc).isoformat()
            raw_posted = item.get("date_posted")
            posted_at = raw_posted if raw_posted else None
            confidence = "high" if raw_posted else "low"
            apply_url = item.get("url", "")

            jobs.append(Job(
                title=title,
                company=item.get("company_name", ""),
                location=location,
                description=description[:5000],
                apply_url=apply_url,
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_posted,
            ))

        logger.info("Findwork: found %s relevant jobs", len(jobs))
        return jobs
