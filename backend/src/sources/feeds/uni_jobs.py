import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# M18: XXE-safe parse of untrusted feed XML. defusedxml ships no type stubs
# and types-defusedxml is not a project dependency, hence the import ignore.
from defusedxml.ElementTree import fromstring as _safe_fromstring  # type: ignore[import-untyped]

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote, _sanitize_xml

logger = logging.getLogger("job360.sources.uni_jobs")

# UK university job RSS feeds (only feeds verified to return valid XML)
UNIVERSITY_FEEDS = [
    {"url": "http://www.jobs.cam.ac.uk/job/?format=rss", "name": "University of Cambridge"},
]


class UniJobsSource(BaseJobSource):
    """UK University RSS feeds — academic research positions."""
    name = "uni_jobs"
    category = "rss"
    DOMAINS = {"academia"}

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for feed in UNIVERSITY_FEEDS:
            xml_text = await self._get_text(feed["url"])
            if not xml_text:
                continue
            jobs.extend(self._parse_feed(xml_text, feed["name"]))

        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("University Jobs: found %s relevant jobs", len(jobs))
        return jobs

    def _parse_feed(self, xml_text: str, university: str) -> list[Job]:
        jobs = []
        try:
            root = _safe_fromstring(_sanitize_xml(xml_text))
        except ET.ParseError as e:
            logger.warning("University Jobs [%s]: XML parse error: %s", university, e)
            return []

        channel = root.find("channel")
        if channel is None:
            channel = root

        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            apply_online_url = (item.findtext("applyOnlineUrl") or "").strip()

            # applyOnlineUrl (92% fill live) is the direct application form;
            # <link> is only the listing page. Prefer the direct form, fall
            # back to the listing when the feed omits it.
            apply_url = apply_online_url or link

            # Use university name as company, or extract department
            company = university

            now_iso = datetime.now(timezone.utc).isoformat()
            posted_at = self._parse_rss_date(pub_date) if pub_date else None
            # Confidence follows the RESULT, not the input. A present-but-
            # unparseable value used to be stamped "high" purely because the
            # field existed — certifying as trustworthy a date we could not read.
            confidence = "high" if posted_at else "low"
            jobs.append(Job(
                title=title,
                company=company,
                location="UK",
                description=description[:5000],
                apply_url=apply_url,
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
