import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.companies import RECRUITEE_COMPANIES, COMPANY_NAME_OVERRIDES
from src.config.keywords import RELEVANCE_KEYWORDS

logger = logging.getLogger("job360.sources.recruitee")


class RecruiteeSource(BaseJobSource):
    name = "recruitee"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None):
        super().__init__(session)
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
                text = f"{title} {desc}".lower()
                if not any(kw in text for kw in RELEVANCE_KEYWORDS):
                    continue
                location = item.get("location", "")
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
        logger.info(f"Recruitee: found {len(jobs)} relevant jobs across {len(self._companies)} companies")
        return jobs
