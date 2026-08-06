import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional, cast

import aiohttp

from src.core.companies import COMPANY_NAME_OVERRIDES, SMARTRECRUITERS_COMPANIES
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.smartrecruiters")

_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Detail-fetch budget per RUN (2026-08-06). The 2026-08-05 detail pass had NO
# cap: the nightly union refresh detail-fetched every UK posting across every
# company, blew the 240s ATS fetch timeout, and the whole source was recorded
# as errored with ZERO jobs stored (it produced 150 before). Jobs past the cap
# keep an empty description — the description-backfill-on-refetch (PR #232)
# fills them across later runs.
_MAX_DETAIL_FETCHES = 60

class SmartRecruitersSource(BaseJobSource):
    name = "smartrecruiters"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else SMARTRECRUITERS_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        detail_budget = _MAX_DETAIL_FETCHES
        for slug in self._companies:
            url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
            data = await self._get_json(url, params={"limit": "100"})
            if not data or "content" not in data:
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in cast(dict[str, Any], data)["content"]:
                title = item.get("name", "")
                loc = item.get("location", {})
                if isinstance(loc, dict):
                    city = loc.get("city", "")
                    country = loc.get("country", "")
                    location = f"{city}, {country}".strip(", ")
                else:
                    location = str(loc)
                ref = item.get("ref", "")
                apply_url = (
                    ref if ref.startswith("http")
                    else f"https://jobs.smartrecruiters.com/{slug}/{item.get('id', '')}"
                )
                now_iso = datetime.now(timezone.utc).isoformat()
                raw_released = item.get("releasedDate")
                posted_at, confidence = normalize_posted_at(raw_released)

                job = Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description="",
                    apply_url=apply_url,
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_released,
                )
                # Job-understanding fix (2026-08-05): the list endpoint has no
                # posting text (150 prod jobs, 100% empty descriptions). The
                # public detail endpoint carries the full jobAd sections
                # (verified live: 6,445 chars for a wise posting). Only
                # UK/remote-relevant jobs are detail-fetched, so the extra
                # request count matches what we actually keep. A failed detail
                # fetch degrades to the empty description, never drops the job.
                if _is_uk_or_remote(location) and detail_budget > 0:
                    detail_budget -= 1
                    job.description = await self._fetch_posting_text(
                        slug, str(item.get("id", ""))
                    )
                jobs.append(job)
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("SmartRecruiters: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs

    async def _fetch_posting_text(self, slug: str, posting_id: str) -> str:
        """Fetch one posting's full text from the public detail endpoint.

        Concatenates the ``jobAd.sections`` texts (companyDescription,
        jobDescription, qualifications, additionalInformation), tag-stripped.
        Returns ``""`` on any failure — absence of text is a data gap, not an
        error (the scorer's unknown-handling treats it neutrally).
        """
        if not posting_id:
            return ""
        detail = await self._get_json(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}"
        )
        if not isinstance(detail, dict):
            return ""
        sections = (detail.get("jobAd") or {}).get("sections") or {}
        parts: list[str] = []
        for key in ("jobDescription", "qualifications", "additionalInformation", "companyDescription"):
            sec = sections.get(key)
            text = (sec or {}).get("text") if isinstance(sec, dict) else None
            if text:
                parts.append(_HTML_TAG_RE.sub(" ", str(text)))
        return " ".join(parts)[:5000].strip()
