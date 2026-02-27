import re
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource
from src.config.companies import GREENHOUSE_COMPANIES
from src.config.keywords import RELEVANCE_KEYWORDS

logger = logging.getLogger("job360.sources.greenhouse")

_HTML_TAG_RE = re.compile(r"<[^>]+>")


class GreenhouseSource(BaseJobSource):
    name = "greenhouse"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None):
        super().__init__(session)
        self._companies = companies if companies is not None else GREENHOUSE_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
            data = await self._get_json(url)
            if not data or "jobs" not in data:
                continue
            company_name = slug.replace("-", " ").title()
            for item in data["jobs"]:
                title = item.get("title", "")
                content = item.get("content", "")
                plain = _HTML_TAG_RE.sub(" ", content)
                text = f"{title} {plain}".lower()
                if not any(kw in text for kw in RELEVANCE_KEYWORDS):
                    continue
                loc = item.get("location", {})
                location = loc.get("name", "") if isinstance(loc, dict) else str(loc)
                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=plain[:5000],
                    apply_url=item.get("absolute_url", ""),
                    source=self.name,
                    date_found=datetime.now(timezone.utc).isoformat(),
                ))
        logger.info(f"Greenhouse: found {len(jobs)} relevant jobs across {len(self._companies)} companies")
        return jobs
