"""Automated ATS company-slug discovery — copy_roadmap.md Tier-1 #1.

Probes public ATS endpoints to validate candidate company slugs *before* they
are added to ``core/companies.py``. This closes the single biggest sourcing gap
identified in ``docs/research/competitor_sourcing_matrix.md``: competitors crawl
10K-150K companies while Job360 hand-curates ~268 ATS slugs. The expensive half
(the ATS parsers in ``src/sources/ats/``) already exists — discovery just finds
*more companies* to point those parsers at.

Design:
  * ``normalize_slug`` / ``merge_slugs`` are pure and offline-testable.
  * ``probe_slug`` / ``discover`` take an injected ``aiohttp.ClientSession`` so
    tests mock HTTP with ``aioresponses`` (CLAUDE.md rule #4 — no live HTTP).
  * A slug is "valid" when its ATS endpoint returns >= ``min_jobs`` live roles.
    Invalid / unknown / empty / error responses all collapse to a count of 0,
    mirroring ``BaseJobSource``'s graceful no-op contract.

This is a discovery helper, not a ``BaseJobSource`` — it does NOT touch the
five load-bearing source surfaces (CLAUDE.md rule #8/#13). It feeds the slug
catalog that those surfaces already consume.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

import aiohttp

__all__ = [
    "ATS_PROBES",
    "ATSProbe",
    "normalize_slug",
    "merge_slugs",
    "probe_slug",
    "discover",
]


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
_SLUG_SAFE = re.compile(r"[^a-z0-9._-]+")


def normalize_slug(raw: str | None) -> str:
    """Lowercase, trim, and collapse a raw company name/slug to ATS-safe chars.

    ``"Octopus Energy"`` -> ``"octopus-energy"``. Non-slug runs become a single
    hyphen; leading/trailing hyphens are stripped. ``None``/empty -> ``""``.
    """
    s = (raw or "").strip().lower()
    s = _SLUG_SAFE.sub("-", s)
    return s.strip("-")


def merge_slugs(existing: Iterable[str], discovered: Iterable[str]) -> list[str]:
    """Return the *new* normalized slugs (sorted) present in ``discovered`` but
    not already in ``existing``. Case-insensitive; empties dropped; deduped."""
    seen = {normalize_slug(s) for s in existing}
    seen.discard("")
    new = {
        norm
        for s in discovered
        if (norm := normalize_slug(s)) and norm not in seen
    }
    return sorted(new)


# --------------------------------------------------------------------------- #
# ATS probe registry
# --------------------------------------------------------------------------- #
def _count_jobs_key(data: object) -> int:
    """Greenhouse / Ashby: ``{"jobs": [...]}``."""
    return len(data["jobs"]) if isinstance(data, dict) and isinstance(data.get("jobs"), list) else 0


def _count_list(data: object) -> int:
    """Lever / Comeet / Rippling: bare JSON list of postings."""
    return len(data) if isinstance(data, list) else 0


def _count_results(data: object) -> int:
    """Workable: ``{"results": [...]}``."""
    return len(data["results"]) if isinstance(data, dict) and isinstance(data.get("results"), list) else 0


def _count_offers(data: object) -> int:
    """Recruitee: ``{"offers": [...]}``."""
    return len(data["offers"]) if isinstance(data, dict) and isinstance(data.get("offers"), list) else 0


def _count_smartrecruiters(data: object) -> int:
    """SmartRecruiters: prefer ``totalFound``, fall back to ``content`` length."""
    if not isinstance(data, dict):
        return 0
    total = data.get("totalFound")
    if isinstance(total, int):
        return total
    content = data.get("content")
    return len(content) if isinstance(content, list) else 0


@dataclass(frozen=True)
class ATSProbe:
    """How to validate a slug against one ATS: a URL template + a job counter."""

    name: str
    url_template: str  # contains "{slug}"
    count: Callable[[object], int]


ATS_PROBES: Mapping[str, ATSProbe] = {
    "greenhouse": ATSProbe(
        "greenhouse",
        "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        _count_jobs_key,
    ),
    "lever": ATSProbe(
        "lever",
        "https://api.lever.co/v0/postings/{slug}?mode=json",
        _count_list,
    ),
    "workable": ATSProbe(
        "workable",
        "https://apply.workable.com/api/v2/accounts/{slug}/jobs",
        _count_results,
    ),
    "ashby": ATSProbe(
        "ashby",
        "https://api.ashbyhq.com/posting-api/job-board/{slug}",
        _count_jobs_key,
    ),
    "smartrecruiters": ATSProbe(
        "smartrecruiters",
        "https://api.smartrecruiters.com/v1/companies/{slug}/postings",
        _count_smartrecruiters,
    ),
    "recruitee": ATSProbe(
        "recruitee",
        "https://{slug}.recruitee.com/api/offers/",
        _count_offers,
    ),
    "comeet": ATSProbe(
        "comeet",
        "https://www.comeet.co/careers-api/2.0/company/{slug}/positions",
        _count_list,
    ),
    "rippling": ATSProbe(
        "rippling",
        "https://ats.rippling.com/api/board/{slug}/jobs",
        _count_list,
    ),
}


def _require_probe(ats: str) -> ATSProbe:
    try:
        return ATS_PROBES[ats]
    except KeyError:
        raise ValueError(
            f"Unknown ATS {ats!r}. Known: {sorted(ATS_PROBES)}"
        ) from None


# --------------------------------------------------------------------------- #
# Async probes
# --------------------------------------------------------------------------- #
async def probe_slug(
    session: aiohttp.ClientSession,
    ats: str,
    slug: str,
    *,
    timeout: float = 10.0,
) -> int:
    """Return the live job count for ``slug`` on ``ats`` (0 if invalid/empty/error).

    Raises ``ValueError`` for an unknown ``ats``. All network/parse failures are
    swallowed to 0 — discovery probes must never raise on a dead slug.
    """
    probe = _require_probe(ats)
    slug = normalize_slug(slug)
    if not slug:
        return 0
    url = probe.url_template.format(slug=slug)
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            if resp.status != 200:
                return 0
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return 0
    try:
        return int(probe.count(data) or 0)
    except (TypeError, ValueError):
        return 0


async def discover(
    session: aiohttp.ClientSession,
    ats: str,
    candidate_slugs: Iterable[str],
    *,
    min_jobs: int = 1,
    concurrency: int = 5,
) -> dict[str, int]:
    """Probe many candidate slugs concurrently for one ATS.

    Returns ``{normalized_slug: job_count}`` for every candidate whose endpoint
    reports ``>= min_jobs`` roles. Duplicates (case-insensitive) are collapsed;
    a bounded semaphore keeps concurrent requests polite.
    """
    _require_probe(ats)  # fail fast on unknown ATS

    # De-dupe + normalize while preserving first-seen order.
    seen: set[str] = set()
    norm_slugs: list[str] = []
    for candidate in candidate_slugs:
        norm = normalize_slug(candidate)
        if norm and norm not in seen:
            seen.add(norm)
            norm_slugs.append(norm)

    sem = asyncio.Semaphore(concurrency)

    async def _one(slug: str) -> tuple[str, int]:
        async with sem:
            return slug, await probe_slug(session, ats, slug)

    results = await asyncio.gather(*(_one(s) for s in norm_slugs))
    return {slug: count for slug, count in results if count >= min_jobs}
