"""Provenance annotation — highlight which lines are the user's OWN facts (grounded
in the source CV/job) vs lines the AI added/reshaped. Line-level, deterministic,
no LLM. Backs the 'highlight facts before download' feature (user request)."""

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
