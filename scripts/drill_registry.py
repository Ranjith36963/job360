#!/usr/bin/env python3
"""The drill registry — every guard must declare how to make itself fail.

WHY THIS EXISTS
---------------
Ten guards shipped here unable to fire, across four consecutive rounds of fixing:

    round 1: six guards shipped unable to fire
    round 2: fixed them -> guards SEVEN and EIGHT were found INSIDE the fixes
    round 3: fixed those -> guards NINE and TEN were found inside those
    round 4: ...

Every one was correct-looking code in the wrong position, reporting success about a
DIFFERENT QUESTION than the one that mattered:

  * a size check that swallowed its own exit code
  * a sed range whose anchor had been deleted, silently feeding 82 wrong lines
  * a route test that read a mutable global, so its verdict changed with test ORDER
  * an LLM alarm sitting below the `return` that fires first
  * a freshness guard querying a column neither table has
  * an alert path whose missing key made it exit 0, GREEN, for weeks
  * DECISION_MARKER printed after main() had already returned — passed every unit
    test and was unreachable in production
  * a self-test that grepped for a string, so deleting the only authorisation call
    left it fully green

Round five would find guard eleven. That is not a strategy.

THE ONE IDEA
------------
A guard is not trusted because it EXISTS. It is trusted because someone has WATCHED
IT GO RED. Until now that depended on a human remembering — which failed ten times.

This makes it structural:

  1. DISCOVER   every script a workflow actually invokes. Adding a guard to CI is
                what triggers the requirement, so you cannot get one in by forgetting
                to register it.
  2. REQUIRE    each discovered guard to be declared here, as either `drilled`
                (it has a self-drill, and we run it) or `owed` (we know it cannot be
                fire-tested yet, and WHY). An undeclared guard fails the build.
  3. RUN        every declared drill, and demand the guard actually goes RED.

WHAT `owed` IS FOR, AND WHAT IT IS NOT
--------------------------------------
Most guards here read production — Sentry, the prod database, the GitHub API. Their
drills need recorded fixtures that do not exist yet. Pretending otherwise would make
this file the eleventh guard: a green tick asserting something nobody checked.

So `owed` is honest debt, and it is LOUD — every run prints the count and the list.
What `owed` does NOT do is let a NEW guard in quietly. New guard, no entry, red build.
That is the whole mechanism: it cannot stop you shipping an undrilled guard, but it
can stop you doing it by accident.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# A drill must finish INSIDE the CI job budget. If it does not, the job is
# cancelled and the DRILL TIMEOUT line below is never printed -- a guard that
# cannot report its own failure, which is the exact class this file exists to
# end. Keep this comfortably under `timeout-minutes` on the `chain` job.
DRILL_TIMEOUT_S = 240

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

# A workflow line invoking a repo script. Both roots are real: ci.yml runs
# `python scripts/mypy_ratchet.py` under `working-directory: backend`, while
# checker-scorecard.yml spells the same shape `python backend/scripts/shelf_xray.py`
# from the repo root. Discovery must resolve both or it silently under-reports —
# and a discovery step that under-reports is exactly the failure this file exists
# to stop.
_SCRIPT_REF = re.compile(r"(?:^|[\s\"'/=])((?:backend/)?scripts/[a-zA-Z_0-9./-]*[a-zA-Z_0-9-]+\.(?:py|sh))")


@dataclass
class Guard:
    """One entry in the registry."""

    status: str  # "drilled" | "owed"
    reason: str = ""  # required for owed: why it cannot be drilled YET
    drill: list[str] = field(default_factory=list)  # argv, run from ROOT
    since: str = ""  # date the debt was taken on, for owed


# ─────────────────────────────────────────────────────────────────────────────
# THE REGISTRY
#
# Ordering is alphabetical, not by importance — importance is a judgement that
# rots, and a sorted list makes an accidental deletion visible in a diff.
# ─────────────────────────────────────────────────────────────────────────────

REGISTRY: dict[str, Guard] = {
    # ── drilled: watched going red, on demand, in CI ────────────────────────
    "scripts/chain_check.py": Guard(
        status="drilled",
        # Breaks three wires by three different mechanisms (renamed output,
        # misspelt dispatch target, literal the producer cannot emit) and
        # includes a negative control that must stay quiet. 4/4 as of the
        # commit that added it.
        drill=[sys.executable, "scripts/chain_check.py", "--drill"],
    ),
    # ── FOUR GUARDS THAT ARRIVED WITH THE CAGE (#348) ────────────────────────
    # It was six. Two came out again, and finding out why was the whole point.
    # `discover()` read `.github/merge-policy.yml` as if it RAN the scripts it
    # names -- but that file is a LIST, and it names scripts precisely because
    # they are sensitive. So `lane.py` and `data_only.py` looked invoked, got
    # declared, and the registry certified two guards that NOTHING RUNS.
    # With the policy file excluded they surface as STALE ENTRY, which is true:
    # lane.py has no consumer until PR #356 wires it into pr-advisor.yml, and
    # data_only.py is imported by merge_cage rather than run by a workflow.
    # Whoever wires either one adds its entry back, with a drill.
    # Written against the OLD main. #348 landed pr-advisor.yml, revert-main.yml,
    # post-merge-watch.yml and merge-policy.yml, each invoking a script this file
    # had never heard of.
    #
    # Found by the `--count-owed` validation added in this same PR: unvalidated,
    # the count is accepted on exit 0, so this PR would have merged a ratchet
    # reading that silently ignored six guards. A loud stop beat a quiet lie.
    #
    # All six are `drilled` -- each --drill asserts real cases and was run under
    # CI conditions. NOT claimed: that they fail under MUTATION (issue #359).
    "scripts/merge_cage.py": Guard(
        status="drilled",
        # Decides what may reach production without the owner. Its drill refuses a
        # scoring change, an auth change, an infra change, an edit to its own
        # guards, an unrecognised path, and a ratchet going backwards.
        drill=[sys.executable, "scripts/merge_cage.py", "--drill"],
    ),
    "scripts/gate_wiring_check.py": Guard(
        status="drilled",
        # Checks the cage is wired somewhere it could actually stop something.
        # Watched red this session on a real judging call with no --baseline.
        drill=[sys.executable, "scripts/gate_wiring_check.py", "--drill"],
    ),
    "scripts/rollback_gear.py": Guard(
        status="drilled",
        # THE UNDO. The product lane's speed is borrowed against this working:
        # "speed comes from being able to UNDO, not from being sure."
        drill=[sys.executable, "scripts/rollback_gear.py", "--drill"],
    ),
    "scripts/revert_gear.py": Guard(
        status="drilled",
        # The other undo -- what revert-main.yml runs to take main back.
        drill=[sys.executable, "scripts/revert_gear.py", "--drill"],
    ),
    # ── THE TWO GUARDS THIS PR WIRES UP ──────────────────────────────────────
    # A guard and its declaration land together, always. #357 removed both of
    # these as STALE ENTRY because nothing ran them; this PR is what gives them a
    # consumer, so this PR is what declares them. Splitting those two acts is how
    # a workflow ends up invoking a script no registry knows about -- which
    # `--count-owed` then refuses, which stops the ratchet, which stops the cage.
    # Verified before declaring: both drills exit 0 with GIT_CONFIG_GLOBAL and
    # GIT_CONFIG_SYSTEM set to /dev/null, i.e. on a runner with no git identity.
    "scripts/lane.py": Guard(
        status="drilled",
        # Turns a path into a lane. Watched RED repeatedly this session --
        # restoring the basename fallback, un-protecting the undo gears, and
        # adding the tempting `**/README.md` one-liner all turn it red.
        drill=[sys.executable, "scripts/lane.py", "--drill"],
    ),
    "scripts/repairable.py": Guard(
        status="drilled",
        # Answers "may the auto-fixer edit this file?" for `pr-repair.yml`,
        # which used to answer it with the hardcoded regex `^(backend|frontend)/`.
        # Watched RED four times while being written, every time for a real
        # reason: against `main` before #444 it called
        # `backend/src/api/routes/auth.py` REPAIRABLE (that deny lived only in
        # merge_cage.py, not in the policy), and an adversarial review found
        # `pyrightconfig.json`, `.coderabbit.yaml` and `backend/scripts/` still
        # open — three ways to go green by moving the line instead of fixing the
        # code. Delete the SELF list and the CI definition becomes editable by
        # the very agent CI is judging.
        drill=[sys.executable, "scripts/repairable.py", "--drill"],
    ),
    "scripts/stale_path_check.py": Guard(
        status="drilled",
        # Asks git which paths a change renamed and fails if any tracked file
        # still names an old one. The guard that makes a file MOVE safe.
        drill=[sys.executable, "scripts/stale_path_check.py", "--drill"],
    ),
    "scripts/worktree_census.py": Guard(
        status="drilled",
        # Sorts 62 worktrees / 209 local branches into SAFE / KEEP / ASK. The
        # danger is asymmetric: a wrong KEEP costs disk, a wrong SAFE costs the
        # owner unsaved work. 20 worktrees hold uncommitted work and 14 of those
        # sit on branches ALREADY MERGED, so the obvious "merged => junk" rule
        # deletes 14 folders of real work. The drill plants work in a merged
        # worktree five different ways (modified / untracked / staged / a live
        # .env / backend/data) and demands the classifier stop calling it safe,
        # plus two negative controls: a genuinely clean merged worktree stays
        # SAFE, and an unrelated newcomer does not move an existing verdict.
        #
        # ONLY THE DRILL RUNS IN CI, AND THAT IS NOT AN OVERSIGHT. The drill
        # builds its own git repo in a temp dir, so it is portable and
        # network-free. The CENSUS reads D:\ and a runner cannot see D:\ --
        # pointing this step at the checkout would census one clean worktree and
        # one branch, report all-green, and be the eleventh dead guard.
        drill=[sys.executable, "scripts/worktree_census.py", "--drill"],
    ),
    "scripts/ruleset_gate.py": Guard(
        status="drilled",
        # The org-level version of this repo's signature bug. Ruleset
        # `main-production-gate` names 11 required status checks and sits at
        # enforcement=disabled, so it stops nothing -- and a required check is
        # matched by CONTEXT NAME, so flipping it on with one name that no job
        # reports means nothing can ever merge, silently, forever.
        #
        # The drill breaks the checker eight ways (dead context, right name but
        # wrong app, a context missing from one PR head, an open PR with no
        # checks at all, stale evidence, a new check mistaken for a flaky one, a
        # gap swallowed instead of named, a scope that mutes too much) and
        # demands it name each. Six negative controls must stay silent -- the
        # important one being a context absent from a NON-PR main commit, which
        # is legitimate: only the tip of a push gets a run, so 7 of 30 recent
        # main commits have no CI and no ruleset will ever judge them. Confusing
        # that with a dead context is how this guard would cry wolf until it was
        # switched off. Offline and network-free; it reads a recorded snapshot.
        drill=[sys.executable, "scripts/ruleset_gate.py", "--drill"],
    ),
    "scripts/check_alert_paths.py": Guard(
        status="drilled",
        # Guards the two properties an alerting path must have: it may not flip
        # the verdict of the work it describes (a failing mid-job Slack step
        # makes `failure()` true for every later step), and it may not die with
        # the step it quotes (a gh 403 means the output never exists, every
        # branch skips, and the outage goes unannounced). Both were live bugs
        # here. Brute-forces the gate expressions rather than pattern-matching a
        # known-bad string, so a NEW way of writing the same bug is still caught.
        drill=[sys.executable, "scripts/check_alert_paths.py", "--drill"],
    ),
    "scripts/check_workflow_slack_wiring.py": Guard(
        status="drilled",
        # Checks the CALLERS, not the sender: every slack step passes a token, a
        # non-empty title and a real channel, and every `run:` block still parses
        # under `bash -n`.
        #
        # WAS `owed`, on a WINDOWS measurement. The drill applies 3 mutations and
        # re-runs the whole check for each, ~300 `bash -n` spawns a pass. On
        # Windows one pass measured ~150s and three could not finish inside
        # DRILL_TIMEOUT_S (240s); reproduced here, it blew past a 120s ceiling.
        # The budget is enforced where CI runs it -- on Linux, where a spawn costs
        # orders of magnitude less. Promoted rather than accepted as debt, because
        # `owed drills` may only FALL and leaving it owed takes the count 15 -> 16.
        # CI's `--run-drills` is the proof: if Linux cannot finish it either, that
        # step goes red and the claim is withdrawn.
        drill=[sys.executable, "scripts/check_workflow_slack_wiring.py", "--drill"],
    ),
    "scripts/slack_transition.py": Guard(
        status="drilled",
        # The volume rule: announce the TRANSITION, not the state. Its drill
        # breaks the decision table three ways — red->red starts announcing (the
        # spam bug), red->green goes silent (he never learns it recovered), and
        # an unreadable previous state gets GUESSED instead of refused — and
        # each must go red. Plus a negative control (reword a human-readable
        # reason string) that must stay quiet, because a checker that fires at
        # any change is not a checker. Offline: no token, no network.
        drill=[sys.executable, "scripts/slack_transition.py", "--drill"],
    ),
    "scripts/review_debt.py": Guard(
        status="drilled",
        # Reads unresolved CodeRabbit threads. Two failure modes, both drilled.
        # (1) It handles untrusted text: 55 of 63 unresolved comment bodies
        # carry a `Prompt for AI Agents` block — imperative instructions aimed
        # at an agent, sitting in a pull request. The drill plants an injection
        # four ways and demands none reach a model.
        # (2) It must NEVER become a gate. All six merged PRs with findings
        # merged with them open, so a threads-must-be-resolved condition would
        # have refused six of six and been switched off in a week. "63 real
        # findings still exit 0" is drilled as a NEGATIVE CONTROL, held to the
        # same standard as the deliberate breaks — teeth here are a regression.
        # 12/12 as of PR #346. CodeRabbit caught this line claiming 9/9 when
        # self_drill() already appended 10 results — a declared count that
        # drifts is the registry telling a story about a guard instead of
        # measuring it. Cases 10 and 11 (the truncation refusal and its
        # negative control) took it to 12. Network-free: runs off a recorded
        # GraphQL payload in scripts/fixtures/review_threads/.
        drill=[sys.executable, "scripts/review_debt.py", "--drill"],
    ),
    "scripts/encoding_guard.py": Guard(
        status="drilled",
        # Born from the cage crashing on 3 of 6 PRs: text=True decodes with the
        # machine's locale, so an em-dash in a PR title killed the reader thread
        # and the cage stopped answering instead of refusing. 36 more instances
        # were live across 18 files. Ratcheted by file+count, never by line
        # number -- a line-number baseline rots on the next import and gets
        # switched off.
        drill=[sys.executable, "scripts/encoding_guard.py", "--drill"],
    ),
    "scripts/drill_registry.py": Guard(
        status="drilled",
        # This file drills itself. A registry that cannot fail is the eleventh
        # guard, and it would be the worst one — it would certify the other ten.
        drill=[sys.executable, "scripts/drill_registry.py", "--drill"],
    ),
    # ── THE GUARD THIS PR WIRES UP (docs/plans/2026-09-04-url-fetch) ────────
    # A guard and its declaration land together, always — same rule as the
    # lane.py/repairable.py pair above. The guard on the ONE route that makes
    # outbound requests to a URL a stranger chose. Ten mutations, each a real
    # bypass, plus a negative control: a guard that denies every host passes
    # all ten breaks and is useless. Offline — the resolver is injected, no
    # DNS, no sockets.
    "scripts/ssrf_drill.py": Guard(
        status="drilled",
        drill=[sys.executable, "scripts/ssrf_drill.py", "--drill"],
    ),
    # ── owed: real guards, no fire-test yet, and here is exactly why ────────
    "scripts/absence_check.py": Guard(
        status="owed",
        reason="reads the live GitHub API for loops that have stopped running; a drill "
        "needs a recorded `gh run list` fixture, which does not exist yet",
        since="2026-08-16",
    ),
    "scripts/already_built.py": Guard(
        status="owed",
        reason="offline and therefore the cheapest debt here to clear — a drill can "
        "plant a duplicate-looking file and demand it is named",
        since="2026-08-16",
    ),
    "scripts/checker_scorecard.py": Guard(
        status="owed",
        reason="measures whether the other checkers fired; it silently measured 0 and "
        "then stopped entirely on 2026-08-10, which is precisely the failure a "
        "drill would have caught — highest-value debt in this list",
        since="2026-08-16",
    ),
    "scripts/data_invariants.py": Guard(
        status="owed",
        reason="queries the production database; a drill needs a seeded throwaway "
        "schema, not prod, and this repo has no staging",
        since="2026-08-16",
    ),
    "scripts/doc_clutter_check.py": Guard(
        status="owed",
        reason="offline; a drill can plant a cluttered doc tree in a temp copy",
        since="2026-08-16",
    ),
    "scripts/doc_sync_check.py": Guard(
        status="drilled",
        # Owed since 2026-08-16 for exactly the reasons below; PAID 2026-08-25.
        # It had shipped blind twice (a file missing from LIVING_DOCS, and a
        # case-sensitive regex that skipped the uppercase constant it existed
        # to watch), so the drill had to plant a false count AND reach a doc
        # the checker was not watching. It does both: doc_sync_mutation_test.py
        # breaks every guard in turn and fails if any stays green, and its
        # structural half covers the paths no text mutation can express —
        # a deleted source folder retiring its own check, a gapped migration
        # sequence, a control byte inside a guard's own regex, and a pillar doc
        # nobody added to the watched list.
        drill=[sys.executable, "scripts/doc_sync_mutation_test.py"],
    ),
    "scripts/doc_sync_mutation_test.py": Guard(
        status="owed",
        reason="it IS the drill for doc_sync_check.py, so drilling it means "
        "breaking a mutation on purpose and proving the runner notices — "
        "worth doing, but the honest state today is that nothing checks the "
        "checker's checker. Added to doc-sync.yml 2026-08-25 after Fable 5 "
        "found it ran in NO workflow at all, which let 27 guards degrade to "
        "always-green with no signal",
        since="2026-08-25",
    ),
    "scripts/gen_doc_blocks.py": Guard(
        status="drilled",
        # Generation, not detection: it writes the countable facts INTO the doc
        # from code, so those cannot drift at all. Two failure paths, both
        # watched going red by hand before this entry was written — a wrong
        # value inside the block, and DELETING the marker (a block quietly
        # going back to being hand-written, which is the sneakier one).
        drill=[sys.executable, "scripts/gen_doc_blocks.py"],
    ),
    "scripts/journey_probe.py": Guard(
        status="owed",
        reason="drives the live signup journey and MUTATES production (it creates real "
        "accounts); a drill must not run it, and a safe fixture mode does not exist",
        since="2026-08-16",
    ),
    "scripts/product_assertions.py": Guard(
        status="owed",
        reason="reads the production catalogue; guard #9 lived here — its decision path "
        "was unreachable while every unit test passed, so a drill is owed against "
        "the real exit path, not the function",
        since="2026-08-16",
    ),
    "scripts/provider_probe.py": Guard(
        status="owed",
        reason="calls paid LLM providers; a drill would spend money on every CI run "
        "unless it stubs the client",
        since="2026-08-16",
    ),
    "scripts/sentry_poll.py": Guard(
        status="owed",
        reason="reads the live Sentry API; needs a recorded issue payload",
        since="2026-08-16",
    ),
    "scripts/user_journey_audit.py": Guard(
        status="owed",
        reason="reads the production database for per-user funnel state",
        since="2026-08-16",
    ),
    "scripts/watchdog_check.py": Guard(
        status="owed",
        reason="watches the other loops via the live GitHub API; same fixture gap as "
        "absence_check.py, and the two should share one recorded fixture",
        since="2026-08-16",
    ),
    "backend/scripts/eval_ranking.py": Guard(
        status="owed",
        reason="scores search quality against a labelled set and needs an LLM key; CI "
        "has none, which is why it recorded 39%->66%->78% and then went quiet",
        since="2026-08-16",
    ),
    "backend/scripts/mypy_ratchet.py": Guard(
        status="owed",
        reason="offline and easy: a drill can add a deliberate type error and demand "
        "the ratchet refuses it",
        since="2026-08-16",
    ),
    "backend/scripts/shelf_xray.py": Guard(
        status="owed",
        reason="counts filled profile fields in production; it has already lied in BOTH "
        "directions (salary 83% vs 52%, skills 69% vs 95%) by counting shapes "
        "instead of values, so its drill must assert against a known-value fixture",
        since="2026-08-16",
    ),
}


def discover(github_dir: Path) -> dict[str, set[str]]:
    """Every repo script invoked from .github -> which file invokes it.

    Deliberately a text scan, not a YAML parse: scripts get invoked from inside
    multi-line `run:` blocks, heredocs and `if` branches, and a structural parse
    would miss exactly the ones buried deepest.

    Scans .github RECURSIVELY, not just workflows/. A composite action under
    .github/actions/ can run a guard exactly like a workflow can, and scanning
    only workflows/ would let that guard escape the registry entirely -- an
    enforcer with a blind spot is worse than none, because it certifies the gap.
    """
    found: dict[str, set[str]] = {}
    if not github_dir.is_dir():
        return found
    files = sorted(github_dir.rglob("*.yml")) + sorted(github_dir.rglob("*.yaml"))
    for wf in files:
        # NAMING A SCRIPT IS NOT RUNNING IT -- and `.github/merge-policy.yml` is a
        # LIST of paths, not a runner. It names scripts precisely BECAUSE they are
        # sensitive (`harness_owner` exists to say "a machine may not merge these"),
        # so every path added there was read here as a new undeclared guard. Adding
        # six protections to the policy therefore broke the registry, which broke
        # the ratchet, which stopped the cage. The policy has no `run:` and cannot
        # execute anything. (Third instance today of naming-vs-running: see also
        # `_invokes_cage` in gate_wiring_check.py.)
        if wf.name == "merge-policy.yml":
            continue
        # A SCRIPT NAMED IN A COMMENT IS NOT A GUARD THAT RUNS.
        #
        # Fourth instance of one confusion in this repo, found four different
        # ways: `gate_wiring_check` counted a filename inside an `echo`; this
        # function counted one inside merge-policy.yml (see above); the lane
        # matcher counted a basename at any depth; and this line counted a
        # filename inside a YAML COMMENT.
        #
        # Measured on PR #344's tree: `check_workflow_slack_wiring.py` had 0 real
        # invocations and 3 comment mentions; `check_alert_paths.py` had 0 and 2.
        # Both were therefore DECLARED as guards, and the registry certified two
        # guards that nothing runs -- exactly the failure it exists to prevent.
        # Worse, one of them had been promoted from `owed` to `drilled` on the
        # strength of that phantom wiring.
        #
        # Comment stripping is deliberately line-based and dumb: a line whose
        # first non-space character is `#` is a comment. YAML has no block
        # comments, and `#` inside a quoted string is not a script reference, so
        # the crude rule is exact here. It is NOT applied to shell heredocs
        # inside `run:` blocks, where a `#` line is still shell comment anyway.
        text = "\n".join(
            "" if line.lstrip().startswith("#") else line
            for line in wf.read_text(encoding="utf-8", errors="replace").splitlines()
        )
        for m in _SCRIPT_REF.finditer(text):
            found.setdefault(m.group(1), set()).add(wf.relative_to(github_dir).as_posix())
    return found


def _resolve(ref: str, root: Path) -> Path | None:
    """Where a workflow's script reference actually lands on disk, or None."""
    for candidate in (root / ref, root / "backend" / ref):
        if candidate.is_file():
            return candidate
    return None


def check(root: Path, registry: dict[str, Guard]) -> list[str]:
    """Every guard declared, every declaration real. Returns failure lines."""
    fails: list[str] = []
    found = discover(root / ".github")

    # Normalise: `scripts/x.py` invoked with working-directory: backend is the
    # same guard as `backend/scripts/x.py`. Key on where it lives on disk.
    canonical: dict[str, set[str]] = {}
    for ref, wfs in found.items():
        path = _resolve(ref, root)
        if path is None:
            fails.append(
                f"BROKEN REFERENCE: {', '.join(sorted(wfs))} invokes `{ref}`, which does "
                f"not exist at {ref} or backend/{ref}. The step will die on a missing "
                f"file, and a workflow that cannot start cannot alarm."
            )
            continue
        key = path.relative_to(root).as_posix()
        canonical.setdefault(key, set()).update(wfs)

    for key, wfs in sorted(canonical.items()):
        if key not in registry:
            fails.append(
                f"UNDECLARED GUARD: {key} runs in {', '.join(sorted(wfs))} but has no "
                f"entry in REGISTRY. Add one: `drilled` with a drill command you have "
                f"WATCHED go red, or `owed` with the reason it cannot be drilled yet. "
                f"Ten guards here shipped unable to fire; this is the step that was "
                f"missing each time."
            )

    for key, guard in sorted(registry.items()):
        if key not in canonical:
            fails.append(
                f"STALE ENTRY: REGISTRY declares {key} but no workflow invokes it. Either "
                f"the guard was removed and this entry should go, or it was quietly "
                f"unwired from CI — which is how a guard stops firing without anyone "
                f"noticing."
            )
            continue
        if guard.status == "drilled" and not guard.drill:
            fails.append(f"MALFORMED: {key} is marked `drilled` but declares no drill command.")
        if guard.status == "owed" and not guard.reason:
            fails.append(
                f"MALFORMED: {key} is marked `owed` with no reason. Debt without a reason "
                f"is indistinguishable from an oversight."
            )
        if guard.status not in {"drilled", "owed"}:
            fails.append(f"MALFORMED: {key} has unknown status {guard.status!r}.")

    return fails


def run_drills(root: Path, registry: dict[str, Guard], only: str | None = None) -> list[str]:
    """Run every declared drill. A guard that stays green during its own drill is broken."""
    fails: list[str] = []
    for key, guard in sorted(registry.items()):
        if guard.status != "drilled":
            continue
        if only and only not in key:
            continue
        # A drill is not allowed to invoke itself — that is an infinite regress,
        # and it would also be the most flattering possible self-report.
        if Path(key).name == Path(__file__).name and only is None:
            # Announced, never silent. A skip nobody is told about is how a
            # guard stops running while its row still reads as covered.
            print(f"  [SKIPPED] {key} — a drill cannot invoke itself; it runs as its "
                  f"own CI step (`--drill`), which must be present in the workflow.")
            continue
        try:
            proc = subprocess.run(
                guard.drill, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=DRILL_TIMEOUT_S
            )
        except subprocess.TimeoutExpired:
            fails.append(f"DRILL TIMEOUT: {key} did not finish in {DRILL_TIMEOUT_S}s.")
            continue
        except OSError as exc:
            fails.append(f"DRILL UNRUNNABLE: {key} -> {exc}")
            continue
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-6:]
            fails.append(
                f"DRILL FAILED: {key} exited {proc.returncode}. Its guard did not go red "
                f"when deliberately broken, so it cannot be trusted when something breaks "
                f"for real.\n      " + "\n      ".join(tail)
            )
        else:
            print(f"  [DRILL PASS] {key}")
    return fails


# ─────────────────────────────────────────────────────────────────────────────
# This file's own drill.
#
# The registry is the most dangerous file in the harness: if it silently stops
# detecting, it certifies every other guard. So it is held to the standard it
# imposes — broken on purpose, by each mechanism it claims to catch, plus a
# negative control proving it does not simply alarm at any change.
# ─────────────────────────────────────────────────────────────────────────────


def self_drill() -> int:
    print("DRILL — breaking the registry on purpose. It must go RED and name the reason.")
    print("=" * 72)

    baseline = check(ROOT, REGISTRY)
    if baseline:
        print(f"  baseline: {len(baseline)} pre-existing failure(s) — a drill counts only NEW")
        print("            findings, so real problems cannot mask a broken drill.")
    base = set(baseline)
    results: list[tuple[str, bool, str]] = []

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "repo"
        (tmp / ".github" / "workflows").mkdir(parents=True)
        shutil.copytree(ROOT / ".github", tmp / ".github", dirs_exist_ok=True)
        # Only the files the checks actually resolve need to exist.
        for key in REGISTRY:
            p = tmp / key
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# drill stub\n", encoding="utf-8")

        def new_findings(reg: dict[str, Guard]) -> list[str]:
            return [f for f in check(tmp, reg) if f not in base]

        # 1. THE ENFORCER. A new guard wired into CI with no registry entry.
        wf = tmp / ".github" / "workflows" / "_drill_new_guard.yml"
        wf.write_text(
            "name: drill\non: workflow_dispatch\njobs:\n  x:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: python scripts/a_brand_new_guard.py\n",
            encoding="utf-8",
        )
        (tmp / "scripts" / "a_brand_new_guard.py").write_text("# stub\n", encoding="utf-8")
        f = new_findings(REGISTRY)
        hit = next((x for x in f if "UNDECLARED GUARD" in x and "a_brand_new_guard" in x), "")
        results.append(("new guard wired into CI with no drill declared", bool(hit), hit))
        wf.unlink()
        (tmp / "scripts" / "a_brand_new_guard.py").unlink()

        # 2. A workflow calling a script that is not there. This is how a step dies
        #    on a missing file, and a workflow that cannot start cannot alarm.
        wf.write_text(
            "name: drill\non: workflow_dispatch\njobs:\n  x:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: python scripts/deleted_yesterday.py\n",
            encoding="utf-8",
        )
        f = new_findings(REGISTRY)
        hit = next((x for x in f if "BROKEN REFERENCE" in x and "deleted_yesterday" in x), "")
        results.append(("workflow invokes a script that does not exist", bool(hit), hit))
        wf.unlink()

        # 3. A guard quietly unwired from CI while its entry stays behind — the
        #    silent way a guard stops firing with nothing going red.
        reg = dict(REGISTRY)
        reg["scripts/never_wired_anywhere.py"] = Guard(status="owed", reason="drill", since="x")
        f = new_findings(reg)
        hit = next((x for x in f if "STALE ENTRY" in x and "never_wired" in x), "")
        results.append(("registry entry for a guard no workflow runs", bool(hit), hit))

        # 4. Debt with no reason — indistinguishable from an oversight.
        reg = dict(REGISTRY)
        reg["scripts/chain_check.py"] = Guard(status="owed", reason="")
        f = new_findings(reg)
        hit = next((x for x in f if "MALFORMED" in x and "no reason" in x), "")
        results.append(("owed entry with no reason given", bool(hit), hit))

        # 5. NEGATIVE CONTROL. A harmless workflow that runs no script at all must
        #    produce nothing. A checker that fires at any change is not a checker.
        wf.write_text(
            "name: drill\non: workflow_dispatch\njobs:\n  x:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: echo hello\n",
            encoding="utf-8",
        )
        f = new_findings(REGISTRY)
        # The fixture above runs `echo hello`, not a guard -- so what this proves
        # is that a workflow invoking NO script produces no findings. The old
        # name claimed it ran an already-registered guard, which would be a
        # different and stronger control. Named for what it actually does.
        # (CodeRabbit, PR #336.)
        results.append(("NEGATIVE CONTROL (workflow that invokes no script at all)", not f,
                        "" if not f else f"expected silence, got: {f[0][:120]}"))
        wf.unlink()

    # 5b. A SCRIPT NAMED ONLY IN A COMMENT IS NOT WIRED.
    #     Found on PR #344: `check_workflow_slack_wiring.py` had 0 real
    #     invocations and 3 comment mentions; `check_alert_paths.py` had 0 and
    #     2. Both were therefore treated as wired, declared as guards, and this
    #     registry certified two guards that NOTHING RUNS -- the exact failure
    #     it exists to prevent. One had even been promoted from `owed` to
    #     `drilled` on the strength of that phantom wiring.
    #
    #     Both directions are asserted, because a comment-stripper that ate
    #     real `run:` lines would be the worse bug: it would silently
    #     UN-declare working guards, and nothing would notice.
    with tempfile.TemporaryDirectory() as td2:
        ghd = Path(td2) / ".github" / "workflows"
        ghd.mkdir(parents=True)
        (ghd / "wf.yml").write_text(
            "jobs:\n  a:\n    steps:\n"
            "      # python scripts/phantom_guard.py --drill  <- a comment\n"
            "      - run: python scripts/real_guard.py   # trailing comment\n",
            encoding="utf-8")
        seen = discover(Path(td2) / ".github")
        results.append(("a script named ONLY in a comment is not counted as wired",
                        "scripts/phantom_guard.py" not in seen,
                        "a commented-out reference was treated as an invocation"))
        results.append(("...and a real `run:` invocation is still found",
                        "scripts/real_guard.py" in seen,
                        "the comment stripper ate a real invocation"))
    # 6. A drill that does not actually make its guard go red must be caught by
    #    run_drills. Point an entry at a command that exits 0 doing nothing.
    fake = {"scripts/chain_check.py": Guard(status="drilled",
                                            drill=[sys.executable, "-c", "raise SystemExit(0)"])}
    stayed_green = not run_drills(ROOT, fake, only="chain_check")
    fake_red = {"scripts/chain_check.py": Guard(status="drilled",
                                                drill=[sys.executable, "-c", "raise SystemExit(1)"])}
    caught_red = bool(run_drills(ROOT, fake_red, only="chain_check"))
    results.append(("a drill whose guard exits non-zero is reported as FAILED",
                    caught_red and stayed_green,
                    "" if caught_red else "run_drills did not report a failing drill"))

    print()
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if ok and detail:
            print(f"         RED -> {detail.splitlines()[0][:150]}")
        elif not ok and detail:
            print(f"         {detail[:200]}")

    passed = sum(1 for _, ok, _ in results if ok)
    print()
    print("=" * 72)
    print(f"DRILL RESULT: {passed}/{len(results)} passed")
    if passed != len(results):
        print("The registry did NOT catch something it claims to catch. It is guard eleven.")
        return 1
    print("Every deliberate break was caught and named; the harmless change was ignored.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--drill", action="store_true", help="break this registry on purpose")
    ap.add_argument("--run-drills", action="store_true", help="run every declared guard drill")
    ap.add_argument("--only", help="restrict --run-drills to keys containing this substring")
    ap.add_argument("--count-owed", action="store_true",
                    help="print just the number of guards never watched failing, and exit")
    args = ap.parse_args(argv)

    if args.count_owed:
        # The single place this number comes from. scripts/merge_cage.py used to
        # re-derive it with a regex over this script's prose and fall back to the
        # literal 999 when the regex missed -- a sentinel no real value can ever
        # exceed, so a DELETED guard produced an un-regressable baseline. A
        # ratchet input either prints a number it measured or exits non-zero.
        #
        # VALIDATE BEFORE COUNTING. The ratchet in merge_cage.py accepts this
        # number whenever the command exits 0, so counting an unvalidated
        # REGISTRY lets malformed entries -- a guard naming a drill that does not
        # exist, a bad status string -- produce a confident, wrong, ACCEPTED
        # number. Exiting non-zero here is the whole contract: the ratchet then
        # reads "could not measure", which blocks, instead of a false low count,
        # which merges. (CodeRabbit, PR #336.)
        problems = check(ROOT, REGISTRY)
        if problems:
            print("cannot count owed drills -- the registry itself is malformed:",
                  file=sys.stderr)
            for pr_ in problems:
                print(f"  * {pr_}", file=sys.stderr)
            return 1
        print(sum(1 for g in REGISTRY.values() if g.status == "owed"))
        return 0

    if args.drill:
        return self_drill()

    fails = check(ROOT, REGISTRY)

    drilled = sorted(k for k, g in REGISTRY.items() if g.status == "drilled")
    owed = sorted((k, g) for k, g in REGISTRY.items() if g.status == "owed")

    print(f"drill_registry: {len(REGISTRY)} guards - {len(drilled)} drilled, {len(owed)} owed")

    if args.run_drills:
        fails += run_drills(ROOT, REGISTRY, only=args.only)

    if owed:
        # Printed on EVERY run, never folded away. Debt you cannot see is debt
        # you will not clear, and a quiet `owed` list would make this file a
        # certificate rather than a check.
        print()
        print(f"OWED - {len(owed)} guards have never been watched failing:")
        for key, guard in owed:
            print(f"  * {key}")
            print(f"      {guard.reason}")

    if fails:
        print()
        print("REGISTRY FAILURES")
        print("-" * 72)
        for f in fails:
            print(f"  {f}")
        return 1

    print()
    print("Every guard CI runs is declared, every declaration points at a real file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
