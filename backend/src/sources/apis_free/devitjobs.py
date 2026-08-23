import logging
from datetime import datetime, timezone

from src.models import Job
from src.sources.base import BaseJobSource, _is_uk_or_remote
from src.utils.dates import normalize_posted_at

logger = logging.getLogger("job360.sources.devitjobs")

class DevITJobsSource(BaseJobSource):
    name = "devitjobs"
    category = "free_json"
    DOMAINS = {"tech"}

    async def fetch_jobs(self) -> list[Job]:
        data = await self._get_json("https://devitjobs.uk/api/jobsLight")
        if not data or not isinstance(data, list):
            return []

        jobs = []
        for item in data:
            title = item.get("name", "")
            company = item.get("company", "")
            location = item.get("actualCity", "")
            # The API's `jobUrl` is a SLUG, not a URL - "FBI-TMT-Metadata-Lead".
            # Stored raw, it produced an Apply button that goes nowhere, on 2,805
            # jobs: 43% of the entire catalog, since devitjobs is our largest
            # source. Nothing detected it for months, because a bad link only
            # fails when a user clicks it and we never see that click.
            # Found 2026-08-03 by the data-invariants detector on its first run.
            apply_url = (item.get("jobUrl") or "").strip()
            if apply_url and not apply_url.startswith(("http://", "https://")):
                # VERIFIED against the live site, not guessed: /jobs/<slug>
                # returns the real page (9.6 KB, company name present) while
                # /job/<slug> and /<slug> both return the 4.7 KB empty SPA shell.
                apply_url = f"https://devitjobs.uk/jobs/{apply_url.lstrip('/')}"
            now_iso = datetime.now(timezone.utc).isoformat()
            # `publishedAt` does not exist on the live API - checked all 2,375
            # live items. The real key is `activeFrom` (100% fill, ISO format
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

            # `contractRateFrom`/`contractRateTo` (17% fill) hold DAY rates
            # for contract roles, which otherwise get NO salary at all since
            # their pay never lands in annualSalaryFrom/To. Only look at
            # them when the annual fields are absent, and only trust
            # `contractRateType` when it already says the figure is annual
            # ("YEAR"/"ANNUM") - pass that straight through, no math needed.
            # A day rate needs a working-days-per-year assumption (contractor
            # convention is usually ~220, but that is a guess, not a fact
            # from the API) to become an annual figure; we deliberately do
            # NOT invent that number here. A fabricated annual salary would
            # corrupt salary-based scoring/filtering worse than having none,
            # so day/hour/week/month rates are left as salary=None - the job
            # still surfaces, just without a salary figure.
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

            # `hasVisaSponsorship` arrives as the STRING "Yes"/"No", never a
            # boolean - and `bool("No")` is True in Python. That one-word bug
            # marked EVERY devitjobs row as sponsoring and then wrote
            # "Visa sponsorship available" into the description we compose
            # below. Measured in prod 2026-08-07: 1,977 active rows claimed
            # sponsorship while the API says exactly 2 of 2,377 actually
            # offer it. Users who need sponsorship were being shown - and
            # ranked UP on - jobs that explicitly refuse it, and the visa text
            # detector then read our own fabricated sentence back as evidence.
            # Parse the value; never trust a non-empty string to mean "true".
            visa_flag = str(item.get("hasVisaSponsorship") or "").strip().lower() in (
                "yes", "true", "1",
            )
            exp_level = item.get("expLevel", "")

            # Job-understanding fix (2026-08-05): jobsLight has NO prose
            # description - 3,041 prod jobs (42% of the whole catalog, our
            # largest source) sat with EMPTY description text, unmatchable by
            # the skill scorer and unreadable by enrichment/embeddings. But
            # the API publishes the tech stack + structured attributes on
            # every row (verified live: `technologies`, `filterTags`,
            # `techCategory`, `jobType`, `remoteType`, `companySize`).
            # Compose them into an honest structured description - API facts
            # verbatim, nothing fabricated.
            desc_bits: list[str] = []
            techs = [str(t) for t in (item.get("technologies") or []) if t]
            if techs:
                desc_bits.append("Technologies: " + ", ".join(techs))
            tags = [str(t).replace("-", " ") for t in (item.get("filterTags") or []) if t]
            if tags:
                desc_bits.append("Tags: " + ", ".join(tags))
            for label, key in (
                ("Tech category", "techCategory"),
                ("Category", "metaCategory"),
                ("Job type", "jobType"),
                # `workplace` (office/hybrid/remote), NOT `remoteType`:
                # measured live, remoteType is null on 2,324 of 2,377 rows
                # while workplace is populated on all of them. Reading the
                # empty one threw away a structured workplace signal for the
                # single largest source in the catalog.
                ("Workplace", "workplace"),
                ("Experience level", "expLevel"),
                ("Company size", "companySize"),
            ):
                val = item.get(key)
                if val:
                    desc_bits.append(f"{label}: {val}")
            if visa_flag:
                desc_bits.append("Visa sponsorship available")
            description = ". ".join(desc_bits)

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
            ))

        jobs = [j for j in jobs if _is_uk_or_remote(j.location)]
        logger.info("DevITjobs: found %s relevant jobs", len(jobs))
        return jobs
