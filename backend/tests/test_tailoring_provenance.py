"""Provenance annotation — highlight which lines are the user's OWN facts (grounded
in their profile) vs lines copied from the job ad vs lines the AI added/reshaped.
Line-level, deterministic, no LLM. Backs the 'highlight facts before download' feature.
The ad can label a line ``from_ad`` (source="ad") but never ``grounded`` (gap-audit #17)."""

from src.services.tailoring.provenance import annotate_provenance


def test_verbatim_line_is_grounded():
    src = "Senior ML Engineer at Monzo. Python, AWS, PyTorch."
    segs = annotate_provenance("Senior ML Engineer at Monzo", src)
    assert segs[0]["grounded"] is True


def test_invented_line_is_flagged_ai_added():
    src = "Python developer. AWS."
    segs = annotate_provenance("Led a global team of fifty across three continents", src)
    assert segs[0]["grounded"] is False


def test_blank_line_is_neutral_grounded():
    segs = annotate_provenance("", "anything here")
    assert segs[0]["grounded"] is True


def test_one_segment_per_line_mixed():
    src = "Python AWS Monzo Kubernetes"
    out = "Python AWS Kubernetes\nInvented flamboyant nonsense phrasing here"
    segs = annotate_provenance(out, src)
    assert len(segs) == 2
    assert segs[0]["grounded"] is True   # all real skills → your facts
    assert segs[1]["grounded"] is False  # nothing from source → AI-added


def test_segments_preserve_text_exactly():
    out = "Line one here\nLine two there"
    segs = annotate_provenance(out, "Line one here Line two there")
    assert [s["text"] for s in segs] == ["Line one here", "Line two there"]


def test_no_source_marks_everything_ai_added():
    segs = annotate_provenance("Some real content words", "")
    # With no source to ground against, non-blank lines can't be verified.
    assert segs[0]["grounded"] is False


# ── The job ad is a separate source: it never establishes a candidate fact ────

_CV = "Jane Doe. Built fraud models in Python at Acme. Mentored two junior analysts."
_AD = "We need 5+ years of Kubernetes in production. Experience with Python is a plus."


def test_line_copied_from_the_ad_is_from_ad_not_grounded():
    segs = annotate_provenance("5+ years of Kubernetes in production", _CV, _AD)
    assert segs[0]["grounded"] is False
    assert segs[0]["source"] == "ad"


def test_line_from_the_cv_is_grounded_from_profile():
    segs = annotate_provenance("Built fraud models in Python at Acme", _CV, _AD)
    assert segs[0]["grounded"] is True
    assert segs[0]["source"] == "profile"


def test_line_in_neither_is_ungrounded_and_not_from_ad():
    segs = annotate_provenance("Won the Nobel Prize in Chemistry", _CV, _AD)
    assert segs[0]["grounded"] is False
    assert segs[0]["source"] == "none"


def test_line_in_both_is_grounded_profile_wins():
    cv = _CV + " Five years of Kubernetes in production."
    ad = "Five years of Kubernetes in production is required."
    segs = annotate_provenance("Five years of Kubernetes in production", cv, ad)
    assert segs[0]["grounded"] is True
    assert segs[0]["source"] == "profile"


def test_ad_alone_never_grounds_a_line_even_with_empty_profile():
    segs = annotate_provenance("5+ years of Kubernetes in production", "", _AD)
    assert segs[0] == {"text": "5+ years of Kubernetes in production", "grounded": False, "source": "ad"}


def test_blank_line_is_neutral_and_not_from_ad():
    segs = annotate_provenance("", _CV, _AD)
    assert segs[0]["grounded"] is True
    assert segs[0]["source"] == "profile"


def test_mixed_document_labels_each_line():
    out = "Built fraud models in Python at Acme\n5+ years of Kubernetes in production\nWon the Nobel Prize in Chemistry"
    assert [s["source"] for s in annotate_provenance(out, _CV, _AD)] == ["profile", "ad", "none"]


def test_no_source_marks_everything_none():
    segs = annotate_provenance("Some real content words", "", "")
    assert segs[0]["source"] == "none"


def test_a_target_title_the_user_never_held_does_not_ground_a_line():
    """Review of #17: target_job_titles are roles the user WANTS. A headline copied from
    the ad must stay "ad" even when it matches a target title."""
    from types import SimpleNamespace

    from src.services.applications.cv_checks import profile_source_text

    profile = SimpleNamespace(
        cv_data=SimpleNamespace(raw_text="Analyst at Acme, built fraud models."),
        preferences=SimpleNamespace(
            additional_skills=[], about_me="", target_job_titles=["Senior Data Engineer"]
        ),
    )
    assert "Senior Data Engineer" in profile_source_text(profile)
    own = profile_source_text(profile, include_targets=False)
    assert "Senior Data Engineer" not in own

    [seg] = annotate_provenance("Senior Data Engineer", own, "We hire a Senior Data Engineer")
    assert seg["source"] == "ad"
    assert seg["grounded"] is False
