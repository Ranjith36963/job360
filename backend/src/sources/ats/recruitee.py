import logging
from datetime import datetime, timezone
from typing import Any, Optional, cast

import aiohttp

from src.core.companies import COMPANY_NAME_OVERRIDES, RECRUITEE_COMPANIES
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.recruitee")


def _coerce_salary(value: Any) -> Optional[float]:
    """Recruitee sends salary.min/max as STRINGS ('0', '25000', '13.40').

    Verified live 2026-08-10 across the 31-slug list: 53 of 671 UK/remote
    offers carry string salaries (first offender: transperfect / "Marketing
    Lead - Legal Solutions", min='0' max='0'). `Job.__post_init__`
    (models.py:92) compares salary_min against an int, so an un-coerced
    string raised TypeError and killed the ENTIRE source mid-loop — the
    scheduler recorded it as a failure (scheduler.py:186) and recruitee
    contributed 0 jobs every run. Coerce here, in the source, so the shared
    Job model keeps its numeric contract.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _workplace_mode_from_offer(item: dict[str, Any]) -> Optional[str]:
    """Recruitee's own `remote`/`hybrid`/`on_site` booleans (confirmed live
    2026-08-17, transperfect board: 81 remote / 109 hybrid / 436 on_site out
    of 591) form a genuine closed 3-state field the source itself defines —
    translating it to our single workplace_mode shelf is a literal read of
    the source's own classification, not a guess.
    """
    if item.get("remote") is True:
        return "remote"
    if item.get("hybrid") is True:
        return "hybrid"
    if item.get("on_site") is True:
        return "onsite"
    return None


class RecruiteeSource(BaseJobSource):
    name = "recruitee"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else RECRUITEE_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            url = f"https://{slug}.recruitee.com/api/offers/"
            data = await self._get_json(url)
            if not data or "offers" not in data:
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in cast(dict[str, Any], data)["offers"]:
                title = item.get("title", "")
                desc = item.get("description", "")
                # `requirements` is skills/experience prose, a SEPARATE field
                # from `description` on the live API (verified 2026-08-16:
                # 926 of 1,194 UK/remote rows carry it, avg ~350 chars, no
                # overlap with description). Concatenating recovers real
                # skills text that was silently dropped.
                requirements = item.get("requirements", "")
                if requirements:
                    desc = f"{desc}\n\nRequirements: {requirements}" if desc else requirements
                location = item.get("location", "")
                if not _is_uk_or_remote(location):
                    continue
                apply_url = item.get("careers_url", "") or item.get("url", "")
                now_iso = datetime.now(timezone.utc).isoformat()
                raw_published = item.get("published_at")
                posted_at, confidence = normalize_posted_at(raw_published)

                # Live schema (verified 2026-08-08): salary is a NESTED object,
                # not flat min_salary/max_salary keys — those never existed.
                salary_obj = item.get("salary")
                if not isinstance(salary_obj, dict):
                    salary_obj = {}
                salary_min = _coerce_salary(salary_obj.get("min"))
                salary_max = _coerce_salary(salary_obj.get("max"))

                # close_at is the offer's closing date. Null in the sample, so
                # guard for it — a missing/unparseable value must never crash
                # or fabricate a deadline.
                deadline = None
                deadline_source = None
                raw_close_at = item.get("close_at")
                if raw_close_at:
                    try:
                        deadline = datetime.fromisoformat(
                            str(raw_close_at).replace("Z", "+00:00")
                        ).date().isoformat()
                        deadline_source = "listing"
                    except ValueError:
                        pass

                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=desc[:5000],
                    apply_url=apply_url,
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=raw_published,
                    salary_min=salary_min,
                    salary_max=salary_max,
                    deadline=deadline,
                    deadline_source=deadline_source,
                    # `experience_code` (entry_level/mid_level/experienced) is
                    # 100% filled on the live API (verified 2026-08-16, 176/176
                    # UK/remote rows) and was never read — raw value, the gate
                    # owns normalising it against the closed seniority enum.
                    seniority=item.get("experience_code"),
                    # `employment_type_code` (fulltime_permanent/parttime_
                    # fixed_term/contract/freelance/internship/...) is
                    # Recruitee's OWN compound closed vocabulary, verified
                    # live 2026-08-17 across 5 boards, and was never read at
                    # all — this was a pure gap, not a normalisation miss.
                    employment_type=item.get("employment_type_code"),
                    # `category_code` (sales/design/healthcare/legal_
                    # services/...) is Recruitee's own industry taxonomy,
                    # verified live 2026-08-17 (transperfect: 30 distinct
                    # codes across 591 offers) and was never read.
                    category=item.get("category_code"),
                    workplace_mode=_workplace_mode_from_offer(item),
                ))
        logger.info("Recruitee: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs
