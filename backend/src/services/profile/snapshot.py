"""Human-readable snapshot identity for one profile "intake" — one set of
CV / LinkedIn / GitHub / preference inputs a user submitted on one day.

WHY this exists (owner request, 2026-08-08). ``user_profile_versions``
(migration 0007) stamps every save with a bare autoincrement integer
(47, 48, 49...). That number says nothing about WHEN the user submitted it,
WHOSE it is, or WHETHER two versions came from the exact same underlying
inputs. A human-readable id that encodes date + user + content answers all
three at a glance — in a log line or a support ticket — without a DB lookup.

Format::

    SNAP-YYYYMMDD-<user4>-<content8>

    YYYYMMDD   UTC date of the save (a DB timestamp is UTC, so the id and
               the row it names agree — no calendar-local ambiguity).
    user4      first 4 hex chars of sha256(user_id) — NEVER the raw user_id.
               This id is designed to show up in logs and support tickets;
               user_id is a real account key and must never leak into either.
    content8   first 8 hex chars of sha256(canonical JSON of the RAW INPUTS).

THE CONTENT HASH IS THE LOAD-BEARING DESIGN DECISION. It hashes the raw
material the user handed us — CV text, LinkedIn text, GitHub username, and
the preference values — NEVER the extracted output (skills, job_titles,
career_domain, ESCO map, LLM-inferred skills, ...). Extraction re-runs on
unchanged inputs constantly: a re-parse, an LLM model swap, a bug fix in
cv_parser. None of those are the user submitting anything new — the
submission is byte-identical, so the snapshot id MUST be identical, or every
routine re-extraction would mint a "new" intake for a user who did nothing.
Hashing the output would do exactly that and make the id meaningless as a
"did the user actually change something" signal.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

from src.services.profile.models import CVData, UserPreferences


def _stable_user_segment(user_id: str) -> str:
    """First 4 hex chars of sha256(user_id) — a stable, non-reversible tag.

    The same user always gets the same 4 chars (deterministic), but the raw
    user_id can't be recovered from them — one-way hash, and 4 hex chars is
    a lossy 16-bit space besides.
    """
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    return digest[:4]


def _raw_input_payload(cv_data: CVData, preferences: UserPreferences) -> dict[str, Any]:
    """Pick out ONLY the fields the user actually supplied — never a field an
    extraction pass derived. This distinction is the whole point of the
    content hash; see the module docstring for why.

    Deliberately excluded, and why:
      - Every ``CVData`` field except ``raw_text`` / ``linkedin_raw_text`` is
        extraction OUTPUT (skills, job_titles, career_domain, the ESCO map,
        LLM-inferred skills, ...). Re-running extraction changes these
        without the user submitting anything new.
      - ``preferences.experience_level_inferred`` is inferred from the CV's
        dated job titles (see ``services/profile/seniority.py``), not typed
        by the user — see that field's own docstring in ``models.py``.
        Hashing it would reproduce the same failure mode as above.
      - ``preferences.industries`` (a preference the user set) IS included;
        it is distinct from ``cv_data.industries``, which is CV-extracted
        output and is excluded for the same reason as the other CVData
        fields above.
    """
    return {
        "cv_raw_text": cv_data.raw_text,
        "linkedin_raw_text": cv_data.linkedin_raw_text,
        "github_username": preferences.github_username,
        "target_job_titles": preferences.target_job_titles,
        "additional_skills": preferences.additional_skills,
        "excluded_skills": preferences.excluded_skills,
        "preferred_locations": preferences.preferred_locations,
        "industries": preferences.industries,
        "salary_min": preferences.salary_min,
        "salary_max": preferences.salary_max,
        "work_arrangement": preferences.work_arrangement,
        "experience_level": preferences.experience_level,
        "negative_keywords": preferences.negative_keywords,
        "about_me": preferences.about_me,
        "preferred_workplace": preferences.preferred_workplace,
        "needs_visa": preferences.needs_visa,
    }


def _content_segment(cv_data: CVData, preferences: UserPreferences) -> str:
    """First 8 hex chars of sha256(canonical JSON of the raw inputs).

    ``sort_keys=True`` makes the serialization deterministic regardless of
    dict/field iteration order, so identical inputs always hash identically.
    """
    payload = _raw_input_payload(cv_data, preferences)
    canonical = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return digest[:8]


def make_snapshot_id(
    user_id: str,
    cv_data: CVData,
    preferences: UserPreferences,
    *,
    when: Optional[datetime] = None,
) -> str:
    """Return the human-readable snapshot id for one profile intake.

    Pure function — no DB access, no implicit clock reads. ``when`` defaults
    to ``datetime.now(timezone.utc)`` but accepting it keeps this testable:
    same inputs + same ``when`` always produce the same id, with no
    wall-clock flake. A tz-aware ``when`` is normalised to UTC before the
    date is taken; a naive ``when`` is assumed to already be UTC (callers in
    this codebase only ever pass ``datetime.now(timezone.utc)``).
    """
    moment = when if when is not None else datetime.now(timezone.utc)
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    date_segment = moment.strftime("%Y%m%d")
    user_segment = _stable_user_segment(user_id)
    content_segment = _content_segment(cv_data, preferences)
    return f"SNAP-{date_segment}-{user_segment}-{content_segment}"
