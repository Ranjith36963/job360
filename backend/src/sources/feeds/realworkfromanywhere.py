import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# M18: XXE-safe parse of untrusted feed XML. defusedxml ships no type stubs
# and types-defusedxml is not a project dependency, hence the import ignore.
from defusedxml.ElementTree import fromstring as _safe_fromstring  # type: ignore[import-untyped]

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote, _sanitize_xml
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.realworkfromanywhere")

# The feed carries ~144 items/run; the per-job JSON-LD (validThrough,
# baseSalary) sits on the detail page, a second request
# (RATE_LIMITS["realworkfromanywhere"]: 2.0s/request). Fetching all 144
# would take ~290s, well past SOURCE_FETCH_TIMEOUT -- cap the detail pass
# the same way linkedin.py / uni_jobs.py do.
_MAX_DETAIL_FETCHES = 20
_LDJSON_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL
)


class RealWorkFromAnywhereSource(BaseJobSource):
    """RealWorkFromAnywhere — remote jobs via RSS feed."""
    name = "realworkfromanywhere"
    category = "rss"

    async def fetch_jobs(self) -> list[Job]:
        xml_text = await self._get_text("https://www.realworkfromanywhere.com/rss.xml")
        if not xml_text:
            return []

        jobs = self._parse_feed(xml_text)

        detailed = 0
        for job in jobs[:_MAX_DETAIL_FETCHES]:
            try:
                if await self._fill_detail(job):
                    detailed += 1
            except Exception as e:  # noqa: BLE001 -- a detail miss never drops the job
                logger.debug(
                    "RealWorkFromAnywhere: detail fetch failed for %s: %s", job.apply_url, e
                )

        logger.info(
            "RealWorkFromAnywhere: found %s relevant jobs (%s with deadline/salary)",
            len(jobs), detailed,
        )
        return jobs

    async def _fill_detail(self, job: Job) -> bool:
        """Pull validThrough (deadline) + baseSalary + employmentType +
        jobLocationType from the job's own schema.org JobPosting ld+json
        (confirmed live 2026-08-16: validThrough 12/12, baseSalary 6/12 on a
        live sample; confirmed live 2026-08-17: employmentType 8/8,
        jobLocationType 8/8 on the SAME already-fetched object -- previously
        parsed and simply never read). applicantLocationRequirements also
        exists but is deliberately NOT mapped here -- every job in the
        live sample carried the SAME ~184-country boilerplate list (the
        site's "open to remote worldwide" default), so it is not real
        per-job signal to replace the hardcoded "Remote" with.
        """
        page = await self._get_text(job.apply_url)
        if not page:
            return False
        # EVERY BLOCK, AND THE THREE SHAPES SCHEMA.ORG REALLY EMITS.
        # `search()` looked at the FIRST `ld+json` block only, and pages
        # routinely put Organization / WebSite / BreadcrumbList before the
        # JobPosting. A reorder upstream would return False for every job, and
        # the only symptom is `detailed=0` in the log — identical to the site
        # dropping JSON-LD altogether, so nobody could tell the two apart.
        #
        # A top-level ARRAY and a `{"@graph": [...]}` wrapper are both standard
        # output and were both discarded by the bare-dict check.
        # (CodeRabbit, PR #388.)
        data = None
        for block in _LDJSON_RE.findall(page):
            try:
                parsed = json.loads(block)
            except (ValueError, TypeError):
                continue
            candidates = (
                parsed if isinstance(parsed, list)
                else parsed.get("@graph", [parsed]) if isinstance(parsed, dict)
                else []
            )
            if not isinstance(candidates, list):
                candidates = [candidates]
            for candidate in candidates:
                if isinstance(candidate, dict) and candidate.get("@type") == "JobPosting":
                    data = candidate
                    break
            if data is not None:
                break
        if data is None:
            return False

        found_any = False

        valid_through = data.get("validThrough")
        if isinstance(valid_through, str) and valid_through:
            deadline_iso, deadline_confidence = normalize_posted_at(valid_through)
            if deadline_confidence == "high" and deadline_iso:
                job.deadline = deadline_iso[:10]
                job.deadline_source = "listing"
                found_any = True

        base_salary = data.get("baseSalary")
        if isinstance(base_salary, dict):
            value = base_salary.get("value")
            if isinstance(value, dict):
                min_v, max_v = value.get("minValue"), value.get("maxValue")
                if isinstance(min_v, (int, float)) or isinstance(max_v, (int, float)):
                    job.salary_min = float(min_v) if isinstance(min_v, (int, float)) else None
                    job.salary_max = float(max_v) if isinstance(max_v, (int, float)) else None
                    found_any = True
                unit_text = value.get("unitText")
                if isinstance(unit_text, str) and unit_text:
                    job.salary_period = unit_text
            # schema.org allows the currency on the MonetaryAmount OR on the
            # nested QuantitativeValue. Reading only the outer one left
            # `salary_currency` None for the nested form, and `_annualise_one`
            # then defaults to GBP — so a USD figure was stored as sterling.
            # (CodeRabbit, PR #388.)
            currency = base_salary.get("currency")
            if not (isinstance(currency, str) and currency) and isinstance(value, dict):
                currency = value.get("currency")
            if isinstance(currency, str) and currency:
                job.salary_currency = currency

        employment_type = data.get("employmentType")
        if isinstance(employment_type, str) and employment_type:
            job.employment_type = employment_type
            found_any = True

        job_location_type = data.get("jobLocationType")
        if isinstance(job_location_type, str) and job_location_type:
            job.workplace_mode = job_location_type
            found_any = True

        return found_any

    def _parse_feed(self, xml_text: str) -> list[Job]:
        jobs = []
        try:
            root = _safe_fromstring(_sanitize_xml(xml_text))
        except ET.ParseError as e:
            logger.warning("RealWorkFromAnywhere: XML parse error: %s", e)
            return []

        channel = root.find("channel")
        if channel is None:
            channel = root

        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            author = (item.findtext("author") or "").strip()

            if not _is_uk_or_remote(f"{title} {description}"):
                continue

            # <author> (100% fill live) holds the exact company name.
            # Splitting the title on " at "/" - "/" @ " is brittle (breaks on
            # any title containing those substrings) — prefer author, fall
            # back to the old split only when it's missing.
            if author:
                company = author
            else:
                company = "Unknown"
                for sep in [" at ", " - ", " @ "]:
                    if sep in title:
                        company = title.split(sep, 1)[1].strip()
                        break

            now_iso = datetime.now(timezone.utc).isoformat()
            posted_at = self._parse_rss_date(pub_date) if pub_date else None
            # Confidence follows the RESULT, not the input. A present-but-
            # unparseable value used to be stamped "high" purely because the
            # field existed — certifying as trustworthy a date we could not read.
            confidence = "high" if posted_at else "low"
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
