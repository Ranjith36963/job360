import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource

logger = logging.getLogger("job360.sources.careerjet")


class CareerjetSource(BaseJobSource):
    name = "careerjet"

    def __init__(self, session: aiohttp.ClientSession, affid: str = "", search_config=None):
        super().__init__(session, search_config=search_config)
        self._affid = affid

    @property
    def is_configured(self) -> bool:
        return bool(self._affid)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.info("Careerjet: no CAREERJET_AFFID, skipping")
            return []

        jobs = []
        seen_urls = set()

        for query in (self.search_queries[:6] or self.job_titles[:6]):
            params = {
                "keywords": query,
                "location": "United Kingdom",
                "affid": self._affid,
                "locale_code": "en_GB",
                "pagesize": "50",
                "page": "1",
                "sort": "date",
            }
            data = await self._get_json(
                "https://search.api.careerjet.net/v4/query",
                params=params,
            )
            if not data or "jobs" not in data:
                continue

            for item in data["jobs"]:
                title = item.get("title", "")
                description = item.get("description", "")
                text = f"{title} {description}".lower()

                if not self._relevance_match(text):
                    continue

                apply_url = item.get("url", "")
                if apply_url in seen_urls:
                    continue
                seen_urls.add(apply_url)

                date_found = item.get("date") or datetime.now(timezone.utc).isoformat()

                # Parse salary if available
                salary = item.get("salary", "")
                salary_min = item.get("salary_min")
                salary_max = item.get("salary_max")
                if salary_min is not None:
                    try:
                        salary_min = float(salary_min)
                    except (ValueError, TypeError):
                        salary_min = None
                if salary_max is not None:
                    try:
                        salary_max = float(salary_max)
                    except (ValueError, TypeError):
                        salary_max = None

                jobs.append(Job(
                    title=title,
                    company=item.get("company", ""),
                    location=item.get("locations", ""),
                    description=description[:5000],
                    apply_url=apply_url,
                    source=self.name,
                    date_found=date_found,
                    salary_min=salary_min,
                    salary_max=salary_max,
                ))

        logger.info(f"Careerjet: found {len(jobs)} relevant jobs")
        return jobs
