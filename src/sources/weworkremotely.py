import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _sanitize_xml, _is_uk_or_remote

logger = logging.getLogger("job360.sources.weworkremotely")


class WeWorkRemotelySource(BaseJobSource):
    """We Work Remotely — remote tech jobs via RSS feed."""
    name = "weworkremotely"
    category = "rss"

    async def fetch_jobs(self) -> list[Job]:
        xml_text = await self._get_text("https://weworkremotely.com/remote-jobs.rss")
        if not xml_text:
            return []

        jobs = self._parse_feed(xml_text)
        logger.info("WeWorkRemotely: found %s relevant jobs", len(jobs))
        return jobs

    def _parse_feed(self, xml_text: str) -> list[Job]:
        jobs = []
        try:
            root = ET.fromstring(_sanitize_xml(xml_text))
        except ET.ParseError as e:
            logger.warning("WeWorkRemotely: XML parse error: %s", e)
            return []

        channel = root.find("channel")
        if channel is None:
            channel = root

        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            region = (item.findtext("region") or "").strip()

            text = f"{title} {description}".lower()
            if not any(kw in text for kw in self.relevance_keywords):
                continue

            # Check region for UK/Europe/EMEA/GMT compatibility
            location = region or "Remote"
            if not _is_uk_or_remote(f"{location} {description}"):
                continue

            # Extract company: title often "Company: Role"
            company = "Unknown"
            if ": " in title:
                company = title.split(": ", 1)[0].strip()
                title = title.split(": ", 1)[1].strip()

            date_found = self._parse_rss_date(pub_date)

            jobs.append(Job(
                title=title,
                company=company,
                location=location,
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
