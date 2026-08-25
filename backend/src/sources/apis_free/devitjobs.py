import logging
import re
from datetime import datetime, timezone

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.devitjobs")

_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Detail-fetch budget per RUN (2026-08-16), same reasoning as
# smartrecruiters/workday (see those files): an uncapped detail pass over
# every UK-relevant posting -- devitjobs is the biggest source, 2,507 live --
# risks the same fate as the 2026-08-06 smartrecruiters incident (240s ATS
# ceiling blown, source recorded as errored with ZERO jobs). Jobs past the
# budget keep the structured, composed description (still non-empty, never
# regresses to blank) instead of the real prose.
_MAX_DETAIL_FETCHES = 60

class DevITJobsSource(BaseJobSource):
    name = "devitjobs"
    category = "free_json"
    DOMAINS = {"tech"}

    async def fetch_jobs(self) -> list[Job]:
        data = await self._get_json("https://devitjobs.uk/api/jobsLight")
        if not data or not isinstance(data, list):
            return []

        jobs = []
        detail_budget = _MAX_DETAIL_FETCHES
        for item in data:
            title = item.get("name", "")
            company = item.get("company", "")
            location = item.get("actualCity", "")
            # The API jobUrl field is a SLUG, not a URL, e.g.
            # "FBI-TMT-Metadata-Lead". Stored raw, it produced an Apply
            # button that goes nowhere, on 2,805 jobs: 43% of the entire
            # catalog, since devitjobs is our largest source. Nothing
            # detected it for months, because a bad link only fails when a
            # user clicks it and we never see that click. Found 2026-08-03
            # by the data-invariants detector on its first run.
            apply_url = (item.get("jobUrl") or "").strip()
            if apply_url and not apply_url.startswith(("http://", "https://")):
                # VERIFIED against the live site, not guessed: /jobs/<slug>
                # returns the real page (9.6 KB, company name present) while
                # /job/<slug> and /<slug> both return the 4.7 KB empty SPA shell.
                apply_url = "https://devitjobs.uk/jobs/" + apply_url.lstrip("/")
            now_iso = datetime.now(timezone.utc).isoformat()
            # publishedAt does not exist on the live API, checked all 2,375
            # live items. The real key is activeFrom (100% fill, ISO format
            # "2026-08-05T14:27:12.425+00:00"). This alone gives 2,154 jobs
            # (our biggest source) a real date and date_confidence="high"
            # instead of the None/low every row silently got before.
            raw_published = item.get("activeFrom")
            posted_at, confidence = normalize_posted_at(raw_published)

            salary_min = item.get("annualSalaryFrom")
            salary_max = item.get("annualSalaryTo")
            if salary_min is not None:
                try:
                    salary_min = float(salary_min)
                except (ValueError, TypeError):
                    salary_min = None
            if salary_max is not None:
                try:
                    salary_max = float(salary_max)
                except (ValueError, TypeError):
                    salary_max = None

            if salary_min is None and salary_max is None:
                contract_rate_type = str(item.get("contractRateType") or "").strip().upper()
                if contract_rate_type in ("YEAR", "YEARLY", "ANNUM", "ANNUAL", "PER_ANNUM"):
                    rate_from = item.get("contractRateFrom")
                    rate_to = item.get("contractRateTo")
                    try:
                        salary_min = float(rate_from) if rate_from is not None else None
                    except (ValueError, TypeError):
                        salary_min = None
                    try:
                        salary_max = float(rate_to) if rate_to is not None else None
                    except (ValueError, TypeError):
                        salary_max = None

            # hasVisaSponsorship arrives as the STRING Yes/No, never a
            # boolean -- and bool of the string No is True in Python. That
            # one-word bug marked EVERY devitjobs row as sponsoring. Parse
            # the value; never trust a non-empty string to mean true.
            has_visa_sponsorship_raw = item.get("hasVisaSponsorship")
            visa_flag = str(has_visa_sponsorship_raw or "").strip().lower() in (
                "yes", "true", "1",
            )
            exp_level = item.get("expLevel", "")

            desc_bits = []
            techs = [str(t) for t in (item.get("technologies") or []) if t]
            if techs:
                desc_bits.append("Technologies: " + ", ".join(techs))
            # `technologies` (99% fill) + `filterTags` (99.7% fill) were only
            # ever folded into the composed description string above -- the
            # job's own vocabulary was thrown away as prose instead of
            # reaching source_tags (the skills shelf) as structured data.
            # Deduped, order-preserving union; raw strings only, no guessing.
            source_tags: list[str] = []
            _seen_tags: set[str] = set()
            for t in techs + [str(x) for x in (item.get("filterTags") or []) if x]:
                key = t.lower()
                if t and key not in _seen_tags:
                    source_tags.append(t)
                    _seen_tags.add(key)
            tags = [str(t).replace("-", " ") for t in (item.get("filterTags") or []) if t]
            if tags:
                desc_bits.append("Tags: " + ", ".join(tags))
            for label, key in (
                ("Tech category", "techCategory"),
                ("Category", "metaCategory"),
                ("Job type", "jobType"),
                ("Workplace", "workplace"),
                ("Experience level", "expLevel"),
                ("Company size", "companySize"),
            ):
                val = item.get(key)
                if val:
                    desc_bits.append(label + ": " + str(val))
            if visa_flag:
                desc_bits.append("Visa sponsorship available")
            fallback_description = ". ".join(desc_bits)

            description = fallback_description
            # THE ID IS CHECKED BEFORE THE BUDGET IS SPENT. `_fetch_job_detail`
            # returns {} immediately when the id is empty, so no HTTP request
            # happens — but the slot was already gone, and a feed with missing
            # ids could burn the whole detail budget without making a single
            # call. `nofluffjobs.py` gets the order right in this same PR
            # (`if posting_id and detail_budget > 0`); this now matches it.
            # (CodeRabbit, PR #388.)
            job_id = str(item.get("_id", ""))
            if job_id and detail_budget > 0 and _is_uk_or_remote(location):
                detail_budget -= 1
                detail = await self._fetch_job_detail(job_id)
                detail_description = self._extract_detail_description(detail)
                if detail_description:
                    description = detail_description

            jobs.append(Job(
                title=title,
                company=company,
                location=location,
                description=description,
                apply_url=apply_url,
                source=self.name,
                date_found=now_iso,
                posted_at=posted_at,
                date_confidence=confidence,
                date_posted_raw=raw_published,
                salary_min=salary_min,
                salary_max=salary_max,
                visa_flag=visa_flag,
                experience_level=exp_level,
                # Raw upstream value only -- no normalising here (the gate
                # owns that). services/shelf_gate.py::_fill_visa_status
                # names this field as its planned step-2 input and does not
                # consume it yet, so this line changes zero live scoring
                # behaviour today.
                visa_status=has_visa_sponsorship_raw,
                employment_type=item.get("jobType"),
                workplace_mode=item.get("workplace"),
                # `expLevel` (100% fill) already fed the legacy free-text
                # `experience_level` above; also feeds the closed-enum
                # `seniority` shelf, same raw string, no interpretation.
                seniority=exp_level or None,
                source_tags=source_tags,
            ))

        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("DevITjobs: found %s relevant jobs", len(jobs))
        return jobs

    async def _fetch_job_detail(self, job_id):
        """Fetch one posting raw detail JSON from the public detail endpoint.

        Returns {} on any failure (including no id) -- callers treat a
        missing detail as keep-the-fallback-description, never an error.
        """
        if not job_id:
            return {}
        detail = await self._get_json("https://devitjobs.uk/api/job/" + job_id)
        return detail if isinstance(detail, dict) else {}

    @staticmethod
    def _extract_detail_description(detail):
        """Concatenate the detail endpoint three prose fields, tag-stripped.

        requirementsNiceTextArea is near-always empty (measured live: 1 of
        15) so it is skipped; the other three carry the actual ad text.
        """
        parts = []
        for key in ("description", "responsibilitiesTextArea", "requirementsMustTextArea"):
            text = detail.get(key)
            if text:
                parts.append(_HTML_TAG_RE.sub(" ", str(text)))
        return " ".join(parts)[:5000].strip()
