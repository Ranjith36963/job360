"""Don't pay twice to read the same CV.

WHY THIS EXISTS. Every profile change re-runs the whole two-pass extraction, and
each run makes a paid LLM call per input. So editing one preference re-sent an
untouched CV to a paid model at full price. The only guard was
PROFILE_EXTRACT_MAX_PER_HOUR — a rate limit, which caps the bleeding rather than
stopping it, and pays for our waste with a 429 in the user's face.

The fix stores the input's HASH, not the LLM's output, because the output is
already merged into the same CVData. An unchanged hash means "that result is
already in these fields", so the call can be skipped outright.

That makes the risk obvious and worth pinning hard: a cache that skips a call it
should have made silently ships a worse profile, and nobody finds out. Every test
below is about that failure, not about the saving.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from src.services.profile import llm_curate
from src.services.profile import two_pass as tp
from src.services.profile.models import CVData, UserPreferences, UserProfile

CV_TEXT = """
Jane Okafor
Registered Nurse
SUMMARY
Senior nurse with ICU experience across NHS trusts.
EXPERIENCE
Staff Nurse, Royal London Hospital
Delivered care using ALS and BLS protocols and Epic charting.
"""


class _Counter:
    """Counts calls per input so a test can assert WHICH passes ran, not just
    how many — per-input caching is the whole point and a total would hide a
    pass that ran when it should not have."""

    def __init__(self) -> None:
        self.cv = self.linkedin = self.github = self.about_me = 0


def _run(profile: UserProfile, counter: _Counter, *, cv_fails: bool = False) -> UserProfile:
    """Run the real extractor with every paid call stubbed and counted."""

    async def fake_cv(text, *a, **kw):
        counter.cv += 1
        if cv_fails:
            raise RuntimeError("provider down")
        return CVData(skills=["Epic"], job_titles=["Registered Nurse"])

    async def fake_li(text, *a, **kw):
        counter.linkedin += 1
        # A REAL successful LinkedIn pass returns usable data. Returning {} here
        # modelled "the call succeeded but produced nothing", which the
        # soft-failure-cache fix now (correctly) refuses to cache — so the stub
        # must produce data or the cache legitimately keeps retrying it.
        return {"skills": ["Triage"], "positions": [{"title": "Nurse", "company": "NHS"}]}

    async def fake_gh(brief, *a, **kw):
        counter.github += 1
        return ["Python"]

    async def fake_about(text, *a, **kw):
        counter.about_me += 1
        return ["Teamwork"]

    async def _noop_list(*a, **kw):
        return []

    async def _passthrough(items, *a, **kw):
        return items

    with patch.object(tp, "llm_cv_fields_from_text", fake_cv), \
         patch.object(tp, "llm_linkedin_fields", fake_li), \
         patch.object(tp, "llm_infer_github_skills", fake_gh), \
         patch.object(tp, "llm_infer_from_about_me", fake_about), \
         patch.object(llm_curate, "llm_suggest_adjacent_skills", _noop_list), \
         patch.object(llm_curate, "llm_merge_duplicates", _passthrough):
        return asyncio.run(tp.run_two_pass_extraction(profile))


def _profile(**cv_kw) -> UserProfile:
    return UserProfile(
        cv_data=CVData(raw_text=CV_TEXT, **cv_kw),
        preferences=UserPreferences(about_me="I like ICU work."),
    )


def test_an_unchanged_profile_costs_nothing_the_second_time():
    """The saving itself. Re-extraction with nothing changed must make ZERO
    paid calls — this is the case that fires whenever a user edits a setting."""
    p = _profile()
    c = _Counter()

    _run(p, c)
    assert (c.cv, c.about_me) == (1, 1), "first run must actually call the LLM"

    _run(p, c)
    assert (c.cv, c.about_me) == (1, 1), (
        "an unchanged profile was sent to a paid model again"
    )


def test_changing_one_input_re_reads_only_that_input():
    """Per-input, not all-or-nothing. Editing 'about me' must not re-bill the
    user for re-reading a CV that did not change."""
    p = _profile()
    c = _Counter()
    _run(p, c)

    p.preferences.about_me = "Actually I want theatre work now."
    _run(p, c)

    assert c.about_me == 2, "the changed input must be re-read"
    assert c.cv == 1, "an untouched CV was re-sent to a paid model"


def test_a_failed_pass_is_retried_not_cached():
    """A provider outage must NOT be remembered as 'already read'. Caching a
    failure would freeze a half-extracted profile forever, and the user would
    never learn why their matches are bad."""
    p = _profile()
    c = _Counter()

    _run(p, c, cv_fails=True)
    assert "cv" not in p.cv_data.llm_input_hashes, "a failure was cached"

    _run(p, c)
    assert c.cv == 2, "a failed CV pass was never retried"
    assert "Epic" in p.cv_data.skills, "the retry's result never landed"


def test_re_uploading_the_same_cv_after_a_reset_still_calls_the_llm():
    """The dangerous case. reset_cv_owned_fields() wipes CV-derived fields; if
    the hash survived, re-uploading the SAME file — exactly what someone does
    after a bad parse — would skip the LLM and leave the wiped profile."""
    p = _profile()
    c = _Counter()
    _run(p, c)
    assert "Epic" in p.cv_data.skills

    tp.reset_cv_owned_fields(p.cv_data)
    assert "cv" not in p.cv_data.llm_input_hashes, (
        "the hash outlived the fields it guards — a re-upload would be skipped"
    )

    _run(p, c)
    assert c.cv == 2, "the re-uploaded CV was not re-read"
    assert "Epic" in p.cv_data.skills, "the profile was not rebuilt"


def test_a_reset_does_not_discard_linkedin_or_github_work():
    """Only the CV's hash may be cleared. LinkedIn/GitHub data deliberately
    survives a CV swap, so re-paying to re-read them would be pure waste."""
    p = _profile(linkedin_raw_text="Top Skills\nTriage\n", github_repos_brief="repo: icu-tools")
    c = _Counter()
    _run(p, c)
    assert c.linkedin == 1 and c.github == 1

    tp.reset_cv_owned_fields(p.cv_data)
    _run(p, c)

    assert c.linkedin == 1, "LinkedIn was re-read because of a CV swap"
    assert c.github == 1, "GitHub was re-read because of a CV swap"


def test_reordered_github_data_is_not_mistaken_for_a_change():
    """github_repos_brief can come back from a re-fetch in a different order
    with identical content. Hashing raw repr would call that a change and
    re-bill the user, so the digest is order-stable."""
    a = tp._input_hash([{"name": "b", "lang": "Go"}, {"name": "a", "lang": "Py"}])
    b = tp._input_hash([{"name": "a", "lang": "Py"}, {"name": "b", "lang": "Go"}])
    assert a != b, "distinct list ORDER is still distinct content"

    # But identical content with differently-ordered dict keys must match.
    x = tp._input_hash([{"name": "a", "lang": "Py"}])
    y = tp._input_hash([{"lang": "Py", "name": "a"}])
    assert x == y, "dict key order must not read as a content change"


def test_a_soft_empty_pass_is_not_frozen_by_the_cache():
    """THE ROHITH BUG, found by the journey simulation 2026-08-05.

    A rate-limited free-tier provider can return a valid but EMPTY result
    (`{"positions": [], "skills": []}`) without raising. The cache used to
    record that as a success and skip re-extraction forever — so a profile
    that momentarily failed extraction was frozen with zero LinkedIn data,
    while the LLM extracted 7 positions fine when retried in isolation.

    An empty result from a non-empty input must NOT be cached: it is exactly
    the case you most want to retry.
    """
    calls = {"n": 0}

    async def li_empty_then_full(text, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {}  # soft failure: valid response, no data
        return {"skills": ["Triage"], "positions": [{"title": "Nurse", "company": "NHS"}]}

    async def _noop(*a, **kw):
        return CVData()

    async def _noop_list(*a, **kw):
        return []

    async def _pass(items, *a, **kw):
        return items

    p = _profile(linkedin_raw_text="Top Skills\nTriage\n")
    with patch.object(tp, "llm_cv_fields_from_text", _noop), \
         patch.object(tp, "llm_linkedin_fields", li_empty_then_full), \
         patch.object(tp, "llm_infer_github_skills", _noop_list), \
         patch.object(tp, "llm_infer_from_about_me", _noop_list), \
         patch.object(llm_curate, "llm_suggest_adjacent_skills", _noop_list), \
         patch.object(llm_curate, "llm_merge_duplicates", _pass):
        asyncio.run(tp.run_two_pass_extraction(p))
        assert "linkedin" not in p.cv_data.llm_input_hashes, \
            "an empty LinkedIn pass was cached — it will now be frozen forever"
        asyncio.run(tp.run_two_pass_extraction(p))

    assert calls["n"] == 2, "the empty pass was not retried — it was frozen"
    assert p.cv_data.linkedin_positions, "the retry's positions never landed"
