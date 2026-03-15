"""SmartRecruiters — ATS with free public posting API.

No API key needed. JSON API.
Covers: large enterprise companies (Visa, Bosch, Siemens, Adidas, etc.)
URL: https://developers.smartrecruiters.com/
"""

import re
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.companies import SMARTRECRUITERS_COMPANIES, COMPANY_NAME_OVERRIDES
from src.filters.skill_matcher import get_relevance_keywords

logger = logging.getLogger("job360.sources.smartrecruiters")

_HTML_TAG_RE = re.compile(r"<[^>]+>")


class SmartRecruitersSource(BaseJobSource):
    name = "smartrecruiters"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None):
        super().__init__(session)
        self._companies = companies if companies is not None else SMARTRECRUITERS_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        keywords = get_relevance_keywords()

        for slug in self._companies:
            data = await self._get_json(
                f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                params={"limit": 100},
            )
            if not data or "content" not in data:
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in data["content"]:
                title = item.get("name", "")
                desc = item.get("customField", [])
                # SmartRecruiters uses department/location objects
                dept = item.get("department", {})
                dept_name = dept.get("label", "") if isinstance(dept, dict) else ""
                loc = item.get("location", {})
                if isinstance(loc, dict):
                    city = loc.get("city", "")
                    country = loc.get("country", "")
                    location = f"{city}, {country}".strip(", ") if city else country
                else:
                    location = str(loc) if loc else ""

                text = f"{title} {dept_name}".lower()
                if not any(kw in text for kw in keywords):
                    continue

                ref = item.get("ref", "") or item.get("id", "")
                apply_url = item.get("ref", "")
                if not apply_url or not apply_url.startswith("http"):
                    apply_url = f"https://jobs.smartrecruiters.com/{slug}/{ref}"
                date_found = item.get("releasedDate", "") or datetime.now(timezone.utc).isoformat()
                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location or "Global",
                    description=dept_name,
                    apply_url=apply_url,
                    source=self.name,
                    date_found=date_found,
                ))
        logger.info(f"SmartRecruiters: found {len(jobs)} relevant jobs across {len(self._companies)} companies")
        return jobs
