import html
import logging
import re
from datetime import datetime, timezone

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.landingjobs")

_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Country codes that count as UK/relevant
_UK_CODES = {"GB", "UK"}
_MAX_JOBS = 200

class LandingJobsSource(BaseJobSource):
    name = "landingjobs"
    category = "free_json"
    DOMAINS = {"tech"}

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        offset = 0
        limit = 50

        while offset < _MAX_JOBS:
            params = {"limit": str(limit), "offset": str(offset)}
            data = await self._get_json(
                "https://landing.jobs/api/v1/jobs.json",
                params=params,
            )
            if not data or not isinstance(data, list) or len(data) == 0:
                break

            for item in data:
                # Filter for UK or remote jobs
                locations = item.get("locations", [])
                is_remote = item.get("remote", False)
                is_uk = any(
                    loc.get("country_code", "").upper() in _UK_CODES
                    for loc in locations
                    if isinstance(loc, dict)
                )
                if not is_uk and not is_remote:
                    continue

                title = item.get("title", "")
                tags = " ".join(item.get("tags", []))

                # `role_description` (100% fill, HTML prose) is the real
                # description field. The old code used the space-joined tag
                # list instead, which is a keyword bag, not readable prose.
                # Strip HTML the same way other sources in this repo do
                # (unescape entities first, the tag-strip regex can't see
                # escaped tags). Fall back to tags only when it is missing.
                role_description = item.get("role_description") or ""
                if role_description:
                    description = _HTML_TAG_RE.sub(" ", html.unescape(role_description)).strip()
                else:
                    description = tags

                # Build location string
                location_parts = []
                for loc in locations:
                    if isinstance(loc, dict):
                        city = loc.get("city", "")
                        country = loc.get("country_code", "")
                        if city:
                            location_parts.append(f"{city}, {country}" if country else city)
                if is_remote:
                    location_parts.append("Remote")
                location = "; ".join(location_parts) if location_parts else ""

                # NOTE (verified live): this endpoint has no company field at
                # all. `company_name`/`company_id` are both absent on every
                # sampled row; this fallback chain is a no-op today but is
                # left alone deliberately in case the upstream adds one back.
                company = str(item.get("company_name", "") or item.get("company_id", ""))
                apply_url = item.get("url", "")
                now_iso = datetime.now(timezone.utc).isoformat()
                raw_pub = item.get("published_at")
                posted_at, confidence = normalize_posted_at(raw_pub)

                # `expires_at` (100% fill, e.g. "2026-09-02") is the
                # listing's own application deadline.
                raw_expires = item.get("expires_at")
                expires_iso, expires_confidence = normalize_posted_at(raw_expires)
                deadline = expires_iso[:10] if expires_confidence == "high" and expires_iso else None
                deadline_source = "listing" if deadline else None

                # `gross_salary_low`/`gross_salary_high` (22% fill, numeric).
                raw_salary_min = item.get("gross_salary_low")
                raw_salary_max = item.get("gross_salary_high")
                try:
                    salary_min = float(raw_salary_min) if raw_salary_min not in (None, "") else None
                except (ValueError, TypeError):
                    salary_min = None
                try:
                    salary_max = float(raw_salary_max) if raw_salary_max not in (None, "") else None
                except (ValueError, TypeError):
                    salary_max = None

                jobs.append(Job(
                    title=title,
                    company=company,
                    location=location,
                    description=description,
                    apply_url=apply_url,
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_pub,
                    deadline=deadline,
                    deadline_source=deadline_source,
                    salary_min=salary_min,
                    salary_max=salary_max,
                ))

            if len(data) < limit:
                break
            offset += limit

        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("LandingJobs: found %s relevant UK/remote jobs", len(jobs))
        return jobs
