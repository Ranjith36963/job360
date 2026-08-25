import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional, cast

import aiohttp

from src.core.companies import COMPANY_NAME_OVERRIDES, WORKABLE_COMPANIES
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.workable")

_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Detail-fetch budget per RUN, same shape and reasoning as workday.py (40) and
# smartrecruiters.py (60): one run must never blow SOURCE_FETCH_TIMEOUT_ATS
# (240s, core/settings.py). Workable is 21 companies at concurrent=2/delay=1.5
# (RATE_LIMITS), so 21 list calls + 60 detail calls is ~60s of wall clock — a
# quarter of the budget. Jobs past the cap now keep the LIST description (the
# widget endpoint returns one on 100% of rows) instead of an empty one, and are
# still enriched later by services/description_backfill.py, which lists
# "workable" as a capable source.
_MAX_DETAIL_FETCHES = 60

# ...AND A PER-COMPANY SLICE OF IT, for the reason successfactors.py already
# documents (`_MAX_DETAIL_FETCHES_PER_COMPANY = 20`).
#
# The old POST /api/v2 endpoint capped at 10 rows per page, so no single
# company could take much of a shared budget. The widget endpoint above returns
# EVERY posting — abm-careers alone has 178 — so the first company in the list
# can now spend all 60 slots and every company after it gets zero detail
# fetches. The switch that fixed the descriptions quietly created a starvation
# order dependency, where the fix works for whoever happens to be first.
# (CodeRabbit, PR #388.)
_MAX_DETAIL_FETCHES_PER_COMPANY = 20


class WorkableSource(BaseJobSource):
    name = "workable"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else WORKABLE_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        detail_budget = _MAX_DETAIL_FETCHES
        for slug in self._companies:
            # Reset per company, then bounded by the shared budget as well: a
            # company cannot starve the others, and the run total still holds.
            company_detail_budget = _MAX_DETAIL_FETCHES_PER_COMPANY
            # Job-understanding fix (2026-08-16): the POST /api/v2/.../jobs
            # endpoint (old code) returns `description` empty on every row
            # (verified live, 2026-08-08) AND caps at 10 results per page with
            # no cursor this source ever followed — abm-careers alone has 178
            # postings, so at most 10 were ever read.
            # The public widget endpoint `GET /api/v1/widget/accounts/{slug}`
            # (no auth, verified live 2026-08-16) returns EVERY posting in one
            # call — abm-careers: 178/178 — each carrying a real HTML
            # `description` (huggingface sample: 1,800+ chars), plus
            # `employment_type` and `experience` (seniority-ish free text)
            # neither of which the old endpoint exposed at all.
            url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
            data = await self._get_json(url, params={"details": "true"})
            if not data or "jobs" not in data:
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in cast(dict[str, Any], data)["jobs"]:
                title = item.get("title", "")
                # Field names measured against the LIVE widget endpoint
                # (2026-08-24, huggingface board, 7 postings). This matters:
                # the endpoint was switched to /api/v1/widget above, but the
                # field names below were still the ones the OLD POST /api/v2
                # endpoint used, and they DO NOT EXIST on this response:
                #
                #   location    0% present  (it is flat `city` + `country`)
                #   published   0% present  (it is `published_on`)
                #
                # So every Workable job was parsed with an EMPTY location — which
                # then fails `_is_uk_or_remote` below and drops the job — and an
                # unparseable date. Measured presence on the real response:
                # country 100%, city 57%, published_on 100%, created_at 100%,
                # shortcode 100%, application_url/shortlink/url 100%.
                city = item.get("city", "")
                country = item.get("country", "")
                location = ", ".join(p for p in (city, country) if p)
                # Kept: the detail fetch below is keyed on it.
                shortcode = item.get("shortcode", "")
                apply_url = (
                    item.get("application_url")
                    or item.get("shortlink")
                    or item.get("url", "")
                    or f"https://apply.workable.com/{slug}/j/{shortcode}/"
                )
                raw_published = item.get("published_on") or item.get("created_at")
                # The list response carries a real HTML description on 100% of
                # rows. The detail endpoint carries MORE (requirements +
                # benefits, 2,168-7,840 chars), so detail still wins when the
                # budget allows — but this is the floor, so a skipped or failed
                # detail fetch no longer means an empty description.
                list_desc = _HTML_TAG_RE.sub(" ", item.get("description") or "").strip()
                posted_at, confidence = normalize_posted_at(raw_published)

                # Issue #334: this used to read `shortDescription` off the LIST
                # response, under a comment asserting the field "genuinely does
                # not exist on this endpoint". The comment was right about the
                # field and wrong about the conclusion — every one of the 115
                # workable rows in prod was stored with an empty description,
                # which zeroes the scorer's 40-point skill component, and the
                # source has been 115/115 empty since its first row.
                #
                # The text is on the per-job DETAIL endpoint, which the adapter
                # simply never called (live probe 2026-08-19 across 5 company
                # slugs: description 2,168-7,840 chars, plus `requirements` and
                # `benefits`, on every one). Only UK/remote-relevant jobs are
                # detail-fetched, so the extra request count matches what we
                # actually keep, and a failed detail fetch degrades to an empty
                # description — never a dropped job.
                desc = list_desc
                if (
                    _is_uk_or_remote(location)
                    and detail_budget > 0
                    and company_detail_budget > 0
                ):
                    detail_budget -= 1
                    company_detail_budget -= 1
                    desc = await self._fetch_posting_text(slug, shortcode) or list_desc

                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=desc[:5000],
                    apply_url=apply_url,
                    source=self.name,
                    date_found=datetime.now(timezone.utc).isoformat(),
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_published,
                    # Raw upstream values — services/shelf_gate.py normalises.
                    employment_type=item.get("employment_type"),
                    seniority=item.get("experience"),
                    # `telecommuting` is a bool ALWAYS present on this API
                    # (verified live 2026-08-17, 4 boards: 100% key
                    # presence). True is an unambiguous "remote" translation
                    # of the source's own field; False just means "not
                    # remote-only" (could be hybrid or onsite), so it is left
                    # unset rather than guessed as either (rule #29).
                    workplace_mode="remote" if item.get("telecommuting") else None,
                    # `function` ("Sales"/"Marketing"/"Product Management"/
                    # "Engineering"/...) is Workable's own job-function field,
                    # verified live 2026-08-17 across 5 boards. Passed
                    # through raw: shelf_gate.py's closed JobCategory enum
                    # only matches the unambiguous ones (sales, marketing,
                    # product_management) — "Engineering" deliberately stays
                    # unmatched, since Workable spans every industry and
                    # "Engineering" is not always software.
                    category=item.get("function") or None,
                ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Workable: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs

    async def _fetch_posting_detail(self, slug: str, shortcode: str) -> dict[str, Any]:
        """Fetch one posting's raw detail JSON from the public detail endpoint.

        Returns ``{}`` on any failure — callers treat a missing detail as an
        absent description, never an error.
        """
        if not slug or not shortcode:
            return {}
        detail = await self._get_json(
            f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{shortcode}"
        )
        return detail if isinstance(detail, dict) else {}

    @staticmethod
    def _extract_description_text(detail: dict[str, Any]) -> str:
        """Concatenate the detail payload's prose fields, tag-stripped.

        ``description`` alone already clears the 200-char scoring floor, but
        ``requirements`` is where the skills a job actually asks for are
        written, and the skill matcher is the reason #334 matters — so both are
        kept. All three fields are HTML.
        """
        parts: list[str] = []
        for key in ("description", "requirements", "benefits"):
            text = detail.get(key)
            if text:
                parts.append(_HTML_TAG_RE.sub(" ", str(text)))
        return " ".join(parts)[:5000].strip()

    async def _fetch_posting_text(self, slug: str, shortcode: str) -> str:
        """Fetch one posting's full text from the public detail endpoint.

        Kept as its own method (rather than inlined) because
        ``src/services/description_backfill.py`` calls this directly by name to
        re-fetch a thin stored description outside the normal ingestion pass —
        changing this signature/return type would break that caller. Returns
        ``""`` on any failure; absence of text is a data gap, not an error.
        """
        return self._extract_description_text(
            await self._fetch_posting_detail(slug, shortcode)
        )
