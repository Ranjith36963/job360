import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

import aiohttp

# M18: XXE-safe parse of untrusted feed XML. defusedxml ships no type stubs
# and types-defusedxml is not a project dependency, hence the import ignore.
from defusedxml.ElementTree import fromstring as _safe_fromstring  # type: ignore[import-untyped]

from src.core.companies import COMPANY_NAME_OVERRIDES, PERSONIO_COMPANIES
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote, _sanitize_xml
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.personio")

# Delay between companies to avoid 429 rate limiting on jobs.personio.de
_INTER_COMPANY_DELAY = 3.0

# Personio's own <type> spelling -> the four period spellings models.py:122
# documents as this field's contract ("hourly"|"daily"|"monthly"|"annual").
# Verified live 2026-08-16 (flatpay/herdify/holidu feeds): <salaryInformation>
# <type> only ever reads "yearly"/"monthly" — a literal word-for-word
# translation of one closed field, not a unit conversion or a guess.
# ONE REAL TRANSLATION, AND NO INVENTED ONES.
#
# `"weekly": "daily"` was a UNIT ERROR, not a spelling: a weekly figure stored
# as daily understates the pay by roughly five, and a scorer reading it would
# rank a good job as a bad one. The other four entries mapped a word to itself.
#
# The contract (models.py) is hourly|daily|monthly|annual — there is no
# "weekly" in it, so a weekly figure CANNOT be expressed here. Unstated is the
# honest answer for anything outside the contract; guessing a neighbouring unit
# is the one thing worse than leaving it empty (rule #29).
# (CodeRabbit, PR #388.)
_SALARY_PERIOD_CONTRACT = frozenset({"hourly", "daily", "monthly", "annual"})
_SALARY_TYPE_MAP = {"yearly": "annual"}


def _salary_period(raw_type: Optional[str]) -> Optional[str]:
    """Personio's `<type>` -> our period contract, or None when it cannot be."""
    if not raw_type:
        return None
    key = raw_type.strip().lower()
    key = _SALARY_TYPE_MAP.get(key, key)
    return key if key in _SALARY_PERIOD_CONTRACT else None


def _parse_salary(position: ET.Element) -> tuple[Optional[float], Optional[float], Optional[str], Optional[str]]:
    """<salaryInformation><min>/<max>/<currencyCode>/<type></salaryInformation>.

    Verified live 2026-08-16: present and populated on a real minority of
    boards (flatpay, herdify, holidu had it; stark/merantix/intigriti/olio/
    gridx/maltego did not) — the field is genuinely optional per company, so
    absence here is a real "not stated", not a parse miss.
    """
    salary_elem = position.find("salaryInformation")
    if salary_elem is None:
        return None, None, None, None

    def _to_float(text: Optional[str]) -> Optional[float]:
        if not text:
            return None
        try:
            return float(text.strip())
        except ValueError:
            return None

    salary_min = _to_float(salary_elem.findtext("min"))
    salary_max = _to_float(salary_elem.findtext("max"))
    currency = (salary_elem.findtext("currencyCode") or "").strip() or None
    raw_type = (salary_elem.findtext("type") or "").strip().lower()
    salary_period = _salary_period(raw_type)
    return salary_min, salary_max, currency, salary_period


class PersonioSource(BaseJobSource):
    """Personio ATS XML feed - multi-sector company job boards."""
    name = "personio"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._companies = companies or PERSONIO_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        consecutive_failures = 0
        for i, slug in enumerate(self._companies):
            if i > 0:
                await asyncio.sleep(_INTER_COMPANY_DELAY)
            url = f"https://{slug}.jobs.personio.de/xml?language=en"
            xml_text = await self._get_text(url)
            if not xml_text:
                consecutive_failures += 1
                if consecutive_failures >= 2:
                    logger.warning("Personio: %s consecutive failures, stopping early", consecutive_failures)
                    break
                continue
            consecutive_failures = 0
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            jobs.extend(self._parse_feed(xml_text, company_name, slug))

        logger.info("Personio: found %s relevant jobs", len(jobs))
        return jobs

    def _parse_feed(self, xml_text: str, company_name: str, slug: str) -> list[Job]:
        jobs = []
        try:
            root = _safe_fromstring(_sanitize_xml(xml_text))
        except ET.ParseError as e:
            logger.warning("Personio [%s]: XML parse error: %s", slug, e)
            return []

        for position in root.iter("position"):
            title = (position.findtext("name") or "").strip()
            office = (position.findtext("office") or "").strip()
            pos_id = (position.findtext("id") or "").strip()
            seniority = (position.findtext("seniority") or "").strip()
            employment_type = (position.findtext("employmentType") or "").strip()
            created_at_raw = (position.findtext("createdAt") or "").strip() or None
            # `<schedule>` ("full-time"/"part-time") is a SEPARATE field from
            # `<employmentType>` ("permanent"/"temporary"/"fixed_term"/
            # "intern"/"working_student") — the former is hours, the latter
            # is contract nature (verified live 2026-08-17, flatpay feed).
            # `<employmentType>` "permanent" carries no hours signal of its
            # own, so when that is the value, prefer the direct full-time/
            # part-time read instead of the gate's "permanent"->full_time
            # approximation — a permanent PART-TIME role must not be
            # reported full_time. Any other employmentType value (intern,
            # fixed_term, temporary, working_student) is more informative
            # than hours and is left as-is for the gate to translate.
            schedule = (position.findtext("schedule") or "").strip()
            if employment_type.lower() == "permanent" and schedule:
                employment_type = schedule
            # `<keywords>` is a comma-separated skills list, confirmed live
            # 2026-08-17 (flatpay: 79/122 filled, e.g. "People,Operations,
            # Human Resources,Fintech,HR") and never read before.
            keywords_raw = (position.findtext("keywords") or "").strip()
            source_tags = [k.strip() for k in keywords_raw.split(",") if k.strip()]
            # `<occupationCategory>` ("it_software"/"human_resources"/...) is
            # Personio's own closed job-function taxonomy, confirmed live
            # 2026-08-17 across 8 boards, and never read before —
            # shelf_gate.py's alias table maps the unambiguous members.
            occupation_category = (position.findtext("occupationCategory") or "").strip() or None

            # Get job descriptions
            desc_elem = position.find("jobDescriptions")
            description = ""
            if desc_elem is not None:
                for desc in desc_elem.iter("jobDescription"):
                    name = desc.findtext("name") or ""
                    value = desc.findtext("value") or ""
                    description += f"{name}: {value}\n"

            if not _is_uk_or_remote(office):
                continue

            apply_url = f"https://{slug}.jobs.personio.de/job/{pos_id}" if pos_id else f"https://{slug}.jobs.personio.de/"

            # Date fix (2026-08-08): the feed carries a real ISO createdAt
            # per posting - route it through normalize_posted_at() instead of
            # the old hardcoded "fabricated" literal (an invalid enum value:
            # the schema is high|medium|low). Absent/unparseable createdAt
            # falls back to no posted_at with low confidence, same as any
            # other source with a missing date field.
            posted_at, date_confidence = normalize_posted_at(created_at_raw)

            salary_min, salary_max, salary_currency, salary_period = _parse_salary(position)

            jobs.append(Job(
                title=title,
                company=company_name,
                location=office or "Remote",
                description=description[:5000],
                apply_url=apply_url,
                source=self.name,
                date_found=datetime.now(timezone.utc).isoformat(),
                posted_at=posted_at,
                date_confidence=date_confidence,
                date_posted_raw=created_at_raw,
                experience_level=seniority,
                # Raw upstream values (<seniority> "entry-level"/"experienced"/
                # ..., <employmentType> "permanent"/"internship"/...) —
                # services/shelf_gate.py owns normalising them against the
                # closed enums.
                seniority=seniority or None,
                employment_type=employment_type or None,
                salary_min=salary_min,
                salary_max=salary_max,
                salary_currency=salary_currency,
                salary_period=salary_period,
                source_tags=source_tags,
                category=occupation_category,
            ))

        return jobs
