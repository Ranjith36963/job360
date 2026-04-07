import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote, _sanitize_xml
from src.config.companies import PERSONIO_COMPANIES, COMPANY_NAME_OVERRIDES

logger = logging.getLogger("job360.sources.personio")

# Delay between companies to avoid 429 rate limiting on jobs.personio.de
_INTER_COMPANY_DELAY = 3.0


class PersonioSource(BaseJobSource):
    """Personio ATS XML feed — multi-sector company job boards."""
    name = "personio"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config=None):
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
            root = ET.fromstring(_sanitize_xml(xml_text))
        except ET.ParseError as e:
            logger.warning("Personio [%s]: XML parse error: %s", slug, e)
            return []

        for position in root.iter("position"):
            title = (position.findtext("name") or "").strip()
            office = (position.findtext("office") or "").strip()
            department = (position.findtext("department") or "").strip()
            pos_id = (position.findtext("id") or "").strip()

            # Get job descriptions
            desc_elem = position.find("jobDescriptions")
            description = ""
            if desc_elem is not None:
                for desc in desc_elem.iter("jobDescription"):
                    name = desc.findtext("name") or ""
                    value = desc.findtext("value") or ""
                    description += f"{name}: {value}\n"

            text = f"{title} {description} {department}".lower()
            if not any(kw in text for kw in self.relevance_keywords):
                continue

            if not _is_uk_or_remote(office):
                continue

            apply_url = f"https://{slug}.jobs.personio.de/job/{pos_id}" if pos_id else f"https://{slug}.jobs.personio.de/"

            jobs.append(Job(
                title=title,
                company=company_name,
                location=office or "Remote",
                description=description[:5000],
                apply_url=apply_url,
                source=self.name,
                date_found=datetime.now(timezone.utc).isoformat(),
            ))

        return jobs
