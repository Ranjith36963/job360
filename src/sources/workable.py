import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.companies import WORKABLE_COMPANIES, COMPANY_NAME_OVERRIDES
from src.config.keywords import RELEVANCE_KEYWORDS

logger = logging.getLogger("job360.sources.workable")


class WorkableSource(BaseJobSource):
    name = "workable"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None):
        super().__init__(session)
        self._companies = companies if companies is not None else WORKABLE_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            url = f"https://apply.workable.com/api/v2/accounts/{slug}/jobs"
            data = await self._post_json(url, body={"query": "", "location": [], "department": [], "worktype": []})
            if not data or "results" not in data:
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in data["results"]:
                title = item.get("title", "")
                desc = item.get("shortDescription", "")
                text = f"{title} {desc}".lower()
                if not any(kw in text for kw in RELEVANCE_KEYWORDS):
                    continue
                loc = item.get("location", {})
                if isinstance(loc, dict):
                    location = f"{loc.get('city', '')}, {loc.get('country', '')}".strip(", ")
                else:
                    location = str(loc)
                shortcode = item.get("shortcode", "")
                apply_url = f"https://apply.workable.com/{slug}/j/{shortcode}/"
                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=desc,
                    apply_url=apply_url,
                    source=self.name,
                    date_found=datetime.now(timezone.utc).isoformat(),
                ))
        logger.info(f"Workable: found {len(jobs)} relevant jobs across {len(self._companies)} companies")
        return jobs
