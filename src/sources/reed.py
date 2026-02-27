import base64
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.keywords import JOB_TITLES, LOCATIONS

logger = logging.getLogger("job360.sources.reed")


class ReedSource(BaseJobSource):
    name = "reed"

    def __init__(self, session: aiohttp.ClientSession, api_key: str = ""):
        super().__init__(session)
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.info("Reed: no API key, skipping")
            return []
        jobs = []
        auth = base64.b64encode(f"{self._api_key}:".encode()).decode()
        headers = {"Authorization": f"Basic {auth}"}
        # Search top job titles in key locations
        queries = JOB_TITLES[:5]
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
                    jobs.append(Job(
                        title=item.get("jobTitle", ""),
                        company=item.get("employerName", ""),
                        location=item.get("locationName", ""),
                        salary_min=item.get("minimumSalary"),
                        salary_max=item.get("maximumSalary"),
                        description=item.get("jobDescription", ""),
                        apply_url=f"https://www.reed.co.uk/jobs/{item.get('jobId', '')}",
                        source=self.name,
                        date_found=datetime.now(timezone.utc).isoformat(),
                    ))
        logger.info(f"Reed: found {len(jobs)} jobs")
        return jobs
