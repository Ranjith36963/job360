import logging
import re
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from src.core.companies import COMPANY_NAME_OVERRIDES, PINPOINT_COMPANIES
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.pinpoint")

_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Pinpoint's own frequency vocabulary -> the four period spellings
# models.py:122 documents as this field's contract ("hourly" | "daily" |
# "monthly" | "annual"). This is a literal word-for-word translation of one
# closed, known field (compensation_frequency only ever says "hour"/"year" on
# the live boards checked 2026-08-16) — not a unit conversion and not a guess
# across ambiguous inputs, so it stays a source-side dumb map rather than
# gate policy. Unmapped/absent values are left None, never guessed.
_FREQUENCY_MAP = {
    "hour": "hourly",
    "day": "daily",
    "month": "monthly",
    "year": "annual",
}

# Pinpoint's OWN `employment_type_text` vocabulary is a compound
# "<contract nature> - <hours>" string (confirmed live 2026-08-17 across 5
# boards — arm/priorygroup/davies/networkplus/natcen: "Permanent - Full
# Time", "Fixed Term - Part Time", "Contract / Temp", "Bank", "Zero Hours",
# "Term Time"). shelf_gate.py's normaliser turns spaces/dashes into a single
# underscore, so "Permanent - Full Time" collapses to
# "permanent___full_time" and never matches any EmploymentType member —
# every non-bare "Full Time"/"Part Time"/"Contract" row was landing as
# absent/not_mapped despite carrying real data (measured baseline: 12.4%
# fill on 371 live rows, all from the few boards whose text happened to be
# bare "Full Time"). This is Pinpoint's own closed-ish vocabulary (same
# precedent as _FREQUENCY_MAP above), so it is translated here rather than
# widening the shared gate's separator-collapsing for one source's
# multi-segment format. "Bank" / "Zero Hours" / "Term Time" are genuine UK
# contract types with no EmploymentType equivalent — left OUT on purpose
# (rule #29), not silently forced onto the nearest guess.
_EMPLOYMENT_TYPE_MAP = {
    "permanent - full time": "full_time",
    "permanent - part time": "part_time",
    "fixed term - full time": "contract",
    "fixed term - part time": "contract",
    "fixed term contract": "contract",
    "full time": "full_time",
    "part time": "part_time",
    "contract": "contract",
    "contract / temp": "contract",
    "permanent": "full_time",
}


def _normalize_employment_type_text(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return raw
    key = raw.strip().lower()
    return _EMPLOYMENT_TYPE_MAP.get(key, raw)


def _strip_html(raw: Optional[str]) -> str:
    return _HTML_TAG_RE.sub(" ", raw or "").strip()


def _build_description(item: dict) -> str:
    """Concatenate every text section Pinpoint gives us for a posting.

    Verified live 2026-08-16 (priorygroup board): `description` alone is
    ~1,173 chars. `key_responsibilities` (~1,454 chars), `skills_knowledge_
    expertise` (~1,044 chars) and `benefits` (~1,136 chars) are separate
    HTML fields on the SAME postings.json response already fetched — no
    extra HTTP call, and none of them duplicate `description`.
    """
    sections = [
        ("", item.get("description")),
        ("Key Responsibilities", item.get("key_responsibilities")),
        ("Skills & Experience", item.get("skills_knowledge_expertise")),
        ("Benefits", item.get("benefits")),
    ]
    parts = []
    for heading, raw in sections:
        text = _strip_html(raw)
        if not text:
            continue
        parts.append(f"{heading}: {text}" if heading else text)
    return "\n\n".join(parts)


class PinpointSource(BaseJobSource):
    name = "pinpoint"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else PINPOINT_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            url = f"https://{slug}.pinpointhq.com/postings.json"
            data = await self._get_json(url)
            if not data or not isinstance(data, (list, dict)):
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            postings = data.get("data", data) if isinstance(data, dict) else data
            if not isinstance(postings, list):
                continue
            for item in postings:
                title = item.get("title", "")
                desc = _build_description(item)
                loc = item.get("location", {})
                if isinstance(loc, dict):
                    location = loc.get("name", str(loc))
                else:
                    location = str(loc) if loc else ""
                # Live schema (verified 2026-08-08): top-level `compensation`
                # is a plain string (e.g. "Competitive"), never the numeric
                # object the old code guarded for — that branch never fired.
                # The real numeric fields are compensation_minimum /
                # compensation_maximum. compensation_currency / _frequency are
                # real too (verified live 2026-08-16, 100% fill alongside the
                # numbers) — mapped onto the Universal Shelf salary metadata
                # below so the gate can annualise hourly rows instead of the
                # models.py clamp silently discarding them (anything <£10k
                # reads as "obviously wrong" there).
                salary_min = item.get("compensation_minimum")
                salary_max = item.get("compensation_maximum")
                salary_currency = item.get("compensation_currency")
                raw_frequency = (item.get("compensation_frequency") or "").strip().lower()
                salary_period = _FREQUENCY_MAP.get(raw_frequency)

                # deadline_at exists on this endpoint — guard for null, never
                # crash or fabricate.
                deadline = None
                deadline_source = None
                raw_deadline_at = item.get("deadline_at")
                if raw_deadline_at:
                    try:
                        deadline = datetime.fromisoformat(
                            str(raw_deadline_at).replace("Z", "+00:00")
                        ).date().isoformat()
                        deadline_source = "listing"
                    except ValueError:
                        pass

                apply_url = item.get("url", f"https://{slug}.pinpointhq.com/postings/{item.get('id', '')}")
                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=desc[:5000],
                    apply_url=apply_url,
                    source=self.name,
                    date_found=datetime.now(timezone.utc).isoformat(),
                    posted_at=None,
                    # Pinpoint genuinely has no posted-date field anywhere in
                    # its 24-key schema (verified live 2026-08-08), so
                    # "fabricated" is the CORRECT label — it is not an invalid
                    # value, it is a deliberate 4th state that
                    # skill_matcher._recency_points() reads to return 0 and
                    # refuse any recency credit (Batch 2.1 precedence rule).
                    # Downgrading this to "low" would let the job fall through
                    # to `date_found * 0.6` and earn 60% freshness credit for a
                    # date that does not exist. Do not "simplify" this.
                    date_confidence="fabricated",
                    date_posted_raw=None,
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_currency=salary_currency,
                    salary_period=salary_period,
                    deadline=deadline,
                    deadline_source=deadline_source,
                    # `employment_type_text` is the human label ("Full time",
                    # "Part time", "Permanent"...); `employment_type` is a
                    # short code. Text reads more reliably against the closed
                    # enum the gate normalises against, so prefer it and fall
                    # back to the code. Pinpoint's own compound phrasing
                    # ("Permanent - Full Time") is translated locally first —
                    # see _EMPLOYMENT_TYPE_MAP above.
                    employment_type=_normalize_employment_type_text(
                        item.get("employment_type_text")
                    ) or item.get("employment_type"),
                    # `workplace_type_text` ("Onsite"/"Remote"/"Hybrid") was
                    # fetched on every response already but never read onto
                    # the Job (verified live 2026-08-17, 5 boards: 100% key
                    # presence) — shelf_gate.py owns normalising it.
                    workplace_mode=item.get("workplace_type_text") or item.get("workplace_type"),
                ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Pinpoint: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs
