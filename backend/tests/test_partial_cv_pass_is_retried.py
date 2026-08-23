"""A CV pass that returns skills but loses cv_positions must be RETRIED, not
cached as a success.

WHY THIS EXISTS. Four LLM passes run concurrently in one `asyncio.gather` in
`run_two_pass_extraction`. Providers rate-limit, so one call can come back
VALID but with a section silently missing — measured on a real profile: "0
positions via the concurrent API path, 7 in isolation". The retry block exists
for exactly that case, but its old trigger was `_pass_produced_data`, which
answers a DIFFERENT question ("is this worth caching?", true on skills OR
titles alone). A CV pass returning skills with zero `cv_positions` passed that
check, so it was graded a success: no retry, and the input hash was cached —
freezing the user's dated career history out of the profile with no recovery
path (re-uploading the identical CV hits the same cache hash).

This file pins the new, separate question: "is this CV result PARTIAL?" —
`two_pass._cv_pass_is_partial`. It must fire on partial, stay silent on a
complete pass (the control — without it, "always retry" would pass these
tests too) and on a fully empty pass (that is a different, already-handled
case, not partial), and never apply to the other three inputs, whose result
shapes don't have a subsection that can go missing independently of the rest.
"""

from __future__ import annotations

from src.services.profile import two_pass as tp
from src.services.profile.models import CVData


def test_skills_without_positions_is_partial():
    """THE ROHITH SHAPE: skills/titles landed, cv_positions did not."""
    res = CVData(skills=["Python"], job_titles=["Engineer"])
    assert tp._cv_pass_is_partial(res) is True


def test_titles_without_positions_is_also_partial():
    """Either skills OR titles is enough to prove the pass ran and read the
    document — cv_positions can still be the piece that silently dropped."""
    res = CVData(job_titles=["Engineer"])
    assert tp._cv_pass_is_partial(res) is True


def test_a_complete_pass_is_not_partial():
    """THE CONTROL. A CV result carrying skills AND cv_positions is a normal
    success — without this test, a predicate that always returns True (i.e.
    "always retry") would pass every other test in this file too."""
    res = CVData(
        skills=["Python"],
        job_titles=["Engineer"],
        cv_positions=[{"company": "Acme", "title": "Engineer"}],
    )
    assert tp._cv_pass_is_partial(res) is False


def test_a_fully_empty_pass_is_not_double_counted_as_partial():
    """An empty CVData() has no skills and no titles either — that is the
    EMPTY case, already caught by `_pass_produced_data` in the retry loop.
    Calling it "partial" too would just fire the same retry twice for one
    cause, and (per the retry loop's swap rule) risks treating a genuinely
    position-less CV as if it had recoverable data."""
    assert tp._cv_pass_is_partial(CVData()) is False


def test_none_result_is_not_partial():
    """A pass that raised (`_safe` returns None) is EMPTY, not partial — the
    existing empty-check already retries it; this predicate must not also
    claim it."""
    assert tp._cv_pass_is_partial(None) is False


def test_non_cv_keys_are_never_partial():
    """LinkedIn's own positions are already folded into `_pass_produced_data`
    (skills OR positions OR education); GitHub/about_me each return a flat
    list[str] with no subsection that can go missing on its own. Only the CV
    shape is decidable, so the retry loop must never ask this question for
    the other three keys — pinned here by construction: the predicate takes
    no `key` argument at all, so there is nothing for a caller to get wrong
    for "linkedin"/"github"/"about_me" other than simply not calling it. This
    test documents that contract so a future edit adding a `key` parameter
    has to consciously decide the other three stay excluded.
    """
    # A dict/list shaped like a LinkedIn/GitHub/about_me result has no
    # `cv_positions` attribute at all — `_cv_pass_is_partial` must treat that
    # as "not partial" rather than raising, which is exactly what lets the
    # retry loop restrict the partial check to the "cv" key without this
    # function needing to know which key it was called for.
    assert tp._cv_pass_is_partial({"skills": ["Triage"], "positions": []}) is False
    assert tp._cv_pass_is_partial(["Python", "Docker"]) is False


def test_never_raises_on_an_odd_shape():
    """This runs in the upload hot path — a crash here must never cost a user
    their upload. Feed it shapes an LLM adapter could plausibly hand back on
    a malformed response, and require a plain bool every time."""
    for odd in (
        None,
        {},
        [],
        "",
        0,
        object(),
        {"skills": None, "job_titles": None},
    ):
        assert tp._cv_pass_is_partial(odd) in (True, False)
