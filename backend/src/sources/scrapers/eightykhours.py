import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional, cast

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.eightykhours")

# `salary` (confirmed live 2026-08-17, 75.8% fill) is the board's own
# free-text compensation line -- "$100,000 - $175,000", "£65,000 -
# £145,000", "$50 - $100 per hour". A STRICT full-string match only: a
# leading currency symbol, one or two numbers, an optional pay-period
# suffix, nothing else. This deliberately REJECTS "£25,000 stipend" (a
# lump sum, not a rate), "$750 per piece", "Base: $110,000" and dual-
# currency lines like "£81,000 - £93,000; $116,000 - $176,000" -- any of
# those parsed naively would produce a confidently WRONG number, worse
# than none (rule #29). Measured live: 199/213 (93%) of non-empty salary
# strings are this clean.
_SALARY_CURRENCY_SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR"}
_SALARY_RE = re.compile(
    # The SECOND symbol is captured, not discarded. `[£$€]?` threw it away, so
    # "$50 - £100 per hour" parsed as USD 50-100 and the gate then converted a
    # sterling figure as dollars — a confidently wrong number, which this
    # function's own docstring says it never produces. (CodeRabbit, PR #388.)
    r"^([£$€])([\d,]+(?:\.\d+)?)\s*(?:-\s*([£$€]?)([\d,]+(?:\.\d+)?))?"
    r"\s*(per hour|per month|per annum|per year)?$",
    re.IGNORECASE,
)
_SALARY_PERIOD_MAP = {
    "per hour": "hourly",
    "per month": "monthly",
    "per annum": "annual",
    "per year": "annual",
}


def _parse_clean_salary_text(
    text: str,
) -> tuple[Optional[float], Optional[float], Optional[str], Optional[str]]:
    """(min, max, currency, period) for an UNAMBIGUOUS salary line, else all
    None. Never guesses at anything with a stipend/award/base-pay/dual-
    currency shape."""
    m = _SALARY_RE.match(text.strip())
    if not m:
        return None, None, None, None
    symbol, first, second_symbol, second, period_raw = m.groups()
    # A RANGE THAT CROSSES CURRENCIES IS NOT UNAMBIGUOUS. Refuse it whole
    # rather than keep half: one number in the wrong currency is worse than no
    # number at all, and the dual-currency shape is exactly what the docstring
    # above promises to decline.
    if second_symbol and second_symbol != symbol:
        return None, None, None, None
    currency = _SALARY_CURRENCY_SYMBOLS[symbol]
    first_val = float(first.replace(",", ""))
    second_val = float(second.replace(",", "")) if second else None
    period = _SALARY_PERIOD_MAP.get((period_raw or "").lower())
    if second_val is not None:
        return min(first_val, second_val), max(first_val, second_val), currency, period
    return first_val, None, currency, period

# 80,000 Hours uses Algolia for their job board (jobs.80000hours.org)
# These are public search-only keys; overridable via env vars.
_ALGOLIA_APP_ID = os.getenv("EIGHTYKHOURS_ALGOLIA_APP_ID", "W6KM1UDIB3")
_ALGOLIA_API_KEY = os.getenv("EIGHTYKHOURS_ALGOLIA_API_KEY", "d1d7f2c8696e7b36837d5ed337c4a319")
_ALGOLIA_INDEX = "jobs_prod"
_ALGOLIA_URL = f"https://{_ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{_ALGOLIA_INDEX}/query"

class EightyKHoursSource(BaseJobSource):
    """80,000 Hours — high-impact AI safety jobs via Algolia API."""
    name = "eightykhours"
    category = "scrapers"
    # S7 fix: without an override this inherited the base class's
    # {"general"} default, so it fired for every user regardless of
    # domain (a nurse/professor search got AI-safety/EA noise injected).
    # See docs/harness/fable/AUDIT-2026-07-23-FULL-REVERIFY.md.
    DOMAINS = {"tech"}

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_ids = set()
        headers = {
            "X-Algolia-Application-Id": _ALGOLIA_APP_ID,
            "X-Algolia-API-Key": _ALGOLIA_API_KEY,
            "Content-Type": "application/json",
        }

        queries = self.search_queries[:6]  # Bounded to prevent source timeout
        if not queries:
            logger.info("80,000 Hours: no search queries in profile, skipping")
            return []

        for query in queries:
            body = {
                "query": query,
                "hitsPerPage": 50,
            }
            data = await self._post_json(_ALGOLIA_URL, body=body, headers=headers)
            if not data:
                continue
            if "hits" not in data:
                # S4: structural health check. A real Algolia query response
                # always has a "hits" key (empty list on zero matches). If the
                # server answered but that key is gone, the Algolia index/API
                # contract changed — not a real zero-results query.
                logger.error(
                    "[eightykhours] STRUCTURE CHANGED: expected 'hits' key not "
                    "found in Algolia response — parser may be broken"
                )
                continue

            for hit in cast(dict[str, Any], data)["hits"]:
                obj_id = hit.get("objectID", "")
                if obj_id in seen_ids:
                    continue
                seen_ids.add(obj_id)

                title = hit.get("title", "") or hit.get("job_title", "")
                company = hit.get("company_name", "") or hit.get("organisation_name", "") or "Unknown"
                locations = hit.get("locations", [])
                if isinstance(locations, list):
                    location = ", ".join(
                        loc.get("name", "") if isinstance(loc, dict) else str(loc)
                        for loc in locations
                    )
                else:
                    location = str(locations) if locations else ""

                if not _is_uk_or_remote(location):
                    continue

                # Build apply URL
                slug = hit.get("id_external_80_000_hours", "") or hit.get("url_external", "") or obj_id
                if slug.startswith("http"):
                    apply_url = slug
                else:
                    apply_url = f"https://jobs.80000hours.org/jobs/{obj_id}"

                description = hit.get("description_short", "") or hit.get("description", "") or title

                now_iso = datetime.now(timezone.utc).isoformat()
                # BUG FIX (confirmed live 2026-08-16): the payload has no
                # "date_published" key at all -- 0/20 filled in a live sample.
                # The real posting date is "posted_at" (Algolia epoch
                # seconds), 20/20 filled. The old key name meant every
                # eightykhours job stored posted_at=None, date_confidence="low"
                # regardless of what the source actually knows.
                raw_published = hit.get("posted_at")
                posted_at, confidence = normalize_posted_at(raw_published)

                # closes_at (Algolia epoch seconds, ~45% filled live) is the
                # posting's own application deadline -- distinct from
                # posted_at, goes on the deadline shelf only.
                raw_closes = hit.get("closes_at")
                deadline_iso, deadline_confidence = normalize_posted_at(raw_closes)
                deadline = deadline_iso[:10] if deadline_confidence == "high" and deadline_iso else None
                deadline_source = "listing" if deadline else None

                # tags_exp_required (100% filled live, e.g. "Mid (5-9 years
                # experience)") is seniority-shaped free text the gate can
                # normalise later -- take the raw first value, no mapping here.
                tags_exp = hit.get("tags_exp_required")
                seniority = (
                    tags_exp[0] if isinstance(tags_exp, list) and tags_exp else None
                )

                # tags_role_type (100% filled live 2026-08-17, e.g.
                # "Full-time"/"Internship") and tags_location_type (~15%
                # filled, "Remote") ride the SAME hit this source already
                # reads and were thrown away entirely -- raw first value
                # only, no enum-mapping here.
                tags_role = hit.get("tags_role_type")
                employment_type = (
                    tags_role[0] if isinstance(tags_role, list) and tags_role else None
                )
                tags_loc_type = hit.get("tags_location_type")
                workplace_mode = (
                    tags_loc_type[0] if isinstance(tags_loc_type, list) and tags_loc_type else None
                )

                raw_salary_text = (hit.get("salary") or "").strip()
                salary_min = salary_max = salary_currency = salary_period = None
                if raw_salary_text:
                    salary_min, salary_max, salary_currency, salary_period = (
                        _parse_clean_salary_text(raw_salary_text)
                    )

                jobs.append(Job(
                    title=title,
                    company=company,
                    location=location or "Remote",
                    description=description[:5000],
                    apply_url=apply_url,
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=str(raw_published) if raw_published else None,
                    deadline=deadline,
                    deadline_source=deadline_source,
                    seniority=seniority,
                    employment_type=employment_type,
                    workplace_mode=workplace_mode,
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_currency=salary_currency,
                    salary_period=salary_period,
                ))

        logger.info("80,000 Hours: found %s relevant jobs", len(jobs))
        return jobs
