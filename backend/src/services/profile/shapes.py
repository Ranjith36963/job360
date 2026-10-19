"""SHAPES - one table of what a valid value looks like, per editable structured path.

Owner decision 2026-10-10 ("never lose an answer"): an assistant guesses record
shapes when nothing shows them. This table is the ONE place that answers, three ways:

* a refused ``update_profile`` quotes ``Example: <json>`` for each bad path
  (:func:`example_json`) - never the caller's own value;
* ``get_profile`` returns ``shapes`` (:func:`compact_shapes`) so the shape is on screen
  BEFORE the first write;
* the contract test validates every example through ``validate_edit`` so this table
  can never drift from the validators.

The allowed key / value lists are read off the validators' own schemas, not retyped.
"""
from __future__ import annotations

import json
from typing import Any

from src.services.profile import assistant_settings as _settings
from src.services.profile import edits as _edits
from src.services.profile import user_info as _ui
from src.services.profile.models import (
    VALID_CHECK_EVERY_VALUES,
    VALID_DAILY_CHECK_VALUES,
    VALID_EXPERIENCE_LEVELS,
    VALID_LANGUAGE_LEVELS,
    VALID_WORK_ARRANGEMENTS,
    VALID_WORK_AUTH_STATUSES,
)


def _sorted(values: Any) -> list[str]:
    return sorted(values)


_LIST_NOTE = "a list of strings, one short item each (at most 200 characters); duplicates are dropped"

# path -> {"example": a VALID value, "allowed_keys": [...] and/or "allowed_values": [...], "note": one line}
SHAPES: dict[str, dict[str, Any]] = {
    "user_info.contact": {
        "example": {
            "email": "sam@example.com", "phone": "+44 7700 900123", "address_country": "GB",
            "linkedin_url": "https://www.linkedin.com/in/sam-example",
        },
        "allowed_keys": list(_ui._CONTACT),
        "note": "countries are ISO alpha-2 codes (names like 'United Kingdom' are converted)",
    },
    "user_info.right_to_work": {
        "example": {
            "countries": [
                {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False},
                {"country": "ANY", "remote_ok": True, "onsite": "only_if_sponsored",
                 "note": "remote anywhere; on-site only if sponsored"},
            ],
            "citizenship": ["GB"],
            "sanctions_country_citizen": False,
        },
        "allowed_keys": list(_ui._RIGHT_TO_WORK),
        "record_keys": list(_ui._RTW_COUNTRY),
        "allowed_values": {
            "work_authorization": _sorted(VALID_WORK_AUTH_STATUSES), "onsite": list(_ui.ONSITE_VALUES),
            "sanctions_country_citizen": [True, False, _ui.PREFER_NOT_TO_SAY],
            "needs_sponsorship": [True, False], "remote_ok": [True, False],
        },
        "note": (
            "one record per country; country 'ANY' = every other country. citizenship = list of ISO alpha-2 "
            f"codes. sanctions_country_citizen = true, false or \"{_ui.PREFER_NOT_TO_SAY}\" (nothing else). "
            "needs_sponsorship and remote_ok = true or false. visa_expires = YYYY-MM or YYYY-MM-DD. "
            "visa_type and note = one short line. work_authorization citizen / permanent_resident cannot go with "
            "needs_sponsorship true, and needs_sponsorship cannot go with needs_sponsorship false"
        ),
    },
    "user_info.logistics": {
        "example": {
            "notice_period": "1 month",
            "countries": [{
                "country": "GB", "willing_to_relocate": True, "driving_licence": True,
                "driving_licence_type": "provisional", "driving_licence_country": "GB",
            }],
            "start_on_site_abroad": "yes, after sponsorship is agreed",
            "current_salary": {"amount": 45000, "currency": "GBP", "period": "year"},
        },
        "allowed_keys": list(_ui._LOGISTICS),
        "record_keys": list(_ui._LOGISTICS_COUNTRY),
        "allowed_values": {"driving_licence_type": list(_ui.LICENCE_TYPES)},
    },
    "user_info.languages": {
        "example": [{"language": "English", "level": "native"}],
        "allowed_keys": list(_ui._LANGUAGE),
        "allowed_values": {"level": _sorted(VALID_LANGUAGE_LEVELS)},
    },
    "user_info.equality": {
        "example": {"gender": "Prefer not to say", "disability": "No"},
        "allowed_keys": list(_ui._EQUALITY),
        "note": "text answers; true/false are stored as Yes/No",
    },
    "user_info.answers": {
        "example": [{
            "question": "Why do you want this job?", "answer": "I like the product.",
            "approved": False, "topic": "why_this_job",
        }],
        "allowed_keys": list(_ui._ANSWER_KEYS),
        "note": (
            "company = set it when the answer is only for one employer (consent to X's privacy notice, why X). "
            "topic = a short stable key you choose (letters digits _ . only, 60 chars max, e.g. work_from.IN, "
            "notice_period_days); a gap or question with the same topic is answered by this one. "
            "aliases = up to 5 other wordings of the same question (a fallback match only)"
        ),
    },
    "preferences.salary_by_country": {
        "example": [{"country": "GB", "min": 60000, "max": 80000, "currency": "GBP", "period": "year"}],
        "allowed_keys": list(_ui._SALARY_KEYS),
        "allowed_values": {"period": list(_ui._SALARY_PERIODS)},
    },
    "preferences.work_arrangement": {
        "example": "remote", "allowed_values": _sorted(VALID_WORK_ARRANGEMENTS),
        "note": "one value; put nuance (e.g. on-site only if sponsored) in user_info.right_to_work note",
    },
    "preferences.experience_level": {
        "example": "mid", "allowed_values": _sorted(VALID_EXPERIENCE_LEVELS),
        "note": "one value; use experience_years_min/max for a range",
    },
    "preferences.experience_years_min": {"example": 2, "note": "whole number 0-60, not above max"},
    "preferences.experience_years_max": {"example": 5, "note": "whole number 0-60"},
    "preferences.check_every": {"example": "3h", "allowed_values": _sorted(VALID_CHECK_EVERY_VALUES)},
    "preferences.daily_check": {"example": "ask", "allowed_values": _sorted(VALID_DAILY_CHECK_VALUES)},
    "preferences.work_authorization_countries": {
        "example": ["GB", "IE"], "note": "ISO alpha-2 codes where no sponsorship is needed",
    },
    "preferences.target_companies": {
        "example": ["Mistral AI", "Hugging Face"], "note": "companies to check first; short names, empty = no list",
    },
    "preferences.target_job_titles": {"example": ["Data Engineer", "Analytics Engineer"], "note": _LIST_NOTE},
    "preferences.preferred_locations": {"example": ["London", "Remote UK"], "note": _LIST_NOTE},
    "preferences.industries": {"example": ["Fintech", "Health"], "note": _LIST_NOTE},
    "preferences.additional_skills": {"example": ["dbt", "Airflow"], "note": _LIST_NOTE},
    "preferences.excluded_skills": {"example": ["PHP"], "note": _LIST_NOTE},
    "preferences.negative_keywords": {"example": ["unpaid", "commission only"], "note": _LIST_NOTE},
    "preferences.looking_for": {"example": ["Small team", "Ownership of a product"], "note": _LIST_NOTE},
    "preferences.dealbreakers": {"example": ["No sponsorship", "On-site five days"], "note": _LIST_NOTE},
    "preferences.avoid_companies": {"example": ["Acme Staffing"], "note": _LIST_NOTE},
    "cv_data.education": {
        "example": ["BSc Computer Science, University of Leeds, 2019"],
        "note": "a list of strings, one line per qualification (not objects); " + _LIST_NOTE,
    },
    "cv_data.links": {
        "example": ["https://github.com/sam-example", "https://sam.example.com"],
        "note": "a list of strings, one plain URL each (not objects); " + _LIST_NOTE,
    },
    "preferences.assistant_notes": {"example": ["Never apply to agencies."], "note": "one short line per note"},
    "cv_data.cv_positions": {
        "example": [{
            "company": "Acme Ltd", "title": "Data Engineer", "dates": "Jan 2020 - Present",
            "location": "London", "bullets": ["Built the billing pipeline."],
        }],
        "allowed_keys": list(_edits.RECORD_SCHEMAS["cv_data.cv_positions"]),
    },
    "cv_data.cv_projects": {
        "example": [{"name": "Job tracker", "description": "A side project.", "technologies": ["Python"]}],
        "allowed_keys": list(_edits.RECORD_SCHEMAS["cv_data.cv_projects"]),
    },
    _settings.SETUP_PROGRESS_PATH: {
        "example": {"targets": {"done_at": "2026-10-10T09:00:00+00:00"}},
        "allowed_keys": list(_settings.SETUP_ROUNDS),
        "note": "send the current value plus the round just finished",
    },
    _settings.APPLY_MIN_SCORE_PATH: {"example": 70, "note": "the user's OWN score line, 0-100"},
    _settings.ABOVE_LINE_PATH: {"example": "ask", "allowed_values": _sorted(_settings.VALID_LINE_SWITCHES)},
    _settings.BELOW_LINE_PATH: {"example": "ask", "allowed_values": _sorted(_settings.VALID_BELOW_SWITCHES)},
}


def shape_for(path: str) -> dict[str, Any] | None:
    """The shape entry for ``path``, or ``None`` when it has none."""
    return SHAPES.get(path)


def example_json(path: str) -> str | None:
    """A valid example for ``path`` as compact JSON, or ``None`` (never the caller's value)."""
    shape = SHAPES.get(path)
    if shape is None or "example" not in shape:
        return None
    return json.dumps(shape["example"], ensure_ascii=False, separators=(",", ":"))


def compact_shapes() -> dict[str, dict[str, Any]]:
    """Every shape in the form ``get_profile`` returns: the example plus the closed key / value lists."""
    out: dict[str, dict[str, Any]] = {}
    for path, shape in SHAPES.items():
        row = {k: v for k, v in shape.items() if k != "note"}
        if "note" in shape:
            row["note"] = shape["note"]
        out[path] = row
    return out
