import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote, _sanitize_xml

logger = logging.getLogger("job360.sources.biospace")


class BioSpaceSource(BaseJobSource):
    """BioSpace — biotech/pharma AI jobs via RSS feed."""
    name = "biospace"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        # BioSpace has category-specific RSS feeds
        feed_urls = [
            "https://www.biospace.com/rss/jobs/data-science",
            "https://www.biospace.com/rss/jobs/research-development",
            "https://www.biospace.com/rss/jobs",
        ]
        for url in feed_urls:
            xml_text = await self._get_text(url)
            if not xml_text:
                continue
            jobs.extend(self._parse_feed(xml_text))
            if jobs:
                break  # Got results, no need to try fallback URLs

        logger.info(f"BioSpace: found {len(jobs)} relevant jobs")
        return jobs

    def _parse_feed(self, xml_text: str) -> list[Job]:
        jobs = []
        try:
            root = ET.fromstring(_sanitize_xml(xml_text))
        except ET.ParseError as e:
            logger.warning(f"BioSpace: XML parse error: {e}")
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
            if not self._relevance_match(text):
                continue

            if not _is_uk_or_remote(description):
                continue

            # Extract company from title
            company = "Unknown"
            for sep in [" at ", " - ", " @ "]:
                if sep in title:
                    company = title.split(sep, 1)[1].strip()
                    break

            date_found = self._parse_rss_date(pub_date)

            jobs.append(Job(
                title=title,
                company=company,
                location="",
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
