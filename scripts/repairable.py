#!/usr/bin/env python3
"""May the auto-fixer edit this file?

WHY THIS EXISTS
---------------
`pr-repair.yml` hardcoded its cage as the regex `^(backend|frontend)/`, in two
places. That is narrower than the lane map and it silently disqualified most of
this repo: measured on 2026-08-27, three of the four PRs that had been stuck for
a day and a half were docs or scripts changes, so the fixer refused them before
it read a line. A fixer that cannot touch the thing that is broken reports
success and does nothing, which looks exactly like having nothing to do.

So the answer now comes from `.github/merge-policy.yml` — the same file the lane
classifier and the merge cage read. One list, one answer.

TWO CONDITIONS, AND THE SECOND IS NOT REDUNDANT
-----------------------------------------------
A file is repairable when:

  1. its lane is `product` or `harness` — a lane a machine may decide, AND
  2. it is not one of the SELF paths below.

Condition 2 looks redundant and is the load-bearing one. Measured on `main`
before PR #444 landed:

    .github/workflows/ci.yml        -> lane `harness`   (auto_merge TRUE)
    .claude/hooks/commit-gate.sh    -> lane `harness`   (auto_merge TRUE)

Condition 1 alone would therefore have let the fixer edit the CI definition and
the commit gate — the guards that judge its own work. #444 moves those to
`harness_owner` and closes it, but a cage that is only safe while another file
stays correct is not a cage. This holds even if the policy regresses.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lane import lane_of_file, load_policy  # noqa: E402

REPAIRABLE_LANES = frozenset({"product", "harness"})

# The fixer may never edit the machinery that judges the fixer, whatever lane
# the policy puts it in. An agent editing its own guards is how a cage is
# escaped, and it is the one failure that hides every other failure.
SELF: tuple[str, ...] = (
    ".github/",         # workflows, the policy, the actions that run the checks
    "scripts/",         # the cage, the lane map, the drills, the ratchets
    ".claude/",         # hooks, skills, agent instructions
    "backend/scripts/", # the ratchets and health scripts CI shells out to
)

# ...and the CONFIGURATION that decides whether a check passes. These are not
# under the directories above, so the prefix rule misses them, and every one is
# a way to go green by moving the line rather than by fixing the code. Found by
# an adversarial review of this very design: `pyrightconfig.json` and
# `backend/scripts/health-daily.sh` were both repairable in the first cut.
#
# `backend/pyproject.toml` carries [tool.mypy]/[tool.ruff]/[tool.pytest] and is
# already owner-lane via `**/pyproject.toml`; test files and conftest are
# already reverted wholesale by the testguard step. This list is the remainder.
SELF_FILES: frozenset[str] = frozenset({
    ".coderabbit.yaml",          # the reviewer's own configuration
    "pyrightconfig.json",        # what the type checker is allowed to ignore
    "mypy.ini", "setup.cfg", "tox.ini", "pytest.ini", ".ruff.toml", "ruff.toml",
    ".pre-commit-config.yaml",
    "codecov.yml", ".codecov.yml",
    ".gitattributes",            # can change what a diff even shows
})


def why_not(path: str, policy: dict) -> str | None:
    """Return the reason this path may not be repaired, or None if it may."""
    norm = path.replace("\\", "/")
    # `lstrip("./")` strips CHARACTERS, not a prefix: `.github/x` lost its leading
    # dot and became `github/x`, so the SELF check below never matched and the
    # path was refused only by accident (lane "unknown"). Strip a literal `./`.
    while norm.startswith("./"):
        norm = norm[2:]
    for prefix in SELF:
        if norm.startswith(prefix):
            return f"`{path}` is part of the harness that judges this repair (SELF: {prefix})"
    if norm in SELF_FILES or norm.rsplit("/", 1)[-1] in SELF_FILES:
        return (f"`{path}` configures a check that judges this repair — going green by "
                f"editing it is not going green")
    lane = lane_of_file(norm, policy)
    if lane not in REPAIRABLE_LANES:
        return f"`{path}` is in the `{lane}` lane — a machine may not decide it"
    return None


# ── WHAT IS WORTH WAKING THE FIXER FOR (added 2026-10-02) ────────────────────
# PRs #665, #666, #679 and #703 all ended `autofix:exhausted` within ~2 minutes
# without the fixer ever having a real chance, because the doorbell dispatched
# for findings the fixer cannot or need not act on: threads on SELF paths,
# threads on code that already changed (outdated), and red checks not caused by
# the PR's own code. `finding-watch.yml` and `pr-repair.yml` both call `triage`,
# so "fixable" means ONE thing -- and it is built on `why_not`, the same matcher
# that cages the agent's edits, so the doorbell can never ring for a file the
# cage would then refuse.

# (pattern on the check-run name, why the fixer cannot help). A failure of one
# of these is NOT a finding the fixer can fix.
UNFIXABLE_CHECKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(npm audit|pip-audit)", re.I),
     "a dependency audit: it fails when an advisory lands on the dependencies "
     "(lockfiles are the owner's), not because of this PR's code"),
    # Reviewer checks: a REAL finding arrives as review threads, which are
    # counted on their own. A red reviewer CHECK with no thread is "did not
    # finish" (out of turns, cancelled, no token) and re-running a fixer cannot
    # change that. Telling the two apart would mean parsing the check's output,
    # so the whole check is excluded and the threads carry the real findings.
    (re.compile(r"^reviewer-", re.I),
     "a reviewer check: its real findings arrive as review threads, a red "
     "check alone means it did not finish"),
    (re.compile(r"^wake-the-fixer$"),
     "the fixer's own doorbell, not a test of this PR's code"),
)
# Conclusions that mean the check never gave a verdict about the code.
NO_VERDICT = {
    "cancelled": "it was cancelled and never produced a verdict, re-run it",
    "action_required": "it is waiting for someone to approve the run",
}
CODE_RED = frozenset({"failure", "timed_out"})


def _clean(text: object) -> str:
    """Make untrusted text safe to quote in a PR comment (no ticks, no mentions)."""
    return re.sub(r"[`@\r\n]", "", str(text or ""))[:120]


def triage(threads: list[dict], checks: list[dict], policy: dict) -> dict:
    """Split findings into what the fixer can act on and what only a human can.

    `threads`: reviewThreads nodes (id, isResolved, isOutdated, path).
    `checks`: check_runs (name, status, conclusion).
    """
    t_open = [t for t in threads if not t.get("isResolved")]
    t_fixable: list[dict] = []
    t_outdated = 0
    t_blocked: list[str] = []
    for t in t_open:
        path = t.get("path")
        if t.get("isOutdated"):
            t_outdated += 1
        elif path and why_not(path, policy):
            t_blocked.append(path)
        else:
            # No path at all is not "outside the cage": it is a finding the
            # fixer should read, so it stays fixable.
            t_fixable.append(t)

    red = [c for c in checks
           if c.get("status") == "completed"
           and c.get("conclusion") in (CODE_RED | set(NO_VERDICT))]
    c_fixable: list[str] = []
    c_unfixable: list[dict] = []
    for c in red:
        name, concl = c.get("name") or "?", c.get("conclusion")
        why = NO_VERDICT.get(concl)
        for pat, reason in UNFIXABLE_CHECKS:
            if why is None and pat.search(name):
                why = reason
        if why is None:
            c_fixable.append(name)
        else:
            c_unfixable.append({"name": name, "conclusion": concl, "why": why})

    lines: list[str] = []
    if t_blocked:
        paths = sorted({_clean(p) for p in t_blocked})
        shown = ", ".join(f"`{p}`" for p in paths[:5]) + (", ..." if len(paths) > 5 else "")
        lines.append(f"{len(t_blocked)} review thread(s) on files the auto-fixer may not edit ({shown})")
    if t_outdated:
        lines.append(f"{t_outdated} outdated review thread(s) (the code they point at has "
                     f"since changed): resolve or dismiss them by hand")
    for u in c_unfixable[:5]:
        lines.append(f"red check `{_clean(u['name'])}` ({u['conclusion']}) is not something the "
                     f"auto-fixer can fix: {u['why']}")
    if len(c_unfixable) > 5:
        lines.append(f"...and {len(c_unfixable) - 5} more red check(s) of that kind")

    return {
        "threads": {"open": len(t_open), "fixable": len(t_fixable),
                    "fixable_ids": [t.get("id") for t in t_fixable],
                    "outdated": t_outdated, "blocked": len(t_blocked)},
        "checks": {"red": len(red), "fixable": c_fixable, "unfixable": len(c_unfixable)},
        "fixable": len(t_fixable) + len(c_fixable),
        "raw": len(t_open) + len(red),
        "explanation": "; ".join(lines),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", help="repo-relative paths; omit to read stdin")
    ap.add_argument("--drill", action="store_true", help="prove this guard can go red")
    ap.add_argument("--threads-json", help="reviewThreads nodes (JSON array); with "
                    "--checks-json, prints the fixable/unfixable split as JSON")
    ap.add_argument("--checks-json", help="check_runs (JSON array); see --threads-json")
    ap.add_argument("--paths-only", action="store_true",
                    help="print just the blocked paths, one per line — for shell pipelines")
    args = ap.parse_args()

    if args.drill:
        return drill()

    if args.threads_json or args.checks_json:
        def load(p: str | None) -> list[dict]:
            return json.loads(Path(p).read_text(encoding="utf-8")) if p else []
        print(json.dumps(triage(load(args.threads_json), load(args.checks_json),
                                load_policy())))
        return 0

    paths = args.files or [ln.strip() for ln in sys.stdin if ln.strip()]
    if not paths:
        print("repairable: no paths given — refusing, because an empty question "
              "is not a yes", file=sys.stderr)
        return 2

    policy = load_policy()
    blocked = [(p, r) for p, r in ((p, why_not(p, policy)) for p in paths) if r]
    for path, reason in blocked:
        print(path if args.paths_only else reason)
    # EXIT 0 EVEN WHEN BLOCKED, in --paths-only mode. The shell callers run this
    # inside `$(... )` with `set -euo pipefail`; a non-zero exit there kills the
    # step before it can print its own explanation, turning "these files are the
    # owner's" into an unexplained red X. The PATH LIST is the answer; emptiness
    # is the pass. Interactive/CI use without the flag still exits 1.
    if args.paths_only:
        return 0
    return 1 if blocked else 0


def drill() -> int:
    """Break it on purpose. A guard nobody has watched fail is not a guard."""
    policy = load_policy()
    cases: list[tuple[str, str, bool]] = [
        # (path, what it is, must_be_blocked)
        ("backend/src/api/routes/jobs.py", "ordinary product code", False),
        ("frontend/src/app/page.tsx", "ordinary frontend code", False),
        ("docs/product/pillars/README.md", "product prose (the case that was stuck)", False),
        ("backend/tests/test_api.py", "a test", False),
        (".github/workflows/ci.yml", "the CI definition — SELF", True),
        (".github/workflows/auto-merge.yml", "the merge arm — SELF", True),
        ("scripts/merge_cage.py", "the cage itself — SELF", True),
        ("scripts/repairable.py", "THIS FILE — SELF", True),
        (".claude/hooks/commit-gate.sh", "a git hook — SELF", True),
        # ── ADDED 2026-09-17, WHEN THE LANE MAP MOVED UNDER THIS FILE ────────
        # The owner carved `.claude/skills/**` and `.claude/agents/**` into the
        # `harness` lane so the daily truth-check PRs stop needing a hand. That
        # is condition 1 (`lane in REPAIRABLE_LANES`) flipping to TRUE for two
        # directories — which is exactly the situation the module docstring
        # calls "a cage that is only safe while another file stays correct".
        # Condition 2, the hardcoded `.claude/` prefix in SELF, is what still
        # blocks them, and until now nothing proved it: the only `.claude` case
        # here was a hook, which condition 1 blocked anyway.
        #
        # THIS IS A SEPARATE DECISION FROM THE MERGE LANE, and it is deliberately
        # NOT taken here. "A machine may merge a skill doc that a human wrote and
        # CodeRabbit reviewed" and "the auto-fixer may rewrite the instructions
        # it is about to read, unsupervised, with no review in the loop" are not
        # the same question. The owner decided the first. The second stays no.
        (".claude/skills/hard-rules/SKILL.md",
         "a skill doc — fast lane now, but still SELF", True),
        # Blocked TWICE over: `harness_owner` fails condition 1 and the
        # `.claude/` prefix fails condition 2. Both are stated because they
        # protect against different mistakes — the lane could be widened again,
        # and SELF could be "tidied". reviewer-bugs raised the lane half as a P0
        # on PR #582; this file was already right, and now says so out loud.
        (".claude/agents/reviewer-bugs.md",
         "a reviewer definition — owner lane AND SELF", True),
        (".claude/agents/verifier.md",
         "the verifier's definition — owner lane AND SELF", True),
        ("CLAUDE.md", "the owner's own words — owner lane", True),
        ("backend/CLAUDE.md", "...at depth too", True),
        (".coderabbit.yaml", "the reviewer's own config", True),
        ("pyrightconfig.json", "what the type checker may ignore", True),
        ("backend/scripts/health-daily.sh", "a script CI shells out to", True),
        ("backend/pyproject.toml", "carries [tool.mypy]/[tool.ruff]", True),
        ("backend/migrations/0032_x.up.sql", "a migration — owner lane", True),
        ("backend/src/models.py", "normalized_key — owner lane", True),
        ("backend/src/api/routes/auth.py", "authentication — owner lane", True),
        ("some_unclassified_dir/thing.py", "in no lane at all", True),
    ]
    bad = 0
    print("repairable.py --drill")
    for path, what, must_block in cases:
        blocked = why_not(path, policy) is not None
        ok = blocked == must_block
        if not ok:
            bad += 1
        print("  %-4s %-46s %s" % (
            "ok" if ok else "FAIL", path,
            f"{what} -> {'blocked' if blocked else 'repairable'}"
            + ("" if ok else f"  WANTED {'blocked' if must_block else 'repairable'}")))
    total = len(cases)

    # ── ADDED 2026-10-02: blocked FOR THE RIGHT REASON, and the doorbell split ─
    # `lstrip("./")` turned `.github/x` into `github/x`, so every SELF-prefix
    # case above was blocked by the "unknown lane" accident, not by SELF — and
    # the drill could not tell, because it only checks blocked-or-not. These
    # prove the SELF rule itself fires, with and without a `./` prefix.
    for path in (".github/workflows/x.yml", "./.github/workflows/x.yml",
                 ".claude/x.md", "scripts/x.py"):
        total += 1
        why = why_not(path, policy) or ""
        ok = "SELF" in why
        bad += 0 if ok else 1
        print("  %-4s %-46s %s" % ("ok" if ok else "FAIL", path,
                                   "blocked by the SELF rule" if ok else f"WRONG REASON: {why!r}"))

    def th(i: str, path: str | None, outdated: bool = False, resolved: bool = False) -> dict:
        return {"id": i, "path": path, "isOutdated": outdated, "isResolved": resolved}

    def ck(name: str, concl: str) -> dict:
        return {"name": name, "status": "completed", "conclusion": concl}

    triage_cases: list[tuple[str, list[dict], list[dict], int, int]] = [
        # (what, threads, checks, want_fixable, want_raw)
        ("only .github threads (PR #679)", [th("a", ".github/workflows/x.yml")], [], 0, 1),
        ("a backend thread -> dispatch", [th("a", "backend/src/api/routes/jobs.py")], [], 1, 1),
        ("outdated thread is not fixable", [th("a", "backend/src/a.py", outdated=True)], [], 0, 1),
        ("resolved thread is not a finding", [th("a", ".github/x.yml", resolved=True)], [], 0, 0),
        ("clean PR (PR #703 at f89ec5f9)", [], [ck("verify", "success")], 0, 0),
        ("npm audit red is main's problem (PR #666)", [], [ck("npm audit (frontend deps)", "failure")], 0, 1),
        ("reviewer ran out of turns", [], [ck("reviewer-bugs", "failure")], 0, 1),
        ("cancelled / action_required are no verdict", [],
         [ck("verify / backend", "cancelled"), ck("verify / frontend", "action_required")], 0, 2),
        ("a real red test IS fixable", [], [ck("verify / backend", "failure")], 1, 1),
    ]
    for what, threads, checks, want_fix, want_raw in triage_cases:
        total += 1
        got = triage(threads, checks, policy)
        ok = got["fixable"] == want_fix and got["raw"] == want_raw
        bad += 0 if ok else 1
        print("  %-4s triage: %-37s fixable=%d raw=%d%s" % (
            "ok" if ok else "FAIL", what, got["fixable"], got["raw"],
            "" if ok else f"  WANTED fixable={want_fix} raw={want_raw}"))
    print(f"\n{total - bad}/{total}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
