"""Tests for scripts/user_journey_audit.py — the per-user pipeline walker.

WHY THESE EXIST. Issue #198 ran for 9 straight days saying user `7edd5b59`
was "BROKEN at feed - only 0 jobs reached this user". The feed writer was
never at fault. That account never confirmed its email, and `POST /api/search`
is gated on a confirmed email (`src/api/routes/search.py:177` ->
`auth_deps.require_verified_user`, `src/api/auth_deps.py:105-128`). The feed is
only ever written by a search or by a profile-change re-score, so an unconfirmed
account CANNOT have feed rows. Calling that "broken at feed" points the reader
at a bug that does not exist.

No DB, no HTTP — the audit's stage logic is pure given a fake cursor.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# The audit lives at the REPO ROOT (scripts/), not backend/scripts/, so it is
# not importable by name from here. Load it by path — module-level imports are
# stdlib only (psycopg is imported inside main()), so this is offline-safe.
_AUDIT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "user_journey_audit.py"


def _load_audit() -> Any:
    spec = importlib.util.spec_from_file_location("_user_journey_audit", _AUDIT_PATH)
    assert spec and spec.loader, f"cannot load {_AUDIT_PATH}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def audit() -> Any:
    return _load_audit()


class FakeCursor:
    """Answers the audit's queries from a dict, in the order it asks them.

    The audit only ever runs two shapes: a scalar `SELECT count(...)`/`max(...)`
    and the one `SELECT cv_data, preferences` row. Dispatch on the SQL text.
    """

    def __init__(self, *, cv: dict, prefs: dict, counts: dict[str, int]) -> None:
        self._cv = cv
        self._prefs = prefs
        self._counts = counts
        self._result: tuple = ()

    def execute(self, sql: str, params: tuple = ()) -> None:
        s = " ".join(sql.split())
        if "cv_data, preferences" in s:
            self._result = (json.dumps(self._cv), json.dumps(self._prefs))
        elif "FROM user_profiles" in s:
            self._result = (self._counts.get("profile_rows", 0),)
        elif "score >=" in s:
            self._result = (self._counts.get("visible", 0),)
        elif "llm_fit_score" in s:
            self._result = (self._counts.get("llm_verdicts", 0),)
        elif "count(DISTINCT score)" in s:
            self._result = (self._counts.get("distinct_scores", 0),)
        elif "max(score)" in s:
            self._result = (self._counts.get("max_score", 0),)
        elif "FROM user_feed" in s:
            self._result = (self._counts.get("feed_rows", 0),)
        else:  # pragma: no cover — a new query shape must not pass silently
            raise AssertionError(f"unexpected query: {s}")

    def fetchone(self) -> tuple:
        return self._result


FULL_CV = {"raw_text": "x" * 5000, "skills": ["a"] * 142, "job_titles": ["t1", "t2"]}
FULL_PREFS = {"target_job_titles": ["t1", "t2"], "additional_skills": ["a"] * 142}


def _cursor(feed_rows: int = 0, **counts: int) -> FakeCursor:
    base = {"profile_rows": 1, "feed_rows": feed_rows}
    base.update(counts)
    return FakeCursor(cv=FULL_CV, prefs=FULL_PREFS, counts=base)


class TestUnconfirmedEmail:
    """The live #198 case: full profile, zero feed, email never confirmed."""

    def test_unconfirmed_user_is_not_reported_broken(self, audit: Any) -> None:
        stage, verdict, facts = audit.audit_user(
            _cursor(feed_rows=0), "7edd5b59", "j@example.com", verified=False
        )
        assert verdict == "not-started", (
            "an account that never confirmed its email cannot reach the feed by "
            "design — calling it BROKEN sends the reader hunting a feed bug"
        )
        assert stage == "email-not-confirmed"
        assert facts["email_confirmed"] is False

    def test_unconfirmed_user_is_still_visible_not_swallowed(self, audit: Any) -> None:
        """Silently dropping them would hide a real signup-funnel leak."""
        _stage, _verdict, facts = audit.audit_user(
            _cursor(feed_rows=0), "7edd5b59", "j@example.com", verified=False
        )
        # the profile facts are still gathered, so the report can say what they
        # DID give us before they stalled at the door
        assert facts["skills"] == 142
        assert facts["cv_chars"] == 5000

    def test_no_profile_still_reads_as_signup(self, audit: Any) -> None:
        cur = FakeCursor(cv={}, prefs={}, counts={"profile_rows": 0})
        stage, verdict, _f = audit.audit_user(cur, "d5a1b8c2", "d@example.com", verified=False)
        assert (stage, verdict) == ("signup", "not-started")


class TestConfirmedEmailStillDetectsRealBreaks:
    """The fix must not blind the audit for people who DID confirm."""

    def test_confirmed_user_with_empty_feed_is_broken(self, audit: Any) -> None:
        stage, verdict, _f = audit.audit_user(
            _cursor(feed_rows=0), "ad13c75d", "r@gmail.com", verified=True
        )
        assert (stage, verdict) == ("feed", "broken")

    def test_confirmed_user_with_flat_scores_is_broken_at_scoring(self, audit: Any) -> None:
        stage, verdict, _f = audit.audit_user(
            _cursor(feed_rows=500, max_score=0, distinct_scores=1),
            "ad13c75d",
            "r@gmail.com",
            verified=True,
        )
        assert (stage, verdict) == ("scoring", "broken")

    def test_healthy_confirmed_user_completes(self, audit: Any) -> None:
        stage, verdict, facts = audit.audit_user(
            _cursor(
                feed_rows=7300,
                max_score=92,
                distinct_scores=60,
                visible=7300,
                llm_verdicts=158,
            ),
            "ad13c75d",
            "r@gmail.com",
            verified=True,
        )
        assert (stage, verdict) == ("complete", "ok")
        assert facts["visible_pct"] == 100
