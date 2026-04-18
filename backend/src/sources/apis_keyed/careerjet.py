import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.careerjet")


class CareerjetSource(BaseJobSource):
    name = "careerjet"
    category = "keyed_api"

    def __init__(self, session: aiohttp.ClientSession, affid: str = "", search_config=None):
        super().__init__(session, search_config=search_config)
        self._affid = affid

    @property
    def is_configured(self) -> bool:
        return bool(self._affid)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.warning("Careerjet: no CAREERJET_AFFID, skipping")
            return []

        jobs = []
        seen_urls = set()

        for query in self.job_titles[:6]:
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

                apply_url = item.get("url", "")
                if apply_url in seen_urls:
                    continue
                seen_urls.add(apply_url)

                now_iso = datetime.now(timezone.utc).isoformat()
                raw_date = item.get("date")
                posted_at = raw_date if raw_date else None
                confidence = "high" if raw_date else "low"

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
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_date,
                    salary_min=salary_min,
                    salary_max=salary_max,
                ))

        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Careerjet: found %s relevant jobs", len(jobs))
        return jobs
