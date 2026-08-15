"""The journey probe's preflight message must name the RIGHT knob.

WHY THIS TEST EXISTS.

`journey.yml` has been red every night with one line:

    ::error::JOURNEY_API is not set, so the journey probe has nothing to walk.

That sentence is true and still misleading, in the exact way that costs a
person an evening: `JOURNEY_API` is a repo **variable** (`vars.JOURNEY_API`,
journey.yml), not a secret. Someone reading "is not set" reaches for
`gh secret set JOURNEY_API` — which succeeds, prints nothing alarming, and
changes NOTHING, because `vars.` and `secrets.` are different namespaces. The
next night is red again with the same message.

A guard that sends you to the wrong command is worse than no guard: it costs a
cycle AND spends the trust you need for the next alarm.

Two further facts the message must not hide, because each one is a separate
night lost to discovering it:

  * the probe MUTATES — it registers accounts, uploads CVs, and runs real
    searches (scripts/journey_probe.py) — so the target cannot be production;
  * the default corpus `User_info/` is gitignored real-person PII, so it is
    never on a runner. Setting the variable alone still cannot make this green.

These are text assertions on a YAML file, which is unusual for a test — but
this message IS the interface between the harness and the one person who can
fix it. Nothing else checks it, and it was wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
JOURNEY_YML = REPO / ".github" / "workflows" / "journey.yml"


@pytest.fixture(scope="module")
def guard_text() -> str:
    """The shell body of the step that fires when the target is unset."""
    assert JOURNEY_YML.is_file(), f"missing workflow: {JOURNEY_YML}"
    doc = yaml.safe_load(JOURNEY_YML.read_text(encoding="utf-8"))
    steps = doc["jobs"]["walk"]["steps"]
    guards = [
        s.get("run", "")
        for s in steps
        if "::error::" in str(s.get("run", "")) and "JOURNEY_API" in str(s.get("run", ""))
    ]
    assert guards, "no step in journey.yml raises a ::error:: about JOURNEY_API"
    return "\n".join(guards)


class TestGuardNamesTheRightKnob:
    def test_gives_the_exact_command_that_works(self, guard_text: str) -> None:
        # `gh variable set` is the only command that populates `vars.`.
        assert "gh variable set JOURNEY_API" in guard_text, (
            "the guard must print the exact command that fixes it, not a description"
        )

    def test_says_variable_not_secret(self, guard_text: str) -> None:
        assert "variable" in guard_text.lower()
        # It must actively warn, because `gh secret set` is the intuitive guess
        # and it fails SILENTLY — the wrong namespace, no error, no change.
        assert "gh secret set" in guard_text, (
            "the guard must name the wrong command explicitly and say it does nothing; "
            "leaving it unsaid is what caused the near-miss"
        )


class TestGuardDoesNotHideTheOtherBlockers:
    def test_warns_the_probe_mutates(self, guard_text: str) -> None:
        low = guard_text.lower()
        assert "creates accounts" in low or "registers accounts" in low, (
            "the guard must say the probe WRITES, so nobody points it at production"
        )

    def test_names_the_corpus_blocker(self, guard_text: str) -> None:
        # Setting the variable alone leaves the run red on the NEXT blocker.
        # A guard that reveals one blocker per night is a guard that wastes days.
        assert "JOURNEY_CORPUS" in guard_text and "User_info" in guard_text, (
            "the guard must say the default corpus is gitignored and never on a runner"
        )


class TestCorpusReallyIsAbsentFromTheRepo:
    """The claim the guard prints is only worth printing if it is true.

    A checkout on a runner sees exactly what git tracks. If nothing under
    `User_info/` is tracked, the probe's own corpus check
    (`no CVs found ... nothing to walk`, scripts/journey_probe.py) fires on
    every runner, forever — no variable can change that.
    """

    def test_nothing_under_user_info_is_tracked(self) -> None:
        import subprocess

        out = subprocess.run(
            ["git", "ls-files", "--", "User_info"],
            cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert out == "", (
            "User_info/ is now tracked — real CVs may have been committed (PII), "
            "or the guard's corpus warning is stale. Check both."
        )

    def test_user_info_is_gitignored(self) -> None:
        ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
        assert "User_info/" in ignore, ".gitignore no longer excludes User_info/"
