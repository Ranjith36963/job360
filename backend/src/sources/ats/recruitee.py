import logging
from datetime import datetime, timezone
from typing import Any, Optional, cast

import aiohttp

from src.core.companies import COMPANY_NAME_OVERRIDES, RECRUITEE_COMPANIES
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.recruitee")


def _coerce_salary(value: Any) -> Optional[float]:
    """Recruitee sends salary.min/max as STRINGS ('0', '25000', '13.40').

    Verified live 2026-08-10 across the 31-slug list: 53 of 671 UK/remote
    offers carry string salaries (first offender: transperfect / "Marketing
    Lead - Legal Solutions", min='0' max='0'). `Job.__post_init__`
    (models.py:92) compares salary_min against an int, so an un-coerced
    string raised TypeError and killed the ENTIRE source mid-loop — the
    scheduler recorded it as a failure (scheduler.py:186) and recruitee
    contributed 0 jobs every run. Coerce here, in the source, so the shared
    Job model keeps its numeric contract.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


class RecruiteeSource(BaseJobSource):
    name = "recruitee"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else RECRUITEE_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            url = f"https://{slug}.recruitee.com/api/offers/"
            data = await self._get_json(url)
            if not data or "offers" not in data:
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in cast(dict[str, Any], data)["offers"]:
                title = item.get("title", "")
                desc = item.get("description", "")
                location = item.get("location", "")
                if not _is_uk_or_remote(location):
                    continue
                apply_url = item.get("careers_url", "") or item.get("url", "")
                now_iso = datetime.now(timezone.utc).isoformat()
                raw_published = item.get("published_at")
                posted_at, confidence = normalize_posted_at(raw_published)

                # Live schema (verified 2026-08-08): salary is a NESTED object,
                # not flat min_salary/max_salary keys — those never existed.
                salary_obj = item.get("salary")
                if not isinstance(salary_obj, dict):
                    salary_obj = {}
                salary_min = _coerce_salary(salary_obj.get("min"))
                salary_max = _coerce_salary(salary_obj.get("max"))

                # close_at is the offer's closing date. Null in the sample, so
                # guard for it — a missing/unparseable value must never crash
                # or fabricate a deadline.
                deadline = None
                deadline_source = None
                raw_close_at = item.get("close_at")
                if raw_close_at:
                    try:
                        deadline = datetime.fromisoformat(
                            str(raw_close_at).replace("Z", "+00:00")
                        ).date().isoformat()
                        deadline_source = "listing"
                    except ValueError:
                        pass

                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=desc[:5000],
                    apply_url=apply_url,
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_published,
                    salary_min=salary_min,
                    salary_max=salary_max,
                    deadline=deadline,
                    deadline_source=deadline_source,
                ))
        logger.info("Recruitee: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs
