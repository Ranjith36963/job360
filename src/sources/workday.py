import re
import logging
from datetime import datetime, timezone, timedelta

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.config.companies import WORKDAY_COMPANIES, COMPANY_NAME_OVERRIDES

logger = logging.getLogger("job360.sources.workday")

# Parse "Posted 3 Days Ago", "Posted Today", "Posted Yesterday", "Posted 30+ Days Ago"
_POSTED_RE = re.compile(r"Posted\s+(\d+)\s+Days?\s+Ago", re.IGNORECASE)


def _parse_posted_on(text: str) -> str:
    """Convert Workday 'Posted X Days Ago' to ISO date string."""
    if not text:
        return datetime.now(timezone.utc).isoformat()
    lower = text.lower()
    if "today" in lower:
        return datetime.now(timezone.utc).isoformat()
    if "yesterday" in lower:
        return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    m = _POSTED_RE.search(text)
    if m:
        days = int(m.group(1))
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return datetime.now(timezone.utc).isoformat()


class WorkdaySource(BaseJobSource):
    name = "workday"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[dict] | None = None, search_config=None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else WORKDAY_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_keys = set()
        for entry in self._companies:
            tenant = entry["tenant"]
            wd = entry["wd"]
            site = entry["site"]
            company_name = COMPANY_NAME_OVERRIDES.get(
                tenant, entry.get("name", tenant.replace("-", " ").title())
            )
            base_url = f"https://{tenant}.{wd}.myworkdayjobs.com"
            api_url = f"{base_url}/wday/cxs/{tenant}/{site}/jobs"

            company_failed = False
            for query in self.job_titles[:8]:
                if company_failed:
                    break
                body = {
                    "appliedFacets": {},
                    "searchText": query,
                    "limit": 20,
                    "offset": 0,
                }
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                data = await self._post_json(api_url, body=body, headers=headers)
                if data is None:
                    # API rejected request (422/404/etc) — skip remaining queries for this company
                    logger.debug("Workday [%s]: API unavailable, skipping", company_name)
                    company_failed = True
                    continue
                if "jobPostings" not in data:
                    continue
                for item in data["jobPostings"]:
                    title = item.get("title", "")
                    location = item.get("locationsText", "")
                    text = f"{title} {location}".lower()
                    if not any(kw in text for kw in self.relevance_keywords):
                        continue
                    if not _is_uk_or_remote(location):
                        continue
                    ext_path = item.get("externalPath", "")
                    apply_url = f"{base_url}/en-US{ext_path}" if ext_path else ""
                    dedup_key = (tenant, title.lower())
                    if dedup_key in seen_keys:
                        continue
                    seen_keys.add(dedup_key)
                    posted_on = item.get("postedOn", "")
                    jobs.append(Job(
                        title=title,
                        company=company_name,
                        location=location,
                        description="",
                        apply_url=apply_url,
                        source=self.name,
                        date_found=_parse_posted_on(posted_on),
                    ))

        logger.info("Workday: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs
