import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import aiohttp

from src.models import Job
from src.sources.base import BaseJobSource, _sanitize_xml, _is_uk_or_remote

logger = logging.getLogger("job360.sources.nhs_jobs")


class NHSJobsSource(BaseJobSource):
    """UK NHS Jobs via XML API — healthcare data/digital roles."""
    name = "nhs_jobs"
    category = "rss"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_ids = set()

        queries = self.search_queries[:6]  # Bounded to prevent source timeout
        if not queries:
            logger.info("NHS Jobs: no search queries in profile, skipping")
            return []
        for query in queries:
            xml_text = await self._get_text(
                "https://www.jobs.nhs.uk/api/v1/search_xml",
                params={"keywords": query, "page": "1"},
            )
            if not xml_text:
                continue
            for job in self._parse_xml(xml_text):
                # Deduplicate across queries
                key = job.apply_url
                if key not in seen_ids:
                    seen_ids.add(key)
                    jobs.append(job)

        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("NHS Jobs: found %s relevant jobs", len(jobs))
        return jobs

    def _parse_xml(self, xml_text: str) -> list[Job]:
        jobs = []
        try:
            root = ET.fromstring(_sanitize_xml(xml_text))
        except ET.ParseError as e:
            logger.warning("NHS Jobs: XML parse error: %s", e)
            return []

        for vacancy in root.iter("vacancy"):
            title = (vacancy.findtext("title") or "").strip()
            employer = (vacancy.findtext("employer") or "").strip()
            location = (vacancy.findtext("location") or "").strip()
            salary = (vacancy.findtext("salary") or "").strip()
            closing_date = (vacancy.findtext("closingDate") or "").strip()
            vacancy_id = (vacancy.findtext("id") or "").strip()
            advert_url = (vacancy.findtext("advertUrl") or "").strip()

            apply_url = advert_url or f"https://www.jobs.nhs.uk/candidate/jobadvert/{vacancy_id}"

            # Parse salary range
            salary_min, salary_max = self._parse_salary(salary)

            # closingDate is the posting DEADLINE, not the post date. Using it as
            # date_found would produce dates in the future (which inflate recency)
            # or old posts labelled "today". posted_at=None until the XML feed
            # exposes a real postedDate; closing_date retained in date_posted_raw.
            now_iso = datetime.now(timezone.utc).isoformat()

            jobs.append(Job(
                title=title,
                company=employer or "NHS",
                location=location or "UK",
                description=f"{title} - {salary}" if salary else title,
                apply_url=apply_url,
                source=self.name,
                date_found=now_iso,
                posted_at=None,
                date_confidence="low",
                date_posted_raw=closing_date or None,
                salary_min=salary_min,
                salary_max=salary_max,
            ))

        return jobs

    @staticmethod
    def _parse_salary(salary_str: str) -> tuple:
        if not salary_str:
            return None, None
        import re
        numbers = re.findall(r"[\d,]+", salary_str.replace(",", ""))
        nums = []
        for n in numbers:
            try:
                val = int(n)
                if 10000 <= val <= 500000:
                    nums.append(val)
            except ValueError:
                continue
        if len(nums) >= 2:
            return float(min(nums)), float(max(nums))
        if len(nums) == 1:
            return float(nums[0]), None
        return None, None
