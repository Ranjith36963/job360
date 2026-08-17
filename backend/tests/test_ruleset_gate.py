"""The ruleset must be able to go GREEN before it is allowed to block.

WHY THIS TEST EXISTS
--------------------
Repository ruleset 20793798 (`main-production-gate`) names ELEVEN required status
checks and has `enforcement: "disabled"`. It stops nothing. The obvious next move —
flip it to `active` — carries a failure mode that is much worse than the current
state, and it is the same shape as the ten guards that shipped here unable to fire:

    a required status check is matched by CONTEXT NAME.

If a context names something no job ever reports, that check can never be satisfied.
GitHub does not error, does not warn, and does not time out: the PR sits at
"Expected — waiting for status to be reported" forever. On a repo with ONE admin and
an auto-deploying default branch, that is a self-inflicted outage.

So the checkable question is NOT "is enforcement enabled" — that is one boolean and it
passes forever once flipped. It is:

    would every required context ACTUALLY get reported, by the app the rule pins,
    on the commits this gate will judge?

THE HONEST COMPLICATION, WHICH IS THE WHOLE DIFFICULTY
------------------------------------------------------
A check can be legitimately absent from a given commit. Measured on this repo
(2026-08-17): 7 of the last 30 commits on `main` carry no CI at all, because only the
TIP of a push gets a workflow run — the commits underneath it never do. A guard that
samples arbitrary main commits and cries "never observed" would be crying wolf about
commits no gate will ever judge.

The population that matters is the one the rule acts on: PULL REQUEST HEAD COMMITS.
So presence is measured there, and "never observed anywhere" is a different verdict
from "absent from this particular commit". Getting that wrong in the permissive
direction wedges the repo; in the strict direction it cries wolf. Both are tested
below, and the negative controls are the half that matters.

WHAT THESE TESTS PIN
--------------------
  1. a required context nothing reports          -> WEDGE (test_dead_context_*)
  2. a required context reported by a DIFFERENT
     app than the rule pins                      -> WEDGE (test_wrong_app_*)
  3. a required context missing from a PR head   -> WEDGE (test_intermittent_*)
  4. the same absence on a non-PR main commit    -> SILENCE (negative control)
  5. an open PR with no checks at all            -> WEDGE (test_pr_with_no_checks)
  6. a good ruleset over good observations       -> SILENCE (negative control)
  7. a check that runs everywhere and is not
     required                                    -> named as a CANDIDATE, not a wedge
  8. a stale snapshot certifying a stale world   -> WEDGE (test_stale_snapshot)
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

# A hard import, deliberately. `pytest.importorskip` would turn a missing or
# broken guard into a SKIP — green-looking, and proving nothing. That is the
# precise shape of the ten guards that shipped here unable to fire.
import ruleset_gate  # noqa: E402

ACTIONS_APP = 15368
CODEQL_APP = 57789


# ── fixtures: the smallest world that can answer the question ────────────────


def _ruleset(contexts, enforcement="disabled", app=ACTIONS_APP, pins=None):
    """`pins` overrides the pinned integration_id for named contexts.

    Needed because the real ruleset pins EVERY context to one app, so a test that
    wants one mismatched context must not accidentally mismatch the other three —
    that would make the negative control pass for the wrong reason.
    """
    pins = pins or {}
    return ruleset_gate.Ruleset(
        ruleset_id=20793798,
        name="main-production-gate",
        enforcement=enforcement,
        required=tuple(ruleset_gate.RequiredContext(c, pins.get(c, app)) for c in contexts),
        bypass_actors=({"actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "always"},),
        include_refs=("~DEFAULT_BRANCH",),
    )


def _obs(label, names, *, is_pr_head=True, app=ACTIONS_APP, conclusion="success", number=None):
    return ruleset_gate.Observation(
        sha=f"{abs(hash(label)):040x}"[:40],
        label=label,
        is_pr_head=is_pr_head,
        number=number,
        author="Ranjith36963",
        runs=tuple(
            ruleset_gate.CheckRun(n, app, conclusion) if isinstance(n, str) else ruleset_gate.CheckRun(*n)
            for n in names
        ),
    )


GOOD = ["Backend (Python 3.12)", "Frontend (Node 20)", "offline-suite"]


def _world(pr_names_per_head, main_names_per_commit=(), open_prs=()):
    return ruleset_gate.World(
        fetched_at=dt.datetime.now(dt.timezone.utc),
        pr_heads=tuple(_obs(f"PR #{100+i}", n, number=100 + i) for i, n in enumerate(pr_names_per_head)),
        main_commits=tuple(_obs(f"main {i}", n, is_pr_head=False) for i, n in enumerate(main_names_per_commit)),
        open_prs=tuple(open_prs),
    )


def _codes(findings, level=None):
    return sorted(f.code for f in findings if level is None or f.level == level)


# ── 1. a required context nothing has ever reported ──────────────────────────


def test_dead_context_is_a_wedge():
    """The trap in one test: a context no job produces can never go green."""
    rs = _ruleset(GOOD + ["Analyze (python)"])
    world = _world([GOOD] * 5)
    findings = ruleset_gate.analyze(rs, world)
    assert "DEAD_CONTEXT" in _codes(findings, "wedge")
    hit = [f for f in findings if f.code == "DEAD_CONTEXT"]
    assert any("Analyze (python)" in f.detail for f in hit), hit


def test_dead_context_names_the_context_not_just_a_count():
    rs = _ruleset(GOOD + ["a-job-nobody-runs"])
    findings = ruleset_gate.analyze(rs, _world([GOOD] * 5))
    detail = " ".join(f.detail for f in findings if f.code == "DEAD_CONTEXT")
    assert "a-job-nobody-runs" in detail


# ── 2. right name, wrong app — the subtle one ────────────────────────────────


def test_context_reported_by_a_different_app_is_a_wedge():
    """`CodeQL` is reported here by app 57789, not by Actions (15368).

    Pinning it to the Actions integration means the rule waits for a report that
    the app it is pinned to will never send. Name-matching alone would call this
    fine — which is exactly the correct-looking-code-in-the-wrong-position shape.
    """
    rs = _ruleset(GOOD + ["CodeQL"], app=ACTIONS_APP)
    world = _world([GOOD + [("CodeQL", CODEQL_APP, "success")]] * 5)
    findings = ruleset_gate.analyze(rs, world)
    assert "WRONG_APP" in _codes(findings, "wedge")


def test_matching_app_is_not_flagged():
    """NEGATIVE CONTROL for the app check."""
    rs = _ruleset(GOOD + ["CodeQL"], pins={"CodeQL": CODEQL_APP})
    world = _world([GOOD + [("CodeQL", CODEQL_APP, "success")]] * 5)
    findings = ruleset_gate.analyze(rs, world)
    assert "WRONG_APP" not in _codes(findings)


# ── 3. the honest complication: absent HERE vs absent EVERYWHERE ─────────────


def test_intermittent_on_a_pr_head_is_a_wedge():
    """Absent from a PR head = that PR would hang forever. That is a wedge."""
    rs = _ruleset(GOOD)
    world = _world([GOOD, GOOD, GOOD[:-1], GOOD, GOOD])  # one head lacks offline-suite
    findings = ruleset_gate.analyze(rs, world)
    assert "INTERMITTENT" in _codes(findings, "wedge")
    assert any("offline-suite" in f.detail for f in findings if f.code == "INTERMITTENT")


def test_absence_on_a_non_pr_main_commit_stays_quiet():
    """NEGATIVE CONTROL, and the reason this guard is not a wolf-crier.

    Measured: 7 of 30 recent main commits have no checks, because only the tip of
    a push gets a run. No ruleset will ever judge those commits. If this test goes
    red, the guard has started reporting on a population the gate cannot act on.
    """
    rs = _ruleset(GOOD)
    world = _world([GOOD] * 5, main_names_per_commit=[[], GOOD, []])
    findings = ruleset_gate.analyze(rs, world)
    assert _codes(findings, "wedge") == [], findings


# ── 4. an open PR that can never satisfy anything ────────────────────────────


def test_open_pr_with_no_checks_is_a_wedge():
    """Live today: PR #342 by github-actions[bot] has zero check-runs.

    GitHub does not start workflows for commits pushed with the default
    GITHUB_TOKEN, so every PR the harness loops open has no checks at all. Under
    an active ruleset those PRs become permanently unmergeable — and they are the
    output of the automation, so the automation would silently stop landing.
    """
    rs = _ruleset(GOOD)
    stuck = _obs("PR #342", [], number=342)
    stuck = ruleset_gate.Observation(**{**stuck.__dict__, "author": "github-actions[bot]"})
    world = _world([GOOD] * 5, open_prs=[stuck])
    findings = ruleset_gate.analyze(rs, world)
    assert "OPEN_PR_NO_CHECKS" in _codes(findings, "wedge")
    assert any("342" in f.detail for f in findings if f.code == "OPEN_PR_NO_CHECKS")


def test_open_pr_that_is_merely_red_is_not_a_wedge():
    """NEGATIVE CONTROL. A failing check is the gate WORKING, not a wedge.

    Confusing 'this PR is blocked because a test failed' with 'this PR can never
    be judged' is the permissive/strict error in miniature.
    """
    rs = _ruleset(GOOD)
    red = _obs("PR #268", GOOD, number=268, conclusion="failure")
    world = _world([GOOD] * 5, open_prs=[red])
    findings = ruleset_gate.analyze(rs, world)
    assert "OPEN_PR_NO_CHECKS" not in _codes(findings)


# ── 5. the whole-world negative control ──────────────────────────────────────


def test_a_healthy_world_produces_no_wedge():
    """A checker that fires at any change is not a checker."""
    rs = _ruleset(GOOD)
    world = _world([GOOD] * 8, main_names_per_commit=[GOOD, [], GOOD])
    findings = ruleset_gate.analyze(rs, world)
    assert _codes(findings, "wedge") == [], findings


def test_renaming_the_ruleset_changes_nothing():
    """NEGATIVE CONTROL: an unrelated ruleset field must not move the verdict."""
    world = _world([GOOD] * 8)
    a = ruleset_gate.analyze(_ruleset(GOOD), world)
    rs2 = _ruleset(GOOD)
    rs2 = ruleset_gate.Ruleset(**{**rs2.__dict__, "name": "renamed-gate"})
    b = ruleset_gate.analyze(rs2, world)
    assert _codes(a) == _codes(b)


# ── 6. the reverse question: what runs and is NOT required ───────────────────


def test_a_check_that_always_runs_and_is_not_required_is_named():
    """`Chain wires (harness)` is the only thing proving the guards can fire.

    It reports on every PR head since it was added and is NOT in the required
    list. That is a candidate, reported as INFO — never as a wedge, because
    adding it is the owner's decision, not this script's.
    """
    rs = _ruleset(GOOD)
    world = _world([GOOD + ["Chain wires (harness)"]] * 5)
    findings = ruleset_gate.analyze(rs, world)
    assert "UNREQUIRED_CANDIDATE" in _codes(findings)
    assert all(f.level != "wedge" for f in findings if f.code == "UNREQUIRED_CANDIDATE")
    assert any("Chain wires (harness)" in f.detail for f in findings if f.code == "UNREQUIRED_CANDIDATE")


def test_a_brand_new_check_is_named_as_new_not_ignored():
    """`Chain wires (harness)` landed 2026-08-16 and has reported on the 3 most
    recent PR heads only. Judged by "present on ALL recent heads" it silently
    fails to be a candidate — and it is the ONLY check proving the guards can
    fire, so silently dropping it is the worst possible miss.

    New is not flaky. A check present on the most recent heads CONTIGUOUSLY and
    absent before them was added; a check that flickers on and off was not.
    """
    rs = _ruleset(GOOD)
    heads = [GOOD + ["Chain wires (harness)"]] * 3 + [GOOD] * 5  # newest first
    findings = ruleset_gate.analyze(rs, _world(heads))
    assert "NEW_CHECK" in _codes(findings)
    assert any("Chain wires (harness)" in f.detail for f in findings if f.code == "NEW_CHECK")
    assert all(f.level != "wedge" for f in findings if f.code == "NEW_CHECK")


def test_heads_are_ordered_by_merge_time_not_last_update():
    """The "newest heads" window is only meaningful if newest means MERGED last.

    Found by running --live: GitHub's `sort=updated` put PR #273 between #337 and
    #333, because a comment on an old PR bumps it. That interleaving broke the
    contiguity test and reported the genuinely-new `Chain wires (harness)` as
    flaky — a guard quietly answering a different question, again.
    """
    prs = [
        {"number": 273, "merged_at": "2026-08-10T00:00:00Z"},
        {"number": 338, "merged_at": "2026-08-16T23:17:27Z"},
        {"number": 333, "merged_at": "2026-08-16T08:30:41Z"},
        {"number": 337, "merged_at": "2026-08-16T15:16:24Z"},
    ]
    assert [p["number"] for p in ruleset_gate.newest_merged_first(prs)] == [338, 337, 333, 273]


def test_ordering_survives_a_missing_merge_time():
    """An unmerged entry must not crash the sort or jump to the front."""
    prs = [{"number": 1, "merged_at": "2026-08-16T00:00:00Z"}, {"number": 2, "merged_at": None}]
    assert [p["number"] for p in ruleset_gate.newest_merged_first(prs)] == [1, 2]


def test_a_flickering_check_is_not_called_new():
    """NEGATIVE CONTROL. On/off/on is flaky, not new, and must not be recommended."""
    rs = _ruleset(GOOD)
    heads = [GOOD + ["flaky"], GOOD, GOOD + ["flaky"], GOOD, GOOD, GOOD, GOOD, GOOD]
    findings = ruleset_gate.analyze(rs, _world(heads))
    detail = " ".join(f.detail for f in findings if f.code in {"NEW_CHECK", "UNREQUIRED_CANDIDATE"})
    assert "flaky" not in detail


def test_a_partially_present_check_is_reported_not_swallowed():
    """MEASURED, and the reason this category exists.

    `Chain wires (harness)` landed in PR #333 (merged 2026-08-16T08:30:41Z). PR
    #273 merged five minutes later at 08:35:57Z from a branch cut BEFORE that, so
    its head never ran the new job. The check is therefore present-absent-present
    and is neither "new" nor "flaky".

    From check-run data alone those two causes are indistinguishable, and the
    consequence is the same either way: require it today and every PR branched
    before the job existed hangs until it is rebased. So it is REPORTED, with
    the gap named, and never silently dropped and never recommended.
    """
    rs = _ruleset(GOOD)
    heads = [GOOD + ["Chain wires (harness)"], GOOD + ["Chain wires (harness)"], GOOD,
             GOOD + ["Chain wires (harness)"]] + [GOOD] * 4
    findings = ruleset_gate.analyze(rs, _world(heads))
    partial = [f for f in findings if f.code == "UNREQUIRED_PARTIAL"]
    assert partial, _codes(findings)
    assert any("Chain wires (harness)" in f.detail for f in partial)
    assert all(f.level != "wedge" for f in partial)
    # It must NOT be sold as safe to require.
    assert "UNREQUIRED_CANDIDATE" not in _codes(findings)
    assert "NEW_CHECK" not in _codes(findings)


def test_partial_report_names_which_heads_are_missing_it():
    """A fraction with no names is not actionable — the owner needs to know which
    branches to rebase."""
    rs = _ruleset(GOOD)
    heads = [GOOD + ["x"], GOOD, GOOD + ["x"]] + [GOOD] * 5
    findings = ruleset_gate.analyze(rs, _world(heads))
    detail = " ".join(f.detail for f in findings if f.code == "UNREQUIRED_PARTIAL")
    assert "PR #101" in detail, detail


def test_an_always_skipped_check_is_not_a_candidate():
    """NEGATIVE CONTROL. `auto-fix` and `Doc clutter` are skipped on every PR
    head. A skipped check satisfies a required check vacuously, so requiring one
    would install a guard that cannot fail — the exact bug this repo keeps
    shipping."""
    rs = _ruleset(GOOD)
    world = _world([GOOD + [("auto-fix", ACTIONS_APP, "skipped")]] * 5)
    findings = ruleset_gate.analyze(rs, world)
    detail = " ".join(f.detail for f in findings if f.code == "UNREQUIRED_CANDIDATE")
    assert "auto-fix" not in detail


def test_a_third_party_check_is_not_offered_as_a_candidate():
    """NEGATIVE CONTROL. semgrep reports from app 4314990; it cannot satisfy a
    rule pinned to the Actions app, so offering it would be advice that wedges."""
    rs = _ruleset(GOOD, app=ACTIONS_APP)
    world = _world([GOOD + [("semgrep-cloud-platform/scan", 4314990, "success")]] * 5)
    findings = ruleset_gate.analyze(rs, world)
    detail = " ".join(f.detail for f in findings if f.code == "UNREQUIRED_CANDIDATE")
    assert "semgrep" not in detail


# ── 7. a snapshot must not certify a stale world ─────────────────────────────


def test_stale_snapshot_is_a_wedge():
    """The offline check reads a recorded snapshot so CI needs no network. A
    snapshot nobody refreshes is a guard reporting on a world that has moved —
    which is how a green tick outlives the thing it was measuring."""
    rs = _ruleset(GOOD)
    world = _world([GOOD] * 5)
    old = ruleset_gate.World(**{**world.__dict__,
                               "fetched_at": dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=45)})
    findings = ruleset_gate.analyze(rs, old, max_snapshot_age_days=14)
    assert "STALE_SNAPSHOT" in _codes(findings, "wedge")


def test_fresh_snapshot_is_quiet():
    """NEGATIVE CONTROL for staleness."""
    rs = _ruleset(GOOD)
    findings = ruleset_gate.analyze(rs, _world([GOOD] * 5), max_snapshot_age_days=14)
    assert "STALE_SNAPSHOT" not in _codes(findings)


# ── 8. enforcement is reported, and is not the verdict ───────────────────────


def test_enforcement_disabled_is_reported_but_is_not_a_wedge_by_default():
    """`disabled` is today's deliberate state. Reporting it as a build failure
    would train the owner to ignore this guard. It is INFO until asked."""
    rs = _ruleset(GOOD, enforcement="disabled")
    findings = ruleset_gate.analyze(rs, _world([GOOD] * 5))
    assert "ENFORCEMENT_OFF" in _codes(findings)
    assert all(f.level != "wedge" for f in findings if f.code == "ENFORCEMENT_OFF")


def test_require_active_makes_enforcement_a_wedge():
    rs = _ruleset(GOOD, enforcement="disabled")
    findings = ruleset_gate.analyze(rs, _world([GOOD] * 5), require_active=True)
    assert "ENFORCEMENT_OFF" in _codes(findings, "wedge")


# ── 9. the verdict the owner actually asked for ──────────────────────────────


def test_safe_to_enable_is_false_when_anything_would_wedge():
    rs = _ruleset(GOOD + ["Analyze (python)"])
    assert ruleset_gate.safe_to_enable(ruleset_gate.analyze(rs, _world([GOOD] * 5))) is False


def test_safe_to_enable_is_true_on_a_clean_world():
    rs = _ruleset(GOOD)
    assert ruleset_gate.safe_to_enable(ruleset_gate.analyze(rs, _world([GOOD] * 8))) is True


# ── 10. scoping the ALARM without hiding the FINDING ─────────────────────────


def test_pr_gate_scope_does_not_go_red_for_the_open_pr_queue():
    """A PR author cannot fix a bot PR sitting in the queue.

    Firing the PR gate at them is how an alarm gets switched off. So the PR-gate
    scope excludes queue-shaped findings from the EXIT CODE — and only from the
    exit code. Nothing is hidden from the report.
    """
    rs = _ruleset(GOOD)
    stuck = ruleset_gate.Observation(**{**_obs("PR #342", [], number=342).__dict__,
                                        "author": "github-actions[bot]"})
    findings = ruleset_gate.analyze(rs, _world([GOOD] * 8, open_prs=[stuck]))
    assert "OPEN_PR_NO_CHECKS" in _codes(findings, "wedge")
    assert ruleset_gate.blocking(findings, scope="ruleset") == []
    assert ruleset_gate.blocking(findings, scope="all") != []


def test_pr_gate_scope_still_goes_red_for_a_dead_context():
    """The scope must not become a way to pass. A dead context is exactly what a
    commit CAN cause, so it must still block the PR gate."""
    rs = _ruleset(GOOD + ["nothing-emits-this"])
    findings = ruleset_gate.analyze(rs, _world([GOOD] * 8))
    assert ruleset_gate.blocking(findings, scope="ruleset") != []


def test_pr_gate_scope_still_goes_red_for_a_stale_snapshot():
    rs = _ruleset(GOOD)
    w = _world([GOOD] * 8)
    old = ruleset_gate.World(**{**w.__dict__,
                                "fetched_at": dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=99)})
    findings = ruleset_gate.analyze(rs, old, max_snapshot_age_days=14)
    assert ruleset_gate.blocking(findings, scope="ruleset") != []


# ── 11. the drill must be real ───────────────────────────────────────────────


def test_self_drill_passes():
    """The drill breaks the guard on purpose and demands it goes red. If the
    drill itself can be satisfied without the guard detecting anything, this
    file is guard number twelve."""
    assert ruleset_gate.self_drill() == 0


def test_subprocess_calls_pass_encoding():
    """`gh` prints em-dashes. text=True alone decodes with the Windows locale and
    raises UnicodeDecodeError — measured on this machine while writing this
    guard. There is a repo guard for this; this test is the local one."""
    src = (REPO / "scripts" / "ruleset_gate.py").read_text(encoding="utf-8")
    for i, line in enumerate(src.splitlines(), 1):
        if "subprocess.run" in line:
            window = "\n".join(src.splitlines()[i - 1: i + 8])
            assert 'encoding="utf-8"' in window, f"line {i}: subprocess.run without encoding"
            assert 'errors="replace"' in window, f"line {i}: subprocess.run without errors="
