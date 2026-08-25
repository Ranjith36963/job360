import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional, cast
from urllib.parse import urlsplit

import aiohttp

# M18: XXE-safe parse of untrusted feed XML. defusedxml ships no type stubs
# and types-defusedxml is not a project dependency, hence the import ignore.
from defusedxml.ElementTree import fromstring as _safe_fromstring  # type: ignore[import-untyped]

from src.core.companies import SUCCESSFACTORS_COMPANIES
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote, _sanitize_xml
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.successfactors")

_MAX_INDEX_SITEMAPS = 5
_JOB_URL_RE = re.compile(r"/job/", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_JSONLD_RE = re.compile(r"<script type=.application/ld\+json.>(.*?)</script>", re.DOTALL)
_GEOLOCATION_RE = re.compile(r"class=.jobGeoLocation.>([^<]*)<")
_MICRODATA_DESC_MARKER = 'itemprop="description"'
# Bounded window (not a closing-tag regex): verified live 2026-08-16, the
# QinetiQ template's description block is the LAST field on the page --
# no later `joblayouttoken` marker ever follows it, so a regex requiring one
# as a closing boundary never matches at all (0/0 descriptions extracted).
# A fixed character window after the opening tag is simple and robust
# against markup this irregular; downstream tag-stripping + a 5000-char cap
# makes the exact window size non-critical.
_MICRODATA_DESC_WINDOW = 8000

# Per-COMPANY budget (not a shared global pool): BAE Systems' sitemap shards
# are geographically clustered (shard 1 sampled 100% US, verified live
# 2026-08-16), so a shared budget spent entirely inside BAE's US-heavy shard
# would starve QinetiQ/Thales of any detail fetches at all. Splitting evenly
# guarantees every configured company gets a fair shot at surfacing its real
# UK postings within the same total request budget.
_MAX_DETAIL_FETCHES_PER_COMPANY = 20


def _as_number(value):
    """A JSON-LD number, or None. Never raises — see the call site."""
    if isinstance(value, bool):  # bool is an int subclass; a flag is not a salary
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _extract_from_jsonld(page_html):
    # EVERY ld+json BLOCK, NOT THE FIRST. Careers pages routinely carry an
    # Organization or BreadcrumbList block before the JobPosting one, and
    # taking `search()` meant landing on that, returning a dict with none of
    # the fields we want — and then BLOCKING the microdata fallback, because a
    # non-None return reads as "JSON-LD handled it". Scan for the block that
    # actually is a JobPosting. (CodeRabbit, PR #388.)
    blocks = _JSONLD_RE.findall(page_html)
    if not blocks:
        return None
    data = None
    for block in blocks:
        try:
            candidate = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, list):
            candidate = next(
                (c for c in candidate
                 if isinstance(c, dict) and "JobPosting" in str(c.get("@type", ""))),
                None,
            )
        if not isinstance(candidate, dict):
            continue
        if "JobPosting" in str(candidate.get("@type", "")):
            data = candidate
            break
        if data is None:
            data = candidate  # keep the first parseable one as a fallback
    if not isinstance(data, dict):
        return None

    loc = data.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    addr = loc.get("address", {}) if isinstance(loc, dict) else {}
    locality = (addr.get("addressLocality") or "").strip()
    country = (addr.get("addressCountry") or "").strip()
    location = ", ".join(p for p in (locality, country) if p)

    desc_raw = data.get("description") or ""
    description = _HTML_TAG_RE.sub(" ", html.unescape(str(desc_raw)))[:5000].strip()

    emp = data.get("employmentType")
    if isinstance(emp, list):
        employment_type = emp[0] if emp else None
    elif isinstance(emp, str):
        employment_type = emp
    else:
        employment_type = None

    # `datePosted` is a standard schema.org JobPosting field present on
    # every BAE-template page (confirmed live 2026-08-17, 6/8 sampled pages
    # — 2 pages had no JSON-LD at all, a different template) and was never
    # read; `posted_at` was hardcoded to None for this whole source.
    date_posted = data.get("datePosted") or None
    # `baseSalary` is the standard schema.org field for this too — verified
    # live as always null on BAE (0/8 sampled), so this stays absent for
    # that vendor, but the extraction is generic across any JSON-LD-emitting
    # SuccessFactors tenant that DOES populate it.
    base_salary = data.get("baseSalary")
    salary_min = salary_max = salary_currency = None
    if isinstance(base_salary, dict):
        value = base_salary.get("value")
        currency = base_salary.get("currency")
        if isinstance(value, dict):
            # COERCED, because JSON-LD is written by hand by whoever runs the
            # careers site: `"minValue": "45000"` is common and entirely valid
            # JSON. Handing a str downstream raised mid-loop and took the WHOLE
            # source's remaining postings with it — one badly-typed field on one
            # ad silently costing every ad after it. (CodeRabbit, PR #388.)
            salary_min = _as_number(value.get("minValue"))
            salary_max = _as_number(value.get("maxValue"))
        salary_currency = currency

    return {
        "location": location or None,
        "description": description,
        "employment_type": employment_type,
        "date_posted": date_posted,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": salary_currency,
    }


def _extract_from_microdata(page_html):
    loc_match = _GEOLOCATION_RE.search(page_html)
    location = loc_match.group(1).strip() if loc_match else None

    description = ""
    marker_idx = page_html.find(_MICRODATA_DESC_MARKER)
    if marker_idx != -1:
        tag_end = page_html.find(">", marker_idx)
        if tag_end != -1:
            chunk = page_html[tag_end + 1: tag_end + 1 + _MICRODATA_DESC_WINDOW]
            description = _HTML_TAG_RE.sub(" ", html.unescape(chunk))[:5000].strip()

    if location is None and not description:
        return None
    return {"location": location, "description": description, "employment_type": None}


class SuccessFactorsSource(BaseJobSource):
    name = "successfactors"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies=None, search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._companies = companies or SUCCESSFACTORS_COMPANIES

    async def fetch_jobs(self) -> list:
        jobs = []
        for company in self._companies:
            sitemap_url = company["sitemap_url"]
            company_name = company["name"]

            xml_text = await self._get_text(sitemap_url)
            if not xml_text:
                continue
            detail_budget = [_MAX_DETAIL_FETCHES_PER_COMPANY]
            jobs.extend(await self._parse_sitemap_or_index(xml_text, company_name, detail_budget))

        logger.info("SuccessFactors: found %s relevant jobs", len(jobs))
        return jobs

    async def _parse_sitemap_or_index(self, xml_text, company_name, detail_budget):
        root = self._parse_xml(xml_text, company_name)
        if root is None:
            return []

        if self._local_name(root.tag) == "sitemapindex":
            return await self._parse_sitemap_index(root, company_name, detail_budget)
        return await self._parse_urlset(root, company_name, detail_budget)

    def _parse_xml(self, xml_text, company_name):
        try:
            return cast(ET.Element, _safe_fromstring(_sanitize_xml(xml_text)))
        except ET.ParseError as e:
            logger.warning("SuccessFactors [%s]: XML parse error: %s", company_name, e)
            return None

    async def _parse_sitemap_index(self, root, company_name, detail_budget):
        jobs = []
        child_urls = [
            self._local_findtext(sitemap_elem, "loc")
            for sitemap_elem in self._iter_local(root, "sitemap")
        ]
        child_urls = [u for u in child_urls if u][:_MAX_INDEX_SITEMAPS]

        for child_url in child_urls:
            xml_text = await self._get_text(child_url)
            if not xml_text:
                continue
            child_root = self._parse_xml(xml_text, company_name)
            if child_root is None:
                continue
            jobs.extend(await self._parse_urlset(child_root, company_name, detail_budget))

        return jobs

    async def _parse_urlset(self, root, company_name, detail_budget):
        jobs = []
        now = datetime.now(timezone.utc).isoformat()

        for url_elem in self._iter_local(root, "url"):
            loc = self._local_findtext(url_elem, "loc")
            if not loc:
                continue
            if not _JOB_URL_RE.search(loc):
                continue

            title = self._title_from_url(loc)
            if not title:
                continue

            if not _is_uk_or_remote(title):
                continue

            if detail_budget[0] <= 0:
                continue
            detail_budget[0] -= 1

            page_html = await self._get_text(loc)
            if not page_html:
                continue

            extracted = _extract_from_jsonld(page_html) or _extract_from_microdata(page_html)
            if not extracted or not extracted.get("location"):
                continue

            real_location = extracted["location"]
            # Test UK-eligibility on the COUNTRY component alone, not the
            # combined "City, Country" string. Verified live 2026-08-16: the
            # combined form trips a known uk_gate dual-site escape (a UK
            # place name sharing a name with a foreign one, e.g. Westminster
            # UK vs Westminster, Maryland US) meant for genuinely ambiguous
            # two-site postings like "London / New York" -- admitting BAE's
            # US postings as UK. A single bare segment can never hit that
            # escape (it requires >1 named segment), so this is a source-side
            # workaround, not a uk_gate.py change (out of scope for this
            # batch; the underlying gazetteer gap is a separate finding).
            country_segment = real_location.rsplit(",", 1)[-1].strip()
            if not _is_uk_or_remote(country_segment):
                continue

            raw_date_posted = extracted.get("date_posted")
            posted_at, date_confidence = (None, "low")
            if raw_date_posted:
                posted_at, date_confidence = normalize_posted_at(raw_date_posted)

            jobs.append(Job(
                title=title,
                company=company_name,
                location=real_location,
                description=extracted.get("description") or title,
                apply_url=loc,
                source=self.name,
                date_found=now,
                posted_at=posted_at,
                date_confidence=date_confidence,
                date_posted_raw=raw_date_posted,
                employment_type=extracted.get("employment_type"),
                salary_min=extracted.get("salary_min"),
                salary_max=extracted.get("salary_max"),
                salary_currency=extracted.get("salary_currency"),
            ))

        return jobs

    @staticmethod
    def _local_name(tag):
        return tag.split("}")[-1]

    @classmethod
    def _iter_local(cls, root, tag_name):
        return [elem for elem in root.iter() if cls._local_name(elem.tag) == tag_name]

    @classmethod
    def _local_findtext(cls, elem, tag_name):
        for child in elem:
            if cls._local_name(child.tag) == tag_name:
                return (child.text or "").strip()
        return ""

    @staticmethod
    def _title_from_url(url):
        """Extract a readable title from a career page URL path.

        QinetiQ (and likely other legacy-template SuccessFactors vendors)
        appends the numeric job ID as its OWN trailing path segment --
        `.../job/Canberra-Systems-Engineering-.../1368001233/` -- verified
        live 2026-08-16: 154/154 QinetiQ sitemap URLs have a digit-only last
        segment. Taking the last segment alone (the old behaviour) always
        returned that bare ID, `_title_from_url` returned "", and
        `_parse_urlset`'s `if not title: continue` silently dropped every
        QinetiQ posting -- this source has been contributing ZERO QinetiQ
        jobs since the day it was added, regardless of the location fix
        above. Walking backward past digit-only segments recovers the real
        title segment for both templates (BAE's last segment already IS the
        title, so this is a no-op there).
        """
        # THE PATH ONLY. Splitting the whole URL on "/" leaves `https:` and
        # the host in the list, so a posting whose path segments are all digits
        # walked backward straight onto the hostname — or onto `https:` — and
        # stored that as the job title. `urlsplit().path` cannot do that.
        # (CodeRabbit, PR #388.)
        segments = [
            s.split("?")[0] for s in urlsplit(url).path.rstrip("/").split("/") if s
        ]
        path = ""
        for seg in reversed(segments):
            if seg and not seg.isdigit():
                path = seg
                break
        if not path:
            return ""
        title = re.sub(r"[-_]+", " ", path)
        if title.strip().isdigit():
            return ""
        return title.strip().title()
