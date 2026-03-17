import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _sanitize_xml

logger = logging.getLogger("job360.sources.uni_jobs")

# UK university job RSS feeds (only feeds verified to return valid XML)
UNIVERSITY_FEEDS = [
    {"url": "http://www.jobs.cam.ac.uk/job/?format=rss", "name": "University of Cambridge"},
]


class UniJobsSource(BaseJobSource):
    """UK University RSS feeds — academic research positions."""
    name = "uni_jobs"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for feed in UNIVERSITY_FEEDS:
            xml_text = await self._get_text(feed["url"])
            if not xml_text:
                continue
            jobs.extend(self._parse_feed(xml_text, feed["name"]))

        logger.info(f"University Jobs: found {len(jobs)} relevant jobs")
        return jobs

    def _parse_feed(self, xml_text: str, university: str) -> list[Job]:
        jobs = []
        try:
            root = ET.fromstring(_sanitize_xml(xml_text))
        except ET.ParseError as e:
            logger.warning(f"University Jobs [{university}]: XML parse error: {e}")
            return []

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

            # Use university name as company, or extract department
            company = university

            date_found = self._parse_rss_date(pub_date)

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
    def _parse_rss_date(date_str: str) -> str:
        if not date_str:
            return datetime.now(timezone.utc).isoformat()
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                    "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                return datetime.strptime(date_str.strip(), fmt).isoformat()
            except ValueError:
                continue
        return datetime.now(timezone.utc).isoformat()
