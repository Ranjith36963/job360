import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote, _sanitize_xml

logger = logging.getLogger("job360.sources.workanywhere")


class WorkAnywhereSource(BaseJobSource):
    """WorkAnywhere.pro remote Data/AI jobs via RSS feed."""
    name = "workanywhere"
    category = "rss"

    async def fetch_jobs(self) -> list[Job]:
        # Try category-specific feed first, fallback to main feed
        xml_text = await self._get_text("https://workanywhere.pro/rss/data-ai.xml")
        if not xml_text:
            await asyncio.sleep(3.0)
            xml_text = await self._get_text("https://workanywhere.pro/rss.xml")
        if not xml_text:
            return []

        jobs = self._parse_feed(xml_text)
        logger.info("WorkAnywhere: found %s relevant jobs", len(jobs))
        return jobs

    def _parse_feed(self, xml_text: str) -> list[Job]:
        jobs = []
        try:
            root = ET.fromstring(_sanitize_xml(xml_text))
        except ET.ParseError as e:
            logger.warning("WorkAnywhere: XML parse error: %s", e)
            return []

        channel = root.find("channel")
        if channel is None:
            channel = root

        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()

            # Check for UK/Europe/GMT timezone compatibility
            location_text = f"{title} {description}"
            if not _is_uk_or_remote(location_text):
                continue

            # Extract company from title (format: "Role at Company" or "Role - Company")
            company = "Unknown"
            for sep in [" at ", " - ", " @ "]:
                if sep in title:
                    company = title.split(sep, 1)[1].strip()
                    break

            now_iso = datetime.now(timezone.utc).isoformat()
            posted_at = self._parse_rss_date(pub_date) if pub_date else None
            confidence = "high" if pub_date else "low"

            jobs.append(Job(
                title=title,
                company=company,
                location="Remote",
                description=description[:5000],
                apply_url=link,
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=pub_date or None,
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
