import re
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.core.companies import GREENHOUSE_COMPANIES, COMPANY_NAME_OVERRIDES

logger = logging.getLogger("job360.sources.greenhouse")

_HTML_TAG_RE = re.compile(r"<[^>]+>")


class GreenhouseSource(BaseJobSource):
    name = "greenhouse"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config=None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else GREENHOUSE_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
            data = await self._get_json(url)
            if not data or "jobs" not in data:
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in data["jobs"]:
                title = item.get("title", "")
                content = item.get("content", "")
                plain = _HTML_TAG_RE.sub(" ", content)
                loc = item.get("location", {})
                location = loc.get("name", "") if isinstance(loc, dict) else str(loc)
                if not _is_uk_or_remote(location):
                    continue
                # Greenhouse public API exposes only `updated_at` (edit timestamp),
                # never a `created_at`. Using it as posted_at would contaminate the
                # 24h bucket every time an employer tweaks salary or department.
                # Keep in date_posted_raw for audit; posted_at=None + confidence=low.
                now_iso = datetime.now(timezone.utc).isoformat()
                raw_updated_at = item.get("updated_at")
                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=plain[:5000],
                    apply_url=item.get("absolute_url", ""),
                    source=self.name,
                    date_found=now_iso,
                    posted_at=None,
                    date_confidence="low",
                    date_posted_raw=raw_updated_at,
                ))
        logger.info("Greenhouse: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs
