import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional, cast

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.remotive")

_HOURLY_RE = re.compile(r"/\s*hour|per\s*hour|/\s*hr\b", re.IGNORECASE)
_OTE_RE = re.compile(r"\bote\b", re.IGNORECASE)
_NUM_RE = re.compile(r"(\d[\d,.]*)\s*([kK])?")


def _parse_remotive_salary(raw: Any) -> tuple[Optional[float], Optional[float]]:
    """Parse Remotive's free-text `salary` field into (min, max).

    Live check (2026-08, 14 real values) found the old parser — which only
    understood "$45,000 - $50,000" — matched 1 of 14. The rest used `k`
    shorthand ("$20k -$35k", "$31,2k- $52k"), `/hour` rates ("$90 - $150
    /hour"), and "OTE" prefixes ("OTE $25k - $35k"). Hourly rates are
    skipped entirely rather than stored as if annual — a $100/hr contract
    rate stored as a £100 salary would be worse than no salary at all.
    Never raises: any parse failure falls through to (None, None).
    """
    try:
        if not raw:
            return None, None
        text = str(raw).strip()
        if not text:
            return None, None
        if _HOURLY_RE.search(text):
            return None, None
        # Strip "OTE" (on-target earnings — still an annual figure once the
        # word is gone) and currency symbols before hunting for numbers.
        cleaned = _OTE_RE.sub(" ", text)
        cleaned = cleaned.replace("$", "").replace("£", "").replace("€", "")

        values: list[float] = []
        for num_str, k_suffix in _NUM_RE.findall(cleaned):
            num_str = num_str.strip().strip(".")
            if not num_str:
                continue
            # A comma immediately followed by 1-2 digits then "k" is a
            # decimal separator ("31,2k" == 31.2k == 31200); anywhere else a
            # comma is a thousands separator ("31,000") and gets dropped.
            if k_suffix and re.match(r"^\d+,\d{1,2}$", num_str):
                num_str = num_str.replace(",", ".")
            else:
                num_str = num_str.replace(",", "")
            try:
                n = float(num_str)
            except ValueError:
                continue
            if k_suffix:
                n *= 1000
            values.append(n)

        if len(values) >= 2:
            return values[0], values[1]
        if len(values) == 1:
            return values[0], values[0]
        return None, None
    except (ValueError, TypeError, AttributeError, IndexError):
        return None, None


class RemotiveSource(BaseJobSource):
    name = "remotive"
    category = "free_json"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        data = await self._get_json(
            "https://remotive.com/api/remote-jobs",
            params={"category": "software-dev", "limit": "100"},
        )
        if not data or "jobs" not in data:
            return []
        for item in cast(dict[str, Any], data)["jobs"]:
            title = item.get("title", "")
            desc = item.get("description", "")
            now_iso = datetime.now(timezone.utc).isoformat()
            raw_pub = item.get("publication_date")
            posted_at, confidence = normalize_posted_at(raw_pub)

            salary_min, salary_max = _parse_remotive_salary(item.get("salary", ""))
            jobs.append(Job(
                title=title,
                company=item.get("company_name", ""),
                location=item.get("candidate_required_location", ""),
                description=desc[:5000],
                apply_url=item.get("url", ""),
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_pub,
                salary_min=salary_min,
                salary_max=salary_max,
            ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Remotive: found %s relevant jobs", len(jobs))
        return jobs
