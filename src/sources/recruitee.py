"""Recruitee — ATS with free public API.

No API key needed. JSON API.
Covers: mid-size companies globally.
URL: https://support.recruitee.com/en/articles/1066286
"""

import re
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.companies import RECRUITEE_COMPANIES, COMPANY_NAME_OVERRIDES
from src.filters.skill_matcher import get_relevance_keywords

logger = logging.getLogger("job360.sources.recruitee")

_HTML_TAG_RE = re.compile(r"<[^>]+>")


class RecruiteeSource(BaseJobSource):
    name = "recruitee"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None):
        super().__init__(session)
        self._companies = companies if companies is not None else RECRUITEE_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        keywords = get_relevance_keywords()

        for slug in self._companies:
            data = await self._get_json(
                f"https://{slug}.recruitee.com/api/offers",
            )
            if not data or "offers" not in data:
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in data["offers"]:
                title = item.get("title", "")
                desc = item.get("description", "")
                plain = _HTML_TAG_RE.sub(" ", desc) if desc else ""

                text = f"{title} {plain}".lower()
                if not any(kw in text for kw in keywords):
                    continue

                location = item.get("location", "") or item.get("city", "")
                url = item.get("careers_url", "") or item.get("url", "")
                if not url:
                    offer_id = item.get("slug", item.get("id", ""))
                    url = f"https://{slug}.recruitee.com/o/{offer_id}" if offer_id else ""
                if not url:
                    continue

                sal_min = item.get("min_salary")
                sal_max = item.get("max_salary")
                try:
                    sal_min = float(sal_min) if sal_min else None
                    sal_max = float(sal_max) if sal_max else None
                except (ValueError, TypeError):
                    sal_min = sal_max = None

                date_found = item.get("published_at", "") or item.get("created_at", "") or datetime.now(timezone.utc).isoformat()
                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    salary_min=sal_min,
                    salary_max=sal_max,
                    description=plain[:500],
                    apply_url=url,
                    source=self.name,
                    date_found=date_found,
                ))
        logger.info(f"Recruitee: found {len(jobs)} relevant jobs across {len(self._companies)} companies")
        return jobs
