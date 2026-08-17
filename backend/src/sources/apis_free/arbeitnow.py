import logging
from datetime import datetime, timezone
from typing import Any, cast

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.arbeitnow")

class ArbeitnowSource(BaseJobSource):
    name = "arbeitnow"
    category = "free_json"

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        data = await self._get_json("https://www.arbeitnow.com/api/job-board-api")
        if not data or "data" not in data:
            return []
        for item in cast(dict[str, Any], data)["data"]:
            now_iso = datetime.now(timezone.utc).isoformat()
            raw_created = item.get("created_at")
            posted_at, confidence = normalize_posted_at(raw_created)

            # `tags` (~93% fill live) is the job's OWN vocabulary --
            # arbeitnow-assigned keyword tags, not a guess we are making.
            # Raw list straight onto source_tags; the gate/scorer read it,
            # no normalising here.
            #
            # `job_types` (58% fill live, verified 2026-08-17) is a LIST that
            # conflates employment-type words ("Full Time", "Part time") AND
            # seniority/experience words ("Entry", "Mid", "Executive") in one
            # field with no fixed order -- arbeitnow's own taxonomy, not ours.
            # Only the FIRST element is used here (list -> scalar unwrap, the
            # same pattern jobicy's `jobType[0]` already uses): most listings
            # put the employment-type word first ("Full Time", "Part-time",
            # "Contract"), and the gate's closed-enum matcher only accepts an
            # exact/aliased match, so a wrong guess here just lands as an
            # honest "not_mapped" rather than a wrong classification.
            # Deliberately NOT attempting to split the list across
            # employment_type/seniority by position -- that would be
            # interpreting arbeitnow's own field, which belongs to the gate
            # (if ever), not this dumb mapper.
            job_types = item.get("job_types")
            employment_type = job_types[0] if isinstance(job_types, list) and job_types else None

            # `remote` (100% fill live) is arbeitnow's own boolean for "this
            # posting is remote-friendly". Only the TRUE case is mapped -- a
            # False here just means "not tagged remote", not "definitely
            # onsite" or "definitely hybrid" (rule #29: never invent the
            # untold half of a fact).
            workplace_mode = "Remote" if item.get("remote") else None

            jobs.append(Job(
                title=item.get("title", ""),
                company=item.get("company_name", ""),
                location=item.get("location", ""),
                description=item.get("description", ""),
                apply_url=item.get("url", ""),
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_created,
                source_tags=item.get("tags") or [],
                employment_type=employment_type,
                workplace_mode=workplace_mode,
            ))
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("Arbeitnow: found %s relevant jobs", len(jobs))
        return jobs
