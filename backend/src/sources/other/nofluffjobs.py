import html
import logging
import re
from datetime import datetime, timezone
from typing import Any

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.nofluffjobs")

_MAX_RESULTS = 200
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# NoFluffJobs API endpoints to try (the public API is unofficial and may change)
_API_URLS = [
    "https://nofluffjobs.com/api/posting",
    "https://nofluffjobs.com/api/search/posting",
]

# Per-posting detail endpoint. Issue #334: the LIST payload carries NO body
# text at all — a live probe over 1,000 postings (2026-08-19) found the longest
# string on any list item was the 138-char `id`, and the only text-ish keys are
# id/url/title/name/category. So there was nothing for `fetch_jobs` to read and
# the adapter simply never set `description=`; all 40 nofluffjobs rows in prod
# are empty. The prose lives at `requirements.description` on this endpoint
# (2,117 chars on the probed posting); `details.description` exists but was
# empty on every posting sampled, so it is deliberately NOT read.
_DETAIL_URL = "https://nofluffjobs.com/api/posting/{posting_id}"

_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Detail-fetch budget per RUN, in the same shape as workday.py (40) /
# smartrecruiters.py (60) / workable.py (60). Without one, a run that hit
# _MAX_RESULTS would fire 200 extra requests at concurrent=2/delay=1.5
# (RATE_LIMITS) — about 150s, which starts crowding the fetch timeout. Only
# jobs that already survived the UK/remote filter are fetched, so this budget
# is spent on rows we are actually keeping; prod holds 40 nofluffjobs rows
# total, so 40 covers the real volume with room to spare. Anything past it is
# picked up later by services/description_backfill.py.
_MAX_DETAIL_FETCHES = 40


def _plausible_gbp(val: Any) -> bool:
    """A loose sanity bound for a GBP annual salary figure -- used only when
    salary.currency is absent, to decide whether a bare number is safe to
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
        detail_budget = _MAX_DETAIL_FETCHES
        for item in postings:
            title = item.get("title", "")
            # NOTE (live probe 2026-08-08): this used to fall back to name
            # with the comment "some responses use name instead of title".
            # That is WRONG -- name is the EMPLOYER, not an alias for the
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

            # Skip bare "Remote" or empty location -- NoFluffJobs is Polish-focused
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
            # NoFluffJobs is Polish-focused -- 19,229 of 20,631 postings are
            # priced in PLN, and salary.currency is 100% filled. Job has no
            # currency field, so a bare PLN number stored into
            # salary_min/salary_max would silently be compared as GBP
            # downstream (a multi-x overstatement). Only trust the value when
            # it is GBP, or when currency is entirely absent AND the value is
            # a plausible GBP figure -- never convert here (no FX in this
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

            # seniority[] (100% fill live, e.g. ["Senior"]) -- take the first
            # element. Zero extra HTTP cost, this list is already fetched.
            # Feeds the legacy free-text `experience_level` (unchanged) AND
            # -- new -- the closed-enum `seniority` shelf: same raw string,
            # no interpretation. Verified live 2026-08-17 (1,000-posting
            # sample): Senior/Mid/Junior match SeniorityLevel exactly
            # (93.1% of the sample); "Expert" does not map to a single tier
            # and is deliberately left unmatched by the gate rather than
            # guessed here.
            seniority_raw = item.get("seniority")
            if isinstance(seniority_raw, list) and seniority_raw:
                experience_level = str(seniority_raw[0])
                seniority_scalar = str(seniority_raw[0])
            else:
                experience_level = ""
                seniority_scalar = None

            # `fullyRemote` (100% fill live) is NoFluffJobs' own boolean.
            # Only the TRUE case is mapped -- False just means "not tagged
            # fully remote", not "definitely onsite/hybrid" (rule #29).
            workplace_mode = "Remote" if item.get("fullyRemote") else None

            # `category` (100% fill live, e.g. "sales", "finance", "backend")
            # is NoFluffJobs' own function/domain tag -- closest thing to the
            # JobCategory shelf this listing endpoint exposes. Raw value
            # only; the gate matches the (small) subset that is an exact
            # synonym ("sales", "finance") and leaves the rest honestly
            # unmapped rather than guessing "backend" -> software_engineering.
            category_raw = item.get("category")

            # Live probe 2026-08-08: the company key does NOT exist in the
            # posting payload (0 of 20,631 items had it) -- the employer name
            # is carried in name. Reading company meant every NoFluffJobs job
            # was stored with an empty company. Prefer name, keep company as
            # a fallback in case the upstream schema changes back.
            company_name = item.get("name") or item.get("company", "")

            # tiles.values[] (100% fill, verified live 2026-08-16 across
            # 21,739 postings) carries category + skill/requirement tags on
            # every posting, e.g. [{"value": "HubSpot", "type": "requirement"}].
            # Raw values only, straight onto source_tags -- the job own
            # vocabulary, no guessing.
            tiles = item.get("tiles") or {}
            tile_values = tiles.get("values") if isinstance(tiles, dict) else None
            source_tags = [
                str(v.get("value")) for v in (tile_values or [])
                if isinstance(v, dict) and v.get("value")
            ]

            # Issue #334 — the list endpoint above has NO description field at
            # all, so description was never passed to Job() here, which also
            # silently disabled visa and deadline extraction downstream (both
            # read job.description). The prose sits on the per-posting detail
            # endpoint (see _DETAIL_URL -> requirements.description, 100% hit in
            # a spot-check), budgeted the same way as smartrecruiters/devitjobs
            # so an uncapped pass cannot blow the fetch ceiling. A failed detail
            # fetch degrades to an empty description, never a dropped job.
            #
            # ONE fetch, three uses: the raw detail dict is fetched once and
            # then read for description, skills and deadline. `expiresAt` (100%
            # hit in spot-checks, ISO "2026-08-19T23:59:59") therefore rides the
            # SAME response at zero extra HTTP cost. It only covers the
            # detail_budget subset, not every posting; the rest stay honestly
            # absent rather than spending a second budget on it (rule #29).
            description = ""
            deadline = None
            deadline_source = None
            if posting_id and detail_budget > 0:
                detail_budget -= 1
                detail = await self._fetch_posting_detail(str(posting_id))
                description = self._extract_detail_description(detail)
                raw_expires = detail.get("expiresAt")
                expires_iso, expires_confidence = normalize_posted_at(raw_expires)
                if expires_confidence == "high" and expires_iso:
                    deadline = expires_iso[:10]
                    deadline_source = "listing"

            jobs.append(Job(
                title=title,
                company=company_name,
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
                salary_min=salary_min,
                salary_max=salary_max,
                experience_level=experience_level,
                seniority=seniority_scalar,
                workplace_mode=workplace_mode,
                category=category_raw,
                source_tags=source_tags,
            ))

            if len(jobs) >= _MAX_RESULTS:
                logger.info("NoFluffJobs: hit cap of %s results", _MAX_RESULTS)
                break

        logger.info("NoFluffJobs: found %s relevant jobs", len(jobs))
        return jobs

    async def _fetch_posting_detail(self, posting_id: str) -> dict:
        """Fetch one posting's RAW detail JSON. Returns ``{}`` on any failure.

        Split from ``_fetch_posting_text`` so the caller can read description,
        skills AND ``expiresAt`` out of a single response instead of paying for
        the same fetch twice. Callers treat a missing detail as absent data,
        never an error.
        """
        if not posting_id:
            return {}
        detail = await self._get_json(_DETAIL_URL.format(posting_id=posting_id))
        return detail if isinstance(detail, dict) else {}

    @staticmethod
    def _extract_detail_description(detail: dict) -> str:
        """Body text from a raw detail response — prose plus the asked-for skills.

        The prose lives at ``requirements.description`` (HTML) — verified live
        2026-08-16, 15/15 sampled postings hit.

        ``requirements.musts`` / ``.nices`` are appended because they are the
        skills the ad actually asks for, and the 40-point skill component is the
        whole reason an empty description matters. They are genuinely fetched ad
        content, not padding — nothing here writes text the posting did not say.
        """
        block = detail.get("requirements")
        if not isinstance(block, dict):
            return ""
        parts: list[str] = []
        body = block.get("description")
        if body:
            parts.append(_HTML_TAG_RE.sub(" ", html.unescape(str(body))))
        for key, label in (("musts", "Must have"), ("nices", "Nice to have")):
            values = [
                str(entry.get("value"))
                for entry in (block.get(key) or [])
                if isinstance(entry, dict) and entry.get("value")
            ]
            if values:
                parts.append(f"{label}: {', '.join(values)}")
        return " ".join(parts)[:5000].strip()

    async def _fetch_posting_text(self, posting_id: str) -> str:
        """Fetch one posting's body text from the per-posting detail endpoint.

        Kept as its own method with this exact name because
        ``src/services/description_backfill.py`` calls it directly to refill a
        thin stored row — changing this signature or return type would break
        that caller. Now a thin wrapper over ``_fetch_posting_detail`` +
        ``_extract_detail_description`` so the backfill path and the fetch path
        can never drift apart in what they consider "the text".
        """
        return self._extract_detail_description(
            await self._fetch_posting_detail(posting_id)
        )
