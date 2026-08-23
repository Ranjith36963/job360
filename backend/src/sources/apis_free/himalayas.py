import logging
from datetime import datetime, timezone
from typing import Any, cast

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.himalayas")

class HimalayasSource(BaseJobSource):
    name = "himalayas"
    category = "free_json"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        params = {"limit": "50"}
        data = await self._get_json(
            "https://himalayas.app/jobs/api", params=params
        )
        if not data or "jobs" not in data:
            return []
        for item in cast(dict[str, Any], data)["jobs"]:
            loc_restrictions = item.get("locationRestrictions", [])
            location = ", ".join(loc_restrictions) if isinstance(loc_restrictions, list) else str(loc_restrictions)
            now_iso = datetime.now(timezone.utc).isoformat()
            raw_pub = item.get("pubDate") or item.get("createdAt")
            posted_at, confidence = normalize_posted_at(raw_pub)

            # `applicationUrl`/`url` never appear on the live API — verified
            # against a real response. The real key is `applicationLink`
            # (100% fill). Without this every Himalayas job got apply_url=""
            # and users had no way to apply. Keep the old keys as fallback in
            # case the upstream shape shifts again.
            apply_url = (
                item.get("applicationLink")
                or item.get("applicationUrl")
                or item.get("url", "")
            )

            # `expiryDate` (100% fill) is a unix timestamp for the listing's
            # own application deadline. Route it through the same tested
            # epoch parser used for posted_at (handles seconds/ms + range
            # sanity) rather than reinventing timestamp math here.
            raw_expiry = item.get("expiryDate")
            expiry_iso, expiry_confidence = normalize_posted_at(raw_expiry)
            deadline = expiry_iso[:10] if expiry_confidence == "high" and expiry_iso else None
            deadline_source = "listing" if deadline else None

            # `seniority` (100% fill) arrives as a list, e.g. ["Senior"].
            seniority = item.get("seniority")
            if isinstance(seniority, list) and seniority:
                experience_level = str(seniority[0])
            elif isinstance(seniority, str):
                experience_level = seniority
            else:
                experience_level = ""

            jobs.append(Job(
                title=item.get("title", ""),
                company=item.get("companyName", ""),
                location=location,
                salary_min=item.get("minSalary"),
                salary_max=item.get("maxSalary"),
                description=item.get("excerpt", ""),
                apply_url=apply_url,
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_pub,
                deadline=deadline,
                deadline_source=deadline_source,
                experience_level=experience_level,
            ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Himalayas: found %s relevant jobs", len(jobs))
        return jobs
