"""NHS Jobs — all-current-vacancies XML feed.

Replacement-track for `nhs_jobs.py` (which keyword-searched `search_xml`
and whose `closingDate` hit the wrong-field bug fixed in Batch 1). This
variant pulls the full vacancy feed once and exposes `createdDate`
directly → `posted_at` with `date_confidence="high"`.

URL: https://www.jobs.nhs.uk/api/v1/feed/all_current_vacancies.xml
"""
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from src.models import Job
from src.sources.base import BaseJobSource, _sanitize_xml, _is_uk_or_remote

logger = logging.getLogger("job360.sources.nhs_jobs_xml")


class NHSJobsXMLSource(BaseJobSource):
    name = "nhs_jobs_xml"
    category = "rss"  # 15-min tier

    FEED_URL = "https://www.jobs.nhs.uk/api/v1/feed/all_current_vacancies.xml"

    async def fetch_jobs(self) -> list[Job]:
        xml_text = await self._get_text(self.FEED_URL)
        if not xml_text:
            return []
        return self._parse_xml(xml_text)

    def _parse_xml(self, xml_text: str) -> list[Job]:
        results: list[Job] = []
        try:
            root = ET.fromstring(_sanitize_xml(xml_text))
        except ET.ParseError as e:
            logger.warning("NHSJobsXML: XML parse error: %s", e)
            return []

        now_iso = datetime.now(timezone.utc).isoformat()
        for vacancy in root.iter("vacancy"):
            title = (vacancy.findtext("title") or "").strip()
            employer = (vacancy.findtext("employer") or "").strip() or "NHS"
            location = (vacancy.findtext("location") or "").strip() or "UK"
            vacancy_id = (vacancy.findtext("id") or "").strip()
            advert_url = (vacancy.findtext("advertUrl") or "").strip()
            created = (vacancy.findtext("createdDate") or "").strip()

            if not _is_uk_or_remote(location):
                continue

            apply_url = advert_url or f"https://www.jobs.nhs.uk/candidate/jobadvert/{vacancy_id}"
            posted_at = created if created else None
            confidence = "high" if created else "low"

            results.append(Job(
                title=title,
                company=employer,
                location=location,
                description=title,
                apply_url=apply_url,
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=created or None,
            ))

        logger.info("NHSJobsXML: found %s relevant jobs", len(results))
        return results
