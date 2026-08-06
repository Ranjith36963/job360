import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, cast

import aiohttp

from src.core.companies import COMPANY_NAME_OVERRIDES, WORKDAY_COMPANIES
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.workday")

# Parse "Posted 3 Days Ago", "Posted Today", "Posted Yesterday", "Posted 30+ Days Ago"
_POSTED_RE = re.compile(r"Posted\s+(\d+)\s+Days?\s+Ago", re.IGNORECASE)
# Strip HTML tags from the CXS detail endpoint's jobDescription.
_DESC_TAG_RE = re.compile(r"<[^>]+>")
# Detail-fetch budget per RUN (2026-08-06) — same timeout regression as
# smartrecruiters: uncapped detail fetches blew the 240s ATS ceiling in the
# nightly union refresh and zeroed the source (537 jobs before). Past-cap jobs
# keep empty descriptions; the description-backfill-on-refetch (PR #232) fills
# them across later runs.
_MAX_DETAIL_FETCHES = 40


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

    def __init__(self, session: aiohttp.ClientSession, companies: list[dict[str, Any]] | None = None, search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else WORKDAY_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_keys = set()
        detail_budget = _MAX_DETAIL_FETCHES
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
                for item in cast(dict[str, Any], data)["jobPostings"]:
                    title = item.get("title", "")
                    location = item.get("locationsText", "")
                    if not _is_uk_or_remote(location):
                        continue
                    ext_path = item.get("externalPath", "")
                    apply_url = f"{base_url}/en-US{ext_path}" if ext_path else ""
                    dedup_key = (tenant, title.lower())
                    if dedup_key in seen_keys:
                        continue
                    seen_keys.add(dedup_key)
                    posted_on = item.get("postedOn", "")
                    now_iso = datetime.now(timezone.utc).isoformat()
                    # Workday postedOn is a relative string ("Posted 3 Days
                    # Ago"); parsed value is approximate — medium confidence.
                    if posted_on:
                        parsed_posted_at = _parse_posted_on(posted_on)
                        confidence = "medium"
                    else:
                        parsed_posted_at = None
                        confidence = "low"
                    # Job-understanding fix (2026-08-05): the search response
                    # has no description (537 prod jobs, 100% empty). The CXS
                    # detail endpoint at {base}/wday/cxs/{tenant}/{site}{path}
                    # returns jobPostingInfo.jobDescription (verified live:
                    # 12,746 chars for an astrazeneca posting). Only UK/remote
                    # survivors reach this point, so the request count matches
                    # what we keep. Failure degrades to "", never drops the job.
                    description = ""
                    if detail_budget > 0:
                        detail_budget -= 1
                        description = await self._fetch_job_description(
                            base_url, tenant, site, ext_path
                        )
                    jobs.append(Job(
                        title=title,
                        company=company_name,
                        location=location,
                        description=description,
                        apply_url=apply_url,
                        source=self.name,
                        date_found=now_iso,
                        posted_at=parsed_posted_at,
                        date_confidence=confidence,
                        date_posted_raw=posted_on or None,
                    ))

        logger.info("Workday: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs

    async def _fetch_job_description(
        self, base_url: str, tenant: str, site: str, ext_path: str
    ) -> str:
        """Fetch one posting's ``jobPostingInfo.jobDescription`` (HTML → text).

        Returns ``""`` on any failure — a missing description is a data gap the
        scorer treats neutrally, not a reason to drop the job.
        """
        if not ext_path:
            return ""
        detail = await self._get_json(f"{base_url}/wday/cxs/{tenant}/{site}{ext_path}")
        if not isinstance(detail, dict):
            return ""
        desc = (detail.get("jobPostingInfo") or {}).get("jobDescription") or ""
        if not desc:
            return ""
        return _DESC_TAG_RE.sub(" ", str(desc))[:5000].strip()
