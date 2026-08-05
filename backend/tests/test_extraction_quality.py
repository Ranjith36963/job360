"""The universal extraction gate — works for any CV, any profession, forever.

WHY THESE TESTS MATTER. We fixed extraction seven times for seven CVs. Each fix
was correct and each was a patch for a layout we had seen. That road has no end:
the eighth upload invents a new shape.

This module scores the RESULT instead of parsing the shape, so it needs no
knowledge of the document's layout, the person's field, or the vocabulary of
their trade. These tests pin that property — every case below is built from a
DIFFERENT profession on purpose, because a metric that only works for software
CVs is just overfitting with extra steps.
"""

from __future__ import annotations

from src.services.profile.extraction_quality import (
    needs_escalation,
    score_extraction,
)

NURSE_CV = """
Jane Okafor
Registered Nurse
SUMMARY
Senior nurse with ICU experience across NHS trusts.
EXPERIENCE
Staff Nurse, Royal London Hospital
Delivered care using ALS and BLS protocols, managed Epic charting,
supervised triage under NEWS2 scoring.
CERTIFICATIONS
NMC Registration, ALS Provider, Paediatric Immunisation
"""

WELDER_CV = """
Tomas Novak
Fabrication Welder
EXPERIENCE
Welder, Kraftwerk Engineering
Performed MIG and TIG welding to ISO 9606 standard, read GD&T drawings,
operated CNC plasma cutting and worked to ASME IX procedures.
"""


def test_score_is_high_when_extraction_captured_the_document():
    """A good extraction of a NURSING cv must score well — the metric cannot
    be secretly tuned to software."""
    s = score_extraction(
        NURSE_CV,
        ["ALS", "BLS", "Epic", "NEWS2", "ICU", "Triage"],
        job_titles=["Registered Nurse", "Staff Nurse"],
        summary="Senior nurse with ICU experience across NHS trusts.",
        certifications=["NMC Registration", "ALS Provider"],
    )
    assert s.verdict == "good", f"good nursing extraction scored {s.overall}: {s.problems}"
    assert not needs_escalation(s)


def test_score_is_low_when_extraction_missed_the_document():
    """Same CV, almost nothing extracted -> must escalate. This is the case a
    parser cannot detect about itself."""
    s = score_extraction(NURSE_CV, ["Nursing"])
    assert s.verdict != "good"
    assert needs_escalation(s)
    assert s.coverage < 0.3


def test_a_trade_cv_scores_on_its_own_terms():
    """WELDING vocabulary — no software terms anywhere. The metric must work
    identically or it is not universal."""
    s = score_extraction(
        WELDER_CV,
        ["MIG", "TIG", "ISO 9606", "GD&T", "CNC", "ASME IX"],
        job_titles=["Fabrication Welder"],
        summary="Fabrication welder",
    )
    assert s.coverage > 0.3, f"trade CV under-scored: {s.as_dict()}"


def test_junk_skills_lower_precision_regardless_of_field():
    s = score_extraction(
        NURSE_CV,
        ["ALS", "validity", "timeliness", "consistency", "basic", "completeness"],
        job_titles=["Registered Nurse"],
    )
    assert s.precision < 0.6, "bare quality words must count as junk"
    assert any("not skills" in p for p in s.problems)


def test_unreadable_input_dominates_the_score():
    """A file with no word boundaries cannot produce a good profile no matter
    what the parser does — the score must say so rather than blaming skills."""
    glued = "SkilledinleveragingPythonSQLandAWStodeliversolutions" * 12
    s = score_extraction(glued, ["Python", "SQL", "AWS"])
    assert s.input_health <= 0.2
    assert needs_escalation(s)
    assert any("word boundaries" in p for p in s.problems)


def test_missing_certifications_are_reported_when_the_cv_mentions_them():
    """ATS filters screen on certifications. If the document says 'certification'
    and we extracted none, that must surface — in any profession."""
    s = score_extraction(NURSE_CV, ["ALS", "BLS"], job_titles=["Registered Nurse"])
    assert any("certification" in p.lower() for p in s.problems)


def test_coverage_is_not_punished_for_places_employers_and_names():
    """A CV thick with proper nouns must not be graded as a failed extraction.

    MEASURED, on 7 real CVs: about half of what coverage counts as "missed" is
    not a skill at all — the person's own name, Leeds, United Kingdom, General
    Motors, the word "Awards". Coverage therefore has a practical ceiling well
    below 1.0, and this test exists to stop anyone (a future session, or a loop
    told to "improve extraction") from chasing it by stuffing employers and
    cities into the skills list. That would raise the number and RUIN matching.

    Here every genuine skill was captured. The verdict must not be "broken".
    """
    noun_heavy_cv = """
    Priya Raman
    London, United Kingdom
    EXPERIENCE
    Structural Engineer, Arup Group, Manchester
    Previously at Balfour Beatty and Laing O'Rourke, Birmingham.
    Chartered with the Institution of Civil Engineers.
    Delivered designs in Revit and Tekla to Eurocode 2 and BS 5950.
    EDUCATION
    University of Leeds, Yorkshire
    """
    s = score_extraction(
        noun_heavy_cv,
        ["Revit", "Tekla", "Eurocode 2", "BS 5950", "Structural Design"],
        job_titles=["Structural Engineer"],
        summary="Chartered structural engineer.",
        certifications=["Institution of Civil Engineers"],
    )
    # Every real skill in the document was extracted, so this must not read as a
    # failure — even though coverage is dragged down by ~10 place/employer names.
    assert s.verdict != "broken", (
        f"a complete extraction was graded broken by proper-noun drag: {s.as_dict()}"
    )
    assert s.precision == 1.0, "no junk was extracted, precision must be perfect"
    assert s.completeness == 1.0, "all four matcher-critical fields were filled"


def test_empty_extraction_is_broken_not_merely_weak():
    s = score_extraction(NURSE_CV, [])
    assert s.verdict == "broken"
    assert s.precision == 0.0


# ---------------------------------------------------------------------------
# The gate must actually FIRE in the pipeline, not just exist as a function.
# A scorer nobody calls is the same as no scorer — and this repo has been
# burned by exactly that shape before (a telemetry file nothing wrote, a
# detector nothing woke).
# ---------------------------------------------------------------------------

def test_two_pass_writes_the_score_onto_the_profile():
    """run_two_pass_extraction must leave its verdict on cv_data, so the API
    and the UI can act on a thin profile instead of shipping it silently."""
    import asyncio
    from unittest.mock import patch

    from src.services.profile import two_pass as tp
    from src.services.profile.models import CVData, UserPreferences, UserProfile

    profile = UserProfile(
        cv_data=CVData(
            raw_text=NURSE_CV,
            skills=["ALS", "BLS", "Epic"],
            job_titles=["Registered Nurse"],
            summary="Senior nurse with ICU experience.",
        ),
        preferences=UserPreferences(target_job_titles=["Nurse"]),
    )

    # Neutralise every paid/network pass — we are testing the GATE, not the LLM.
    # llm_cv_fields_from_text returns a CVData (not a dict), and the merge that
    # follows reads .skills off it, so the stub must keep that shape.
    async def _noop(*a, **kw):
        return CVData()

    async def _noop_list(*a, **kw):
        return []

    # llm_curate is imported INSIDE run_two_pass_extraction, so it must be
    # patched at its SOURCE module — patching a two_pass attribute misses it.
    # The LinkedIn/GitHub passes no-op on their own here: this profile carries
    # no LinkedIn text and no GitHub data, which is the documented behaviour.
    from src.services.profile import llm_curate

    with patch.object(tp, "llm_cv_fields_from_text", _noop), \
         patch.object(tp, "llm_infer_github_skills", _noop_list), \
         patch.object(tp, "llm_infer_from_about_me", _noop_list), \
         patch.object(llm_curate, "llm_suggest_adjacent_skills", _noop_list), \
         patch.object(llm_curate, "llm_merge_duplicates", _noop_list):
        out = asyncio.run(tp.run_two_pass_extraction(profile))

    score = out.cv_data.extraction_score
    assert score, "the gate did not run — a scorer nobody calls is no scorer"
    assert "overall" in score and "verdict" in score
    assert 0.0 <= score["overall"] <= 1.0


def test_scoring_failure_never_costs_the_user_their_upload():
    """If scoring itself breaks, extraction must still return a profile. A
    quality check that can destroy the thing it measures is worse than none."""
    import asyncio
    from unittest.mock import patch

    from src.services.profile import two_pass as tp
    from src.services.profile.models import CVData, UserPreferences, UserProfile

    profile = UserProfile(
        cv_data=CVData(raw_text=NURSE_CV, skills=["ALS"]),
        preferences=UserPreferences(),
    )

    async def _noop(*a, **kw):
        return CVData()

    async def _noop_list(*a, **kw):
        return []

    def _boom(*a, **kw):
        raise RuntimeError("scorer exploded")

    from src.services.profile import llm_curate

    with patch.object(tp, "llm_cv_fields_from_text", _noop), \
         patch.object(tp, "llm_infer_github_skills", _noop_list), \
         patch.object(tp, "llm_infer_from_about_me", _noop_list), \
         patch.object(llm_curate, "llm_suggest_adjacent_skills", _noop_list), \
         patch.object(llm_curate, "llm_merge_duplicates", _noop_list), \
         patch.object(tp, "score_extraction", _boom):
        out = asyncio.run(tp.run_two_pass_extraction(profile))

    assert out is profile, "a scoring crash must not lose the extraction"
    assert out.cv_data.skills == ["ALS"], "the extracted data must survive"
