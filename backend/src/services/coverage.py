"""VALUE-PRESENCE for the matching dimensions — the single source of truth.

Born 2026-08-07 from a real blindness incident: `scripts/shelf_xray.py` used
to test *shape*, not *value* — its salary predicate was
`e.salary NOT IN ('{}', '')`. A `job_enrichment.salary` column holding
`{"min": null, "max": null}` is neither `'{}'` nor `''`, so it passed the
predicate while carrying zero usable information. The script reported salary
coverage at **83%**; parsing the JSON and checking for an actual number put
the real figure at **~5%**. Sixteen points of a scoring dimension (rule #27,
`SALARY_WEIGHT`) were being fed on data that mostly did not exist.

This module is the fix: ONE function that answers "does this row actually
carry a usable value for this matching dimension?", so every consumer
(the X-ray, any future coverage report, any future gate) asks the same
question the same way. Per root CLAUDE.md rule #21 (value-presence >
schema-presence): a column being non-NULL is not evidence a value exists —
only parsing it and finding a real value is.

`job_row` / `enrichment_row` are plain dicts (or `enrichment_row=None`) on
purpose — no ORM, no DB handle — so this module is a pure function over data
and can be unit-tested without a database (`tests/test_coverage_canary.py`)
and reused by scripts that fetch rows differently than shelf_xray does.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from src.services.visa_signal import VisaStatus, detect_visa_status

# Every matching dimension this module knows how to grade. Keep this in sync
# with the `job_has_value` dispatch below — it exists so callers (shelf_xray)
# can iterate dimensions without hardcoding the list a second time.
DIMENSIONS: tuple[str, ...] = (
    "skills",
    "titles",
    "salary",
    "location",
    "workplace",
    "seniority",
    "visa",
    "domain",
)

# job_enrichment columns backing the plain enum-style dimensions. The LLM
# prompt (job_enrichment.py `_build_prompt`) is instructed to always emit the
# literal string "unknown" rather than omit a field, so "present" for these
# columns means "not unknown" — never a bare NULL check, which is what let
# the original bug through.
_ENUM_ENRICHMENT_FIELD = {
    "workplace": "workplace_type",
    "seniority": "seniority",
    "domain": "category",
}

# Keys actually written into job_enrichment.salary — see `SalaryBand` in
# job_enrichment_schema.py (`min: Optional[float]`, `max: Optional[float]`).
# Do NOT guess a different key name here; it was cross-checked against the
# schema and against `normalize_salary()` in services/salary.py, which reads
# the identical two keys.
_SALARY_KEYS = ("min", "max")

# `skill_matcher.score_job` builds its match text as `f"{job.title} {job.description}"`
# and only produces real skill signal once there is enough prose to match
# against (see the length-normalisation note in scoring_dimensions.py — the
# scorer itself treats short/empty descriptions as a known data problem, not
# a real absence-of-skills signal). 200 chars is the same threshold the old
# (buggy) shelf_xray predicate used for "has skill text" — kept because it
# was never the wrong idea, only the salary predicate was.
_SKILL_TEXT_MIN_CHARS = 200


def _parse_json_field(value: Any) -> Any:
    """Best-effort decode of a job_enrichment JSON-TEXT column.

    Postgres returns these columns as raw TEXT (see migration 0008 — `salary
    TEXT NOT NULL DEFAULT '{}'`), so callers always start from a string, an
    already-parsed dict/list (test fixtures, or a caller that pre-decoded),
    or None (no enrichment row / LEFT JOIN miss). Any decode failure
    (empty string, non-JSON garbage like the literal word "unknown", wrong
    type) returns None rather than raising — an unparsable column is exactly
    as informative as a missing one, which is the whole point of this
    module: don't let a strange shape count as a value.
    """
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _is_real_number(value: Any) -> bool:
    """True for an actual numeric salary figure.

    Excludes bool deliberately — `isinstance(True, int)` is True in Python,
    and a stray boolean in a numeric-looking JSON field is a malformed value,
    not a salary.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _has_salary_value(job_row: dict[str, Any], enrichment_row: Optional[dict[str, Any]]) -> bool:
    """THE bug this module exists to fix — see the module docstring.

    A job has a usable salary only if a real number is findable: either the
    catalog's own `salary_min`/`salary_max` columns, or a numeric `min`/`max`
    inside the parsed enrichment JSON. `{"min": null, "max": null}` — which
    is what the LLM emits for the overwhelming majority of UK ads that never
    state pay — must resolve to False. It could not under the old
    `NOT IN ('{}', '')` string predicate; it does here because we parse the
    JSON and check the values, not the container.
    """
    if job_row.get("salary_min") is not None or job_row.get("salary_max") is not None:
        return True
    if not enrichment_row:
        return False
    parsed = _parse_json_field(enrichment_row.get("salary"))
    if not isinstance(parsed, dict):
        return False
    return any(_is_real_number(parsed.get(key)) for key in _SALARY_KEYS)


def _has_skill_value(job_row: dict[str, Any], enrichment_row: Optional[dict[str, Any]]) -> bool:
    """True when there is real text (or an LLM-extracted list) to match skills against.

    Two independent sources count, matching what `JobScorer` actually reads
    (skill_matcher.py): the raw description text (title+description is the
    scorer's match string), or the LLM's `required_skills` list when
    enrichment has run. Either alone is sufficient — a job enriched from a
    thin ATS description can still carry real extracted skills, and a rich
    description carries real signal even before enrichment runs.
    """
    description = job_row.get("description") or ""
    if isinstance(description, str) and len(description) > _SKILL_TEXT_MIN_CHARS:
        return True
    if enrichment_row:
        skills = _parse_json_field(enrichment_row.get("required_skills"))
        if isinstance(skills, list) and any(
            isinstance(s, str) and s.strip() for s in skills
        ):
            return True
    return False


def _has_nonempty_string(value: Any) -> bool:
    """True for a real, non-blank string — titles and locations are read
    verbatim off the `jobs` row (no enrichment fallback), so this is the
    whole rule: is there actually a string here, not just a column that
    isn't NULL."""
    return isinstance(value, str) and value.strip() != ""


def _has_enum_value(raw: Any) -> bool:
    """True for an enrichment enum-style column that carries real information.

    The LLM contract (job_enrichment.py `_build_prompt` / `_SYSTEM_PROMPT`)
    guarantees these columns are never NULL once enrichment has run — absence
    of information is always written as the literal string `"unknown"`, not
    omitted. So `IS NOT NULL` (the shape of the old bug) is worthless here by
    construction; the only real test is "not unknown, not blank". Non-string
    values (None, or a stray non-text type) can never be a real enum value,
    since these are declared `TEXT` columns (migration 0008) — treated as
    absent rather than raising, same reasoning as `_parse_json_field`.
    """
    if not isinstance(raw, str):
        return False
    return raw.strip().lower() not in ("", "unknown")


def job_has_value(
    dimension: str,
    job_row: dict[str, Any],
    enrichment_row: Optional[dict[str, Any]],
) -> bool:
    """Does this job actually carry a usable value for `dimension`?

    This is the ONLY place matching-dimension coverage should be decided.
    `scripts/shelf_xray.py` and any future coverage consumer must call this
    rather than writing their own SQL/Python predicate — see the module
    docstring for why a second predicate is exactly how this class of bug
    reappears.

    Args:
        dimension: one of `DIMENSIONS`.
        job_row: dict with (at least) `title`, `location`, `description`,
            `salary_min`, `salary_max` — the `jobs` table's own columns.
        enrichment_row: dict with (at least) `workplace_type`, `seniority`,
            `visa_sponsorship`, `category`, `salary`, `required_skills` — the
            `job_enrichment` row for this job, or None if no row exists yet
            (enrichment hasn't run / LEFT JOIN miss).

    Returns:
        True iff a real, usable value is present for that dimension.

    Raises:
        ValueError: unknown dimension name — fails loudly rather than
            silently reporting 0% for a typo'd dimension key.
    """
    if dimension == "salary":
        return _has_salary_value(job_row, enrichment_row)
    if dimension == "skills":
        return _has_skill_value(job_row, enrichment_row)
    if dimension == "titles":
        return _has_nonempty_string(job_row.get("title"))
    if dimension == "location":
        return _has_nonempty_string(job_row.get("location"))
    if dimension == "visa":
        # Delegates to the real detector (visa_signal.py) rather than
        # re-reading `job_enrichment.visa_sponsorship` alone. That column is
        # only 8% populated on prod; the text detector reads the description
        # every job already has and resolves ~36% more of the catalog. A
        # coverage script that doesn't call the detector reports a number
        # the product doesn't actually deliver.
        status = detect_visa_status(
            job_row.get("description"),
            job_row.get("title"),
            enrichment_value=(enrichment_row or {}).get("visa_sponsorship"),
        )
        return status != VisaStatus.UNKNOWN
    if dimension in ("workplace", "seniority"):
        # Same principle as the visa branch above, and the reason it is worth
        # repeating: SCORING sees these dimensions through
        # `job_signals.signal_backed_lookup`, which fills an 'unknown'
        # enrichment value from the job's TITLE and LOCATION. Those fields
        # exist for the 1,311 catalog rows that carry almost no description.
        #
        # If this function read only the enrichment column it would report
        # coverage the product does NOT have a problem with — understating
        # seniority by 33 points (27% stored vs 60% effective) and workplace by
        # 7. An instrument must count with the same eyes its consumer reads.
        field = _ENUM_ENRICHMENT_FIELD[dimension]
        raw = (enrichment_row or {}).get(field)
        if _has_enum_value(raw):
            return True
        from src.services.job_signals import (  # noqa: PLC0415 — avoids a cycle
            detect_seniority,
            detect_workplace,
        )

        title = job_row.get("title") or ""
        desc = job_row.get("description") or ""
        # `Any`: the two detectors return different signal types
        # (`WorkplaceSignal` / `SenioritySignal`) and only their duck-typed
        # `.value` is read below.
        got: Any
        if dimension == "workplace":
            got = detect_workplace(
                desc, title, location=job_row.get("location") or ""
            )
        else:
            got = detect_seniority(title, desc)
        val = getattr(getattr(got, "value", got), "value", getattr(got, "value", got))
        return str(val).strip().lower() not in ("", "unknown")
    if dimension in _ENUM_ENRICHMENT_FIELD:
        field = _ENUM_ENRICHMENT_FIELD[dimension]
        raw = (enrichment_row or {}).get(field)
        return _has_enum_value(raw)
    raise ValueError(f"unknown matching dimension: {dimension!r}")
