"""UK Department for Education — Teaching Vacancies API (schema.org JobPosting).

Docs: https://teaching-vacancies.service.gov.uk/pages/api_specification
Licence: Open Government Licence v3.0. No auth required.
No documented rate limit — be polite (polled on the 15-min RSS tier).
"""
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.teaching_vacancies")

# baseSalary.value.value is FREE-TEXT prose (confirmed live 2026-08-17), e.g.
# "Salary: Support Staff Pay Scale F: £25,120 to £27,249 pro-rata per annum
# (Actual) (£28,598 - £31,022 full time/full year equivalent)" -- a naive
# "grab the first two numbers" parser would read the PRO-RATA figure as the
# full-time one, or vice versa. That is a WRONG number, worse than none
# (rule #29). These two patterns only match a string that is JUST the number
# (or range) and nothing else -- any surrounding words (Actual, FTE, pro
# rata, pay-award caveats) fail the full-string match and the shelf stays
# UNSET rather than guessed. Measured live: 13/100 sampled jobs are this
# clean (a real, if modest, gain from an always-0% baseline).
_SALARY_RANGE_RE = re.compile(
    r"^£([\d,]+(?:\.\d+)?)\s*(?:-|–|to)\s*£([\d,]+(?:\.\d+)?)$", re.IGNORECASE
)
_SALARY_HOURLY_RE = re.compile(r"^£([\d.]+)\s*per hour$", re.IGNORECASE)


def _parse_clean_salary_text(text: str) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Returns (min, max, period) for an UNAMBIGUOUS salary string, else
    (None, None, None). Never guesses at anything with extra prose."""
    text = text.strip()
    m = _SALARY_RANGE_RE.match(text)
    if m:
        lo = float(m.group(1).replace(",", ""))
        hi = float(m.group(2).replace(",", ""))
        return min(lo, hi), max(lo, hi), None
    m = _SALARY_HOURLY_RE.match(text)
    if m:
        val = float(m.group(1))
        return val, None, "hourly"
    return None, None, None

# Hard cap on pages fetched per run. Live catalog is ~3,900 jobs across ~39
# pages of 100 (meta.count / meta.totalPages, verified live 2026-08-08); the
# old code never sent a page param at all, so every run only ever saw page 1.
# 5 pages (~500 jobs) keeps a run bounded — same MAX_PAGES pattern as
# themuse.py.
MAX_PAGES = 5

class TeachingVacanciesSource(BaseJobSource):
    name = "teaching_vacancies"
    category = "rss"  # 15-min tier — frequent enough for school-day postings
    DOMAINS = {"education"}

    API_URL = "https://teaching-vacancies.service.gov.uk/api/v1/jobs.json"

    async def fetch_jobs(self) -> list[Job]:
        all_raw_jobs: list[Any] = []
        for page in range(1, MAX_PAGES + 1):
            # Page 1 sends no params (identical to the pre-pagination request
            # shape); page 2+ asks explicitly. Confirmed live: the response's
            # `links.next` is `...jobs.json?page=2`, so `page` is the real
            # param name and pages are 1-indexed.
            params = {"page": page} if page > 1 else None
            data = await self._get_json(self.API_URL, params=params)
            if not data or not isinstance(data, dict):
                break

            raw_jobs = data.get("jobs") or data.get("data") or []
            if not isinstance(raw_jobs, list) or not raw_jobs:
                break

            all_raw_jobs.extend(raw_jobs)

        results: list[Job] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for item in all_raw_jobs:
            # schema.org JobPosting structure
            title = (item.get("title") or item.get("jobTitle") or "").strip()
            org = item.get("hiringOrganization") or {}
            company = (org.get("name") if isinstance(org, dict) else str(org)) or "UK School"

            loc_obj = item.get("jobLocation") or {}
            if isinstance(loc_obj, dict):
                addr = loc_obj.get("address") or {}
                if isinstance(addr, dict):
                    location = (
                        addr.get("addressLocality")
                        or addr.get("addressRegion")
                        or addr.get("postalCode")
                        or "UK"
                    )
                else:
                    location = str(addr) or "UK"
            else:
                location = str(loc_obj) or "UK"

            if not _is_uk_or_remote(location):
                continue

            raw_posted = item.get("datePosted") or item.get("date_posted")
            posted_at, confidence = normalize_posted_at(raw_posted)

            apply_url = item.get("url") or item.get("applyUrl") or ""
            description = (item.get("description") or "")[:5000]

            # validThrough (schema.org JobPosting deadline field, 100% fill
            # live) is the listing's own application deadline — distinct
            # from datePosted. baseSalary's value is USUALLY free-text prose
            # (see _parse_clean_salary_text above) -- only the unambiguous
            # subset gets parsed; everything else stays UNSET rather than
            # risk a wrong number.
            raw_base_salary = item.get("baseSalary") or {}
            raw_salary_value = (
                raw_base_salary.get("value") if isinstance(raw_base_salary, dict) else None
            )
            raw_salary_text = (
                raw_salary_value.get("value")
                if isinstance(raw_salary_value, dict)
                else (raw_salary_value if isinstance(raw_salary_value, str) else None)
            )
            salary_min = salary_max = None
            salary_period = None
            if raw_salary_text:
                salary_min, salary_max, salary_period = _parse_clean_salary_text(raw_salary_text)
            salary_currency = "GBP" if salary_min is not None or salary_max is not None else None

            raw_valid_through = item.get("validThrough")
            deadline_iso, deadline_confidence = normalize_posted_at(raw_valid_through)
            deadline = deadline_iso[:10] if deadline_confidence == "high" and deadline_iso else None
            deadline_source = "listing" if deadline else None

            # employmentType (schema.org JobPosting field, 100% fill live
            # 2026-08-16) can be multi-valued (e.g. ["FULL_TIME",
            # "TEMPORARY"]) -- take the raw first value, no enum-mapping
            # here (the gate owns that); the full list stays recoverable
            # from raw_employment_type if a future gate pass wants it.
            raw_employment_type = item.get("employmentType")
            employment_type = (
                raw_employment_type[0]
                if isinstance(raw_employment_type, list) and raw_employment_type
                else (raw_employment_type if isinstance(raw_employment_type, str) else None)
            )

            results.append(Job(
                title=title or "Teaching Vacancy",
                company=company,
                location=location,
                description=description,
                apply_url=apply_url,
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_posted,
                deadline=deadline,
                deadline_source=deadline_source,
                employment_type=employment_type,
                salary_min=salary_min,
                salary_max=salary_max,
                salary_period=salary_period,
                salary_currency=salary_currency,
            ))

        logger.info("TeachingVacancies: found %s relevant jobs", len(results))
        return results
