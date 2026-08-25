"""DfE "Find an apprenticeship" Display Advert API v2 — keyed source.

The old keyless v1 (``findapprenticeship.service.gov.uk/api/v1/vacancies``)
was retired in 2026. Its replacement is the Azure-APIM-gated Display Advert
API v2, which needs a free subscription key (register at
https://developer.apprenticeships.education.gov.uk). Without the key this
source skips gracefully and returns [].

The v2 API has NO free-text keyword parameter — you filter by location,
route, LARS code or recency and page through. We pull the most recently
posted vacancies (``Sort=AgeDesc``) bounded by ``PostedInLastNumberOfDays``
plus a page cap, so we stay inside the 150-requests / 5-min budget, then
keep them all (every DfE apprenticeship is UK, so no location filtering).

Endpoint + contract verified against the live OpenAPI v2 spec at
developer.apprenticeships.education.gov.uk (2026-06).

Rate limit: 150 requests per 5-minute rolling window.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.gov_apprenticeships")

API_URL = "https://api.apprenticeships.education.gov.uk/vacancies/vacancy"

# Pillar 3 batch — REAL BUG FIX. The old code read `wage.wageAmount`, a key
# that DOES NOT EXIST in the live payload (confirmed absent on 200/200
# vacancies sampled live 2026-08-16), so salary was null for every job
# unconditionally. The real number lives as free text in
# `wage.wageAdditionalInformation` ("£15,600 a year", "£16,640 to £26,436.80
# a year") — 198 of 200 (99%) live samples matched this exact shape; the 2
# misses were "Competitive", which this regex correctly refuses rather than
# guessing (rule #29). Anchored (^...$) on purpose: a looser match risks
# parsing a fragment of a longer, ambiguous sentence.
_ANNUAL_WAGE_RE = re.compile(
    r"^£\s*([\d,]+(?:\.\d+)?)(?:\s*(?:to|-)\s*£\s*([\d,]+(?:\.\d+)?))?\s*a\s*year$",
    re.IGNORECASE,
)


def _parse_annual_wage_text(text: str) -> tuple[Optional[float], Optional[float]]:
    """Parse gov_apprenticeships' free-text wage ONLY when it is unambiguous
    ("£X a year" / "£X to £Y a year"). Anything else — "Competitive",
    "National Minimum Wage", a typo, a currency symbol we don't recognise —
    returns (None, None) rather than guessing; JOB SOURCE ENRICHMENT reads
    it later instead (rule #29)."""
    m = _ANNUAL_WAGE_RE.match(text.strip())
    if not m:
        return None, None
    try:
        low = float(m.group(1).replace(",", ""))
        # A MISSING UPPER BOUND STAYS MISSING. Mirroring `low` onto `high`
        # invents a ceiling the ad never stated — "from £18,000" becomes
        # "£18,000 to £18,000", which reads as a capped offer to every consumer
        # downstream and is a fabrication under rule #29. `shelf_gate`'s
        # `_annualise_one` was written for exactly this and documents the same
        # rule one layer up. (CodeRabbit, PR #388.)
        high = float(m.group(2).replace(",", "")) if m.group(2) else None
    except ValueError:
        return None, None
    return low, high


class GovApprenticeshipsSource(BaseJobSource):
    name = "gov_apprenticeships"
    category = "keyed_api"  # subscription key required → 5-min tier
    # Apprenticeships span every domain (trades, healthcare, tech, finance),
    # so keep them in "general" alongside the education tag.
    DOMAINS = {"education", "general"}

    # Bounded paging keeps us well inside the 150-req / 5-min budget.
    PAGE_SIZE = 50
    MAX_PAGES = 5
    POSTED_IN_LAST_DAYS = 30

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str = "",
        search_config: Optional[SearchConfig] = None,
    ):
        super().__init__(session, search_config=search_config)
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.info("GovApprenticeships: no API key, skipping")
            return []

        headers = {
            "Ocp-Apim-Subscription-Key": self._api_key,
            "X-Version": "2",
        }
        jobs: list[Job] = []
        seen: set[str] = set()

        for page in range(1, self.MAX_PAGES + 1):
            params = {
                "PageNumber": page,
                "PageSize": self.PAGE_SIZE,
                "Sort": "AgeDesc",
                "PostedInLastNumberOfDays": self.POSTED_IN_LAST_DAYS,
            }
            data = await self._get_json(API_URL, params=params, headers=headers)
            if not data or not isinstance(data, dict):
                break
            vacancies = data.get("vacancies") or []
            if not isinstance(vacancies, list) or not vacancies:
                break
            for item in vacancies:
                job = self._parse_vacancy(item, seen)
                if job is not None:
                    jobs.append(job)
            total_pages = data.get("totalPages")
            if isinstance(total_pages, int) and page >= total_pages:
                break

        logger.info("GovApprenticeships: found %s jobs", len(jobs))
        return jobs

    def _parse_vacancy(self, item: dict[str, Any], seen: set[str]) -> Optional[Job]:
        ref = item.get("vacancyReference") or ""
        # Prefer the employer's external apply link; fall back to the gov.uk page.
        apply_url = item.get("applicationUrl") or item.get("vacancyUrl") or ""
        dedup_key = ref or apply_url
        if not dedup_key or dedup_key in seen:
            return None
        seen.add(dedup_key)

        location = "UK"
        addresses = item.get("addresses")
        if isinstance(addresses, list) and addresses:
            first = addresses[0] or {}
            location = first.get("addressLine1") or first.get("postcode") or "UK"

        # Only treat an annual wage as salary — apprentice pay is often
        # hourly, and Job.__post_init__ would null small values anyway.
        # `wage.wageAmount` does NOT exist in the live payload (see the
        # module-level comment) — the real number is free text in
        # `wageAdditionalInformation`, parsed only when unambiguous.
        salary_min: Optional[float] = None
        salary_max: Optional[float] = None
        salary_period: Optional[str] = None
        wage = item.get("wage")
        if isinstance(wage, dict):
            wage_unit = wage.get("wageUnit")
            salary_period = str(wage_unit) if wage_unit else None
            if isinstance(wage_unit, str) and wage_unit.lower() == "annually":
                info = wage.get("wageAdditionalInformation")
                if isinstance(info, str) and info.strip():
                    salary_min, salary_max = _parse_annual_wage_text(info)

        # `closingDate` is a REAL deadline sitting in the response we already
        # fetch — confirmed populated on 50/50 live samples 2026-08-16,
        # unread until now. Same strict, never-fabricate parser as reed's
        # expirationDate (himalayas/weworkremotely precedent).
        raw_closing = item.get("closingDate")
        closing_iso, closing_confidence = normalize_posted_at(raw_closing)
        deadline = closing_iso[:10] if closing_confidence == "high" and closing_iso else None
        deadline_source = "listing" if deadline else None

        raw_posted = item.get("postedDate")
        now_iso = datetime.now(timezone.utc).isoformat()

        # `course.route` (one of DfE's 15 published "apprenticeship standard
        # routes" — "Digital", "Education and childcare", "Construction"...,
        # a genuinely CLOSED set) sits on this same item, unread until now.
        # Raw text only; shelf_gate.py's category alias table (rule #30 —
        # closed sets may be enumerated) maps the ones it can do honestly.
        course = item.get("course")
        route = course.get("route") if isinstance(course, dict) else None

        return Job(
            title=item.get("title", ""),
            company=item.get("employerName", "") or "Unknown",
            location=str(location),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_period=salary_period,
            description=item.get("description", "") or "",
            apply_url=apply_url,
            source=self.name,
            date_found=now_iso,
            posted_at=raw_posted if raw_posted else None,
            date_confidence="high" if raw_posted else "low",
            date_posted_raw=raw_posted,
            deadline=deadline,
            deadline_source=deadline_source,
            # `apprenticeshipLevel` ("Intermediate"/"Advanced"/"Higher", all
            # three seen live 2026-08-16) is a real seniority-shaped signal
            # sitting on this same item, unread until now. `course.level`
            # (a bare int) is also available but not mapped here — one raw
            # signal per field, and this one is the more directly
            # seniority-shaped of the two. NOTE (Pillar 3 batch, this
            # worker): confirmed live 2026-08-17 that none of the 4 values
            # ("Intermediate"/"Advanced"/"Higher"/"Degree") ever match
            # SeniorityLevel's junior/mid/senior/staff/... ladder — an
            # apprenticeship LEVEL is a course-complexity axis, not a
            # professional-seniority axis, and guessing "Advanced" ==
            # "senior" would be a fabrication (rule #29). Left as-is: the
            # raw value still lands in provenance as an honest
            # absent/not_mapped, which is correct, not a bug.
            seniority=item.get("apprenticeshipLevel"),
            category=str(route) if route else None,
        )
