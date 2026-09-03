import logging
import re
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

# The Cambridge feed alone carries ~200 items/run and the detail page is a
# second request per job (RATE_LIMITS["uni_jobs"]: 2.0s/request) -- fetching
# every one would take ~400s, well past SOURCE_FETCH_TIMEOUT (60s default).
# Academic jobs live by their closing date more than any other source in
# this batch, so a bounded sample is still worth it: cap detail fetches,
# same pattern as themuse.py's MAX_PAGES / linkedin.py's _MAX_DETAIL_FETCHES.
_MAX_DETAIL_FETCHES = 20

# "<h6>Salary</h6><p>...</p>" / "<h6>Closing date</h6><p>...</p>" sidebar
# rows on the Cambridge detail page (confirmed live 2026-08-16) -- neither
# is in the RSS feed itself.
_SALARY_RE = re.compile(
    r"<h6>\s*Salary\s*</h6>\s*<p>\s*([^<]+?)\s*</p>", re.IGNORECASE
)
_CLOSING_DATE_RE = re.compile(
    r"<h6>\s*Closing date\s*</h6>\s*<p>\s*([^<]+?)\s*</p>", re.IGNORECASE
)
# "<h6>Category</h6><p><a href="/job/?category=1">Academic</a></p>" --
# Cambridge's own job-function taxonomy (confirmed live 2026-08-17: 10/10 on
# a live sample -- "Academic", "Academic-related", "Assistant staff"...).
# Unlike Salary/Closing date, the value sits inside a nested <a>, so the
# optional (?:<a[^>]*>)? / (?:</a>)? skip past it; raw value only, no
# enum-mapping here.
_CATEGORY_RE = re.compile(
    r"<h6>\s*Category\s*</h6>\s*<p>\s*(?:<a[^>]*>)?\s*([^<]+?)\s*(?:</a>)?\s*</p>",
    re.IGNORECASE,
)
# Cambridge renders the closing date as "24 August 2026" -- day, full month
# name, year. Deliberately its own parser: normalize_posted_at does not
# recognise month names, and (per models.py / rule #29) a deadline must
# never be silently fabricated from a format we cannot read.
_CLOSING_DATE_FMT = "%d %B %Y"


class UniJobsSource(BaseJobSource):
    """UK University RSS feeds — academic research positions."""
    name = "uni_jobs"
    category = "rss"
    DOMAINS = {"academia"}

    async def fetch_jobs(self) -> list[Job]:
        # THE PAIR IS CARRIED, NOT REBUILT FROM `id(job)`.
        # `_parse_feed` already yields `(job, listing_url)`. Splitting them and
        # re-joining through `id(job)` — CPython object identity — worked only
        # because `jobs` happened to keep every object alive, so no id was
        # reused. Anything that copies or replaces a Job between the filter and
        # the detail pass would break the lookup silently: `.get(id(job))`
        # returns None, `continue` fires, the detail pass enriches nothing, and
        # the log still reports a normal-looking count. A pairing that already
        # exists should not be reconstructed from an address.
        # (CodeRabbit, PR #388.)
        pairs: list[tuple[Job, str]] = []
        for feed in UNIVERSITY_FEEDS:
            xml_text = await self._get_text(feed["url"])
            if not xml_text:
                continue
            pairs.extend(self._parse_feed(xml_text, feed["name"]))

        pairs = [(j, url) for j, url in pairs if _is_uk_or_remote(j.location)]
        jobs = [j for j, _ in pairs]

        detailed = 0
        for job, listing_url in pairs[:_MAX_DETAIL_FETCHES]:
            if not listing_url:
                continue
            try:
                if await self._fill_detail(job, listing_url):
                    detailed += 1
            except Exception as e:  # noqa: BLE001 -- a detail miss never drops the job
                logger.debug("University Jobs: detail fetch failed for %s: %s", listing_url, e)

        logger.info(
            "University Jobs: found %s relevant jobs (%s with salary/closing date)",
            len(jobs), detailed,
        )
        return jobs

    async def _fill_detail(self, job: Job, listing_url: str) -> bool:
        """Pull Salary + Closing date off the Cambridge listing page's
        sidebar -- neither is in the RSS feed. A miss never drops the job.
        """
        page = await self._get_text(listing_url)
        if not page:
            return False
        found_any = False

        salary_match = _SALARY_RE.search(page)
        if salary_match:
            salary_text = salary_match.group(1).strip()
            if salary_text:
                salary_min, salary_max = self._parse_salary_range(salary_text)
                if salary_min is not None or salary_max is not None:
                    job.salary_min = salary_min
                    job.salary_max = salary_max
                    found_any = True

        closing_match = _CLOSING_DATE_RE.search(page)
        if closing_match:
            closing_text = closing_match.group(1).strip()
            try:
                closing_dt = datetime.strptime(closing_text, _CLOSING_DATE_FMT)
            except ValueError:
                closing_dt = None
            if closing_dt is not None:
                job.deadline = closing_dt.date().isoformat()
                job.deadline_source = "listing"
                found_any = True

        category_match = _CATEGORY_RE.search(page)
        if category_match:
            category_text = category_match.group(1).strip()
            if category_text:
                job.category = category_text
                found_any = True

        return found_any

    @staticmethod
    def _parse_salary_range(text: str) -> tuple[float | None, float | None]:
        """"£35,608-£46,049 pro rata" -> (35608.0, 46049.0). Sanity-bounded
        the same way Job.__post_init__ bounds every other salary figure;
        Job.__post_init__ re-applies its own clamp regardless (models.py).
        """
        numbers = re.findall(r"[\d,]+", text)
        nums = []
        for n in numbers:
            digits = n.replace(",", "")
            if not digits:
                continue
            val = int(digits)
            if 10000 <= val <= 500000:
                nums.append(val)
        if len(nums) >= 2:
            return float(min(nums)), float(max(nums))
        if len(nums) == 1:
            return float(nums[0]), None
        return None, None

    # The REAL shape, not a bare `list`. It was widened rather than corrected
    # when the return became pairs, which hides the very coupling the caller
    # above got wrong. (CodeRabbit, PR #388.)
    def _parse_feed(self, xml_text: str, university: str) -> list[tuple[Job, str]]:
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
            job = Job(
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
            )
            # listing_url (the RSS <link>, distinct from apply_url when the
            # feed also carries applyOnlineUrl) is where Salary/Closing date
            # actually live -- passed back to fetch_jobs for the capped
            # detail pass, never stored on the Job itself.
            jobs.append((job, link or apply_url))

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
