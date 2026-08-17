import logging
from datetime import datetime, timezone
from typing import Any, Optional, cast

import aiohttp

from src.core.companies import ASHBY_COMPANIES, COMPANY_NAME_OVERRIDES
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.ashby")

# Ashby's own interval spelling -> the four period spellings models.py:122
# documents as this field's contract ("hourly"|"daily"|"monthly"|"annual").
# Verified live 2026-08-16 (cohere board): compensation intervals only ever
# read "1 YEAR" / "1 HOUR" / "1 MONTH" / "1 DAY" — a literal word-for-word
# translation of one closed field, not a unit conversion or a guess.
_INTERVAL_MAP = {
    "1 YEAR": "annual",
    "1 HOUR": "hourly",
    "1 MONTH": "monthly",
    "1 DAY": "daily",
}


def _extract_salary(compensation: Any) -> tuple[Optional[float], Optional[float], Optional[str], Optional[str]]:
    """Pull the first "Salary" component out of Ashby's compensationTiers.

    Live shape (verified 2026-08-16, `?includeCompensation=true`):
    `compensation.compensationTiers[].components[]`, each component tagged
    `compensationType` ("Salary" / "EquityCashValue" / ...). Most jobs carry
    the key with every value None (compensation not disclosed) — that is a
    real "not stated", not a parse failure.
    """
    if not isinstance(compensation, dict):
        return None, None, None, None
    for tier in compensation.get("compensationTiers") or []:
        for component in tier.get("components") or []:
            if component.get("compensationType") != "Salary":
                continue
            min_v = component.get("minValue")
            max_v = component.get("maxValue")
            if min_v is None and max_v is None:
                continue
            currency = component.get("currencyCode")
            period = _INTERVAL_MAP.get(str(component.get("interval") or "").strip())
            return min_v, max_v, currency, period
    return None, None, None, None


def _passes_uk_filter(item: dict) -> bool:
    """UK-or-remote check across BOTH the primary location and Ashby's
    `secondaryLocations` list.

    Verified live 2026-08-16: some postings (cohere, openai, replit) list a
    non-UK city as the PRIMARY `location` (e.g. "Paris", "San Francisco")
    but carry "London" / "Remote - United Kingdom" inside
    `secondaryLocations[].location` — the fetch-time filter below only ever
    checked the primary field, so real UK-eligible postings were dropped
    before the door ever saw them (~70 jobs across the configured boards).
    """
    if _is_uk_or_remote(item.get("location", "")):
        return True
    for sec in item.get("secondaryLocations") or []:
        if isinstance(sec, dict) and _is_uk_or_remote(sec.get("location", "")):
            return True
    return False


class AshbySource(BaseJobSource):
    name = "ashby"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else ASHBY_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
            # `includeCompensation=true` is opt-in: without it the `compensation`
            # key is absent from every job (verified live 2026-08-16). With it,
            # 1,101 of 2,019 jobs across the batch carry a real salary tier.
            data = await self._get_json(url, params={"includeCompensation": "true"})
            if not data or "jobs" not in data:
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in cast(dict[str, Any], data)["jobs"]:
                if not _passes_uk_filter(item):
                    continue
                title = item.get("title", "")
                desc = item.get("descriptionPlain", "")
                location = item.get("location", "")
                # Ashby publishedAt is the real posting date; updatedAt is a
                # mutation timestamp and must not populate posted_at.
                now_iso = datetime.now(timezone.utc).isoformat()
                raw_published = item.get("publishedAt")
                posted_at, confidence = normalize_posted_at(raw_published)

                salary_min, salary_max, salary_currency, salary_period = _extract_salary(
                    item.get("compensation")
                )

                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=desc[:5000],
                    # `applicationUrl` does not exist in the live API (verified
                    # across 1,458 jobs). `applyUrl` is the real key (100% fill,
                    # links to the application form); `jobUrl` is the info page.
                    apply_url=item.get("applyUrl", item.get("jobUrl", "")),
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_published,
                    # Raw upstream value ("FullTime"/"PartTime"/"Contract"/...)
                    # — services/shelf_gate.py owns normalising it.
                    employment_type=item.get("employmentType"),
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_currency=salary_currency,
                    salary_period=salary_period,
                ))
        logger.info("Ashby: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs
