"""The ONE chokepoint that fills + accounts for every UNIVERSAL SHELF on
every job (docs/pillars/UNIVERSAL_SHELF.md §5).

STEP 1 ("the frame") ONLY — see UNIVERSAL_SHELF.md §6 for the three-step
dependency order. This module is built and unit-tested in ISOLATION here;
nothing in the pipeline calls it yet. `src/main.py::_score_dedup_and_filter`
(`main.py:681`) still runs its own visa_flag / experience_level / deadline
logic exactly as it did before this file existed. Wiring `fill_shelves()`
into that function — and absorbing the deadline-extraction loop
(`main.py:708-716`) and the unit-aware salary clamp move
(`models.py:__post_init__`) into it — is STEP 2, a deliberate, separately
reviewed change to LIVE SCORING. Merging this file changes zero live
behaviour: nothing calls it.

`fill_shelves(job)` is synchronous and does NO I/O: no DB, no HTTP, no LLM
call. It only reads and normalises fields already sitting on the `Job`
object (rule #29's ABSENT contract; rule #30's closed-set enumeration for the
employment/workplace/seniority enums, which are bounded sets, unlike UK
gate's foreign-city problem).

Two entry points are named in the design (§5 point 4): this file ships
`fill_shelves(job)` for the ingest path. `apply_enrichment(job_row,
enrichment)` — the sweep write-back that lets `how:"llm"` rows share this
same normalisation without ever overwriting a `source`/`derived` fill — is
NOT built in this step; it depends on JOB SOURCE ENRICHMENT running at scale
(step 3) and has no caller yet either.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

from src.models import UNIVERSAL_SHELF, Job
from src.services.job_enrichment_schema import (
    EmploymentType,
    JobCategory,
    SeniorityLevel,
    WorkplaceType,
)
from src.services.visa_signal import VisaStatus, detect_visa_status

# A description this short (or byte-identical to the title) cannot be a real
# job ad — it is a teaser/stub. Handing it to an LLM (JOB SOURCE ENRICHMENT,
# step 3) would produce a confident-sounding fabrication: `enrich_job` is
# idempotent per `job_id` (second call is a no-op unless `force=True`), so a
# wrong answer extracted here is PERMANENT until someone force-re-runs.
# UNIVERSAL_SHELF.md §2 DESCRIPTION row + §6's fabrication proof.
_STUB_DESCRIPTION_MIN_CHARS = 200


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


def _source_entry(field_name: str, *, raw: Optional[str] = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"how": "source", "field": field_name, "at": _now()}
    if raw is not None:
        entry["raw"] = raw
    return entry


def _derived_entry(by: str) -> dict[str, Any]:
    return {"how": "derived", "by": by, "at": _now()}


def _absent_entry(why: str, *, raw: Optional[str] = None) -> dict[str, Any]:
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
    # STEP 2 HOOK: `main.py:708-716`'s `deadline.extract_deadline(description)`
    # regex pass absorbs into this function, so it runs INSIDE the gate
    # instead of after it (UNIVERSAL_SHELF.md §5 point 2: "the existing
    # deadline loop moves into the gate"). For now this function only
    # ACCOUNTS for whatever `job.deadline` / `job.deadline_source` the
    # caller already set — it does not call `extract_deadline` itself, so
    # merging step 1 changes zero live behaviour.
    if job.deadline:
        source = job.deadline_source or "listing"
        if source == "listing":
            return _source_entry("deadline")
        return _derived_entry("deadline.extract_deadline@v1")
    # Most boards have no deadline concept at all — models.py:44 already
    # documents "None means no deadline listed. NEVER fabricated."
    return _absent_entry("not_stated")


def _fill_salary(job: Job) -> dict[str, Any]:
    # STEP 2 HOOK: unit-aware annualisation + currency tagging
    # (services/salary.normalize_salary + core/fx.to_gbp) and the clamp-move
    # OUT of models.py:__post_init__ both land HERE, before this provenance
    # stamp — see UNIVERSAL_SHELF.md §2 SALARY "Gate rule for salary": clamp
    # AFTER annualising, not before, or an honest hourly rate (NHS £30.27/h)
    # gets nulled by a clamp that assumes GBP-annual. Doing that move now
    # would shift live scores with no test guarding it, so step 1 only
    # accounts for whether models.py already let a number through.
    if job.salary_min is not None or job.salary_max is not None:
        return _source_entry("salary")
    # ~70% of the UK corpus omits pay entirely — this is usually a fact
    # about the job, not a gap in our pipeline.
    return _absent_entry("not_stated")


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
    is meant to pass through here exactly once — WIRING THAT IS STEP 2; see
    the module docstring. Nothing calls this function from the pipeline yet.

    Synchronous, no I/O. Normalises the four closed-enum shelves against the
    schemas in `job_enrichment_schema.py`, runs the free visa-text detector,
    and stamps `job.shelf_provenance[shelf]` for EVERY shelf in
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
