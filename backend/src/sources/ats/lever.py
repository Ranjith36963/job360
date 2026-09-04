import logging
import re
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from src.core.companies import COMPANY_NAME_OVERRIDES, LEVER_COMPANIES
from src.models import Job
from src.services.profile.models import SearchConfig
from src.sources.base import BaseJobSource, _is_uk_or_remote

logger = logging.getLogger("job360.sources.lever")

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _build_description(item: dict) -> str:
    """Concatenate every text block Lever gives us for a posting.

    Verified live 2026-08-16 (spotify board): `descriptionPlain` alone
    averages ~1,100 chars — the intro paragraph only. `lists[]` (section
    headings like "What You'll Do" / "Who You Are" / "Where You'll Be")
    carries the REQUIREMENTS text, ~4,000 extra chars per posting, and it
    is NOT a duplicate of descriptionPlain (checked substring-by-substring).
    `additionalPlain` is a further free-text field some postings use for
    benefits/EEO text. All three are already inside the one JSON response
    fetch_jobs() already downloaded — this costs zero extra HTTP calls.
    """
    parts = [item.get("descriptionPlain", item.get("description", "")) or ""]
    for lst in item.get("lists") or []:
        heading = lst.get("text") or ""
        content = lst.get("content") or ""
        plain = _HTML_TAG_RE.sub(" ", content).strip()
        if plain:
            parts.append(f"{heading}: {plain}" if heading else plain)
    additional = item.get("additionalPlain") or ""
    if additional:
        parts.append(additional)
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


class LeverSource(BaseJobSource):
    name = "lever"
    category = "ats"

    def __init__(self, session: aiohttp.ClientSession, companies: list[str] | None = None, search_config: Optional[SearchConfig] = None):
        super().__init__(session, search_config=search_config)
        self._companies = companies if companies is not None else LEVER_COMPANIES

    async def fetch_jobs(self) -> list[Job]:
        jobs = []
        for slug in self._companies:
            url = f"https://api.lever.co/v0/postings/{slug}"
            params = {"mode": "json"}
            data = await self._get_json(url, params=params)
            if not data or not isinstance(data, list):
                continue
            company_name = COMPANY_NAME_OVERRIDES.get(slug, slug.replace("-", " ").title())
            for item in data:
                title = item.get("text", "")
                desc = _build_description(item)
                categories = item.get("categories", {})
                location = categories.get("location", "") if isinstance(categories, dict) else ""
                # Live API also returns a clean ISO-2 `country` code (GB/US/SE/...)
                # at 100% fill — a more reliable UK signal than free-text
                # `categories.location`. Trust it when present (country == "GB"
                # is unambiguous), but keep the existing text-based check as a
                # fallback so remote-friendly / unset-country postings are not
                # newly filtered out.
                country = item.get("country", "")
                is_uk_by_country = country == "GB"
                if not is_uk_by_country and not _is_uk_or_remote(location):
                    continue
                # Lever createdAt is milliseconds since epoch — real posting date.
                now_iso = datetime.now(timezone.utc).isoformat()
                created_at = item.get("createdAt")
                if created_at and isinstance(created_at, (int, float)):
                    posted_at = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc).isoformat()
                    confidence = "high"
                else:
                    posted_at = None
                    confidence = "low"
                jobs.append(Job(
                    title=title,
                    company=company_name,
                    location=location,
                    description=desc[:5000],
                    # `applyUrl` (= hostedUrl + "/apply") is the real application
                    # form; `hostedUrl` is just the job info page. Live API fills
                    # `applyUrl` at 100%.
                    apply_url=item.get("applyUrl", item.get("hostedUrl", "")),
                    source=self.name,
                    date_found=now_iso,
                    posted_at=posted_at,
                    date_confidence=confidence,
                    date_posted_raw=str(created_at) if created_at else None,
                    # Universal Shelf raw values (docs/pillars/UNIVERSAL_SHELF.md
                    # §5): `categories.commitment` ("Full-time"/"Permanent"/...)
                    # and `workplaceType` ("remote"/"hybrid"/"onsite") are copied
                    # verbatim — services/shelf_gate.py owns normalising them
                    # against the closed enums, this source just passes the raw
                    # upstream string through.
                    employment_type=categories.get("commitment") if isinstance(categories, dict) else None,
                    workplace_mode=item.get("workplaceType"),
                ))
        logger.info("Lever: found %s relevant jobs across %s companies", len(jobs), len(self._companies))
        return jobs
