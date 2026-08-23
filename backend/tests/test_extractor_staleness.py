"""Extraction never re-runs on its own, so a prompt fix never reaches an
existing user unless something notices their profile fell behind.

WHY THIS FILE EXISTS. ``run_two_pass_extraction`` has exactly one caller in
this codebase — a user-triggered save route. Nothing scheduled ever re-reads
an EXISTING profile. So when ``EXTRACTOR_VERSION`` bumps because a prompt
improved, every profile whose owner does not happen to re-save stays frozen
at whatever the OLD prompt produced, silently, forever.

This has already been "fixed" four separate times in production with
throwaway, uncommitted scripts that called ``save_profile()`` directly —
which stamps a fresh ``updated_at`` while leaving ``llm_input_hashes``
untouched, so the profile then LOOKS freshly updated while its extraction is
still stale. That is exactly the trap: nothing here may pass by accident just
because a profile "looks recent".

These tests pin ``stale_extraction_inputs`` — the read-only check that says
which raw inputs on a profile were last read by an OLDER extractor than the
one running now. No LLM call anywhere in this file (rule #4): staleness is a
pure hash comparison.
"""

from __future__ import annotations

from src.services.profile import two_pass as tp
from src.services.profile.models import CVData, UserPreferences, UserProfile

CV_TEXT = "Jane Okafor, Registered Nurse. Senior ICU experience."
LI_TEXT = "Top Skills\nTriage\n"


def _profile(**cv_kw) -> UserProfile:
    return UserProfile(cv_data=CVData(**cv_kw), preferences=UserPreferences())


def test_a_hash_from_the_current_version_is_not_stale():
    """The common case: a profile that was extracted under the code running
    right now must not be flagged — otherwise every fresh save would look
    stale forever and the sweep script would burn a paid call on it for
    nothing."""
    p = _profile(raw_text=CV_TEXT)
    p.cv_data.llm_input_hashes["cv"] = tp._input_hash(CV_TEXT)

    assert tp.stale_extraction_inputs(p) == []


def test_a_hash_from_an_older_version_is_stale():
    """The exact defect this whole unit exists to fix: a profile extracted
    under an OLDER EXTRACTOR_VERSION than the code now running must be
    flagged, so a sweep can catch it up."""
    p = _profile(raw_text=CV_TEXT)
    # Build the hash the SAME way production did back when EXTRACTOR_VERSION
    # was "1" — never reimplement the digest, just run it under the old value.
    old_version_hash = tp._input_hash(CV_TEXT)  # computed at current version...
    # ...then monkeypatch the module constant DOWN so this hash is what an
    # older build would really have stored, and restore afterwards.
    real_version = tp.EXTRACTOR_VERSION
    try:
        tp.EXTRACTOR_VERSION = "1"
        old_version_hash = tp._input_hash(CV_TEXT)
    finally:
        tp.EXTRACTOR_VERSION = real_version

    p.cv_data.llm_input_hashes["cv"] = old_version_hash
    assert "cv" in tp.stale_extraction_inputs(p)


def test_no_raw_input_means_not_stale_for_that_key():
    """An empty shelf is not a broken one (rule #29). A profile with no
    LinkedIn text at all must never be flagged for LinkedIn staleness — there
    is nothing to extract, so treating it as backlog would make the sweep
    burn a call trying to re-read nothing, or worse, look broken."""
    p = _profile(raw_text=CV_TEXT)  # no linkedin_raw_text, no github, no about_me
    assert p.cv_data.linkedin_raw_text == ""

    stale = tp.stale_extraction_inputs(p)
    assert "linkedin" not in stale
    assert "github" not in stale
    assert "about_me" not in stale


def test_never_extracted_profile_with_raw_input_is_stale():
    """A profile that has raw input but NO stored hash at all (never run
    through the extractor once) must be treated as stale — it needs the
    FIRST extraction, which the sweep should also catch."""
    p = _profile(raw_text=CV_TEXT)
    assert p.cv_data.llm_input_hashes == {}

    assert "cv" in tp.stale_extraction_inputs(p)


def test_bumping_the_version_makes_a_previously_current_profile_stale():
    """THE REGRESSION THAT MATTERS. A profile extracted and hashed under
    today's EXTRACTOR_VERSION is current right now. The moment the version is
    bumped (a real prompt fix ships), that SAME stored hash must start
    reading as stale — this is the exact mechanism that silently broke four
    times in production, because nothing ever asked this question."""
    p = _profile(raw_text=CV_TEXT)
    p.cv_data.llm_input_hashes["cv"] = tp._input_hash(CV_TEXT)
    assert tp.stale_extraction_inputs(p) == [], "sanity: current before the bump"

    real_version = tp.EXTRACTOR_VERSION
    try:
        tp.EXTRACTOR_VERSION = str(int(real_version) + 1)
        assert "cv" in tp.stale_extraction_inputs(p), (
            "a version bump did not make a previously-current profile stale — "
            "this is the whole class of bug this file exists to catch"
        )
    finally:
        tp.EXTRACTOR_VERSION = real_version


def test_about_me_and_github_participate_too():
    """Staleness isn't CV-only — all four inputs the extractor reads
    (cv/linkedin/github/about_me) must be checked, or a sweep built on this
    helper would silently never catch up LinkedIn, GitHub or preferences."""
    p = _profile(
        raw_text=CV_TEXT,
        linkedin_raw_text=LI_TEXT,
        github_repos_brief=[{"name": "icu-tools"}],
    )
    p.preferences.about_me = "I like ICU work."
    # Everything current except about_me, which has never been hashed.
    p.cv_data.llm_input_hashes["cv"] = tp._input_hash(CV_TEXT)
    p.cv_data.llm_input_hashes["linkedin"] = tp._input_hash(LI_TEXT)
    p.cv_data.llm_input_hashes["github"] = tp._input_hash(
        {"repos": [{"name": "icu-tools"}], "bio": "", "readme": ""}
    )

    stale = tp.stale_extraction_inputs(p)
    assert stale == ["about_me"], stale


def test_current_profile_across_all_four_inputs_is_fully_clean():
    """A profile that is genuinely up to date on every input must come back
    completely clean — the sweep script relies on an empty list meaning
    'skip this user, nothing to do'."""
    p = _profile(
        raw_text=CV_TEXT,
        linkedin_raw_text=LI_TEXT,
        github_repos_brief=[{"name": "icu-tools"}],
    )
    p.preferences.about_me = "I like ICU work."
    p.cv_data.llm_input_hashes["cv"] = tp._input_hash(CV_TEXT)
    p.cv_data.llm_input_hashes["linkedin"] = tp._input_hash(LI_TEXT)
    p.cv_data.llm_input_hashes["github"] = tp._input_hash(
        {"repos": [{"name": "icu-tools"}], "bio": "", "readme": ""}
    )
    p.cv_data.llm_input_hashes["about_me"] = tp._input_hash("I like ICU work.")

    assert tp.stale_extraction_inputs(p) == []
