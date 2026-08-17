import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional, cast

import aiohttp

from src.core.companies import COMPANY_NAME_OVERRIDES, WORKABLE_COMPANIES
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.workable")

_HTML_TAG_RE = re.compile(r"<[^>]+>")


class WorkableSource(BaseJobSource):
    name = "workable"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else WORKABLE_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            # Job-understanding fix (2026-08-16): the POST /api/v2/.../jobs
            # endpoint (old code) returns `description` empty on every row
            # (verified live, 2026-08-08) AND caps at 10 results per page with
            # no cursor this source ever followed — abm-careers alone has 178
            # postings, so at most 10 were ever read.
            # The public widget endpoint `GET /api/v1/widget/accounts/{slug}`
            # (no auth, verified live 2026-08-16) returns EVERY posting in one
            # call — abm-careers: 178/178 — each carrying a real HTML
            # `description` (huggingface sample: 1,800+ chars), plus
            # `employment_type` and `experience` (seniority-ish free text)
            # neither of which the old endpoint exposed at all.
            url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
            data = await self._get_json(url, params={"details": "true"})
            if not data or "jobs" not in data:
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in cast(dict[str, Any], data)["jobs"]:
                title = item.get("title", "")
                desc = _HTML_TAG_RE.sub(" ", item.get("description") or "").strip()
                city = item.get("city", "")
                country = item.get("country", "")
                location = ", ".join(p for p in (city, country) if p)
                apply_url = (
                    item.get("application_url")
                    or item.get("shortlink")
                    or item.get("url", "")
                )
                raw_published = item.get("published_on") or item.get("created_at")
                posted_at, confidence = normalize_posted_at(raw_published)
                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=desc[:5000],
                    apply_url=apply_url,
                    source=self.name,
                    date_found=datetime.now(timezone.utc).isoformat(),
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_published,
                    # Raw upstream values — services/shelf_gate.py normalises.
                    employment_type=item.get("employment_type"),
                    seniority=item.get("experience"),
                ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Workable: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs
