import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.keywords import JOB_TITLES

logger = logging.getLogger("job360.sources.adzuna")


class AdzunaSource(BaseJobSource):
    name = "adzuna"

    def __init__(self, session: aiohttp.ClientSession, app_id: str = "", app_key: str = ""):
        super().__init__(session)
        self._app_id = app_id
        self._app_key = app_key

    @property
    def is_configured(self) -> bool:
        return bool(self._app_id and self._app_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.info("Adzuna: no API keys, skipping")
            return []
        jobs = []
        queries = JOB_TITLES
        for query in queries:
            params = {
                "app_id": self._app_id,
                "app_key": self._app_key,
                "what": query,
                "results_per_page": 50,
                "max_days_old": 14,
                "content-type": "application/json",
            }
            data = await self._get_json(
                "https://api.adzuna.com/v1/api/jobs/gb/search/1",
                params=params,
            )
            if not data or "results" not in data:
                continue
            for item in data["results"]:
                company = item.get("company", {})
                location = item.get("location", {})
                date_found = item.get("created") or datetime.now(timezone.utc).isoformat()
                jobs.append(Job(
                    title=item.get("title", ""),
                    company=company.get("display_name", "") if isinstance(company, dict) else str(company),
                    location=location.get("display_name", "") if isinstance(location, dict) else str(location),
                    salary_min=item.get("salary_min"),
                    salary_max=item.get("salary_max"),
                    description=item.get("description", ""),
                    apply_url=item.get("redirect_url", ""),
                    source=self.name,
                    date_found=date_found,
                ))
        logger.info(f"Adzuna: found {len(jobs)} jobs")
        return jobs
