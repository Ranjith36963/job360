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


def test_empty_extraction_is_broken_not_merely_weak():
    s = score_extraction(NURSE_CV, [])
    assert s.verdict == "broken"
    assert s.precision == 0.0
