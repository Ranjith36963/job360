import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.climatebase")

# Regex to extract Next.js embedded JSON data
_NEXT_DATA_RE = re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)

# S4: structural health check. The `__NEXT_DATA__` script tag is Next.js's
# own SSR data island — it renders on every page load (0 results or not).
# If a non-trivially large response arrives with no `__NEXT_DATA__` tag at
# all, Climatebase changed its rendering (or the whole page layout), not
# just its job count.
_MIN_STRUCTURAL_HTML_LEN = 500


def _to_number(value: object) -> Optional[float]:
    """Coerce an upstream salary value to a float, or None.

    Climatebase sends these as strings; Job.__post_init__ compares salary to
    ints, so an uncoerced string raises TypeError and (via the broad except in
    fetch_jobs) silently zeroes the whole source.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        cleaned = str(value).replace(",", "").replace("£", "").replace("$", "").strip()
        return float(cleaned) if cleaned else None
    except (TypeError, ValueError):
        return None


class ClimatebaseSource(BaseJobSource):
    """Climatebase — climate tech jobs. Extracts from Next.js embedded JSON."""
    name = "climatebase"
    category = "scrapers"
    DOMAINS = {"climate"}

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        seen_ids = set()
        queries = ["data scientist", "machine learning", "AI", "data engineer"]

        for query in queries:
            html = await self._get_text(
                "https://climatebase.org/jobs",
                params={"l": "United Kingdom", "q": query},
            )
            if not html:
                continue

            parsed = self._extract_jobs_from_next_data(html)
            for job in parsed:
                job_id = job.apply_url
                if job_id not in seen_ids:
                    seen_ids.add(job_id)
                    jobs.append(job)

        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Climatebase: found %s relevant jobs", len(jobs))
        return jobs

    def _extract_jobs_from_next_data(self, html: str) -> list[Job]:
        """Extract jobs from Next.js __NEXT_DATA__ script tag."""
        try:
            jobs = []
            now = datetime.now(timezone.utc).isoformat()

            match = _NEXT_DATA_RE.search(html)
            if not match:
                if len(html) > _MIN_STRUCTURAL_HTML_LEN:
                    logger.error(
                        "[climatebase] STRUCTURE CHANGED: expected __NEXT_DATA__ "
                        "script tag not found in a %d-byte response — parser may "
                        "be broken",
                        len(html),
                    )
                # Fallback to HTML scraping if __NEXT_DATA__ not found
                return self._parse_html_fallback(html)

            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                return self._parse_html_fallback(html)

            # Navigate to jobs in the Next.js props structure
            page_props = data.get("props", {}).get("pageProps", {})
            job_list = page_props.get("jobs", [])

            if not isinstance(job_list, list):
                return self._parse_html_fallback(html)

            for item in job_list:
                title = item.get("title", "")
                company = item.get("name_of_employer", "") or item.get("company", "") or "Unknown"

                locations = item.get("locations", [])
                if isinstance(locations, list):
                    location = ", ".join(str(loc) for loc in locations) if locations else "United Kingdom"
                else:
                    location = str(locations) if locations else "United Kingdom"

                job_id = item.get("id", "")
                apply_url = f"https://climatebase.org/jobs/{job_id}" if job_id else ""

                # Climatebase returns salary as a STRING (e.g. "80000"), but
                # Job.__post_init__ range-checks salary against ints. Passing
                # the raw value raised TypeError ("'<' not supported between
                # instances of 'str' and 'int'"), which the broad `except`
                # below swallowed — so this source silently returned ZERO jobs
                # (confirmed live 2026-08-08). Coerce, and drop anything that
                # isn't a real number rather than guessing.
                salary_min = _to_number(item.get("salary_from"))
                salary_max = _to_number(item.get("salary_to"))
                salary_period = item.get("salary_period") or None

                # activation_date is 100% filled live (2026-08-16) and used to
                # be thrown away in favour of a hardcoded None/"low" — every
                # Climatebase job read as undated even though the source
                # states a real one. Route it through the shared normalizer
                # so confidence reflects whether it actually parsed.
                raw_posted = item.get("activation_date")
                posted_at, confidence = normalize_posted_at(raw_posted)

                # job_types / remote_preferences are 100%-filled lists
                # ("Full time role", "Hybrid") thrown away today; take the
                # raw first value — no enum-mapping here, that is the gate's
                # job (rule per shelf_gate.py). sectors (100% filled, e.g.
                # "Research & Education") is the job's own topical
                # vocabulary — same bucket as other sources' tags[].
                job_types = item.get("job_types")
                employment_type = (
                    job_types[0] if isinstance(job_types, list) and job_types else None
                )
                remote_prefs = item.get("remote_preferences")
                workplace_mode = (
                    remote_prefs[0] if isinstance(remote_prefs, list) and remote_prefs else None
                )
                sectors = item.get("sectors")
                source_tags = sectors if isinstance(sectors, list) else []

                jobs.append(Job(
                    title=title,
                    company=company,
                    location=location,
                    description=title,
                    apply_url=apply_url,
                    source=self.name,
                    date_found=now,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_posted,
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_period=salary_period,
                    employment_type=employment_type,
                    workplace_mode=workplace_mode,
                    source_tags=source_tags,
                ))

            return jobs
        except Exception as e:
            logger.warning("Climatebase: HTML/JSON parsing failed: %s", e)
            return []

    def _parse_html_fallback(self, html: str) -> list[Job]:
        """Fallback HTML parsing if __NEXT_DATA__ extraction fails."""
        try:
            jobs = []
            now = datetime.now(timezone.utc).isoformat()

            link_pattern = re.compile(
                r'<a[^>]+href="(/jobs/[^"]+)"[^>]*>([^<]+)</a>',
                re.IGNORECASE
            )

            for match in link_pattern.finditer(html):
                path, title = match.group(1), match.group(2).strip()
                apply_url = f"https://climatebase.org{path}"
                jobs.append(Job(
                    title=title,
                    company="Unknown",
                    location="United Kingdom",
                    description=title,
                    apply_url=apply_url,
                    source=self.name,
                    date_found=now,
                    posted_at=None,
                    date_confidence="low",
                    date_posted_raw=None,
                ))

            return jobs
        except Exception as e:
            logger.warning("Climatebase: HTML fallback parsing failed: %s", e)
            return []
