"""Job facts — country, remote, found_on (owner decision 2026-10-04).

Three facts the ASSISTANT reads off the ad (or the user states) and Job360
stores, never infers: the job's ISO 3166-1 alpha-2 country, whether it is
remote, and where the ad was found (closed set ``settings.JOB_FOUND_ON``).
They feed ``stats`` (``by_country`` / ``by_job_source``) and are shown on the
application.

Stored on the user's OWN ``applications`` row (``job_country`` /
``job_remote`` / ``job_found_on``, migration 0049), never on the shared
``jobs`` catalog row — two users who bring the same ad share that row (hard
rule #10), and one user's fix must never change another user's stats. A SLOT,
like the visa reading: a fix overwrites it (a slot is not history, S7).

Unset is ``None`` everywhere (rule #29) — no default is invented. ONE set of
validators here, used by ``POST /jobs/bring``, ``PATCH /applications/{id}/job``
and both MCP tools that reach them.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping, Optional

from src.core import settings
from src.services.applications.spine import SpineError, get_owned_application

if TYPE_CHECKING:  # pragma: no cover — type-only, same reasoning as spine.py
    from src.repositories.database import JobDatabase

# The three settable fields, in the order every reader emits them.
JOB_FACT_FIELDS = ("country", "remote", "found_on")


def normalize_job_country(value: Optional[str]) -> Optional[str]:
    """ISO alpha-2, upper — the SAME rule as the visa country and the
    candidate's work-authorization list (``visa.normalize_country``; Job360
    keeps no country table). ``None``/'' → ``None``. Bad shape → 422."""
    from src.services.applications.visa import normalize_country  # noqa: PLC0415 — visa imports spine

    try:
        code = normalize_country(value)
    except ValueError as exc:
        raise SpineError(422, str(exc)) from None
    return code or None


def normalize_remote(value: Any) -> Optional[bool]:
    """A real boolean or ``None`` (not said). No string/number coercion."""
    if value is None or isinstance(value, bool):
        return value
    raise SpineError(422, "remote must be true, false or null")


def normalize_found_on(value: Optional[str]) -> Optional[str]:
    """``None``/'' → ``None``; a case/space/hyphen-insensitive spelling of a
    ``JOB_FOUND_ON`` member → that member; anything else → 422 naming the set."""
    if value is None or not value.strip():
        return None
    slug = re.sub(r"[\s\-]+", "_", value.strip().lower())
    if slug not in settings.JOB_FOUND_ON:
        raise SpineError(422, f"found_on must be one of JOB_FOUND_ON {settings.JOB_FOUND_ON}")
    return slug


def validate_job_facts(given: Mapping[str, Any]) -> dict[str, Any]:
    """Validate ONLY the fields present in ``given`` (a key with ``None`` =
    clear it). Unknown keys are a programming error, never user input."""
    out: dict[str, Any] = {}
    if "country" in given:
        out["country"] = normalize_job_country(given["country"])
    if "remote" in given:
        out["remote"] = normalize_remote(given["remote"])
    if "found_on" in given:
        out["found_on"] = normalize_found_on(given["found_on"])
    return out


def job_facts_view(app_row: Mapping[str, Any]) -> dict[str, Any]:
    """The three facts as every reader emits them — ``None`` when unset."""
    remote = app_row.get("job_remote")
    return {
        "country": app_row.get("job_country") or None,
        "remote": None if remote is None else bool(remote),
        "found_on": app_row.get("job_found_on") or None,
    }


_COLUMN = {"country": "job_country", "remote": "job_remote", "found_on": "job_found_on"}


async def set_job_facts(
    db: JobDatabase, *, user_id: str, application_id: int, given: Mapping[str, Any]
) -> dict[str, Any]:
    """Overwrite the given facts on the caller's own application (404 for a
    foreign/unknown id, S2). Only the keys in ``given`` change; ``None``
    clears one. Returns the application id plus the CURRENT three facts."""
    clean = validate_job_facts(given)
    app_row = await get_owned_application(db, user_id, application_id)
    if app_row is None:
        raise SpineError(404, "application not found")
    if not clean:
        raise SpineError(422, "give at least one of country, remote, found_on")
    now = datetime.now(timezone.utc).isoformat()
    sets = ", ".join(f"{_COLUMN[k]} = ?" for k in clean)
    await db._db.execute(
        f"UPDATE applications SET {sets}, updated_at = ? WHERE id = ? AND user_id = ?",  # noqa: S608 — column names from _COLUMN, never input
        (*clean.values(), now, application_id, user_id),
    )
    merged = {**dict(app_row), **{_COLUMN[k]: v for k, v in clean.items()}}
    return {"application_id": application_id, **job_facts_view(merged)}
