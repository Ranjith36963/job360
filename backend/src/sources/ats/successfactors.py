import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Optional, cast

import aiohttp

# M18: XXE-safe parse of untrusted feed XML. defusedxml ships no type stubs
# and types-defusedxml is not a project dependency, hence the import ignore.
from defusedxml.ElementTree import fromstring as _safe_fromstring  # type: ignore[import-untyped]

from src.core.companies import SUCCESSFACTORS_COMPANIES
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote, _sanitize_xml

logger = logging.getLogger("job360.sources.successfactors")

# Bound on how many child sitemaps a sitemap INDEX may fan out to per company,
# to avoid runaway fetching if a vendor's index lists dozens of shards.
_MAX_INDEX_SITEMAPS = 5

# Only URLs carrying a "/job/" segment are real postings. Vendor sitemaps also
# list navigation/category pages, which would otherwise be ingested as jobs
# with nonsense titles derived from the path (see _parse_urlset).
_JOB_URL_RE = re.compile(r"/job/", re.IGNORECASE)


class SuccessFactorsSource(BaseJobSource):
    """SAP SuccessFactors career sites — UK defence/enterprise jobs via sitemap."""
    name = "successfactors"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[dict[str, Any]] | None = None, search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._companies = companies or SUCCESSFACTORS_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for company in self._companies:
            sitemap_url = company["sitemap_url"]
            company_name = company["name"]

            xml_text = await self._get_text(sitemap_url)
            if not xml_text:
                continue
            jobs.extend(await self._parse_sitemap_or_index(xml_text, company_name))

        logger.info("SuccessFactors: found %s relevant jobs", len(jobs))
        return jobs

    async def _parse_sitemap_or_index(self, xml_text: str, company_name: str) -> list[Job]:
        """Parse a sitemap document that may be either a plain urlset or a
        sitemap INDEX (a level of indirection some vendors — BAE, Thales —
        use to shard their sitemap across multiple files).
        """
        root = self._parse_xml(xml_text, company_name)
        if root is None:
            return []

        if self._local_name(root.tag) == "sitemapindex":
            return await self._parse_sitemap_index(root, company_name)
        return self._parse_urlset(root, company_name)

    def _parse_xml(self, xml_text: str, company_name: str) -> Optional[ET.Element]:
        try:
            return cast(ET.Element, _safe_fromstring(_sanitize_xml(xml_text)))
        except ET.ParseError as e:
            logger.warning("SuccessFactors [%s]: XML parse error: %s", company_name, e)
            return None

    async def _parse_sitemap_index(self, root: ET.Element, company_name: str) -> list[Job]:
        """Fetch and parse each child sitemap referenced by a sitemap index,
        bounded to ``_MAX_INDEX_SITEMAPS`` to avoid runaway fetching.
        """
        jobs: list[Job] = []
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
            jobs.extend(self._parse_urlset(child_root, company_name))

        return jobs

    def _parse_urlset(self, root: ET.Element, company_name: str) -> list[Job]:
        jobs = []
        now = datetime.now(timezone.utc).isoformat()

        for url_elem in self._iter_local(root, "url"):
            loc = self._local_findtext(url_elem, "loc")
            if not loc:
                continue

            # A vendor sitemap lists EVERY page on the careers site, not just
            # postings. Verified live 2026-08-08: of 4,664 URLs across the
            # configured companies, only 4,287 were real jobs — the rest were
            # navigation pages (".../global/en", ".../early-career") which
            # previously became jobs titled "En" and "Early Career".
            # Real SuccessFactors postings always carry a "/job/" segment,
            # e.g. .../job/124704BR/Engineer-Senior-Principal-Optical-Systems
            if not _JOB_URL_RE.search(loc):
                continue

            # Extract title from URL path
            title = self._title_from_url(loc)
            if not title:
                continue

            if not _is_uk_or_remote(title):
                continue

            jobs.append(Job(
                title=title,
                company=company_name,
                location="UK",
                description=title,
                apply_url=loc,
                source=self.name,
                date_found=now,
                posted_at=None,
                date_confidence="low",
                date_posted_raw=None,
            ))

        return jobs

    @staticmethod
    def _local_name(tag: str) -> str:
        """Strip any ``{namespace}`` prefix ElementTree attaches to a tag.

        The live QinetiQ sitemap serves the ``google.com/schemas/sitemap``
        namespace instead of the ``sitemaps.org`` one this code used to
        hardcode (verified live: 0/162 <loc> URLs matched under either the
        namespaced lookup or the old no-namespace fallback). Matching on the
        local name only makes this tolerant of whichever namespace — or no
        namespace at all — a given vendor happens to serve.
        """
        return tag.split("}")[-1]

    @classmethod
    def _iter_local(cls, root: ET.Element, tag_name: str) -> list[ET.Element]:
        """Descendants (any depth) whose local tag name matches ``tag_name``,
        regardless of namespace.
        """
        return [elem for elem in root.iter() if cls._local_name(elem.tag) == tag_name]

    @classmethod
    def _local_findtext(cls, elem: ET.Element, tag_name: str) -> str:
        """Direct child's text, matched by local name regardless of namespace."""
        for child in elem:
            if cls._local_name(child.tag) == tag_name:
                return (child.text or "").strip()
        return ""

    @staticmethod
    def _title_from_url(url: str) -> str:
        """Extract a readable title from a career page URL path."""
        # Get last path segment
        path = url.rstrip("/").split("/")[-1]
        # Remove ID suffixes, query params
        path = path.split("?")[0]
        # Replace hyphens/underscores with spaces
        title = re.sub(r"[-_]+", " ", path)
        # Remove pure numeric segments
        if title.strip().isdigit():
            return ""
        return title.strip().title()
