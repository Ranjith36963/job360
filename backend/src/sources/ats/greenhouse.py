import html
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional, cast

import aiohttp

from src.core.companies import COMPANY_NAME_OVERRIDES, GREENHOUSE_COMPANIES
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.greenhouse")

_HTML_TAG_RE = re.compile(r"<[^>]+>")


class GreenhouseSource(BaseJobSource):
    name = "greenhouse"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else GREENHOUSE_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            # Job-understanding fix (2026-08-05): WITHOUT `?content=true` the
            # board list endpoint returns NO `content` field at all — verified
            # live (deepmind board: absent vs 5,359 chars). Every greenhouse
            # job in prod (996 rows, 100% of the slice) had an empty
            # description because this param was missing. Same request count.
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
            data = await self._get_json(url)
            if not data or "jobs" not in data:
                continue
            fallback_company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in cast(dict[str, Any], data)["jobs"]:
                title = item.get("title", "")
                content = item.get("content", "")
                # The API returns HTML-ENTITY-ESCAPED markup (`&lt;p&gt;`), so
                # unescape first — the tag-strip regex can't see escaped tags,
                # and skipping this stores literal `&lt;h4&gt;` noise as text.
                plain = _HTML_TAG_RE.sub(" ", html.unescape(content or ""))
                loc = item.get("location", {})
                location = loc.get("name", "") if isinstance(loc, dict) else str(loc)
                if not _is_uk_or_remote(location):
                    continue
                # `company_name` is a clean official name (e.g. "Monzo", "Stripe")
                # returned by the live API at 100% fill — prefer it over the
                # slug-derived name; fall back to COMPANY_NAME_OVERRIDES/slug
                # title-casing when it is absent.
                company_name = item.get("company_name") or fallback_company_name
                # `first_published` is a real ISO publish timestamp, filled on
                # 100% of jobs (verified live across 928 jobs) — it is the
                # actual posted_at, unlike `updated_at`, which only tracks
                # edits (salary tweak, department change) and would wrongly
                # bump a stale posting back into the "just posted" bucket.
                # Route it through normalize_posted_at() so an unparseable
                # value is reported low-confidence instead of fabricated.
                # Keep updated_at in date_posted_raw for audit.
                now_iso = datetime.now(timezone.utc).isoformat()
                raw_updated_at = item.get("updated_at")
                raw_published = item.get("first_published")
                posted_at, confidence = normalize_posted_at(raw_published)

                # `application_deadline` is a real ISO datetime, rarely filled
                # (verified live 2026-08-16: 1/82 on monzo, 0/578 on stripe —
                # most companies just don't set one) but never opened before.
                # Absent/unparseable never fabricates a deadline.
                deadline = None
                deadline_source = None
                raw_deadline = item.get("application_deadline")
                if raw_deadline:
                    try:
                        deadline = datetime.fromisoformat(
                            str(raw_deadline).replace("Z", "+00:00")
                        ).date().isoformat()
                        deadline_source = "listing"
                    except ValueError:
                        pass

                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=plain[:5000],
                    apply_url=item.get("absolute_url", ""),
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_updated_at,
                    deadline=deadline,
                    deadline_source=deadline_source,
                ))
        logger.info("Greenhouse: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs

    async def _fetch_job_content(self, slug: str, job_id: str) -> str:
        """Fetch ONE posting's content via the per-job detail endpoint.

        fetch_jobs() only ever calls the whole-board listing (?content=true
        gets every posting's text in one shot), so this is a NEW, narrower
        entry point — added for the description-backfill sweep
        (services/description_backfill.py), which needs to refresh a single
        already-stored job without re-listing its whole company. Same parse
        as fetch_jobs(): unescape entities, strip tags, cap 5000 chars.
        Returns "" on any failure — a miss is a data gap, not an error.
        """
        detail = await self._get_json(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}?content=true"
        )
        if not isinstance(detail, dict):
            return ""
        content = detail.get("content", "")
        return _HTML_TAG_RE.sub(" ", html.unescape(content or ""))[:5000].strip()
