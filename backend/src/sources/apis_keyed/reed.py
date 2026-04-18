import base64
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.reed")


class ReedSource(BaseJobSource):
    name = "reed"
    category = "keyed_api"

    def __init__(self, session: aiohttp.ClientSession, api_key: str = "", search_config=None):
        super().__init__(session, search_config=search_config)
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.warning("Reed: no API key, skipping")
            return []
        jobs = []
        auth = base64.b64encode(f"{self._api_key}:".encode()).decode()
        headers = {"Authorization": f"Basic {auth}"}
        # Search top job titles in key locations
        queries = self.job_titles[:12]
        locations = ["London", "UK", "Remote"]
        for query in queries:
            for loc in locations:
                params = {
                    "keywords": query,
                    "locationName": loc,
                    "resultsToTake": 50,
                }
                data = await self._get_json(
                    "https://www.reed.co.uk/api/1.0/search",
                    params=params,
                    headers=headers,
                )
                if not data or "results" not in data:
                    continue
                for item in data["results"]:
                    now_iso = datetime.now(timezone.utc).isoformat()
                    raw_date = item.get("date") or item.get("datePosted")
                    posted_at = raw_date if raw_date else None
                    confidence = "high" if raw_date else "low"
                    jobs.append(Job(
                        title=item.get("jobTitle", ""),
                        company=item.get("employerName", ""),
                        location=item.get("locationName", ""),
                        salary_min=item.get("minimumSalary"),
                        salary_max=item.get("maximumSalary"),
                        description=item.get("jobDescription", ""),
                        apply_url=f"https://www.reed.co.uk/jobs/{item.get('jobId', '')}",
                        source=self.name,
                        date_found=now_iso,
                        posted_at=posted_at,
                        date_confidence=confidence,
                        date_posted_raw=raw_date,
                    ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Reed: found %s jobs", len(jobs))
        return jobs
