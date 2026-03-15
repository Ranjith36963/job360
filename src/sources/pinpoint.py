import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.companies import PINPOINT_COMPANIES, COMPANY_NAME_OVERRIDES
from src.config.keywords import RELEVANCE_KEYWORDS

logger = logging.getLogger("job360.sources.pinpoint")


class PinpointSource(BaseJobSource):
    name = "pinpoint"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None):
        super().__init__(session)
        self._companies = companies if companies is not None else PINPOINT_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            url = f"https://{slug}.pinpointhq.com/postings.json"
            data = await self._get_json(url)
            if not data or not isinstance(data, (list, dict)):
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            postings = data.get("data", data) if isinstance(data, dict) else data
            if not isinstance(postings, list):
                continue
            for item in postings:
                title = item.get("title", "")
                desc = item.get("description", "")
                text = f"{title} {desc}".lower()
                if not any(kw in text for kw in RELEVANCE_KEYWORDS):
                    continue
                loc = item.get("location", {})
                if isinstance(loc, dict):
                    location = loc.get("name", str(loc))
                else:
                    location = str(loc) if loc else ""
                comp = item.get("compensation", {})
                salary_min = None
                salary_max = None
                if isinstance(comp, dict):
                    salary_min = comp.get("min")
                    salary_max = comp.get("max")
                apply_url = item.get("url", f"https://{slug}.pinpointhq.com/postings/{item.get('id', '')}")
                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=desc[:5000],
                    apply_url=apply_url,
                    source=self.name,
                    date_found=datetime.now(timezone.utc).isoformat(),
                    salary_min=salary_min,
                    salary_max=salary_max,
                ))
        logger.info(f"Pinpoint: found {len(jobs)} relevant jobs across {len(self._companies)} companies")
        return jobs
