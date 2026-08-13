import html as _html
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from src.core.settings import RATE_LIMITS, SOURCE_FETCH_TIMEOUT
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.linkedin")

_BASE_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

# Regex patterns for LinkedIn guest HTML fragments
_JOB_CARD_RE = re.compile(
    r'<li[^>]*>.*?</li>',
    re.DOTALL,
)
_TITLE_RE = re.compile(r'<h3[^>]*class="[^"]*base-search-card__title[^"]*"[^>]*>\s*([^<]+)', re.IGNORECASE)
_COMPANY_RE = re.compile(r'<h4[^>]*class="[^"]*base-search-card__subtitle[^"]*"[^>]*>\s*([^<]+)', re.IGNORECASE)
_LOCATION_RE = re.compile(r'<span[^>]*class="[^"]*job-search-card__location[^"]*"[^>]*>\s*([^<]+)', re.IGNORECASE)
_LINK_RE = re.compile(r'href="(https://[^"]*linkedin\.com/jobs/view/[^"]*)"', re.IGNORECASE)

# S4: structural health check. `base-search-card__title` is the class the
# title regex keys on — it MUST appear on any non-trivial LinkedIn guest
# search response (results or not: LinkedIn's "no results" shell is tiny).
# If the response is big but this marker is gone, the guest HTML layout
# changed and the regexes above are silently matching nothing.
_STRUCTURE_ANCHOR = "base-search-card__title"
_MIN_STRUCTURAL_HTML_LEN = 500

# Job-understanding fix (2026-08-06): the job-view pages serve the FULL
# description to guests via JSON-LD SEO markup (verified live: 8,930 chars on
# a real posting with a plain browser UA — the assumed auth-wall does not
# apply to the SEO payload). One extra fetch per kept job, capped.
_LDJSON_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")
_MAX_DETAIL_FETCHES = 30

# WHY A TIME BUDGET EXISTS HERE (measured live 2026-08-13).
#
# This source kept ZERO jobs on EVERY run and had done for as long as the
# detail pass existed. It was not a parser fault and not LinkedIn blocking us:
# it was arithmetic. `scheduler.tick` runs each source under
# `asyncio.wait_for(src.fetch_jobs(), timeout=SOURCE_FETCH_TIMEOUT)` (60s), and
# `wait_for` CANCELS the coroutine — so overrunning does not return a partial
# list, it returns nothing at all. Meanwhile the work cost:
#
#   search  5 queries x (3s limiter + 3s explicit sleep)  =  30s
#   detail 30 jobs    x (3s limiter + 1s explicit sleep)  = 120s
#                                                   total ~150s vs a 60s wall
#
# Two fixes, both in this file:
#
# 1. The explicit sleeps are gone. `RATE_LIMITS["linkedin"]` is
#    {concurrent: 1, delay: 3.0} and `RateLimiter.acquire()` already sleeps
#    that delay before EVERY request, serially. The extra sleeps were spacing
#    requests we had already spaced — pure waste, paid out of a 60s budget.
#
# 2. Work now stops on a wall clock instead of a job count. Both loops check
#    the remaining budget before spending another request, so the source
#    RETURNS what it has rather than being cancelled holding all of it. The
#    first search query is never gated: a source that returns nothing is the
#    exact failure being fixed.
#
# Fraction of the ceiling we allow ourselves. The remainder is headroom for
# parsing plus the final return — finishing at 59.9s of 60s is still a loss.
_BUDGET_FRACTION = 0.8
_DEFAULT_REQUEST_COST_S = 3.0


class LinkedInSource(BaseJobSource):
    name = "linkedin"
    category = "scrapers"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        search_config: Optional[SearchConfig] = None,
        time_budget: Optional[float] = None,
    ) -> None:
        super().__init__(session, search_config=search_config)
        # Derived, not hand-picked: a later change to SOURCE_FETCH_TIMEOUT or
        # to the rate limit cannot silently push this back over the wall.
        self._time_budget = (
            float(time_budget) if time_budget is not None
            else SOURCE_FETCH_TIMEOUT * _BUDGET_FRACTION
        )
        limits = RATE_LIMITS.get(self.name)
        self._request_cost = (
            float(limits["delay"]) if limits else _DEFAULT_REQUEST_COST_S
        )

    def _can_afford_request(self, deadline: float) -> bool:
        """True when the budget still covers one more rate-limited request."""
        return (deadline - time.monotonic()) >= self._request_cost

    async def fetch_jobs(self) -> list[Job]:
        deadline = time.monotonic() + self._time_budget
        jobs = []
        seen_urls = set()
        # `search_titles`, not `search_queries`: the query strings carry a " UK"
        # suffix for sources that send no location, and this request already
        # passes location="United Kingdom" below. Under AND-of-terms keyword
        # matching a dead token can only shrink the result set.
        queries = self.search_titles[:5]
        if not queries:
            logger.info("LinkedIn: no search titles in profile, skipping")
            return []
        # `q_index`, not `i` — the card loop below already owns `i`, and a
        # shadowed counter here would silently disable the budget check on any
        # page that returned a single card.
        for q_index, query in enumerate(queries):
            # `q_index > 0`: the first query always runs. Returning zero jobs is
            # the failure this whole budget exists to prevent — never make the
            # guard cause it.
            if q_index > 0 and not self._can_afford_request(deadline):
                logger.info(
                    "LinkedIn: time budget spent after %d of %d queries",
                    q_index, len(queries),
                )
                break
            params = {
                "keywords": query,
                "location": "United Kingdom",
                "f_TPR": "r604800",
                "start": "0",
            }
            html = await self._get_text(_BASE_URL, params=params)
            if not html:
                continue
            if len(html) > _MIN_STRUCTURAL_HTML_LEN and _STRUCTURE_ANCHOR not in html:
                logger.error(
                    "[linkedin] STRUCTURE CHANGED: expected '%s' not found in a "
                    "%d-byte response — parser may be broken",
                    _STRUCTURE_ANCHOR, len(html),
                )
            try:
                titles = _TITLE_RE.findall(html)
                companies = _COMPANY_RE.findall(html)
                locations = _LOCATION_RE.findall(html)
                links = _LINK_RE.findall(html)
                count = min(len(titles), len(links))
                for i in range(count):
                    url = links[i].split("?")[0]
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    title = titles[i].strip()
                    company = companies[i].strip() if i < len(companies) else ""
                    location = locations[i].strip() if i < len(locations) else "UK"
                    jobs.append(Job(
                        title=title,
                        company=company,
                        location=location,
                        description="",
                        apply_url=url,
                        source=self.name,
                        date_found=datetime.now(timezone.utc).isoformat(),
                        posted_at=None,
                        date_confidence="fabricated",
                        date_posted_raw=None,
                    ))
                    if len(jobs) >= 50:
                        break
            except Exception as e:
                logger.warning("LinkedIn: HTML parsing failed for query '%s': %s", query, e)
            if len(jobs) >= 50:
                break
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        # Detail pass — only for jobs we KEEP, capped by BOTH a job count and
        # the remaining wall clock. A description is an enrichment: a job with
        # an empty one still scores and still reaches the user, whereas
        # overrunning the ceiling loses every job in this list.
        detailed = 0
        for job in jobs[:_MAX_DETAIL_FETCHES]:
            if not self._can_afford_request(deadline):
                break
            try:
                job.description = await self._fetch_description(job.apply_url)
                if job.description:
                    detailed += 1
            except Exception as e:  # noqa: BLE001 — a detail miss never drops the job
                logger.debug("LinkedIn: detail fetch failed for %s: %s", job.apply_url, e)
        logger.info(
            "LinkedIn: found %s relevant jobs (%s with descriptions)", len(jobs), detailed
        )
        return jobs

    async def _fetch_description(self, view_url: str) -> str:
        """Pull the posting's full description from the job-view page's JSON-LD.

        The SEO markup is guest-accessible (verified live 2026-08-06). The
        `description` field arrives as entity-escaped HTML — unescape, strip
        tags, cap 5,000 chars. Returns "" on any miss: absence of text is a
        data gap the scorer treats neutrally, never an error.
        """
        page = await self._get_text(view_url)
        if not page:
            return ""
        m = _LDJSON_RE.search(page)
        if not m:
            return ""
        try:
            data = json.loads(m.group(1))
        except (ValueError, TypeError):
            return ""
        desc = data.get("description") or ""
        if not isinstance(desc, str) or not desc:
            return ""
        return _TAG_RE.sub(" ", _html.unescape(desc))[:5000].strip()
