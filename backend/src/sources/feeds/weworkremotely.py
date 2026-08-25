import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

# M18: XXE-safe parse of untrusted feed XML. defusedxml ships no type stubs
# and types-defusedxml is not a project dependency, hence the import ignore.
from defusedxml.ElementTree import fromstring as _safe_fromstring  # type: ignore[import-untyped]

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote, _sanitize_xml

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
            root = _safe_fromstring(_sanitize_xml(xml_text))
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
            expires_at = (item.findtext("expires_at") or "").strip()
            # <type> (100% fill live 2026-08-16, e.g. "Full-Time"/"Contract")
            # and <skills> (34% fill, comma-shaped) both ride the same
            # already-parsed item and used to be thrown away entirely.
            raw_type = (item.findtext("type") or "").strip()
            raw_skills = (item.findtext("skills") or "").strip()
            # <category> (confirmed live 2026-08-17, e.g. "Sales and
            # Marketing", "Programming") is the board's own job-category
            # vocabulary and was thrown away entirely. Raw value only -- no
            # enum-mapping here, that is the gate's job.
            raw_category = (item.findtext("category") or "").strip()

            # Check region for UK/Europe/EMEA/GMT compatibility
            location = region or "Remote"
            if not _is_uk_or_remote(f"{location} {description}"):
                continue

            # Extract company: title often "Company: Role"
            company = "Unknown"
            if ": " in title:
                company = title.split(": ", 1)[0].strip()
                title = title.split(": ", 1)[1].strip()

            now_iso = datetime.now(timezone.utc).isoformat()
            posted_at = self._parse_rss_date(pub_date) if pub_date else None
            # Confidence follows the RESULT, not the input. A present-but-
            # unparseable value used to be stamped "high" purely because the
            # field existed — certifying as trustworthy a date we could not read.
            confidence = "high" if posted_at else "low"

            # expires_at (100% fill live) is the listing's own application
            # deadline, in the same RSS date format as pubDate. Use the
            # STRICT parser here — deadline must NEVER be fabricated (see
            # Job.deadline docstring in models.py), unlike _parse_rss_date's
            # "now" fallback which is fine for posted_at but would be a lie
            # for a deadline.
            deadline_iso = self._parse_rss_date_strict(expires_at)
            deadline = deadline_iso[:10] if deadline_iso else None
            deadline_source = "listing" if deadline else None

            jobs.append(Job(
                title=title,
                company=company,
                location=location,
                description=description[:5000],
                apply_url=link,
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=pub_date or None,
                deadline=deadline,
                deadline_source=deadline_source,
                employment_type=raw_type or None,
                source_tags=[t.strip() for t in raw_skills.split(",") if t.strip()],
                category=raw_category or None,
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

    @staticmethod
    def _parse_rss_date_strict(date_str: str) -> Optional[str]:
        """Same formats as _parse_rss_date, but returns None on failure
        instead of fabricating "now" — safe for fields (like deadline) that
        must never be guessed."""
        if not date_str:
            return None
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                    "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                return datetime.strptime(date_str.strip(), fmt).isoformat()
            except ValueError:
                continue
        return None
