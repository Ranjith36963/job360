"""Adapter-parity guard — the SAME bug shape, THREE times in one day.

Job360's CV LLM extraction has TWO adapters that turn an LLM's raw JSON
answer into a ``CVData``:

  * ``cv_parser._llm_result_to_cvdata`` — the untyped DEFENSIVE FALLBACK,
    reached only when strict ``CVSchema`` validation exhausts its retries
    (``cv_parser.py::llm_cv_fields_from_text``).
  * ``schemas.cv_schema_to_cvdata`` — the TYPED LIVE adapter every
    successful extraction actually returns through.

Three incidents, found in ONE Pillar-1 audit on 2026-08-07, all had the
SAME shape: a fix landed in one adapter and not the other, so production —
which runs through the live adapter — kept shipping the bug.

  1. ``cv_positions`` (structured, dated experience) — PR #241 added it to
     the fallback adapter; the live adapter had no line for it, so real
     extractions still came back with zero positions. Regression-guarded by
     ``TestCvSchemaCarriesDatedExperience`` in ``test_profile.py``.
  2. ``career_domain`` — the CV prompt never asked for it, and even once it
     does, ``two_pass._merge_cv_llm_into`` had no line copying it from the
     LLM pass into the live profile.
  3. ``cv_skills_esco`` (ESCO skill normalisation) —
     ``_maybe_normalise_skills_via_esco`` was only ever called from the
     fallback adapter; the live adapter built ``CVData(...)`` with no
     ``cv_skills_esco=`` argument at all, so every production profile
     shipped with ``cv_skills_esco={}`` regardless of ``SEMANTIC_ENABLED``.

This test exists so a FOURTH instance of the same bug shape cannot land
silently. It asserts both adapters populate the SAME SET of CVData fields
from equivalent input — add a field to one adapter and not the other, and
this test fails immediately, instead of waiting for the next live-CV audit
to catch it.
"""

from __future__ import annotations

from dataclasses import MISSING, fields
from typing import Any
from unittest.mock import patch

from src.services.profile.cv_parser import _llm_result_to_cvdata
from src.services.profile.models import CVData
from src.services.profile.schemas import CVSchema, cv_schema_to_cvdata

# The fields BOTH adapters are contractually responsible for populating from
# a CV LLM extraction. This mirrors, deliberately, the "what the CV owns"
# field list documented and enforced in ``two_pass.reset_cv_owned_fields`` —
# everything that function clears is everything a CV (re-)extraction is
# expected to (re-)populate. ``raw_text`` is excluded: both adapters take it
# as a plain passthrough parameter, not something derived from the LLM
# result, and ``reset_cv_owned_fields`` explicitly does not clear it either.
_CV_OWNED_FIELDS = [
    "skills", "job_titles", "companies", "education", "certifications",
    "industries", "cv_languages", "cv_skills_esco", "summary",
    "experience_text", "name", "headline", "location", "achievements",
    "career_domain", "cv_positions",
]

# Earned, justified exceptions only. Empty on purpose: this file exists
# because "one adapter has it, the other doesn't" happened three times
# without anyone noticing, so a field mismatch here gets FIXED, not waved
# through. If a genuine, permanent asymmetry is ever discovered (not just an
# oversight), add it here with a one-line reason — never leave the set
# comparison below silently weaker instead.
_ALLOWED_FIELD_DIFFERENCES: dict[str, str] = {}

_PAYLOAD: dict[str, Any] = {
    "name": "Jamie Rivera",
    "headline": "Senior Structural Engineer",
    "location": "Bristol, UK",
    "summary": "Structural engineer with 9 years in bridge design.",
    "skills": ["AutoCAD", "Eurocode 2", "Finite Element Analysis"],
    "experience": [
        {
            "company": "Arup",
            "title": "Senior Structural Engineer",
            "dates": "2019-Present",
            "location": "Bristol",
            "bullets": ["Led design of a 200m footbridge"],
        }
    ],
    "education": [
        {
            "degree": "MEng Civil Engineering",
            "institution": "University of Bristol",
            "dates": "2011-2015",
            "details": ["Dissertation: seismic retrofit"],
        }
    ],
    "certifications": ["Chartered Engineer (IStructE, 2020)"],
    "achievements": ["cut material cost 18% on the Severn footbridge"],
    "industries": ["Construction", "Infrastructure"],
    "languages": ["English", "Spanish"],
    "career_domain": "engineering_physical",
}


def _populated_fields(cv: CVData) -> set[str]:
    """CV-owned field names on ``cv`` holding something other than the
    dataclass default (i.e. the adapter actually wrote something there)."""
    populated = set()
    for f in fields(CVData):
        if f.name not in _CV_OWNED_FIELDS:
            continue
        default = f.default_factory() if f.default_factory is not MISSING else f.default
        if getattr(cv, f.name) != default:
            populated.add(f.name)
    return populated


def _build_both_adapters() -> tuple[CVData, CVData]:
    schema = CVSchema.model_validate(_PAYLOAD)
    live = cv_schema_to_cvdata(schema, raw_text="raw text")
    fallback = _llm_result_to_cvdata("raw text", dict(_PAYLOAD))
    return live, fallback


class TestAdapterFieldParity:
    def test_same_fields_populate_with_esco_unavailable(self):
        """ESCO off/unavailable is the DEFAULT runtime state
        (``SEMANTIC_ENABLED`` defaults false, root rule #18) — the two
        adapters must agree here too, not only when ESCO is on."""
        identity = lambda skills: (skills, {})  # noqa: E731
        with patch(
            "src.services.profile.cv_parser._maybe_normalise_skills_via_esco",
            side_effect=identity,
        ), patch(
            "src.services.profile.schemas._maybe_normalise_skills_via_esco",
            side_effect=identity,
        ):
            live, fallback = _build_both_adapters()

        live_fields = _populated_fields(live)
        fallback_fields = _populated_fields(fallback)
        allowed = set(_ALLOWED_FIELD_DIFFERENCES)
        assert live_fields - allowed == fallback_fields - allowed, (
            f"adapters disagree on which fields populate: "
            f"live-only={live_fields - fallback_fields}, "
            f"fallback-only={fallback_fields - live_fields}"
        )

    def test_same_fields_populate_with_esco_available(self):
        """When ESCO IS available, ``cv_skills_esco`` must populate on BOTH
        paths — this is bug #3 (``cv_skills_esco`` stuck on a dead path)
        made structurally unable to recur."""
        fake_map = {"AutoCAD": "http://esco/autocad"}
        normalise = lambda skills: (skills, dict(fake_map))  # noqa: E731
        with patch(
            "src.services.profile.cv_parser._maybe_normalise_skills_via_esco",
            side_effect=normalise,
        ), patch(
            "src.services.profile.schemas._maybe_normalise_skills_via_esco",
            side_effect=normalise,
        ):
            live, fallback = _build_both_adapters()

        assert live.cv_skills_esco == fake_map, "live adapter did not route through ESCO"
        assert fallback.cv_skills_esco == fake_map

        live_fields = _populated_fields(live)
        fallback_fields = _populated_fields(fallback)
        allowed = set(_ALLOWED_FIELD_DIFFERENCES)
        assert live_fields - allowed == fallback_fields - allowed

    def test_career_domain_and_industries_and_languages_match_on_both(self):
        """Direct value check on the fields this specific audit fixed —
        SET parity alone can't tell you the two adapters read the SAME
        classification, only that both wrote SOMETHING."""
        identity = lambda skills: (skills, {})  # noqa: E731
        with patch(
            "src.services.profile.cv_parser._maybe_normalise_skills_via_esco",
            side_effect=identity,
        ), patch(
            "src.services.profile.schemas._maybe_normalise_skills_via_esco",
            side_effect=identity,
        ):
            live, fallback = _build_both_adapters()

        assert live.career_domain == fallback.career_domain == "engineering_physical"
        assert set(live.cv_industries) == set(fallback.cv_industries) == {"Construction", "Infrastructure"}
        assert set(live.cv_languages) == set(fallback.cv_languages) == {"English", "Spanish"}

    def test_a_field_populated_on_only_one_adapter_fails_the_test(self):
        """Meta-test: prove the guard actually guards. Simulates the exact
        failure mode of the three real incidents — one adapter's output has
        a field the other's doesn't — using two hand-built CVData instances
        instead of re-triggering a real adapter bug."""
        richer = CVData(skills=["Python"], career_domain="data_and_ai")
        poorer = CVData(skills=["Python"])  # career_domain missing, like the real bug

        richer_fields = _populated_fields(richer)
        poorer_fields = _populated_fields(poorer)
        assert richer_fields != poorer_fields, (
            "the field-population helper must be sensitive to a single "
            "missing field, or this whole test file is a no-op guard"
        )
