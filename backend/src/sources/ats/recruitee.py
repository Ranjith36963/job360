import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.core.companies import RECRUITEE_COMPANIES, COMPANY_NAME_OVERRIDES

logger = logging.getLogger("job360.sources.recruitee")


class RecruiteeSource(BaseJobSource):
    name = "recruitee"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config=None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else RECRUITEE_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            url = f"https://{slug}.recruitee.com/api/offers/"
            data = await self._get_json(url)
            if not data or "offers" not in data:
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in data["offers"]:
                title = item.get("title", "")
                desc = item.get("description", "")
                location = item.get("location", "")
                if not _is_uk_or_remote(location):
                    continue
                apply_url = item.get("careers_url", "") or item.get("url", "")
                date_found = item.get("published_at") or datetime.now(timezone.utc).isoformat()
                salary_min = item.get("min_salary")
                salary_max = item.get("max_salary")
                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=desc[:5000],
                    apply_url=apply_url,
                    source=self.name,
                    date_found=date_found,
                    salary_min=salary_min,
                    salary_max=salary_max,
                ))
        logger.info("Recruitee: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs
