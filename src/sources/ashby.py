import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.companies import ASHBY_COMPANIES, COMPANY_NAME_OVERRIDES
from src.config.keywords import RELEVANCE_KEYWORDS

logger = logging.getLogger("job360.sources.ashby")


class AshbySource(BaseJobSource):
    name = "ashby"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None):
        super().__init__(session)
        self._companies = companies if companies is not None else ASHBY_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
            data = await self._get_json(url)
            if not data or "jobs" not in data:
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in data["jobs"]:
                title = item.get("title", "")
                desc = item.get("descriptionPlain", "")
                text = f"{title} {desc}".lower()
                if not any(kw in text for kw in RELEVANCE_KEYWORDS):
                    continue
                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=item.get("location", ""),
                    description=desc[:5000],
                    apply_url=item.get("applicationUrl", item.get("jobUrl", "")),
                    source=self.name,
                    date_found=datetime.now(timezone.utc).isoformat(),
                ))
        logger.info(f"Ashby: found {len(jobs)} relevant jobs across {len(self._companies)} companies")
        return jobs
