import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional, cast

import aiohttp

from src.core.companies import COMPANY_NAME_OVERRIDES, SMARTRECRUITERS_COMPANIES
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.smartrecruiters")

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MAX_DETAIL_FETCHES = 60

class SmartRecruitersSource(BaseJobSource):
    name = "smartrecruiters"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else SMARTRECRUITERS_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        detail_budget = _MAX_DETAIL_FETCHES
        for slug in self._companies:
            url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
            data = await self._get_json(url, params={"limit": "100"})
            if not data or "content" not in data:
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in cast(dict[str, Any], data)["content"]:
                title = item.get("name", "")
                loc = item.get("location", {})
                if isinstance(loc, dict):
                    city = loc.get("city", "")
                    country = loc.get("country", "")
                    location = f"{city}, {country}".strip(", ")
                else:
                    location = str(loc)
                # `ref` on the LIST item is the postings API URL itself
                # (verified live 2026-08-16), NOT a page a human can open.
                # The real human-facing link only exists on the DETAIL
                # response (postingUrl/applyUrl), fetched below for
                # UK/remote-relevant postings -- apply_url is overwritten
                # there. This is only the fallback for postings that never
                # get a detail fetch.
                apply_url = f"https://jobs.smartrecruiters.com/{slug}/{item.get('id', '')}"
                now_iso = datetime.now(timezone.utc).isoformat()
                raw_released = item.get("releasedDate")
                posted_at, confidence = normalize_posted_at(raw_released)

                exp_level_obj = item.get("experienceLevel")
                experience_level = ""
                if isinstance(exp_level_obj, dict):
                    experience_level = exp_level_obj.get("label") or ""

                employment_obj = item.get("typeOfEmployment")
                employment_type = None
                if isinstance(employment_obj, dict):
                    employment_type = employment_obj.get("label")

                description = ""
                salary_min: Optional[float] = None
                salary_max: Optional[float] = None
                if _is_uk_or_remote(location) and detail_budget > 0:
                    detail_budget -= 1
                    detail = await self._fetch_posting_detail(
                        slug, str(item.get("id", ""))
                    )
                    description = self._extract_description_text(detail)
                    comp = detail.get("compensation")
                    if isinstance(comp, dict):
                        salary_min = comp.get("min")
                        salary_max = comp.get("max")
                    detail_apply_url = detail.get("postingUrl") or detail.get("applyUrl")
                    if detail_apply_url:
                        apply_url = detail_apply_url

                job = Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=description,
                    apply_url=apply_url,
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_released,
                    experience_level=experience_level,
                    employment_type=employment_type,
                    salary_min=salary_min,
                    salary_max=salary_max,
                )
                jobs.append(job)
        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("SmartRecruiters: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs

    async def _fetch_posting_detail(self, slug: str, posting_id: str) -> dict[str, Any]:
        if not posting_id:
            return {}
        detail = await self._get_json(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}"
        )
        return detail if isinstance(detail, dict) else {}

    @staticmethod
    def _extract_description_text(detail: dict[str, Any]) -> str:
        sections = (detail.get("jobAd") or {}).get("sections") or {}
        parts: list[str] = []
        for key in ("jobDescription", "qualifications", "additionalInformation", "companyDescription"):
            sec = sections.get(key)
            text = (sec or {}).get("text") if isinstance(sec, dict) else None
            if text:
                parts.append(_HTML_TAG_RE.sub(" ", str(text)))
        return " ".join(parts)[:5000].strip()

    async def _fetch_posting_text(self, slug: str, posting_id: str) -> str:
        detail = await self._fetch_posting_detail(slug, posting_id)
        return self._extract_description_text(detail)
