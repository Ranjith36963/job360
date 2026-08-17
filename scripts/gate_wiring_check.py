#!/usr/bin/env python3
"""gate_wiring_check.py — a gate is only a gate where it can still stop something.

WHY THIS EXISTS
---------------
Measured on PR #337 (2026-08-16):

    15:16:24  merged
    15:16:26  Railway starts building
    15:18:38  real users are on the new code
    15:22:17  CI finishes

CI finished FOUR MINUTES after the users. Every check in that run was correct,
green, and completely unable to stop anything. That is not a broken check. It is
a check in the wrong POSITION — the same disease as the ten guards that shipped
here unable to fire, one level up: not "the guard reports the wrong question" but
"the guard reports the right question too late to matter".

`scripts/chain_check.py` proves the harness wires CONNECT. This file proves the
merge wires are wired in the right ORDER, and that nothing merges on evidence
that was never produced.

THE FOUR RULES, AND THE REAL EVENT BEHIND EACH
-----------------------------------------------
G1  A MERGE DECIDED WITHOUT A BASELINE.
    `.github/workflows/auto-merge.yml` ran `python scripts/merge_cage.py "$pr"
    --slack $merge_flag` — no `--baseline`, ever. `check_ratchets(None)` returns
    status `not_checked`, and `not_checked` blocks, so the whole lane could only
    ever say NO, on every PR, for reasons that had nothing to do with the PR.
    Both ways out of that are bugs: leave it and the lane is decoration, or
    "fix" it by making `not_checked` pass and the merge lane loses its only
    decay detector. The fix is to FEED it a baseline. This rule makes forgetting
    that a red build.

G2  A MERGE ON A POST-MERGE TRIGGER.
    A workflow that reacts to `push` on the default branch, or to another
    workflow COMPLETING, is by construction running after the merge it would be
    judging. Anything that merges from there is a post-mortem with a button.
    Detection belongs on those triggers; merging does not.

G3  A MERGE ON "NO FAILURES" ALONE.
    PR #342 is open on main right now with 1 green check, 0 red, and 0 tests
    run: it was opened by a workflow using GITHUB_TOKEN, so GitHub parked its
    five real workflow runs in `action_required` and they posted no check runs
    at all. "no red" is satisfied by "nothing ran". Only COUNTING GREENS can
    tell those apart, which is precisely what dependabot-auto.yml's
    `green -lt 10` floor does — the single most valuable line in the working
    precedent. A job that merges must either count greens against a floor or
    hand the decision to merge_cage.py, which refuses an empty check list
    outright.

G4  A TREE-SCOPED RATCHET MEASURED ON THE WRONG TREE.
    auto-merge.yml checks out `ref: main` — correct, so a PR cannot widen the
    rules that judge it — and then the ratchets measured there are MAIN's
    numbers, not the PR's. `merge_cage.ground_problem` refuses that, correctly,
    which is the second reason the lane refused 100% of PRs. If a job passes
    `--baseline`, it must also check out `refs/pull/<N>/merge` somewhere, or it
    is comparing a tree to itself.

DRILLING IT
-----------
    python scripts/gate_wiring_check.py --drill

Copies `.github` to a temp directory, breaks each rule on purpose in a way that
has really happened here, and demands this checker go RED and name the file.
Plus a NEGATIVE CONTROL: a workflow that merges nothing must produce silence. A
checker that fires at any change is not a checker.

    python scripts/gate_wiring_check.py --drill --break-checker G1

blinds one rule; the drill must then FAIL. A self-test that cannot be made to
fail on purpose is not a self-test.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

ROOT = Path(__file__).resolve().parent.parent
RULES = ("G1", "G2", "G3", "G4")

# Anything that actually performs a merge. `gh pr merge` is the direct form;
# `merge_cage.py --merge` is this repo's caged form. Both count.
_GH_MERGE = re.compile(r"\bgh\s+pr\s+merge\b")
_CAGE_MERGE = re.compile(r"--merge\b")
_CAGE_CALL = re.compile(r"merge_cage\.py")
_BASELINE = re.compile(r"--baseline\b")
# merge_cage modes that judge NO PR and therefore need no baseline.
_CAGE_NON_JUDGING = ("--drill", "--measure", "--blockers", "--replay")
_PULL_MERGE_REF = re.compile(r"refs/pull/")
# `green=$(... conclusion=="SUCCESS" ...)` — the only shape that can tell
# "everything passed" apart from "nothing ran".
_GREEN_ASSIGN = re.compile(r"(\w+)=\$\([^)]*?SUCCESS[^)]*?\)", re.S)
_FLOOR_TEST = re.compile(r"\"?\$\{?(\w+)\}?\"?\s+-(?:lt|ge|gt|le)\s+\"?(\d+)")


def strip_comments(text: str) -> str:
    """Blank out `#` comments, keeping line count and indentation intact.

    WRITTEN BECAUSE THE FIRST RUN OF THIS CHECKER WAS WRONG. It reported G3
    against `doc-sync.yml` and `repair.yml`, whose only `gh pr merge` text is a
    COMMENT saying they deliberately do not merge — repair.yml's line is
    literally "NO `gh pr merge` here, on purpose, ever". A checker that alarms on
    a comment explaining the correct behaviour is a checker that gets switched
    off in a week, and this repo's whole problem is guards nobody trusts.

    Quote-aware, because `--body "see #123"` is not a comment. Line COUNT and
    length are preserved so any future line-numbered finding still points at the
    right place.
    """
    out: list[str] = []
    for line in text.splitlines():
        q: str | None = None
        cut = None
        for i, ch in enumerate(line):
            if q:
                if ch == q:
                    q = None
            elif ch in "\"'":
                q = ch
            elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
                cut = i
                break
        out.append(line if cut is None else line[:cut])
    return "\n".join(out)


@dataclass(frozen=True)
class Finding:
    rule: str
    file: str
    message: str

    def render(self) -> str:
        return f"  [{self.rule}] {self.file}\n        {self.message}"


def _split_jobs(text: str) -> list[tuple[str, str]]:
    """(job name, job text) for each top-level entry under `jobs:`.

    A deliberately textual split. A structural YAML parse would be tidier and
    would also lose the thing that matters: shell written inside multi-line
    `run:` blocks, heredocs and `if` branches, which is exactly where every
    merge in this repo lives.
    """
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(r"^jobs:\s*$", ln)), None)
    if start is None:
        return []
    jobs: list[tuple[str, list[str]]] = []
    for ln in lines[start + 1:]:
        m = re.match(r"^  ([A-Za-z_][\w-]*):\s*$", ln)
        if m:
            jobs.append((m.group(1), []))
        elif jobs:
            jobs[-1][1].append(ln)
    return [(name, "\n".join(body)) for name, body in jobs]


def _triggers(text: str) -> str:
    """The `on:` block, as text. Everything above `jobs:` is close enough and is
    robust to `on:` / `"on":` / `on: {…}` spellings, which a naive key lookup is
    not (`on` is YAML-true in some parsers, and that has bitten real tools)."""
    idx = text.find("\njobs:")
    return text[:idx] if idx != -1 else text


def judging_cage_calls(text: str) -> list[str]:
    """Every merge_cage.py invocation in `text` that actually JUDGES A PR.

    Keyed on judging, not on merging, deliberately. `--merge` has been deleted
    from merge_cage.py, so a rule written against it would be a rule that can
    never fire — the exact defect this repo has shipped ten times. Judging is
    the thing that needs a baseline, whatever is done with the verdict.
    """
    out: list[str] = []
    for line in text.splitlines():
        if not _CAGE_CALL.search(line):
            continue
        cmd = line[line.index("merge_cage.py"):]
        if any(flag in cmd for flag in _CAGE_NON_JUDGING):
            continue
        out.append(cmd.strip())
    return out


def _merges(text: str) -> bool:
    return bool(_GH_MERGE.search(text)) or (
        bool(_CAGE_CALL.search(text)) and bool(_CAGE_MERGE.search(text))
    )


def _has_green_floor(job_text: str) -> bool:
    """Is a GREEN count compared against a numeric floor in this job?

    Deliberately NOT "does the word green appear". The variable that holds the
    SUCCESS count must be the same variable the floor test names, or the job is
    counting one thing and gating on another — this repo's signature bug.
    """
    counted = {m.group(1) for m in _GREEN_ASSIGN.finditer(job_text)}
    if not counted:
        return False
    return any(m.group(1) in counted for m in _FLOOR_TEST.finditer(job_text))


def check_workflows(workflows_dir: Path, disabled: frozenset[str] = frozenset()) -> list[Finding]:
    """Every finding, over every workflow file. Pure: takes a directory, returns
    a list. The drill feeds it a broken COPY of `.github`, never a mock."""
    out: list[Finding] = []
    if not workflows_dir.is_dir():
        return out
    files = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    for wf in files:
        text = strip_comments(wf.read_text(encoding="utf-8", errors="replace"))
        name = wf.name
        head = _triggers(text)
        post_merge_trigger = (
            re.search(r"^\s*push:\s*$", head, re.M) is not None
            or re.search(r"^\s*workflow_run:\s*$", head, re.M) is not None
        )

        for job_name, job_text in _split_jobs(text):
            merges = _merges(job_text)

            judging = judging_cage_calls(job_text)
            if "G1" not in disabled and judging and not _BASELINE.search(job_text):
                out.append(Finding(
                    "G1", name,
                    f"job `{job_name}` judges a PR with merge_cage.py (`{judging[0][:60]}`) "
                    f"but never passes "
                    f"`--baseline`. Without one the RATCHET cage returns `not_checked`, and "
                    f"`not_checked` blocks — so this lane refuses every PR for a reason that "
                    f"is about the wiring, not the PR. A gate that can only say no gets "
                    f"switched off, and then it protects nothing. "
                    f"FIX: measure the base with `merge_cage.py --measure` on a checkout of "
                    f"the PR's BASE, then judge from `refs/pull/<N>/merge` passing "
                    f"`--baseline '<that JSON>'`. Do NOT make `not_checked` pass."))

            if "G2" not in disabled and merges and post_merge_trigger:
                out.append(Finding(
                    "G2", name,
                    f"job `{job_name}` performs a merge, but this workflow is triggered by "
                    f"`push` or `workflow_run` — both of which fire AFTER the thing they "
                    f"would be judging. Merging here is a post-mortem with a button. "
                    f"Measured on PR #337: merged 15:16:24, users live 15:18:38, CI done "
                    f"15:22:17. FIX: move the merge onto a `pull_request` or `schedule` "
                    f"trigger and leave detection (and only detection) on this one."))

            if "G3" not in disabled and merges and not _CAGE_CALL.search(job_text) \
                    and not _has_green_floor(job_text):
                out.append(Finding(
                    "G3", name,
                    f"job `{job_name}` merges without counting GREEN checks against a floor. "
                    f"\"No red\" is satisfied by \"nothing ran\": a PR opened with "
                    f"GITHUB_TOKEN gets its workflow runs parked in `action_required` and "
                    f"posts no check runs at all (live today on PR #342 — 1 green, 0 red, 0 "
                    f"tests). FIX: copy dependabot-auto.yml's arithmetic — count SUCCESS "
                    f"conclusions into a variable and refuse below a floor — or hand the "
                    f"decision to merge_cage.py, which refuses an empty check list outright."))

            if "G4" not in disabled and _BASELINE.search(job_text) \
                    and not _PULL_MERGE_REF.search(job_text):
                out.append(Finding(
                    "G4", name,
                    f"job `{job_name}` passes `--baseline` (a tree-scoped ratchet comparison) "
                    f"but never checks out `refs/pull/<N>/merge`, so the numbers it measures "
                    f"belong to whatever tree it happens to be standing in — usually `main`. "
                    f"Comparing main to main prints \"no number regressed\" having compared a "
                    f"value to itself. merge_cage.ground_problem already refuses this; the "
                    f"point of this rule is that you find out at lint time, not by watching "
                    f"a lane refuse 100% of PRs for a month. "
                    f"FIX: add a second checkout at `refs/pull/${{PR}}/merge` and measure "
                    f"the baseline from the BASE checkout."))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# The drill. Each break below is a real event from this repo, replayed.
# ─────────────────────────────────────────────────────────────────────────────

_JOB_HEAD = (
    "name: drill\n"
    "on:\n"
    "  schedule:\n"
    "    - cron: \"0 0 * * *\"\n"
    "jobs:\n"
    "  merger:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
)


def self_drill(disabled: frozenset[str] = frozenset()) -> int:
    print("DRILL — wiring a merge into the wrong place on purpose. It must go RED.")
    print("=" * 72)

    live = check_workflows(ROOT / ".github" / "workflows", disabled)
    base = {f.render() for f in live}
    if live:
        print(f"  baseline: {len(live)} pre-existing finding(s) — the drill counts only NEW")
        print("            ones, so a real problem cannot mask a dead drill.")

    results: list[tuple[str, bool, str]] = []

    with tempfile.TemporaryDirectory() as td:
        wfdir = Path(td) / "workflows"
        shutil.copytree(ROOT / ".github" / "workflows", wfdir)
        probe = wfdir / "_drill_probe.yml"

        def new_findings() -> list[Finding]:
            return [f for f in check_workflows(wfdir, disabled) if f.render() not in base]

        def case(title: str, rule: str, body: str, needle: str) -> None:
            probe.write_text(_JOB_HEAD + body, encoding="utf-8")
            hits = [f for f in new_findings() if f.rule == rule and needle in f.message]
            results.append((title, bool(hits), hits[0].message[:150] if hits else
                            f"expected a {rule} finding; got "
                            f"{[f.rule for f in new_findings()] or 'silence'}"))
            probe.unlink()

        # G1 — the exact line that was live in auto-merge.yml:114.
        case("judging a PR with no --baseline is caught", "G1",
             "      - run: python scripts/merge_cage.py \"$pr\" --slack\n",
             "never passes")

        # G1 NEGATIVE CONTROL — `--drill` judges no PR, so it needs no baseline.
        # Without this, the obvious way to make G1 pass is to delete the drill
        # step, and a rule that punishes running your own drill is a rule that
        # gets deleted.
        probe.write_text(_JOB_HEAD + "      - run: python scripts/merge_cage.py --drill\n",
                         encoding="utf-8")
        f = new_findings()
        results.append(("NEGATIVE CONTROL (`merge_cage.py --drill` needs no baseline)",
                        not f, "" if not f else f"the drill step was flagged: {f[0].rule}"))
        probe.unlink()

        # G2 — a merge hung off a post-merge trigger.
        probe_body = (
            "name: drill\non:\n  push:\n    branches: [main]\njobs:\n  merger:\n"
            "    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: |\n"
            "          green=$(gh pr view 1 --jq '[.x[]|select(.conclusion==\"SUCCESS\")]|length')\n"
            "          if [ \"$green\" -ge 10 ]; then gh pr merge 1 --squash; fi\n"
        )
        probe.write_text(probe_body, encoding="utf-8")
        hits = [f for f in new_findings() if f.rule == "G2"]
        results.append(("a merge on a post-merge trigger is caught", bool(hits),
                        hits[0].message[:150] if hits else "no G2 finding"))
        probe.unlink()

        # G3 — merging on "no red", the PR #342 shape.
        case("a merge gated on 'no failures' with no green floor is caught", "G3",
             "      - run: |\n"
             "          red=$(gh pr view 1 --jq '[.x[]|select(.conclusion==\"FAILURE\")]|length')\n"
             "          if [ \"$red\" -eq 0 ]; then gh pr merge 1 --squash; fi\n",
             "nothing ran")

        # G3, sharper — counting greens but gating on a DIFFERENT variable. This
        # is the repo's signature bug (measure one thing, decide on another), and
        # a rule that merely greps for the word "green" stays green through it.
        case("counting greens but testing a different variable is still caught", "G3",
             "      - run: |\n"
             "          green=$(gh pr view 1 --jq '[.x[]|select(.conclusion==\"SUCCESS\")]|length')\n"
             "          if [ \"$total\" -ge 10 ]; then gh pr merge 1 --squash; fi\n",
             "counting GREEN checks against a floor")

        # G4 — a tree-scoped ratchet measured from main.
        case("a --baseline judged without checking out the PR's merge ref is caught", "G4",
             "      - uses: actions/checkout@v7\n"
             "        with:\n"
             "          ref: main\n"
             "      - run: python scripts/merge_cage.py \"$pr\" --baseline \"$BASE\"\n",
             "belong to whatever tree")

        # NEGATIVE CONTROL 0 — a merge that exists only in a COMMENT.
        # Not hypothetical: the first run of this checker reported G3 against
        # doc-sync.yml and repair.yml, whose only `gh pr merge` text is a comment
        # saying they deliberately never merge. repair.yml's reads "NO `gh pr
        # merge` here, on purpose, ever." A checker that alarms on the comment
        # explaining the correct behaviour is one nobody will keep.
        probe.write_text(
            _JOB_HEAD +
            "      - run: |\n"
            "          # G1: NO `gh pr merge` here, on purpose, ever.\n"
            "          gh pr create --base main --title \"x\"\n", encoding="utf-8")
        f = new_findings()
        results.append(("NEGATIVE CONTROL (a merge mentioned only in a comment is ignored)",
                        not f, "" if not f else
                        f"alarmed on a comment: {f[0].rule} — {f[0].message[:100]}"))
        probe.unlink()

        # NEGATIVE CONTROL 1 — a workflow that merges nothing must be silent.
        probe.write_text(_JOB_HEAD + "      - run: echo hello\n", encoding="utf-8")
        f = new_findings()
        results.append(("NEGATIVE CONTROL (a workflow that merges nothing is ignored)",
                        not f, "" if not f else f"expected silence, got {f[0].rule}"))
        probe.unlink()

        # NEGATIVE CONTROL 2 — the working precedent must stay green. If this
        # rule set cannot tell dependabot-auto.yml apart from the broken shapes
        # above, it is a wall, not a checker, and it will be switched off.
        probe.write_text(
            _JOB_HEAD +
            "      - run: |\n"
            "          green=$(gh pr view 1 --jq '[.x[]|select(.conclusion==\"SUCCESS\")]|length')\n"
            "          if [ \"$green\" -lt 10 ]; then continue; fi\n"
            "          gh pr merge 1 --squash\n", encoding="utf-8")
        f = new_findings()
        results.append(("NEGATIVE CONTROL (dependabot-auto's green-floor arithmetic passes)",
                        not f, "" if not f else f"the working precedent was refused: {f[0].rule}"))
        probe.unlink()

    print()
    for title, passed, detail in results:
        print(f"  [{'PASS' if passed else 'FAIL'}] {title}")
        if passed and detail:
            print(f"         RED -> {detail}")
        elif not passed and detail:
            print(f"         {detail[:200]}")

    n = sum(1 for _, p, _ in results if p)
    print()
    print("=" * 72)
    print(f"DRILL RESULT: {n}/{len(results)} passed")
    if n != len(results):
        print("This checker did not catch something it claims to catch. Do not trust it.")
        return 1
    print("Every misplaced gate was caught and named; the correct wiring was left alone.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--drill", action="store_true", help="break the wiring on purpose")
    ap.add_argument("--break-checker", metavar="RULE", action="append", default=[],
                    help="blind one rule (G1..G4) — used to prove the DRILL itself can fail")
    args = ap.parse_args(argv)

    bad = [r for r in args.break_checker if r not in RULES]
    if bad:
        print(f"unknown rule(s) {bad}; known: {', '.join(RULES)}", file=sys.stderr)
        return 2
    disabled = frozenset(args.break_checker)

    if args.drill:
        return self_drill(disabled)

    findings = check_workflows(ROOT / ".github" / "workflows", disabled)
    print(f"gate_wiring_check: {len(findings)} finding(s)")
    if not findings:
        print("Every merge in this repo happens where it can still stop something, "
              "and none of them merges on evidence that was never produced.")
        return 0
    print()
    for f in findings:
        print(f.render())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
