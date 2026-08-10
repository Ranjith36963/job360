import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from src.core.companies import COMPANY_NAME_OVERRIDES, PINPOINT_COMPANIES
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.pinpoint")


class PinpointSource(BaseJobSource):
    name = "pinpoint"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else PINPOINT_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            url = f"https://{slug}.pinpointhq.com/postings.json"
            data = await self._get_json(url)
            if not data or not isinstance(data, (list, dict)):
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            postings = data.get("data", data) if isinstance(data, dict) else data
            if not isinstance(postings, list):
                continue
            for item in postings:
                title = item.get("title", "")
                desc = item.get("description", "")
                loc = item.get("location", {})
                if isinstance(loc, dict):
                    location = loc.get("name", str(loc))
                else:
                    location = str(loc) if loc else ""
                # Live schema (verified 2026-08-08): top-level `compensation`
                # is a plain string (e.g. "Competitive"), never the numeric
                # object the old code guarded for — that branch never fired.
                # The real numeric fields are compensation_minimum /
                # compensation_maximum (compensation_currency / _frequency
                # also exist but aren't stored on Job yet).
                salary_min = item.get("compensation_minimum")
                salary_max = item.get("compensation_maximum")

                # deadline_at exists on this endpoint — guard for null, never
                # crash or fabricate.
                deadline = None
                deadline_source = None
                raw_deadline_at = item.get("deadline_at")
                if raw_deadline_at:
                    try:
                        deadline = datetime.fromisoformat(
                            str(raw_deadline_at).replace("Z", "+00:00")
                        ).date().isoformat()
                        deadline_source = "listing"
                    except ValueError:
                        pass

                apply_url = item.get("url", f"https://{slug}.pinpointhq.com/postings/{item.get('id', '')}")
                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=desc[:5000],
                    apply_url=apply_url,
                    source=self.name,
                    date_found=datetime.now(timezone.utc).isoformat(),
                    posted_at=None,
                    # Pinpoint genuinely has no posted-date field anywhere in
                    # its 24-key schema (verified live 2026-08-08), so
                    # "fabricated" is the CORRECT label — it is not an invalid
                    # value, it is a deliberate 4th state that
                    # skill_matcher._recency_points() reads to return 0 and
                    # refuse any recency credit (Batch 2.1 precedence rule).
                    # Downgrading this to "low" would let the job fall through
                    # to `date_found * 0.6` and earn 60% freshness credit for a
                    # date that does not exist. Do not "simplify" this.
                    date_confidence="fabricated",
                    date_posted_raw=None,
                    salary_min=salary_min,
                    salary_max=salary_max,
                    deadline=deadline,
                    deadline_source=deadline_source,
                ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Pinpoint: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs
