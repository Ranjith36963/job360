import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.companies import LEVER_COMPANIES
from src.config.keywords import RELEVANCE_KEYWORDS

logger = logging.getLogger("job360.sources.lever")


class LeverSource(BaseJobSource):
    name = "lever"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None):
        super().__init__(session)
        self._companies = companies if companies is not None else LEVER_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            url = f"https://api.lever.co/v0/postings/{slug}"
            params = {"mode": "json"}
            data = await self._get_json(url, params=params)
            if not data or not isinstance(data, list):
                continue
            company_name = slug.replace("-", " ").title()
            for item in data:
                title = item.get("text", "")
                desc = item.get("descriptionPlain", item.get("description", ""))
                text = f"{title} {desc}".lower()
                if not any(kw in text for kw in RELEVANCE_KEYWORDS):
                    continue
                categories = item.get("categories", {})
                location = categories.get("location", "") if isinstance(categories, dict) else ""
                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=desc[:5000],
                    apply_url=item.get("hostedUrl", ""),
                    source=self.name,
                    date_found=datetime.now(timezone.utc).isoformat(),
                ))
        logger.info(f"Lever: found {len(jobs)} relevant jobs across {len(self._companies)} companies")
        return jobs
