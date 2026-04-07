import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _sanitize_xml, _is_uk_or_remote

logger = logging.getLogger("job360.sources.jobs_ac_uk")

FEED_URLS = [
    "https://www.jobs.ac.uk/feeds/subject-areas/computer-sciences",
    "https://www.jobs.ac.uk/feeds/subject-areas/engineering-and-technology",
    "https://www.jobs.ac.uk/feeds/subject-areas/mathematics-and-statistics",
    "https://www.jobs.ac.uk/feeds/subject-areas/health-and-medical",
]


class JobsAcUkSource(BaseJobSource):
    """UK Academic/Research Jobs from jobs.ac.uk RSS feeds."""
    name = "jobs_ac_uk"
    category = "rss"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for feed_url in FEED_URLS:
            xml_text = await self._get_text(feed_url)
            if not xml_text:
                continue
            jobs.extend(self._parse_feed(xml_text))

        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("jobs.ac.uk: found %s relevant jobs", len(jobs))
        return jobs

    def _parse_feed(self, xml_text: str) -> list[Job]:
        jobs = []
        try:
            root = ET.fromstring(_sanitize_xml(xml_text))
        except ET.ParseError as e:
            logger.warning("jobs.ac.uk: XML parse error: %s", e)
            return []

        # Handle RSS 2.0 format
        channel = root.find("channel")
        if channel is None:
            channel = root

        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()

            text = f"{title} {description}".lower()
            if not any(kw in text for kw in self.relevance_keywords):
                continue

            # Extract company from title (often "Role - University")
            company = "Unknown"
            if " - " in title:
                parts = title.rsplit(" - ", 1)
                if len(parts) == 2:
                    company = parts[1].strip()

            date_found = self._parse_date(pub_date)

            jobs.append(Job(
                title=title,
                company=company,
                location="UK",
                description=description[:5000],
                apply_url=link,
                source=self.name,
                date_found=date_found,
            ))

        return jobs

    @staticmethod
    def _parse_date(date_str: str) -> str:
        if not date_str:
            return datetime.now(timezone.utc).isoformat()
        # RFC 822 date format from RSS
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                    "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                return datetime.strptime(date_str.strip(), fmt).isoformat()
            except ValueError:
                continue
        return datetime.now(timezone.utc).isoformat()
