import logging
from datetime import datetime, timezone
from typing import Any

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.nofluffjobs")

_MAX_RESULTS = 200

# NoFluffJobs API endpoints to try (the public API is unofficial and may change)
_API_URLS = [
    "https://nofluffjobs.com/api/posting",
    "https://nofluffjobs.com/api/search/posting",
]


def _plausible_gbp(val: Any) -> bool:
    """A loose sanity bound for a GBP annual salary figure — used only when
    `salary.currency` is absent, to decide whether a bare number is safe to
    trust as GBP. Same 10k-500k bound as nhs_jobs._parse_salary."""
    try:
        return 10000 <= float(val) <= 500000
    except (ValueError, TypeError):
        return False

class NoFluffJobsSource(BaseJobSource):
    name = "nofluffjobs"
    category = "other"
    DOMAINS = {"tech"}

    async def fetch_jobs(self) -> list[Job]:
        data = None
        for url in _API_URLS:
            data = await self._get_json(url)
            if data and isinstance(data, (list, dict)):
                break

        if not data:
            logger.info("NoFluffJobs: API unavailable, skipping")
            return []

        # Handle both list and dict responses
        postings = data if isinstance(data, list) else data.get("postings", [])
        if not isinstance(postings, list):
            return []

        jobs = []
        for item in postings:
            title = item.get("title", "")
            # NOTE (live probe 2026-08-08): this used to fall back to `name`
            # with the comment "some responses use name instead of title".
            # That is WRONG — `name` is the EMPLOYER, not an alias for the
            # title (verified across 20,631 live postings: title="Remote Sales
            # Development Representative", name="LevelUp Leads"). The old
            # fallback would have silently titled a job with its company name.
            # A posting with no title is unusable, so skip it instead.
            if not title:
                continue

            # Location handling
            location_obj = item.get("location", {})
            if isinstance(location_obj, dict):
                places = location_obj.get("places", [])
                location = ", ".join(
                    p.get("city", "") for p in places if isinstance(p, dict)
                ) if places else ""
            elif isinstance(location_obj, str):
                location = location_obj
            else:
                location = ""

            remote = item.get("remote", False)
            if remote:
                location = f"{location}, Remote".strip(", ") if location else "Remote"

            # Skip bare "Remote" or empty location — NoFluffJobs is Polish-focused
            if not location or location.strip().lower() == "remote":
                continue

            if not _is_uk_or_remote(location):
                continue

            # Build apply URL from posting ID
            posting_id = item.get("id", "") or item.get("url", "")
            apply_url = f"https://nofluffjobs.com/job/{posting_id}" if posting_id else ""

            # Only 'posted' is a real post date; 'renewed' is a mutation date
            # (listing refresh) and must not populate posted_at.
            now_iso = datetime.now(timezone.utc).isoformat()
            raw_posted = item.get("posted")
            posted_at, confidence = normalize_posted_at(raw_posted)

            # Salary. CURRENCY CORRECTNESS RISK (verified live 2026-08-08):
            # NoFluffJobs is Polish-focused — 19,229 of 20,631 postings are
            # priced in PLN, and `salary.currency` is 100% filled. Job has no
            # currency field, so a bare PLN number stored into
            # salary_min/salary_max would silently be compared as GBP
            # downstream (a multi-x overstatement). Only trust the value when
            # it's GBP, or when currency is entirely absent AND the value is
            # a plausible GBP figure — never convert here (no FX in this
            # source). A missing salary is far better than a wrong one.
            salary_obj = item.get("salary", {})
            salary_min = None
            salary_max = None
            if isinstance(salary_obj, dict):
                currency = salary_obj.get("currency")
                raw_min = salary_obj.get("from")
                raw_max = salary_obj.get("to")

                trust_salary = currency == "GBP" or (
                    currency is None and (_plausible_gbp(raw_min) or _plausible_gbp(raw_max))
                )
                if trust_salary:
                    salary_min = raw_min
                    salary_max = raw_max
                    if salary_min is not None:
                        try:
                            salary_min = float(salary_min)
                        except (ValueError, TypeError):
                            salary_min = None
                    if salary_max is not None:
                        try:
                            salary_max = float(salary_max)
                        except (ValueError, TypeError):
                            salary_max = None

            # seniority[] (100% fill live, e.g. ["Senior"]) — take the first
            # element. Zero extra HTTP cost, this list is already fetched.
            seniority = item.get("seniority")
            if isinstance(seniority, list) and seniority:
                experience_level = str(seniority[0])
            else:
                experience_level = ""

            # Live probe 2026-08-08: the `company` key does NOT exist in the
            # posting payload (0 of 20,631 items had it) — the employer name is
            # carried in `name`. Reading `company` meant every NoFluffJobs job
            # was stored with an empty company. Prefer `name`, keep `company`
            # as a fallback in case the upstream schema changes back.
            company_name = item.get("name") or item.get("company", "")

            jobs.append(Job(
                title=title,
                company=company_name,
                location=location,
                apply_url=apply_url,
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_posted,
                salary_min=salary_min,
                salary_max=salary_max,
                experience_level=experience_level,
            ))

            if len(jobs) >= _MAX_RESULTS:
                logger.info("NoFluffJobs: hit cap of %s results", _MAX_RESULTS)
                break

        logger.info("NoFluffJobs: found %s relevant jobs", len(jobs))
        return jobs
