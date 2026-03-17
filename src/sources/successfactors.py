import logging
import xml.etree.ElementTree as ET
import re
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote, _sanitize_xml
from src.config.companies import SUCCESSFACTORS_COMPANIES

logger = logging.getLogger("job360.sources.successfactors")


class SuccessFactorsSource(BaseJobSource):
    """SAP SuccessFactors career sites — UK defence/enterprise jobs via sitemap."""
    name = "successfactors"

    def __init__(self, session: aiohttp.ClientSession, companies: list[dict] | None = None, search_config=None):
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
            jobs.extend(self._parse_sitemap(xml_text, company_name))

        logger.info(f"SuccessFactors: found {len(jobs)} relevant jobs")
        return jobs

    def _parse_sitemap(self, xml_text: str, company_name: str) -> list[Job]:
        jobs = []
        now = datetime.now(timezone.utc).isoformat()

        try:
            root = ET.fromstring(_sanitize_xml(xml_text))
        except ET.ParseError as e:
            logger.warning(f"SuccessFactors [{company_name}]: XML parse error: {e}")
            return []

        # Sitemap namespace
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        for url_elem in root.findall(".//sm:url", ns):
            loc = (url_elem.findtext("sm:loc", namespaces=ns) or "").strip()
            if not loc:
                # Try without namespace
                loc = (url_elem.findtext("loc") or "").strip()
            if not loc:
                continue

            # Extract title from URL path
            title = self._title_from_url(loc)
            if not title:
                continue

            text = title.lower()
            if not any(kw in text for kw in self.relevance_keywords):
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
            ))

        # Also try plain URL tags without namespace
        if not jobs:
            for url_elem in root.iter("url"):
                loc = (url_elem.findtext("loc") or "").strip()
                if not loc:
                    continue
                title = self._title_from_url(loc)
                if not title:
                    continue
                text = title.lower()
                if not any(kw in text for kw in self.relevance_keywords):
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
                ))

        return jobs

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
