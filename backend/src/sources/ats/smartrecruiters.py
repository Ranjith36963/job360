import re
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.core.companies import SMARTRECRUITERS_COMPANIES, COMPANY_NAME_OVERRIDES

logger = logging.getLogger("job360.sources.smartrecruiters")

_HTML_TAG_RE = re.compile(r"<[^>]+>")


class SmartRecruitersSource(BaseJobSource):
    name = "smartrecruiters"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config=None):
        super().__init__(session, search_config=search_config)
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
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("SmartRecruiters: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs
