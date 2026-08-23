import base64
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional, cast

import aiohttp

from src.core.settings import RATE_LIMITS, SOURCE_FETCH_TIMEOUT
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.reed")

# Reed's documented maximum for `resultsToTake` ("defaults and is limited to
# 100 results" — reed.co.uk/developers/jobseeker). We were asking for 50, so
# HALF of every page Reed was willing to serve was left behind at zero extra
# request cost.
_PAGE_SIZE = 100

# Pages per title. Reed does NOT rerank (its API exposes no sort parameter), so
# page 2 is not "worse matches for the same query" — it is 100 jobs the first
# page could not physically carry. Measured live 2026-08-13 on a real key:
# depth beat breadth on Reed at an equal request budget (2 titles x 3 pages =
# 308 relevant vs 6 titles x 1 page = 281).
_PAGES_PER_TITLE = 3


class ReedSource(BaseJobSource):
    name = "reed"
    category = "keyed_api"

    def __init__(self, session: aiohttp.ClientSession, api_key: str = "", search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_jobs(self) -> list[Job]:
        if not self.is_configured:
            logger.warning("Reed: no API key, skipping")
            return []
        jobs = []
        auth = base64.b64encode(f"{self._api_key}:".encode()).decode()
        headers = {"Authorization": f"Basic {auth}"}
        # S2 — size the fan-out from the actual time budget instead of a
        # hand-picked constant. Reed is rate-limited to one request every
        # RATE_LIMITS["reed"]["delay"] seconds, so N requests cost at LEAST
        # N * delay before any network time. At the previous 8 titles x 3
        # locations that was 24 * 2.0s = 48s of pure waiting against a 60s
        # SOURCE_FETCH_TIMEOUT — it did not always blow the ceiling, which is
        # worse than always failing: the source died intermittently, and a
        # timeout looks identical to "Reed had nothing today".
        #
        # Deriving the cap means a later change to the delay or the timeout
        # cannot silently push this back over the edge.
        #
        # NO `locationName` — this is the fix, not an omission (measured live
        # 2026-08-13 on a real key, same query, same day):
        #     locationName="UK"              ->  29 of 486 jobs (6%)
        #     locationName="United Kingdom"  -> 478
        #     no locationName at all         -> 486
        # `locationName` names a PLACE and Reed searches a RADIUS around it —
        # `distanceFromLocation` defaults to 10 MILES (their own docs). So
        # "UK" never meant "the United Kingdom", it meant a 10-mile circle
        # around whatever Reed geocodes "UK" to, and we threw away ~94% of
        # Reed's supply on every run for the sake of a filter we did not want.
        #
        # The old "London" and "Remote" legs go with it. Not because location
        # does not matter, but because Reed is a UK board and both legs search
        # the SAME corpus as the nationwide leg — they are strict subsets, so
        # they burned two of every three requests to re-find jobs the
        # nationwide leg already returns. The freed budget buys PAGES instead
        # (see _PAGES_PER_TITLE), which do return rows the first page cannot.
        # Non-UK strays are still refused below by `_is_uk_or_remote`, and by
        # the real chokepoint, `uk_gate.check_uk` at insert time (rule #30).
        _reed_limits = RATE_LIMITS.get("reed")
        _delay = float(_reed_limits["delay"]) if _reed_limits else 2.0
        # Spend at most 60% of the budget on enforced delay, leaving the rest
        # for real HTTP latency and parsing.
        _max_requests = max(1, int((SOURCE_FETCH_TIMEOUT * 0.6) / _delay))
        _max_titles = max(1, _max_requests // _PAGES_PER_TITLE)
        queries = self.search_titles[:_max_titles]

        # RUNTIME guard on top of the static budget above, matching the one
        # LinkedIn carries. The derivation can only see the ENFORCED DELAY; it
        # is blind to real network latency. Measured live 2026-08-13 on a fast
        # connection: 18 requests = 36.0s of delay + 14.2s of latency = 50.2s
        # against a 60s SOURCE_FETCH_TIMEOUT — under the wall, but only 9.8s
        # under it. Latency from Railway is not this machine's latency, and at
        # roughly 2x it the whole source times out.
        #
        # That failure is badly asymmetric: a timeout loses EVERY row Reed
        # fetched, turning our largest source into zero, and it looks exactly
        # like "Reed had nothing today" (the same disguise the comment above
        # was written about). Stopping early instead keeps whatever is already
        # in hand, so the worst case degrades to "fewer jobs" rather than
        # "no jobs, silently".
        #
        # `_requests_made` gates the check so the FIRST request is always
        # attempted: a guard that can fire before any call would turn a badly
        # configured timeout into a permanently silent source, which is the
        # exact failure being defended against.
        _deadline = time.monotonic() + (SOURCE_FETCH_TIMEOUT * 0.9)
        _requests_made = 0
        # BREAK, never `return`, out of the time guard below. Everything
        # collected here is still UNFILTERED — the `_is_uk_or_remote` pass that
        # keeps this source UK-only runs AFTER both loops. Returning early
        # skipped it and leaked non-UK rows into the catalog on exactly the slow
        # runs the guard exists for (caught in review, PR #273).
        _out_of_time = False
        for query in queries:
            if _out_of_time:
                break
            for page in range(_PAGES_PER_TITLE):
                if _requests_made and (_deadline - time.monotonic()) < _delay:
                    logger.warning(
                        "Reed: stopping early at %s jobs — %.1fs budget spent, "
                        "not enough left for another request",
                        len(jobs),
                        SOURCE_FETCH_TIMEOUT * 0.9 - (_deadline - time.monotonic()),
                    )
                    _out_of_time = True
                    break
                params = {
                    "keywords": query,
                    "resultsToTake": _PAGE_SIZE,
                    "resultsToSkip": page * _PAGE_SIZE,
                }
                data = await self._get_json(
                    "https://www.reed.co.uk/api/1.0/search",
                    params=params,
                    headers=headers,
                )
                _requests_made += 1
                if not data or "results" not in data:
                    break
                results = cast(dict[str, Any], data)["results"]
                for item in results:
                    now_iso = datetime.now(timezone.utc).isoformat()
                    raw_date = item.get("date") or item.get("datePosted")
                    posted_at, confidence = normalize_posted_at(raw_date)

                    jobs.append(Job(
                        title=item.get("jobTitle", ""),
                        company=item.get("employerName", ""),
                        location=item.get("locationName", ""),
                        salary_min=item.get("minimumSalary"),
                        salary_max=item.get("maximumSalary"),
                        description=item.get("jobDescription", ""),
                        apply_url=f"https://www.reed.co.uk/jobs/{item.get('jobId', '')}",
                        source=self.name,
                        date_found=now_iso,
                        posted_at=posted_at,
                        date_confidence=confidence,
                        date_posted_raw=raw_date,
                    ))
                # A short page is the last page. Asking for the next one costs
                # a full rate-limiter delay to be told the same thing.
                if len(results) < _PAGE_SIZE:
                    break
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Reed: found %s jobs", len(jobs))
        return jobs
