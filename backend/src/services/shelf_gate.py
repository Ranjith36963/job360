"""The ONE chokepoint that fills + accounts for every UNIVERSAL SHELF on
every job (docs/pillars/UNIVERSAL_SHELF.md §5).

WIRED IN (step 2). `src/main.py::_score_dedup_and_filter` calls
`fill_shelves(job)` for every raw job BEFORE it is scored, deduped or
stored, so the scorer reads normalised shelves and no source can bypass the
gate (sources never call it — the orchestrator does, downstream of all of
them). Two things that used to live outside this file now live here:

  * the deadline-extraction pass that used to sit in `main.py` after
    scoring (`extract_deadline(description)`) — now `_fill_deadline`;
  * the salary clamp that used to sit in `models.Job.__post_init__` —
    now `_fill_salary`, and unit-aware: the band is annualised and
    converted to GBP FIRST and only then judged for plausibility, so an
    honest hourly rate survives instead of being nulled by a threshold
    that assumed GBP-annual.

`fill_shelves(job)` is synchronous and does NO I/O: no DB, no HTTP, no LLM
call. It only reads and normalises fields already sitting on the `Job`
object (rule #29's ABSENT contract; rule #30's closed-set enumeration for the
employment/workplace/seniority/period enums, which are bounded sets, unlike
the UK gate's unbounded foreign-city problem).

Two entry points are named in the design (§5 point 4): this file ships
`fill_shelves(job)` for the ingest path. `apply_enrichment(job_row,
enrichment)` — the sweep write-back that lets `how:"llm"` rows share this
same normalisation without ever overwriting a `source`/`derived` fill — is
NOT built yet; it depends on JOB SOURCE ENRICHMENT running at scale
(step 3) and has no caller yet.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from src.core.fx import is_known_currency
from src.models import UNIVERSAL_SHELF, Job
from src.services.deadline import extract_deadline
from src.services.job_enrichment_schema import (
    EmploymentType,
    JobCategory,
    SeniorityLevel,
    WorkplaceType,
)
from src.services.salary import normalize_salary
from src.services.visa_signal import VisaStatus, detect_visa_status

# A description this short (or byte-identical to the title) cannot be a real
# job ad — it is a teaser/stub. Handing it to an LLM (JOB SOURCE ENRICHMENT,
# step 3) would produce a confident-sounding fabrication: `enrich_job` is
# idempotent per `job_id` (second call is a no-op unless `force=True`), so a
# wrong answer extracted here is PERMANENT until someone force-re-runs.
# UNIVERSAL_SHELF.md §2 DESCRIPTION row + §6's fabrication proof.
_STUB_DESCRIPTION_MIN_CHARS = 200

# Salary plausibility band, applied to the ANNUALISED GBP figure only (see
# _fill_salary). Same two thresholds the old unit-blind clamp in
# models.Job.__post_init__ used, so a GBP-annual job behaves EXACTLY as it
# did before this moved; what changes is that an hourly/monthly/foreign-
# currency job is now converted first and therefore judged as a comparable
# number instead of being nulled (or waved through) on its raw face value.
_SALARY_MIN_PLAUSIBLE_GBP_ANNUAL = 10_000
_SALARY_MAX_PLAUSIBLE_GBP_ANNUAL = 500_000

# Upstream pay-period tokens -> the closed set services/salary.py can
# annualise ("hourly"/"daily"/"weekly"/"monthly"/"annual"). Keys are the raw
# token with every non-letter stripped and lower-cased, so 'per day',
# 'PER_DAY' and 'Per-Day' all arrive as 'perday'. Enumerating this set is
# legal under rule #30: pay periods are a CLOSED, finite set (unlike foreign
# cities). Every token below was seen in a real live payload by the four
# source-recovery batches (reed 'per day', careerjet 'Y'/'H'/'M', indeed
# 'yearly', linkedin JSON-LD 'YEAR', nofluffjobs 'Month', jobicy/himalayas
# 'salaryPeriod'). An UNKNOWN token normalises to None: the shelf keeps the
# source's own numbers untouched and provenance records the raw token, which
# is honest — never a guessed unit (rule #29).
_PERIOD_ALIASES: dict[str, str] = {
    "h": "hourly", "hr": "hourly", "hour": "hourly", "hourly": "hourly",
    "perhour": "hourly", "perhr": "hourly", "hourlyrate": "hourly",
    "d": "daily", "day": "daily", "daily": "daily", "perday": "daily",
    "diem": "daily", "perdiem": "daily", "dayrate": "daily",
    "w": "weekly", "week": "weekly", "weekly": "weekly", "perweek": "weekly",
    "m": "monthly", "month": "monthly", "monthly": "monthly",
    "permonth": "monthly", "monthlyrate": "monthly",
    "y": "annual", "yr": "annual", "year": "annual", "yearly": "annual",
    "peryear": "annual", "annual": "annual", "annually": "annual",
    "annum": "annual", "perannum": "annual", "pa": "annual",
}


def _normalize_period(raw: Any) -> Optional[str]:
    """Raw upstream pay-period token -> closed-set period, or None.

    None means "this source did not tell us the unit" — NOT "annual". The
    difference matters: an unknown unit leaves the amounts exactly as the
    source sent them (every legacy consumer already reads them as GBP-annual),
    whereas a known unit triggers a real conversion.
    """
    if raw is None:
        return None
    key = re.sub(r"[^a-z]", "", str(raw).lower())
    if not key:
        return None
    return _PERIOD_ALIASES.get(key)


def is_stub_description(description: Optional[str], title: Optional[str]) -> bool:
    """True if `description` is too thin to safely hand to an LLM.

    Two independent, either-one-disqualifies signals:
      - shorter than 200 chars once whitespace is trimmed (the p10-length
        floor several sources sit at — devitjobs, workday, smartrecruiters)
      - byte-identical to the title once both are trimmed (a known live bug:
        successfactors ships description == title for ~1,800 jobs/run)

    This is the function step 3's LLM sweep must call BEFORE enriching a job
    — see the module docstring's note on `apply_enrichment` not existing yet.
    Exported so that caller can reuse it without re-deriving the rule.
    """
    text = (description or "").strip()
    if len(text) < _STUB_DESCRIPTION_MIN_CHARS:
        return True
    if title and text == title.strip():
        return True
    return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_entry(field_name: str, *, raw: Any = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"how": "source", "field": field_name, "at": _now()}
    if raw is not None:
        entry["raw"] = raw
    return entry


def _derived_entry(by: str) -> dict[str, Any]:
    return {"how": "derived", "by": by, "at": _now()}


def _absent_entry(why: str, *, raw: Any = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"how": "absent", "why": why}
    if raw is not None:
        entry["raw"] = raw
    return entry


def _normalize_closed_enum(raw: Any, enum_cls: Any) -> tuple[Optional[str], Optional[str]]:
    """Normalise `raw` against a CLOSED enum set (rule #30 — employment type,
    workplace mode and seniority are bounded, closed sets, so enumerating
    THEM is legal, unlike the unbounded foreign-city problem the UK gate
    solves with data instead).

    Returns `(normalized_value_or_None, raw_str_or_None)`. The enum's own
    `UNKNOWN` member (a JOB SOURCE ENRICHMENT / LLM-contract sentinel,
    `job_enrichment_schema.py`) is never a valid NORMALIZED target here — an
    unmatched raw value means the catalog shelf is ABSENT (NULL), never the
    literal string "unknown". Only `visa_status` stores "unknown" as a real
    value — see `_fill_visa_status`, and UNIVERSAL_SHELF.md §1 row 8 / §4.
    """
    if raw is None:
        return None, None
    raw_str = str(raw).strip()
    if not raw_str:
        return None, None
    key = raw_str.lower().replace(" ", "_").replace("-", "_")
    for member in enum_cls:
        if member.value == "unknown":
            continue
        if member.value == key:
            return member.value, raw_str
    return None, raw_str


def _fill_closed_enum_shelf(job: Job, attr: str, enum_cls: Any) -> dict[str, Any]:
    """Shared filler for employment_type / seniority / workplace_mode /
    category — all four are closed-set enum shelves normalised the same way
    (UNIVERSAL_SHELF.md §5 point 2). `attr` is both the Job attribute name
    AND the shelf/provenance key for these four (unlike `skills`, which is
    the shelf name for the `source_tags` attribute).
    """
    raw = getattr(job, attr, None)
    normalized, raw_str = _normalize_closed_enum(raw, enum_cls)
    setattr(job, attr, normalized)
    if normalized is not None:
        return _source_entry(attr, raw=raw_str if raw_str != normalized else None)
    if raw_str is not None:
        # A value came in but the gate's normaliser doesn't recognise the
        # token — counted (never silently dropped), and the raw token
        # survives in provenance so a future alias fix is traceable back to
        # the exact string that failed.
        return _absent_entry("not_mapped", raw=raw_str)
    return _absent_entry("not_mapped")


def _fill_title(job: Job) -> dict[str, Any]:
    if job.title and job.title.strip():
        return _source_entry("title")
    return _absent_entry("not_mapped")


def _fill_company(job: Job) -> dict[str, Any]:
    # "Unknown" is Job._clean_company's sentinel for a missing/broken
    # upstream company name — that IS the absent state for this shelf.
    if job.company and job.company != "Unknown":
        return _source_entry("company")
    return _absent_entry("not_mapped")


def _fill_location(job: Job) -> dict[str, Any]:
    if job.location and job.location.strip():
        return _source_entry("location")
    return _absent_entry("not_mapped")


def _fill_description(job: Job) -> dict[str, Any]:
    if is_stub_description(job.description, job.title):
        return _absent_entry("stub")
    return _source_entry("description")


def _fill_posted_at(job: Job) -> dict[str, Any]:
    if job.posted_at:
        return _source_entry("posted_at")
    return _absent_entry("not_mapped")


def _fill_deadline(job: Job) -> dict[str, Any]:
    """Structured source deadline first, then the free text derivation.

    STEP 2: the `extract_deadline(description)` pass that used to run in
    `main.py` AFTER scoring now runs HERE, inside the gate, before scoring —
    same function, same regex, same `deadline_source='description'` stamp,
    just moved to the one door every job passes through
    (UNIVERSAL_SHELF.md §5 point 2). Two things this buys: a source that
    forgets to map a deadline still gets the text pass, and the reason a
    deadline is missing is now recorded instead of inferred.

    Rule #29: `extract_deadline` returns None unless a deadline KEYWORD is
    tied to an unambiguous future date, so an ad with no deadline keeps
    NULL. The gate NEVER invents one — no "30 days from posting" default.
    """
    if job.deadline is None and job.description:
        result = extract_deadline(job.description)
        if result is not None:
            job.deadline, job.deadline_source = result
    if job.deadline:
        source = job.deadline_source or "listing"
        if source == "listing":
            return _source_entry("deadline")
        return _derived_entry("deadline.extract_deadline@v1")
    # Most boards have no deadline concept at all — models.py already
    # documents "None means no deadline listed. NEVER fabricated."
    return _absent_entry("not_stated")


def _annualise_one(
    amount: Optional[float], currency: Optional[str], period: Optional[str]
) -> Optional[int]:
    """One bound -> annual GBP, via services/salary.normalize_salary.

    Deliberately ONE BOUND AT A TIME. `normalize_salary` mirrors a missing
    bound onto the survivor (a band of one point), which is right for the
    scorer's overlap maths but would be a FABRICATION here: a job that
    advertises "from £45,000" must not end up with a stored maximum of
    £45,000 it never stated (rule #29). Passing one bound and reading back
    only that bound keeps the missing side missing.
    """
    if amount is None:
        return None
    band = normalize_salary(
        {
            "min": amount,
            "max": None,
            "currency": currency or "GBP",
            "frequency": period or "annual",
        }
    )
    return None if band is None else band[0]


def _fill_salary(job: Job) -> dict[str, Any]:
    """Unit-aware salary: annualise + currency-convert FIRST, clamp SECOND.

    STEP 2 (UNIVERSAL_SHELF.md §2 "Gate rule for salary"). The old clamp
    lived in `models.Job.__post_init__` and was unit-blind — it assumed every
    number was GBP-annual, so it nulled an honest £30.27/hour NHS rate and
    stored a €60,000 salary as if it were sterling. Here the band is turned
    into annual GBP using the source's OWN unit sidecars (`salary_period`,
    `salary_currency`, mapped raw by the sources in step 2's recovery work)
    and only then judged for plausibility.

    What lands on the Job afterwards:
      * `salary_min` / `salary_max` — the comparable annual-GBP figure when
        the gate could convert (every existing consumer, from the CSV export
        to the email report, already reads these as GBP-annual), otherwise
        the source's own untouched numbers;
      * `salary_min_gbp_annual` / `salary_max_gbp_annual` — the derived pair,
        set ONLY when the conversion was real;
      * `salary_period` / `salary_currency` — rewritten to "annual"/"GBP"
        when a conversion happened, so the stored unit describes the stored
        number; the pre-conversion values survive in provenance.

    Rule #29 is the hard edge: no amount at all stays NULL with
    `absent:not_stated`, and NOTHING here ever invents a number, a unit or a
    currency. An unknown period is not silently called "annual" — the
    amounts are simply left as the source sent them.
    """
    raw_min, raw_max = job.salary_min, job.salary_max
    raw_period, raw_currency = job.salary_period, job.salary_currency
    period = _normalize_period(raw_period)
    currency = (raw_currency or "").strip().upper() or None
    raw_record: dict[str, Any] = {
        "min": raw_min,
        "max": raw_max,
        "period": raw_period,
        "currency": raw_currency,
        "is_estimated": job.salary_is_estimated,
    }
    # Keep the NORMALISED unit even when no amount came with it — reed sends
    # `salaryType: 'per day'` on contract roles whose amounts are both null,
    # and "we know it is a day rate, we just have no number" is a real fact.
    if period is not None:
        job.salary_period = period

    if raw_min is None and raw_max is None:
        job.salary_min_gbp_annual = None
        job.salary_max_gbp_annual = None
        # ~70% of the UK corpus omits pay entirely — usually a fact about the
        # job, not a gap in our pipeline.
        return _absent_entry("not_stated")

    if currency is not None and not is_known_currency(currency):
        # core/fx has no rate for this code, and `to_gbp` would pass it
        # through at 1:1 — a BRL figure wearing a £ sign is a WRONG number,
        # not a rough one. Leave the source's own numbers + code alone and
        # leave the derived GBP pair NULL rather than fabricate a conversion.
        job.salary_min_gbp_annual = None
        job.salary_max_gbp_annual = None
        entry = _source_entry("salary_min/salary_max", raw=raw_record)
        entry["gbp_annual"] = "unpriceable_currency"
        return entry

    min_annual = _annualise_one(raw_min, currency, period)
    max_annual = _annualise_one(raw_max, currency, period)

    # THE CLAMP, moved here from models.py and now applied to a comparable
    # number. Same thresholds and same one-sided directions as before, so a
    # GBP-annual job's outcome is byte-identical to the old behaviour.
    if min_annual is not None and min_annual < _SALARY_MIN_PLAUSIBLE_GBP_ANNUAL:
        min_annual = None
    if max_annual is not None and max_annual > _SALARY_MAX_PLAUSIBLE_GBP_ANNUAL:
        max_annual = None

    job.salary_min = float(min_annual) if min_annual is not None else None
    job.salary_max = float(max_annual) if max_annual is not None else None
    job.salary_min_gbp_annual = job.salary_min
    job.salary_max_gbp_annual = job.salary_max
    if min_annual is not None or max_annual is not None:
        # The stored numbers ARE annual GBP now, so the stored unit must say
        # so — but ONLY where the source actually stated that unit. Stamping
        # "GBP" on a job whose ad never named a currency would be inventing a
        # fact (rule #29); an unstated currency stays NULL and the number
        # stays exactly what the source sent, which is what every legacy
        # consumer has always assumed.
        if period is not None:
            job.salary_period = "annual"
        if currency is not None:
            job.salary_currency = "GBP"

    if job.salary_min is None and job.salary_max is None:
        # Numbers arrived but nothing survived the plausibility band. That is
        # neither "the ad said nothing" nor "nobody looked": it is a value we
        # refused. Recorded as its own reason, with the original figures kept
        # so the refusal is auditable instead of invisible.
        return _absent_entry("implausible", raw=raw_record)
    return _source_entry("salary_min/salary_max", raw=raw_record)


def _fill_visa_status(job: Job) -> dict[str, Any]:
    # No structured source field feeds this yet (that is step 2 — e.g.
    # devitjobs' real `hasVisaSponsorship` bool) and no LLM verdict exists
    # yet (step 3), so this is always the free-derivation regex detector.
    status = detect_visa_status(job.description, job.title)
    job.visa_status = status.value
    if status is VisaStatus.UNKNOWN:
        # Rule #31: unknown IS the third state, stored as the literal value
        # "unknown" above — but the WHY here is still "the ad never said",
        # not "nobody looked" (the detector DID look).
        return _absent_entry("not_stated")
    return _derived_entry("visa_signal.detect_visa_status@v1")


def _fill_skills(job: Job) -> dict[str, Any]:
    if job.source_tags:
        return _source_entry("source_tags")
    return _absent_entry("not_mapped")


# Shelf name -> filler. Keys are exactly UNIVERSAL_SHELF (checked by
# test_gate_accounts_for_every_shelf); a KeyError here on a real run means
# someone added a shelf to the tuple without teaching this dict, which is
# exactly the drift this dict exists to make loud instead of silent.
_SHELF_FILLERS: dict[str, Callable[[Job], dict[str, Any]]] = {
    "title": _fill_title,
    "company": _fill_company,
    "location": _fill_location,
    "description": _fill_description,
    "posted_at": _fill_posted_at,
    "deadline": _fill_deadline,
    "salary": _fill_salary,
    "visa_status": _fill_visa_status,
    "employment_type": lambda job: _fill_closed_enum_shelf(job, "employment_type", EmploymentType),
    "seniority": lambda job: _fill_closed_enum_shelf(job, "seniority", SeniorityLevel),
    "workplace_mode": lambda job: _fill_closed_enum_shelf(job, "workplace_mode", WorkplaceType),
    "skills": _fill_skills,
    "category": lambda job: _fill_closed_enum_shelf(job, "category", JobCategory),
}


def fill_shelves(job: Job) -> Job:
    """The chokepoint (UNIVERSAL_SHELF.md §5). Every job that reaches storage
    passes through here exactly once, called by
    `main.py::_score_dedup_and_filter` BEFORE scoring — see the module
    docstring.

    Synchronous, no I/O. Normalises the four closed-enum shelves against the
    schemas in `job_enrichment_schema.py`, annualises + converts the salary
    band to GBP and then clamps it, derives a deadline from the ad text, runs
    the free visa-text detector, and stamps `job.shelf_provenance[shelf]` for EVERY shelf in
    `UNIVERSAL_SHELF` — filled or absent. The invariant is not "every shelf
    filled" (impossible — most jobs genuinely lack a deadline or a salary);
    it is "every shelf ACCOUNTED FOR": `set(job.shelf_provenance) ==
    set(UNIVERSAL_SHELF)` after this call, always, or it is a bug in this
    function (guarded by
    tests/test_universal_shelf.py::test_gate_accounts_for_every_shelf).

    Mutates and returns `job` (matches the design doc's signature); callers
    that need the pre-gate object should copy first.
    """
    provenance: dict[str, Any] = dict(job.shelf_provenance) if job.shelf_provenance else {}
    for shelf in UNIVERSAL_SHELF:
        filler = _SHELF_FILLERS[shelf]
        provenance[shelf] = filler(job)
    job.shelf_provenance = provenance
    return job
