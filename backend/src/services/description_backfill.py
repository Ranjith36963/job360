"""Re-fetch fresh description text for jobs whose stored text is too thin to
score, enrich, or judge meaningfully.

WHY THIS EXISTS (measured in prod, 2026-08-07): sources fetch job-detail
pages under strict PER-RUN budgets (``_MAX_DETAIL_FETCHES`` — workday.py 40,
smartrecruiters.py 60, linkedin.py 30) so one run never blows the 240s ATS
timeout. A job that lands past that budget is stored with an empty/short
description, and NOTHING in the pipeline ever goes back for it — a source
only re-fetches a stored job if it happens to reappear in a LATER run's
listing. Coverage of every LLM-enriched field (workplace/seniority/visa)
tracks description length almost perfectly, so 1,311 active jobs (30% of the
catalog) carrying under 200 chars of description are functionally
unscoreable. This module is the "go back for it" step. It is called from the
``enrichment_sweep`` cron's BACKFILL phase (``workers/tasks.py``) — never a
separate task, per the instruction to extend the existing self-heal loop
rather than add a new cron surface.

Design principle: reuse each source's OWN parsing logic; never duplicate
HTML-tag-stripping or JSON-field-extraction here. Each allowed source already
exposes (or, for greenhouse, gains one narrow addition) a per-JOB detail-fetch
method — the ingestion path just never needed to call it again AFTER storage.
This module's only genuinely new logic is reconstructing that method's call
arguments FROM a job's stored ``apply_url``, since nothing before now needed
to reverse that encoding.

TERMINAL STATE — real counter, not a padded description (2026-08-07). The
first version of this module tried to avoid a schema migration by padding a
still-thin ``description`` with trailing spaces past the selection floor so
the job would stop being reselected. That was REJECTED in review: it fakes
coverage. ``src/services/coverage.py::_has_skill_value`` counts any
``description`` longer than ``_SKILL_TEXT_MIN_CHARS`` (200) as real skill-text
coverage with no whitespace check, so a padded row started reporting "we
understand this job's skills" when it did not — the exact class of bug a
same-day batch (``coverage.py``'s own docstring) had just spent effort
eliminating (a shelf X-ray predicate once counted an empty JSON shell as
"salary present": reported 83%, real figure ~5%). The padding was also fed to
the LLM judge and the keyword scorer as if it were content, and rendered to
real users on the job card. The fix is migration 0029
(``jobs.description_backfill_attempts``, INTEGER DEFAULT 0): real, restart-
durable state that lives OUTSIDE ``description``. ``description`` is now
NEVER written except with genuinely fetched (and whitespace-stripped) text.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp

from src.core.companies import (
    GREENHOUSE_COMPANIES,
    SMARTRECRUITERS_COMPANIES,
    WORKABLE_COMPANIES,
    WORKDAY_COMPANIES,
)

logger = logging.getLogger("job360.services.description_backfill")

# Selection floor shared with workers/tasks.py's SQL
# (`length(description) < MIN_DESCRIPTION_CHARS`) and the "did this fetch
# actually help" check below — a single constant so the two can never drift
# apart. Also happens to equal coverage.py's `_SKILL_TEXT_MIN_CHARS` (not a
# coincidence: this IS the floor that decides whether the skill-matcher
# considers a description real text).
MIN_DESCRIPTION_CHARS = 200

# Real per-job retry cap (migration 0029, jobs.description_backfill_attempts).
# A job fetched this many times without ever clearing MIN_DESCRIPTION_CHARS
# stops being selected — see the SELECT predicate in
# workers/tasks.py::_backfill_thin_descriptions.
MAX_BACKFILL_ATTEMPTS = 3

# PER-SOURCE CAPABILITY FLAG (non-negotiable per spec) — some sources
# structurally cannot give more text than they already did, and hammering a
# scraper from an unattended background sweep (rather than a human-triggered
# search) risks getting it blocked. Every entry below states WHY it is in.
ALLOWED_BACKFILL_SOURCES: frozenset[str] = frozenset({
    # IN — static company registry (core/companies.py) + a real per-JOB
    # detail endpoint that ingestion already proved works. Re-deriving the
    # call args from apply_url is an EXACT reversal (see _refetch_workday),
    # so this is a single, precise HTTP call per job — not a re-list.
    "workday",
    # IN — same shape as workday: apply_url encodes (slug, posting_id)
    # whenever it follows SmartRecruiters' own public URL pattern.
    "smartrecruiters",
    # IN — apply_url encodes (slug, job_id); a new narrow per-job endpoint
    # was added to GreenhouseSource (fetch_jobs() only ever listed a whole
    # board before).
    "greenhouse",
    # IN — single flat catalog (no per-company partition): ONE HTTP call
    # refreshes the WHOLE feed, so it can backfill every thin devitjobs job
    # selected this tick for the cost of one request. Description is
    # COMPOSED from structured fields (technologies/tags/etc), not prose, so
    # some rows will stay thin forever if the upstream API genuinely has
    # nothing more for them — that is a real ceiling of the source, not a
    # bug in this module.
    "devitjobs",
    # IN (issue #334) — same shape as smartrecruiters: apply_url is built at
    # ingestion as f"https://apply.workable.com/{slug}/j/{shortcode}/", an
    # EXACT, lossless encoding of both call arguments. Workable was neither
    # IN nor documented OUT before — it was simply missing, so all 115 of its
    # prod rows (100% of the source) were empty with nothing to mop them up.
    "workable",
    # IN (issue #334) — apply_url is f"https://nofluffjobs.com/job/{id}" and
    # the detail endpoint keys off that same id, so the reversal is exact and
    # costs one precise HTTP call per job.
    "nofluffjobs",
})
# OUT, by explicit spec mandate — LinkedIn is a scraper (regex over guest
# HTML, no official API). Hammering it from an unattended 30-min cron risks
# the shared IP/UA getting rate-limited or blocked, which would also break
# the LIVE search path real users depend on. LinkedInSource already has its
# own _fetch_description(view_url) and it WOULD work mechanically — this is
# a deliberate exclusion, not a technical gap.
#
# OUT — hn_jobs reads the read-only Algolia HN Search API, which only ever
# exposes a Show/Ask HN post's title + URL. There is no body-text field to
# re-fetch, so an attempt could never produce more text than is already
# stored — it would just waste budget every tick.
#
# OUT — aijobs_ai is an HTML-scraper source whose LISTING page IS the detail
# page (no separate per-job URL exists to fetch); re-fetching would return
# the identical content every time.

_GREENHOUSE_PATH_RE = re.compile(r"/([^/]+)/jobs/(\d+)")
_SMARTRECRUITERS_PATH_RE = re.compile(r"smartrecruiters\.com/([^/]+)/([^/?#]+)")
_WORKABLE_PATH_RE = re.compile(r"apply\.workable\.com/([^/]+)/j/([^/?#]+)")
_NOFLUFFJOBS_PATH_RE = re.compile(r"nofluffjobs\.com/job/([^/?#]+)")


def _normalize_slug(value: str) -> str:
    """Fold a slug down to bare alnum chars.

    Needed because a stored ``apply_url`` and the canonical
    ``core/companies.py`` entry can disagree on casing/dashes — e.g.
    SmartRecruiters' own ``ref`` links sometimes CamelCase a company that the
    postings-list API slugs with dashes (``SamsungRAndDInstituteUk`` vs
    ``samsung-r-and-d-institute-uk``). Comparing normalized forms lets the
    lookup succeed anyway, while still resolving to the CANONICAL registry
    slug the API call actually needs.
    """
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _match_registry_slug(candidate: str, registry: list[str]) -> Optional[str]:
    norm = _normalize_slug(candidate)
    for slug in registry:
        if _normalize_slug(slug) == norm:
            return slug
    return None


async def _refetch_workday(apply_url: str, session: aiohttp.ClientSession) -> Optional[str]:
    """Reconstruct the CXS detail call from a stored Workday apply_url.

    ``apply_url`` was built at ingestion as ``f"{base_url}/en-US{ext_path}"``
    (workday.py's ``fetch_jobs``) — an EXACT, lossless encoding of
    ``ext_path``, so stripping the ``/en-US`` prefix back off recovers the
    identical value the detail endpoint needs. ``wd``/``site`` never appear
    in the URL at all, so they come from the static WORKDAY_COMPANIES
    registry, matched by tenant (the URL's first host label).
    """
    from src.sources.ats.workday import WorkdaySource

    parsed = urlparse(apply_url)
    host = parsed.hostname or ""
    tenant = host.split(".")[0] if host else ""
    if not tenant or not parsed.path:
        return None
    entry = next((e for e in WORKDAY_COMPANIES if e["tenant"] == tenant), None)
    if entry is None:
        return None
    ext_path = parsed.path[len("/en-US"):] if parsed.path.startswith("/en-US") else parsed.path
    base_url = f"https://{tenant}.{entry['wd']}.myworkdayjobs.com"
    source = WorkdaySource(session)
    return await source._fetch_job_description(base_url, tenant, entry["site"], ext_path)


async def _refetch_smartrecruiters(apply_url: str, session: aiohttp.ClientSession) -> Optional[str]:
    """Reconstruct the (slug, posting_id) pair a stored SmartRecruiters
    apply_url encodes, when it follows the public
    ``jobs.smartrecruiters.com/{slug}/{id}`` shape — SmartRecruitersSource's
    own fallback format, and also what most ``ref`` links from the API use.
    A URL that doesn't match (a company's own custom careers-page ref) can't
    be reversed — return None rather than guess.
    """
    from src.sources.ats.smartrecruiters import SmartRecruitersSource

    m = _SMARTRECRUITERS_PATH_RE.search(apply_url)
    if not m:
        return None
    slug = _match_registry_slug(m.group(1), SMARTRECRUITERS_COMPANIES)
    if slug is None:
        return None
    posting_id = m.group(2)
    source = SmartRecruitersSource(session)
    return await source._fetch_posting_text(slug, posting_id)


async def _refetch_greenhouse(apply_url: str, session: aiohttp.ClientSession) -> Optional[str]:
    """Reconstruct (slug, job_id) from a stored Greenhouse absolute_url and
    hit the per-job detail endpoint added to GreenhouseSource for this."""
    from src.sources.ats.greenhouse import GreenhouseSource

    m = _GREENHOUSE_PATH_RE.search(apply_url)
    if not m:
        return None
    slug = _match_registry_slug(m.group(1), GREENHOUSE_COMPANIES)
    if slug is None:
        return None
    job_id = m.group(2)
    source = GreenhouseSource(session)
    return await source._fetch_job_content(slug, job_id)


async def _refetch_workable(apply_url: str, session: aiohttp.ClientSession) -> Optional[str]:
    """Reconstruct the (slug, shortcode) pair a stored Workable apply_url
    encodes. ``WorkableSource.fetch_jobs`` builds it as
    ``f"https://apply.workable.com/{slug}/j/{shortcode}/"``, so the reversal is
    exact — no guessing. The slug is still resolved through the canonical
    ``WORKABLE_COMPANIES`` registry (same reason as SmartRecruiters: casing and
    dashes can disagree between a stored URL and the registry entry the API
    call needs).
    """
    from src.sources.ats.workable import WorkableSource

    m = _WORKABLE_PATH_RE.search(apply_url)
    if not m:
        return None
    slug = _match_registry_slug(m.group(1), WORKABLE_COMPANIES)
    if slug is None:
        return None
    source = WorkableSource(session)
    return await source._fetch_posting_text(slug, m.group(2))


async def _refetch_nofluffjobs(apply_url: str, session: aiohttp.ClientSession) -> Optional[str]:
    """Reconstruct the posting id a stored NoFluffJobs apply_url encodes.

    ``NoFluffJobsSource`` builds it as ``f"https://nofluffjobs.com/job/{id}"``
    from the very id the detail endpoint keys off, so this is a straight
    read-back of one path segment.
    """
    from src.sources.other.nofluffjobs import NoFluffJobsSource

    m = _NOFLUFFJOBS_PATH_RE.search(apply_url)
    if not m:
        return None
    source = NoFluffJobsSource(session)
    return await source._fetch_posting_text(m.group(1))


async def _refetch_devitjobs(
    apply_url: str, session: aiohttp.ClientSession, *, cache: dict[str, dict[str, str]]
) -> Optional[str]:
    """DevITjobs has ONE flat catalog (no per-company split), so the whole
    listing is fetched at most ONCE per sweep tick — ``cache`` is populated
    lazily by the first devitjobs job this tick and every subsequent
    devitjobs job is then a free dict lookup against it.
    """
    from src.sources.apis_free.devitjobs import DevITJobsSource

    if "_data" not in cache:
        source = DevITJobsSource(session)
        try:
            jobs = await source.fetch_jobs()
        except Exception:  # noqa: BLE001 — one bad listing must not break the tick
            logger.warning("devitjobs backfill listing fetch failed", exc_info=True)
            jobs = []
        cache["_data"] = {j.apply_url: j.description for j in jobs if j.apply_url}
    return cache["_data"].get(apply_url)


async def fetch_description(
    job_row: dict[str, Any],
    session: aiohttp.ClientSession,
    *,
    devitjobs_cache: dict[str, dict[str, str]],
) -> Optional[str]:
    """Attempt to fetch fresh description text for ONE thin job row.

    Returns the new text (the caller decides whether it is "long enough" —
    this function's only job is FETCHING, not judging) or ``None`` when the
    source is out of scope, the ``apply_url`` can't be mapped back to a
    fetchable identifier, or the fetch itself failed. Never raises — a
    single bad row must never abort the sweep.
    """
    source_name = job_row.get("source")
    apply_url = job_row.get("apply_url") or ""
    if source_name not in ALLOWED_BACKFILL_SOURCES or not apply_url:
        return None
    try:
        if source_name == "workday":
            return await _refetch_workday(apply_url, session)
        if source_name == "smartrecruiters":
            return await _refetch_smartrecruiters(apply_url, session)
        if source_name == "greenhouse":
            return await _refetch_greenhouse(apply_url, session)
        if source_name == "workable":
            return await _refetch_workable(apply_url, session)
        if source_name == "nofluffjobs":
            return await _refetch_nofluffjobs(apply_url, session)
        if source_name == "devitjobs":
            return await _refetch_devitjobs(apply_url, session, cache=devitjobs_cache)
    except Exception:  # noqa: BLE001 — network/parse failures degrade to "no new text"
        logger.warning(
            "description backfill fetch failed for %s (%s)", apply_url, source_name, exc_info=True
        )
        return None
    return None
