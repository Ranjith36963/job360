"""A prompt improvement must reach users who already uploaded.

MEASURED ON A LIVE PROFILE, 2026-08-08. The LLM cost cache keyed on the INPUT
alone: same CV text -> skip the paid pass. Correct for cost, wrong for
correctness — a prompt fix could never reach an existing user, and re-uploading
the same CV could not help either (identical text, identical hash).

The damage was real and visible: the CV prompt already tells the LLM to mine
skills demonstrated in prose, yet a real CV naming "predictive maintenance",
"fraud detection", "IoT", "RUL" and "multi-agent workflow" in its experience
bullets had NONE of them in cv.skills. The improved prompt had simply never run
over that CV.

Folding EXTRACTOR_VERSION into the digest fixes it: bumping the version
invalidates every cached hash exactly once, so each profile re-extracts under
the better prompt and then caches again.
"""
from __future__ import annotations

from src.services.profile import two_pass
from src.services.profile.models import CVData


class TestExtractorVersionIsPartOfTheCacheKey:
    def test_same_input_same_version_is_cached(self) -> None:
        """The cost guard must still work — this is the whole point of caching."""
        cv = CVData(raw_text="CV TEXT")
        cv.llm_input_hashes["cv"] = two_pass._input_hash("CV TEXT")
        assert two_pass._already_read(cv, "cv", "CV TEXT") is True

    def test_bumping_the_version_invalidates_the_cache(self, monkeypatch) -> None:
        """A prompt improvement re-reads inputs the system has already seen."""
        cv = CVData(raw_text="CV TEXT")
        cv.llm_input_hashes["cv"] = two_pass._input_hash("CV TEXT")
        assert two_pass._already_read(cv, "cv", "CV TEXT") is True

        monkeypatch.setattr(two_pass, "EXTRACTOR_VERSION", "999")
        assert two_pass._already_read(cv, "cv", "CV TEXT") is False, (
            "a prompt bump must re-run the pass, or improvements never reach "
            "existing users"
        )

    def test_a_digest_stored_before_versioning_no_longer_matches(self) -> None:
        """Legacy rows re-extract exactly once — the intended migration."""
        import hashlib

        legacy = hashlib.sha256(b"CV TEXT").hexdigest()  # the OLD formula
        cv = CVData(raw_text="CV TEXT")
        cv.llm_input_hashes["cv"] = legacy
        assert two_pass._already_read(cv, "cv", "CV TEXT") is False

    def test_changed_input_still_invalidates(self) -> None:
        """The original behaviour is untouched: new text -> re-read."""
        cv = CVData(raw_text="OLD")
        cv.llm_input_hashes["cv"] = two_pass._input_hash("OLD")
        assert two_pass._already_read(cv, "cv", "NEW TEXT") is False

    def test_empty_input_is_never_considered_read(self) -> None:
        cv = CVData()
        assert two_pass._already_read(cv, "cv", "") is False

    def test_non_string_inputs_stay_stable(self) -> None:
        """github_repos_brief is a list of dicts; key order must not matter."""
        a = [{"name": "r1", "language": "Python"}]
        b = [{"language": "Python", "name": "r1"}]
        assert two_pass._input_hash(a) == two_pass._input_hash(b)
