import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

# M18: XXE-safe parse of untrusted feed XML. defusedxml ships no type stubs
# and types-defusedxml is not a project dependency, hence the import ignore.
from defusedxml.ElementTree import fromstring as _safe_fromstring  # type: ignore[import-untyped]

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote, _sanitize_xml
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.nhs_jobs")


class NHSJobsSource(BaseJobSource):
    """UK NHS Jobs via XML API — healthcare data/digital roles."""
    name = "nhs_jobs"
    category = "rss"
    DOMAINS = {"healthcare"}

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_ids = set()

        # `search_titles`, not `search_queries`: NHS Jobs is a UK-only board, so
        # the " UK" suffix the query strings carry is pure noise in `keywords`.
        queries = self.search_titles[:6]  # Bounded to prevent source timeout
        if not queries:
            logger.info("NHS Jobs: no search titles in profile, skipping")
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
            root = _safe_fromstring(_sanitize_xml(xml_text))
        except ET.ParseError as e:
            logger.warning("NHS Jobs: XML parse error: %s", e)
            return []

        # Live upstream (verified 2026-08-08) wraps each record in
        # <vacancyDetails>, not the <vacancy> this loop used to look for —
        # that mismatch made this source return ZERO jobs. Keep <vacancy> as
        # a fallback so a future rename back doesn't repeat the outage.
        vacancy_nodes = list(root.iter("vacancyDetails"))
        if not vacancy_nodes:
            vacancy_nodes = list(root.iter("vacancy"))

        for vacancy in vacancy_nodes:
            title = (vacancy.findtext("title") or "").strip()
            employer = (vacancy.findtext("employer") or "").strip()
            salary = (vacancy.findtext("salary") or "").strip()
            close_date = (vacancy.findtext("closeDate") or "").strip()
            post_date = (vacancy.findtext("postDate") or "").strip()
            vacancy_id = (vacancy.findtext("id") or "").strip()
            # Live tag is <url>; <advertUrl> kept as a fallback for the old
            # shape (same reasoning as the vacancyDetails/vacancy fallback).
            advert_url = (vacancy.findtext("url") or vacancy.findtext("advertUrl") or "").strip()

            apply_url = advert_url or f"https://www.jobs.nhs.uk/candidate/jobadvert/{vacancy_id}"

            # Live location is nested <locations><location>...</location>...
            # (can repeat) rather than a flat <location>. Fall back to the
            # flat tag for the old shape.
            locations_el = vacancy.find("locations")
            if locations_el is not None:
                location = ", ".join(
                    (loc.text or "").strip()
                    for loc in locations_el.findall("location")
                    if loc.text and loc.text.strip()
                )
            else:
                location = (vacancy.findtext("location") or "").strip()

            # Parse salary range
            salary_min, salary_max = self._parse_salary(salary)

            # postDate is the real posting date (100% fill live) — route it
            # through the shared normalizer so date_confidence reflects
            # whether it actually parsed, never fabricated.
            posted_at, confidence = normalize_posted_at(post_date)

            # closeDate is the posting DEADLINE, not the post date — it goes
            # on the deadline shelf, never into posted_at/date_posted_raw.
            deadline_iso, deadline_confidence = normalize_posted_at(close_date)
            deadline = deadline_iso[:10] if deadline_confidence == "high" and deadline_iso else None
            deadline_source = "listing" if deadline else None

            now_iso = datetime.now(timezone.utc).isoformat()

            jobs.append(Job(
                title=title,
                company=employer or "NHS",
                location=location or "UK",
                description=f"{title} - {salary}" if salary else title,
                apply_url=apply_url,
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=post_date or None,
                deadline=deadline,
                deadline_source=deadline_source,
                salary_min=salary_min,
                salary_max=salary_max,
            ))

        return jobs

    @staticmethod
    def _parse_salary(salary_str: str) -> tuple[Optional[float], Optional[float]]:
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
