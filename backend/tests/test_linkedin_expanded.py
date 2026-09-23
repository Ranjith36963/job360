"""Batch 1.5 (Pillar 1) — expanded LinkedIn sections, deterministic only.

Originally added coverage for Languages / Projects / Volunteer Experience /
Courses — the 4 sections whose bodies used to be discarded by
``parse_linkedin_pdf_async`` — plus the LLM pass that filled them.

Decision 28 (2026-09-21) deleted that LLM pass entirely: Job360 has no model
of its own, and the prose sections (including these four) are now read and
written by the user's own agent via ``get_profile``/``update_profile``. Every
test here that exercised the LLM (the ``_coerce_*`` JSON shapers, the
prompt-mocked end-to-end parse) is gone with it — there is no LLM output left
to shape or mock.

What's left, and what this file actually tests now, is the deterministic
contract the four sections still have to honour on ``CVData``:
  - ``_empty_linkedin_data()`` still declares them (empty lists, never
    missing keys — a caller can always index them safely).
  - ``enrich_cv_from_linkedin`` still writes them onto their ``linkedin_*``
    shelves when a caller (structural parse today, an agent's
    ``update_profile`` call in general) hands them in.
  - Most importantly: a hand-off that carries an EMPTY section must never
    clear a shelf that already has data on it. That used to be guarded by an
    ``llm_ran`` flag (empty was ambiguous — "nothing there" vs. "the LLM call
    was skipped"); decision 28 replaced the flag with a plain "only assign
    when non-empty" rule, and it is the one behaviour this suite exists to
    pin down.
"""

from __future__ import annotations

from src.services.profile.linkedin_parser import (
    _empty_linkedin_data,
    enrich_cv_from_linkedin,
)
from src.services.profile.models import CVData

# ── _empty_linkedin_data — schema still declares the Batch 1.5 sections ────


def test_empty_linkedin_data_includes_new_fields():
    empty = _empty_linkedin_data()
    for key in ("languages", "projects", "volunteer", "courses"):
        assert key in empty
        assert empty[key] == []


# ── enrich_cv_from_linkedin — writes the new fields ─────────────────


def test_enrich_writes_new_section_fields_onto_cvdata():
    """A caller (structural parse, or an agent via update_profile) hands in
    the four sections as plain dicts/lists — no LLM involved in producing
    this shape any more — and enrich_cv_from_linkedin must land each one on
    its own ``linkedin_*`` shelf."""
    cv = CVData()
    linkedin_data = _empty_linkedin_data() | {
        "languages": [{"language": "English", "proficiency": "Native"}],
        "projects": [{"title": "Job360", "description": "x", "start": "", "end": "", "url": ""}],
        "volunteer": [{"role": "Mentor", "organisation": "CFG", "cause": "Education",
                       "start": "", "end": "", "description": ""}],
        "courses": [{"title": "Stats", "institution": "Stanford", "date": "2022"}],
    }
    cv = enrich_cv_from_linkedin(cv, linkedin_data)
    assert cv.linkedin_languages == [{"language": "English", "proficiency": "Native"}]
    assert cv.linkedin_projects[0]["title"] == "Job360"
    assert cv.linkedin_volunteer[0]["organisation"] == "CFG"
    assert cv.linkedin_courses[0]["title"] == "Stats"


def test_enrich_never_clears_a_shelf_on_empty_reparse():
    """Decision 28's central invariant, pinned down directly on these four
    sections: a hand-off that read nothing for a section must leave whatever
    is already on that shelf alone, never wipe it to empty.

    This used to be guarded by an ``llm_ran`` flag, because an empty section
    was ambiguous under the old LLM pass (genuinely no data on LinkedIn, vs.
    the paid call being skipped by the cost cache). Assigning on the second
    case wiped real data — measured 2026-08-08: upload LinkedIn, touch
    anything else, five sections gone permanently. Decision 28 removed the
    LLM pass and replaced the flag with a plain "only assign when non-empty"
    rule instead, which is what this test exercises: a section that DID come
    back (languages) overwrites, one that came back EMPTY (projects) does
    not.
    """
    cv = CVData(
        linkedin_languages=[{"language": "Old", "proficiency": ""}],
        linkedin_projects=[{"title": "Old", "description": "", "start": "", "end": "", "url": ""}],
    )
    fresh = _empty_linkedin_data() | {
        "languages": [{"language": "Fresh", "proficiency": ""}],
        "projects": [],  # this hand-off carries no projects at all
    }
    cv = enrich_cv_from_linkedin(cv, fresh)
    # Languages: the hand-off DID carry a value, so it wins.
    assert cv.linkedin_languages == [{"language": "Fresh", "proficiency": ""}]
    # Projects: the hand-off carried nothing, so the old value must survive —
    # NOT be wiped to []. This is exactly the 2026-08-08 bug decision 28 fixed.
    assert cv.linkedin_projects == [
        {"title": "Old", "description": "", "start": "", "end": "", "url": ""}
    ]
