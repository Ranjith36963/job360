import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.core.settings import MAX_RESULTS_PER_SOURCE

logger = logging.getLogger("job360.sources.yc_companies")


class YCCompaniesSource(BaseJobSource):
    """YC Company Directory — generates career page links for UK-based YC companies."""
    name = "yc_companies"
    category = "free_json"

    async def fetch_jobs(self) -> list[Job]:
        data = await self._get_json("https://yc-oss.github.io/api/companies/all.json")
        if not data or not isinstance(data, list):
            return []

        jobs = []
        now = datetime.now(timezone.utc).isoformat()

        for company in data:
            locations = company.get("locations", []) or []
            loc_str = " ".join(locations) if isinstance(locations, list) else str(locations)

            if not _is_uk_or_remote(loc_str):
                continue

            desc = company.get("long_description", "") or company.get("one_liner", "") or ""
            tags = " ".join(company.get("tags", []) or [])
            industries = " ".join(company.get("industries", []) or [])
            check_text = f"{desc} {tags} {industries}".lower()

            name = company.get("name", "Unknown")
            website = company.get("website", "")
            slug = company.get("slug", "")
            apply_url = website or f"https://www.ycombinator.com/companies/{slug}"

            jobs.append(Job(
                title=f"{name} - Careers (YC Company)",
                company=name,
                location=loc_str.strip() or "Remote",
                description=desc[:5000],
                apply_url=apply_url,
                source=self.name,
                date_found=now,
            ))

        # Cap results — these are company career page links, not individual job postings
        if len(jobs) > MAX_RESULTS_PER_SOURCE:
            logger.info("YC Companies: capping %s results to %s", len(jobs), MAX_RESULTS_PER_SOURCE)
            jobs = jobs[:MAX_RESULTS_PER_SOURCE]

        logger.info("YC Companies: found %s relevant UK companies", len(jobs))
        return jobs
