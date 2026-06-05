"""Generic Schema.org JSON-LD JobPosting harvester — copy_roadmap.md Tier-2 #3.

Thousands of company career pages embed machine-readable job data as
``<script type="application/ld+json">`` blocks (Schema.org ``JobPosting``).
This module extracts those blocks from arbitrary HTML and maps each JobPosting
to a Job — the low-maintenance long-tail extraction method competitors use
(``competitor_sourcing_matrix.md`` 🚩THEIRS row "Schema.org JSON-LD harvest").

Scope: this is the *extraction core* only. Wiring it into ``SOURCE_REGISTRY``
as a ``BaseJobSource`` is deferred because it would rotate the source count
(50→51), which the hardcoded count assertions in ``test_cli.py`` / ``test_api.py``
guard (CLAUDE.md rules #8/#13) — and ``test_api.py`` can't run on the current
machine. The functions here are pure (no HTTP), mirroring the JobPosting→Job
mapping already used by ``sources/apis_free/teaching_vacancies.py``.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from src.models import Job

__all__ = ["extract_jsonld_blocks", "parse_jobposting", "harvest_jobs"]

_SCRIPT_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _is_type(node: dict, typename: str) -> bool:
    """Schema.org ``@type`` may be a string or a list of strings."""
    t = node.get("@type")
    if isinstance(t, list):
        return typename in t
    return t == typename


def extract_jsonld_blocks(html: str | None) -> list[dict]:
    """Return every JSON object found in ``application/ld+json`` script tags.

    Flattens top-level arrays and ``@graph`` containers so each returned dict is
    a single node. Malformed JSON blocks are skipped (never raise).
    """
    out: list[dict] = []
    for match in _SCRIPT_RE.finditer(html or ""):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for node in candidates:
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph")
            if isinstance(graph, list):
                out.extend(g for g in graph if isinstance(g, dict))
            else:
                out.append(node)
    return out


def _to_float(value: object) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_salary(node: dict) -> tuple[float | None, float | None]:
    """Map ``baseSalary`` (MonetaryAmount) → (min, max). Tolerant of shapes."""
    base = node.get("baseSalary")
    if not isinstance(base, dict):
        return (None, None)
    value = base.get("value")
    if isinstance(value, dict):
        single = value.get("value")
        mn = value.get("minValue", single)
        mx = value.get("maxValue", single)
        return (_to_float(mn), _to_float(mx))
    flat = _to_float(value)
    return (flat, flat)


def _parse_company(node: dict) -> str:
    org = node.get("hiringOrganization")
    if isinstance(org, dict):
        return (org.get("name") or "").strip()
    if isinstance(org, str):
        return org.strip()
    return ""


def _parse_location(node: dict) -> str:
    loc = node.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    if isinstance(loc, dict):
        addr = loc.get("address")
        if isinstance(addr, dict):
            return (
                addr.get("addressLocality")
                or addr.get("addressRegion")
                or addr.get("addressCountry")
                or ""
            ).strip()
        if isinstance(addr, str):
            return addr.strip()
    if isinstance(loc, str):
        return loc.strip()
    return ""


def parse_jobposting(node: dict, *, source: str, date_found: str) -> Job | None:
    """Map a single Schema.org JobPosting dict to a Job.

    Returns ``None`` if ``node`` is not a JobPosting or has no usable title —
    title + a stable URL/company are the minimum for downstream dedup.
    """
    if not _is_type(node, "JobPosting"):
        return None
    title = (node.get("title") or "").strip()
    if not title:
        return None

    smin, smax = _parse_salary(node)
    posted = node.get("datePosted") or None
    description = node.get("description") or ""
    if not isinstance(description, str):
        description = str(description)

    return Job(
        title=title[:300],
        company=_parse_company(node)[:200],
        apply_url=(node.get("url") or "")[:1000],
        source=source,
        date_found=date_found,
        location=_parse_location(node)[:200],
        description=description[:5000],
        salary_min=smin,
        salary_max=smax,
        posted_at=posted,
        date_confidence="high" if posted else "low",
    )


def harvest_jobs(
    html: str | None,
    *,
    source: str = "jsonld",
    date_found: str | None = None,
) -> list[Job]:
    """Extract every JobPosting from ``html`` and map to Job objects."""
    if date_found is None:
        date_found = datetime.now(timezone.utc).isoformat()
    jobs: list[Job] = []
    for node in extract_jsonld_blocks(html):
        if _is_type(node, "JobPosting"):
            job = parse_jobposting(node, source=source, date_found=date_found)
            if job is not None:
                jobs.append(job)
    return jobs
