import re
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.companies import SMARTRECRUITERS_COMPANIES, COMPANY_NAME_OVERRIDES
from src.config.keywords import RELEVANCE_KEYWORDS

logger = logging.getLogger("job360.sources.smartrecruiters")

_HTML_TAG_RE = re.compile(r"<[^>]+>")


class SmartRecruitersSource(BaseJobSource):
    name = "smartrecruiters"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None):
        super().__init__(session)
        self._companies = companies if companies is not None else SMARTRECRUITERS_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
            data = await self._get_json(url, params={"limit": "100"})
            if not data or "content" not in data:
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in data["content"]:
                title = item.get("name", "")
                text = title.lower()
                if not any(kw in text for kw in RELEVANCE_KEYWORDS):
                    continue
                loc = item.get("location", {})
                if isinstance(loc, dict):
                    city = loc.get("city", "")
                    country = loc.get("country", "")
                    location = f"{city}, {country}".strip(", ")
                else:
                    location = str(loc)
                ref = item.get("ref", "")
                apply_url = ref if ref.startswith("http") else f"https://jobs.smartrecruiters.com/{slug}/{item.get('id', '')}"
                date_found = item.get("releasedDate") or datetime.now(timezone.utc).isoformat()
                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description="",
                    apply_url=apply_url,
                    source=self.name,
                    date_found=date_found,
                ))
        logger.info(f"SmartRecruiters: found {len(jobs)} relevant jobs across {len(self._companies)} companies")
        return jobs
