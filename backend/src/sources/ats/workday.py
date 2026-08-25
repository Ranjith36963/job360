import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, cast

import aiohttp

from src.core.companies import COMPANY_NAME_OVERRIDES, WORKDAY_COMPANIES
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.workday")

# Parse "Posted 3 Days Ago", "Posted Today", "Posted Yesterday", "Posted 30+ Days Ago"
_POSTED_RE = re.compile(r"Posted\s+(\d+)\s+Days?\s+Ago", re.IGNORECASE)
# Strip HTML tags from the CXS detail endpoint jobDescription.
_DESC_TAG_RE = re.compile(r"<[^>]+>")
# Detail-fetch budget per RUN (2026-08-06) - same timeout regression as
# smartrecruiters: uncapped detail fetches blew the 240s ATS ceiling in the
# nightly union refresh and zeroed the source (537 jobs before). Past-cap jobs
# keep empty descriptions; the description-backfill-on-refetch (PR #232) fills
# them across later runs.
# Raised 40 -> 60 (2026-08-16): measured live, full 20-company run, AI/ML
# SearchConfig (`fetch_jobs()` timed end to end, real HTTP, no mocks). Old
# cap (40): 161s wall, 40/176 jobs described. New cap (60): 198s wall,
# 60/176 jobs described -- ~42s of margin under the 240s ATS ceiling. 70
# was tried and measured at 218s (~22s margin) -- too thin against real
# network jitter, so 60 is the shipped value.
_MAX_DETAIL_FETCHES = 60

# Wall-clock budget, as a fraction of the ATS ceiling this source runs under.
#
# The 60-detail cap above was tuned to 198s against ONE profile's SearchConfig.
# The nightly catalog cron does not use one profile: it sends the UNION of every
# complete profile's titles, so `search_titles[:8]` grows with the user base and
# the call count grows with it (20 tenants x up to 8 queries = up to 160 POSTs at
# concurrent=2 / delay=1.5, before any detail fetch). Production 2026-08-24
# recorded workday at exactly 240.0s -- the ceiling -- on three consecutive runs.
#
# Hitting the ceiling raises, and the raise discards EVERY job already collected:
# the run logs the source as errored with ZERO, while 542 of its listings sit in
# the catalog going stale. The upstream is healthy (probed live: HTTP 200 in
# 0.5-1.6s, correct shape, `total` of 1,245 for a single tenant), so throwing the
# work away is pure loss.
#
# A count cap cannot fix this, because the thing that grows is the number of
# users, not the number of companies. A clock can: stop fetching near the ceiling
# and RETURN what is in hand. Partial is strictly better than nothing.
_FETCH_BUDGET_FRACTION = 0.8


def _parse_posted_on(text: str) -> str:
    """Convert Workday relative posted-on text to an ISO date string."""
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
        jobs: list[Job] = []
        seen_keys = set()
        detail_budget = _MAX_DETAIL_FETCHES

        # Read the module, not the name, so a test (or a runtime change) can set
        # the ceiling without this having bound the value at import.
        import src.core.settings as _settings  # noqa: PLC0415

        ceiling = getattr(_settings, "SOURCE_FETCH_TIMEOUT_ATS", None) or 0
        # No ceiling configured => no budget to run out of. `None` here is a real
        # state, not a bug: the test conftest leaves the timeout unset, and the
        # first version of this code did `None * 0.8` and crashed the whole
        # source. A source must never fail because a LIMIT is absent.
        budget = ceiling * _FETCH_BUDGET_FRACTION if ceiling else 0
        deadline = (time.monotonic() + budget) if budget else None
        companies_done = 0

        for entry in self._companies:
            # Stop BEFORE starting another company rather than being killed
            # mid-flight. Everything already collected is returned.
            if deadline is not None and time.monotonic() >= deadline:
                logger.warning(
                    "Workday: stopping at %d/%d companies — %.0fs budget spent, "
                    "returning %d jobs already collected rather than losing them "
                    "to the %ss ceiling",
                    companies_done,
                    len(self._companies),
                    budget,
                    len(jobs),
                    ceiling,
                )
                break
            companies_done += 1
            tenant = entry["tenant"]
            wd = entry["wd"]
            site = entry["site"]
            company_name = COMPANY_NAME_OVERRIDES.get(
                tenant, entry.get("name", tenant.replace("-", " ").title())
            )
            base_url = f"https://{tenant}.{wd}.myworkdayjobs.com"
            api_url = f"{base_url}/wday/cxs/{tenant}/{site}/jobs"

            company_failed = False
            for query in self.search_titles[:8]:
                if company_failed:
                    break
                # Also checked per QUERY: the union of user titles is what grew,
                # so a single company can now consume a large slice of the budget
                # on its own.
                if deadline is not None and time.monotonic() >= deadline:
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
                    # API rejected request (422/404/etc) - skip remaining queries for this company
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
                    # Ago") - approximate, medium confidence. This is only a
                    # fallback: the detail fetch below, when it runs, upgrades
                    # posted_at / date_confidence / deadline using the real
                    # ISO startDate/endDate the detail endpoint carries.
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
                    # `job_deadline`, NOT `deadline` — the enclosing loop uses
                    # `deadline` for its TIME BUDGET (a monotonic float, set at
                    # line 94 and read at 100 and 129). Reusing the name here
                    # rebound that float to a date string, so the very next
                    # budget check raised
                    #     TypeError: '>=' not supported between 'float' and 'str'
                    # and killed the whole source mid-fetch. Two different
                    # meanings of "deadline" sharing one name in one function.
                    job_deadline = None
                    deadline_source = None
                    if detail_budget > 0:
                        detail_budget -= 1
                        detail = await self._fetch_job_detail(
                            base_url, tenant, site, ext_path
                        )
                        description = detail["description"] or ""
                        # Date upgrade (2026-08-08): the same detail response
                        # fetched above for the description also carries a
                        # real ISO startDate (verified live 2/2) and endDate
                        # (1/2, a closing date). startDate is a genuine
                        # structured date, so prefer it over the relative-text
                        # guess above, routed through normalize_posted_at()
                        # for real "high" confidence.
                        start_date = detail["start_date"]
                        if start_date:
                            detail_posted_at, detail_confidence = normalize_posted_at(start_date)
                            if detail_posted_at:
                                parsed_posted_at = detail_posted_at
                                confidence = detail_confidence
                        end_date = detail["end_date"]
                        if end_date:
                            deadline_iso, _ = normalize_posted_at(end_date)
                            if deadline_iso:
                                job_deadline = deadline_iso[:10]
                                deadline_source = "listing"
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
                        deadline=job_deadline,
                        deadline_source=deadline_source,
                        # `timeType` ("Full time") is on every SEARCH-result
                        # item already — verified live 2026-08-17
                        # (astrazeneca board: 20/20) — no extra request.
                        # Raw value; shelf_gate.py owns normalising it.
                        employment_type=item.get("timeType"),
                    ))

        logger.info("Workday: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs

    async def _fetch_job_description(
        self, base_url: str, tenant: str, site: str, ext_path: str
    ) -> str:
        """Back-compat wrapper: description text only.

        Kept because src/services/description_backfill.py reconstructs a
        WorkdaySource and calls this exact method by name on a stored
        apply_url (the BACKFILL phase of the enrichment_sweep cron) - it
        must keep working with an unchanged (str) return type. Delegates to
        _fetch_job_detail(), which now also carries the structured dates.
        """
        detail = await self._fetch_job_detail(base_url, tenant, site, ext_path)
        return detail["description"] or ""

    async def _fetch_job_detail(
        self, base_url: str, tenant: str, site: str, ext_path: str
    ) -> dict[str, Optional[str]]:
        """Fetch one job detail payload: description text plus the structured
        startDate/endDate fields (real ISO dates, when present) the CXS detail
        endpoint carries alongside the description already fetched here.

        Returns empty/None values on any failure - a missing detail is a data
        gap the scorer/date logic treats neutrally, not a reason to drop the
        job or downgrade confidence below the relative-text fallback.
        """
        empty: dict[str, Optional[str]] = {"description": "", "start_date": None, "end_date": None}
        if not ext_path:
            return empty
        detail = await self._get_json(f"{base_url}/wday/cxs/{tenant}/{site}{ext_path}")
        if not isinstance(detail, dict):
            return empty
        info = detail.get("jobPostingInfo") or {}
        desc = info.get("jobDescription") or ""
        description = _DESC_TAG_RE.sub(" ", str(desc))[:5000].strip() if desc else ""
        return {
            "description": description,
            "start_date": info.get("startDate") or None,
            "end_date": info.get("endDate") or None,
        }
