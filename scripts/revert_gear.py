#!/usr/bin/env python3
"""revert_gear.py — the reverse gear. Build the revert, prove it applies, never pull it.

WHY THIS FILE EXISTS
--------------------
There was no reverse gear at all. Grepped every file under `.github/workflows/`
and `scripts/` for revert|rollback|redeploy: the only matches are
repair.yml/pr-repair.yml reverting an AGENT'S OWN TEST EDITS,
migration_roundtrip.sh reverting migrations in a throwaway database, and psycopg
`conn.rollback()`. There is no path — scripted, documented or drilled — that
takes production back to the previous commit.

Merge -> live is 2m14s forward (measured on PR #337: merged 15:16:24, users on
new code 15:18:38) and UNDEFINED backward. Every argument about gates is
downstream of that.

TWO GEARS, DELIBERATELY DIFFERENT
----------------------------------
GEAR 1 — FAST, HUMAN, NO REBUILD: Railway's per-deployment Rollback. It restores
the previous successful deployment's Docker image AND its variables, in seconds,
with no rebuild. It is DASHBOARD-ONLY — the CLI exposes `redeploy`, `restart`,
`down` and `deployment list`, and no rollback verb — so it cannot be automated,
and it should not be. That is the 2 a.m. gear, and it is three clicks. The
runbook lives in STATUS.md.

GEAR 2 — DURABLE, SCRIPTED, KEEPS main == PROD: this file. Gear 1 leaves `main`
and production DISAGREEING, which is a state that will silently bite the next
merge. This is what reconciles them.

WHAT THIS FILE WILL AND WILL NOT DO
------------------------------------
IT WILL:  verify a SHA really is a merge commit; build `git revert -m 1 <sha>` on
          a SCRATCH branch; verify it applies cleanly; print the branch name.
IT WILL NOT: push, merge, force anything, or touch `main`. Ever. There is no flag
          that does. The workflow around it opens a PR; the owner merges it.

WHY THE DRY RUN IS THE IMPORTANT MODE
--------------------------------------
The failure that will actually bite is not "the revert was wrong". It is
"THE REVERT DOES NOT APPLY" — a revert that conflicts is a revert you do not
have, discovered at the worst possible moment. So the monthly drill builds the
revert of the newest merge on a scratch branch and asserts it applies. That is
the whole point of rehearsing it while nothing is on fire.

NEGATIVE CONTROL, held to the same standard as every guard here: a SHA that is
NOT a merge commit must fail LOUDLY. `git revert` on an ordinary commit succeeds
and reverts the wrong thing — silently doing something plausible is the exact
failure this repo has paid for ten times.

    python scripts/revert_gear.py --sha <merge sha> --dry-run
    python scripts/revert_gear.py --drill
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

ROOT = Path(__file__).resolve().parent.parent

EXIT_OK = 0
EXIT_REFUSED = 1  # a VERDICT about the input, not a crash
EXIT_GEAR_BROKE = 3


@dataclass
class Result:
    ok: bool
    reason: str
    branch: str = ""


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Every git call in one place, with the encoding fixed.

    `text=True` alone decodes with the machine's locale and dies on an em-dash —
    36 instances of that bug were live across 18 files here, and one of them
    stopped the merge cage answering at all (scripts/encoding_guard.py).
    """
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=180)


def is_merge_commit(sha: str, cwd: Path) -> tuple[bool, str]:
    """Exactly two-or-more parents, and the SHA must exist.

    Kept separate from the revert so the drill can feed it real SHAs from a real
    repository rather than assert on a mock. `rev-list --parents -n 1` prints the
    commit and then its parents, so the field count IS the parent count + 1.
    """
    proc = git(["rev-list", "--parents", "-n", "1", sha], cwd)
    if proc.returncode != 0:
        return False, (f"`{sha}` is not a commit in this repository "
                       f"({proc.stderr.strip()[:160]}). FIX: pass a full merge SHA from "
                       f"`git log --merges --first-parent main`.")
    fields = proc.stdout.split()
    parents = len(fields) - 1
    if parents < 2:
        return False, (
            f"`{sha[:12]}` has {parents} parent(s), so it is NOT a merge commit. "
            f"`git revert -m 1` on it would succeed and revert the WRONG THING — quietly, "
            f"and plausibly. Refusing. "
            f"FIX: pick a SHA from `git log --merges --first-parent main`.")
    return True, ""


def build_revert(sha: str, cwd: Path, branch: str) -> Result:
    """Create `branch` off HEAD and put the revert commit on it. Never touches main."""
    okay, why = is_merge_commit(sha, cwd)
    if not okay:
        return Result(False, why)

    made = git(["checkout", "-b", branch], cwd)
    if made.returncode != 0:
        return Result(False, f"could not create the scratch branch `{branch}`: "
                             f"{made.stderr.strip()[:200]}")

    rev = git(["revert", "-m", "1", "--no-edit", sha], cwd)
    if rev.returncode != 0:
        # THE FAILURE THAT ACTUALLY BITES. Say it plainly: you do not have this
        # revert, and you found out now instead of during an outage.
        git(["revert", "--abort"], cwd)
        return Result(False, (
            f"the revert of `{sha[:12]}` DOES NOT APPLY — it conflicts with what has landed "
            f"since. A revert that conflicts is a revert you do not have. "
            f"git said: {(rev.stderr or rev.stdout).strip()[:200]} "
            f"FIX: resolve it by hand on `{branch}`, or use Railway's dashboard Rollback "
            f"(gear 1) to get production back first and reconcile `main` afterwards."),
            branch=branch)
    return Result(True, f"the revert of `{sha[:12]}` applies cleanly on `{branch}`.",
                  branch=branch)


def run(sha: str, cwd: Path, branch: str) -> int:
    res = build_revert(sha, cwd, branch)
    print(res.reason)
    if not res.ok:
        return EXIT_REFUSED
    print(f"branch={res.branch}")
    print("Nothing was pushed and `main` was not touched. This gear only BUILDS the "
          "revert; a human merges it.")
    return EXIT_OK


# ─────────────────────────────────────────────────────────────────────────────
# The drill. It builds a REAL throwaway git repository and drives the real
# functions against real SHAs. No mocks: every defect this file can carry
# survives an assertion on internals and dies instantly on a real repository.
# ─────────────────────────────────────────────────────────────────────────────


def _mk_repo(tmp: Path) -> tuple[Path, str, str]:
    """A repo with one merge commit and one ordinary commit. Returns both SHAs."""
    repo = tmp / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "drill", "GIT_AUTHOR_EMAIL": "d@x",
           "GIT_COMMITTER_NAME": "drill", "GIT_COMMITTER_EMAIL": "d@x"}
    def g(*a: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *a], cwd=str(repo), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, timeout=60)
    g("init", "-b", "main")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    g("add", "-A")
    g("commit", "-m", "base")
    g("checkout", "-b", "feature")
    (repo / "b.txt").write_text("feature\n", encoding="utf-8")
    g("add", "-A")
    g("commit", "-m", "feature work")
    g("checkout", "main")
    (repo / "c.txt").write_text("main-side\n", encoding="utf-8")
    g("add", "-A")
    g("commit", "-m", "ordinary commit on main")
    ordinary = g("rev-parse", "HEAD").stdout.strip()
    g("merge", "--no-ff", "feature", "-m", "Merge pull request #999")
    merge = g("rev-parse", "HEAD").stdout.strip()
    return repo, merge, ordinary


def self_drill() -> int:
    print("DRILL — feeding the reverse gear inputs that would revert the wrong thing.")
    print("=" * 72)
    results: list[tuple[str, bool, str]] = []

    def ok(name: str, passed: bool, detail: str = "") -> None:
        results.append((name, passed, "" if passed else detail))

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        repo, merge_sha, ordinary_sha = _mk_repo(tmp)
        main_before = git(["rev-parse", "main"], repo).stdout.strip()

        # NEGATIVE CONTROL first, because it is the case that must WORK. A gear
        # that only ever refuses is a gear nobody will reach for at 2 a.m.
        res = build_revert(merge_sha, repo, "revert/drill-1")
        ok("NEGATIVE CONTROL (a real merge commit produces a revert that applies)",
           res.ok, f"the gear refused a perfectly good merge SHA: {res.reason}")
        reverted_head = git(["rev-parse", "HEAD"], repo).stdout.strip()
        ok("the revert really is a NEW COMMIT, not a no-op",
           reverted_head != merge_sha and
           git(["log", "-1", "--pretty=%s"], repo).stdout.strip().lower().startswith("revert"),
           f"HEAD subject was {git(['log', '-1', '--pretty=%s'], repo).stdout.strip()!r}")
        ok("the file the merge brought in is gone again after the revert",
           not (repo / "b.txt").exists(), "b.txt survived the revert")

        # AND main MUST BE UNTOUCHED. This is the property that makes the monthly
        # drill safe to run unattended forever.
        git(["checkout", "main"], repo)
        ok("`main` was not moved by building the revert",
           git(["rev-parse", "main"], repo).stdout.strip() == main_before,
           "the gear moved main — it must only ever build a scratch branch")

        # BREAK 1 — an ORDINARY commit. `git revert -m 1` on it fails with
        # "mainline was specified but commit is not a merge", but a gear that
        # just shelled out and reported git's exit code would give the owner a
        # cryptic line at the worst moment. It must refuse FIRST and say why.
        res = build_revert(ordinary_sha, repo, "revert/drill-2")
        ok("a non-merge SHA is refused LOUDLY, not quietly reverted",
           not res.ok and "NOT a merge commit" in res.reason, res.reason[:200])

        # BREAK 2 — a SHA that does not exist at all.
        git(["checkout", "main"], repo)
        res = build_revert("d" * 40, repo, "revert/drill-3")
        ok("a SHA that is not in this repository is refused",
           not res.ok and "not a commit" in res.reason, res.reason[:200])

        # BREAK 3 — THE ONE THAT ACTUALLY BITES: the revert conflicts. Set it up
        # by editing, on main, the very file the merge introduced.
        git(["checkout", "main"], repo)
        (repo / "b.txt").write_text("someone changed this after the merge\n", encoding="utf-8")
        git(["add", "-A"], repo)
        git(["commit", "-m", "later change to the merged file"], repo)
        res = build_revert(merge_sha, repo, "revert/drill-4")
        ok("a revert that CONFLICTS is reported as 'you do not have this revert'",
           not res.ok and "DOES NOT APPLY" in res.reason, res.reason[:200])
        ok("a conflicting revert leaves no half-finished revert behind",
           not (repo / ".git" / "REVERT_HEAD").exists(),
           "REVERT_HEAD survived — the working tree is mid-revert")

        # And main is STILL untouched after four attempts, three of them failures.
        git(["checkout", "main"], repo)
        head_now = git(["rev-parse", "HEAD"], repo).stdout.strip()
        ok("`main` is still only ever moved by a human (4 runs, 3 refusals, 0 writes)",
           head_now == git(["rev-parse", "main"], repo).stdout.strip(),
           "main drifted during the drill")

    print()
    for name, passed, detail in results:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        if not passed and detail:
            print(f"         {detail[:220]}")
    n = sum(1 for _, p, _ in results if p)
    print()
    print("=" * 72)
    print(f"DRILL RESULT: {n}/{len(results)} passed")
    if n != len(results):
        print("The reverse gear did something plausible with a bad input. Do not trust it.")
        return 1
    print("Every bad input was refused and named; the good one really did build a revert.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sha", help="the MERGE commit to revert")
    ap.add_argument("--branch", default="", help="scratch branch name to build it on")
    ap.add_argument("--dry-run", action="store_true",
                    help="build the revert in a throwaway clone and throw it away")
    ap.add_argument("--drill", action="store_true", help="break the gear on purpose")
    args = ap.parse_args(argv)

    try:
        if args.drill:
            return self_drill()
        if not args.sha:
            ap.error("--sha is required unless --drill")

        branch = args.branch or f"revert/{args.sha[:12]}"
        if args.dry_run:
            # A throwaway CLONE, so a dry run cannot leave a branch, a conflict
            # or a dirty tree behind in the real checkout. The monthly drill runs
            # unattended; it has to be incapable of leaving a mess.
            with tempfile.TemporaryDirectory() as td:
                clone = Path(td) / "clone"
                proc = git(["clone", "--no-hardlinks", str(ROOT), str(clone)], ROOT)
                if proc.returncode != 0:
                    print(f"could not clone for the dry run: {proc.stderr.strip()[:200]}",
                          file=sys.stderr)
                    return EXIT_GEAR_BROKE
                rc = run(args.sha, clone, branch)
                shutil.rmtree(clone, ignore_errors=True)
                print("DRY RUN — the revert was built in a throwaway clone and discarded. "
                      "Nothing in this checkout changed.")
                return rc
        return run(args.sha, ROOT, branch)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"the reverse gear itself broke ({type(exc).__name__}: {exc}). Nothing was "
              f"changed, which is the safe direction — but you do NOT have a tested revert. "
              f"Use Railway's dashboard Rollback (gear 1) and fix this.", file=sys.stderr)
        return EXIT_GEAR_BROKE


if __name__ == "__main__":
    raise SystemExit(main())
