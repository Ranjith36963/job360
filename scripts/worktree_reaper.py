#!/usr/bin/env python3
"""worktree_reaper.py — the ENTRANCE the broom never had.

WHY THIS EXISTS
---------------
Two correct tools already solve the hard part of this problem, and neither has
ever collected a single worktree:

    scripts/worktree_census.py   sorts worktrees/branches into SAFE / KEEP / ASK,
                                 handles squash-merge via patch-ids, and knows
                                 about gitignored work `git status` cannot see.
                                 Its own docstring: "It sorts; it does not sweep."
    scripts/branch_prune.sh      deletes merged local branches, with a real
                                 safety contract and a recovery index.

Both are MANUAL COMMANDS. Between them being written and this file, the estate
went 14 -> 62 -> 98 worktrees. That is the finding:

    A CLEANUP TOOL NOBODY RUNS IS INDISTINGUISHABLE FROM NO CLEANUP TOOL.

So this file adds no classifier. It imports the census, trusts its verdicts, and
supplies the four things that stood between "correct" and "actually ran":

  1. AN ENTRANCE          .claude/hooks/worktree-reaper.sh calls this at
                          SessionStart. Nothing else ever calls the other two.
  2. AN IDLE FLOOR        the census asks "does this folder hold work?" — around
                          14 sessions run in parallel here, and a clean, shipped
                          worktree somebody is simply not typing in right now is
                          SAFE by every test the census has. Mtime is the only
                          cheap evidence that nobody is home.
  3. PRUNE FIRST          when a PR merges GitHub deletes the branch, which breaks
                          the worktree's `.git` link, and then `git worktree
                          remove` REFUSES:
                              fatal: validation failed, cannot remove working
                              tree: '<path>/.git' does not exist
                          Only `git worktree prune` clears those. 17 of 68 were in
                          this state during the 98 -> 28 sweep. The census cannot
                          reach them and neither can `remove`.
  4. BRANCH DELETION ON   branch_prune.sh gates on `git branch --merged
     THE RIGHT ORACLE     origin/main`, which reports 1 on this repo where GitHub
                          reports 343 merged PRs, because the repo SQUASH-merges
                          and a squash is never an ancestor. The census already
                          computes the right answer (`git cherry` patch-ids); this
                          just acts on it.

WHAT IT WILL NOT DO
-------------------
Re-decide anything the census decided. Only `verdict == SAFE` rows are ever
touched, and the idle floor can only ever REMOVE candidates from that set, never
add one. If the census is wrong, this is wrong in the same direction — which is
the point of having one brain rather than two.

USAGE
-----
    python scripts/worktree_reaper.py                  # dry run
    python scripts/worktree_reaper.py --apply          # do it
    python scripts/worktree_reaper.py --drill          # prove it still refuses
    python scripts/worktree_reaper.py --hook-tick DIR  # throttle + report, for the hook
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from worktree_census import census  # noqa: E402  (needs the sys.path line above)

DUE = 0
THROTTLED = 3

DEFAULT_MIN_IDLE_HOURS = 24.0
DEFAULT_MAX_PER_RUN = 10       # worktrees: 10-30 s each on Windows
DEFAULT_MAX_BRANCHES = 50      # branches: a ref write, essentially free


def run(args: list[str], cwd: str | None = None, timeout: int = 120) -> tuple[str, int]:
    """Run a command, returning (stdout, returncode). Never raises."""
    try:
        r = subprocess.run(
            args, cwd=cwd, capture_output=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "", 1
    return (r.stdout or "").strip(), r.returncode


def _norm(p: str) -> str:
    return str(p).replace("\\", "/").rstrip("/").lower()


# ─────────────────────────────────────────────────────────────────────────────
# THE ONE JUDGEMENT THIS FILE ADDS
# ─────────────────────────────────────────────────────────────────────────────


def idle_hours(path: str, now: float) -> float:
    """Hours since anything in this worktree was touched.

    Cheap on purpose. The git dir's `index` and `HEAD` move on every checkout,
    commit, add and status-with-refresh; the worktree root's mtime moves when a
    file appears or disappears at the top level. Walking the tree is not an
    option — counting the files in ONE of these worktrees timed out after two
    minutes, because each carries a full node_modules and .venv.
    """
    newest = 0.0
    gitdir, rc = run(["git", "rev-parse", "--absolute-git-dir"], cwd=path)
    probes = [Path(path)]
    if rc == 0 and gitdir:
        probes += [Path(gitdir) / "index", Path(gitdir) / "HEAD"]
    for p in probes:
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            continue
    if newest == 0.0:
        # Unknown reads as ACTIVE, never as stale. An unreadable mtime must not
        # mean "nobody has been here for a year".
        return 0.0
    return max(0.0, (now - newest) / 3600.0)


def is_idle_enough(path: str, min_idle_hours: float, now: float, self_path: str) -> tuple[bool, str]:
    """(ok, reason). The idle floor, plus the one case mtime cannot see: ourselves."""
    if _norm(path) == _norm(self_path) or _norm(self_path).startswith(_norm(path) + "/"):
        return False, "this process is running inside it"
    h = idle_hours(path, now)
    if h < min_idle_hours:
        return False, f"active {h:.1f}h ago (< {min_idle_hours:g}h idle floor)"
    return True, f"idle {h:.0f}h"


# ─────────────────────────────────────────────────────────────────────────────
# ACTING
# ─────────────────────────────────────────────────────────────────────────────


def log_reaped(repo_root: Path, sha: str, name: str, note: str) -> None:
    """Append to the recovery index BEFORE deleting. It is the only real undo.

    Not reflog: `git branch -d` deletes the branch's own reflog with it, and HEAD
    reflogs are per-worktree, so a branch never checked out here has none here at
    all. branch_prune.sh established this file and this format; keep both.
    """
    logf = repo_root / "docs" / "maintenance" / "REAPED.log"
    logf.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with logf.open("a", encoding="utf-8") as f:
        f.write(f"{stamp}  {sha}  {name}  ({note})\n")


def prune_broken(repo_root: Path) -> int:
    """Clear registrations whose directory or .git link is gone. MUST run first."""
    before, _ = run(["git", "worktree", "list", "--porcelain"], cwd=str(repo_root))
    run(["git", "worktree", "prune"], cwd=str(repo_root))
    after, _ = run(["git", "worktree", "list", "--porcelain"], cwd=str(repo_root))
    n = lambda s: len([b for b in s.split("\n\n") if b.strip()])  # noqa: E731
    return max(0, n(before) - n(after))


# ─────────────────────────────────────────────────────────────────────────────
# THE HOOK TICK — throttle and report
#
# This lives here, not in the shell hook, on purpose: .claude/hooks/ is not
# discovered by scripts/drill_registry.py, so anything decided there is decided
# where nothing can drill it. A throttle that silently never fires looks exactly
# like one that works.
# ─────────────────────────────────────────────────────────────────────────────


def render_last_run(payload: dict) -> str:
    """One line describing the previous run — the only channel it ever has.

    The reaper is launched detached so a session start does not wait on it, which
    means its own output reaches nobody in real time.
    """
    if payload.get("error"):
        return f"- **worktree reaper**: last run did nothing — {payload['error']}"
    wts = payload.get("worktrees_removed") or []
    brs = payload.get("branches_deleted") or []
    pruned = int(payload.get("pruned") or 0)
    if not wts and not brs and not pruned:
        return ""
    bits = []
    if pruned:
        bits.append(f"pruned {pruned} broken registration(s)")
    if wts:
        names = ", ".join(w.get("branch") or "(detached)" for w in wts[:3])
        more = f" (+{len(wts) - 3} more)" if len(wts) > 3 else ""
        bits.append(f"removed {len(wts)} shipped worktree(s): {names}{more}")
    if brs:
        bits.append(f"deleted {len(brs)} merged branch(es)")
    return (
        f"- **worktree reaper**: {'; '.join(bits)}. "
        f"Recovery index: docs/maintenance/REAPED.log"
    )


def is_due(stamp: Path, throttle_hours: float, now: float) -> bool:
    """Has long enough passed since the last launch?

    Throttling matters more than it looks: around 14 sessions open against this
    repo, so an unthrottled SessionStart hook means 14 concurrent sweeps every
    time the owner opens terminals.
    """
    try:
        age_h = (now - stamp.stat().st_mtime) / 3600.0
    except OSError:
        return True  # never run -> due
    return age_h >= throttle_hours


def hook_tick(state_dir: Path, throttle_hours: float) -> int:
    """Print the last run's summary; exit DUE (0) or THROTTLED (3)."""
    log = state_dir / "last-run.json"
    try:
        if log.is_file() and log.stat().st_size:
            line = render_last_run(json.loads(log.read_text(encoding="utf-8")))
            if line:
                print(line)
    except (ValueError, OSError):
        pass
    return DUE if is_due(state_dir / "last-run", throttle_hours, time.time()) else THROTTLED


# ─────────────────────────────────────────────────────────────────────────────
# THE SWEEP
# ─────────────────────────────────────────────────────────────────────────────


def sweep(
    repo_root: Path,
    apply: bool,
    max_per_run: int,
    max_branches: int,
    min_idle_hours: float,
    keep_branches: bool,
    self_path: str,
) -> dict:
    now = time.time()
    result: dict = {
        "pruned": 0,
        "worktrees_removed": [],
        "branches_deleted": [],
        "skipped": [],
        "applied": apply,
    }

    # 1. PRUNE FIRST. `remove` cannot touch a broken registration, and a merged
    #    PR is exactly what breaks one (GitHub deletes the branch, the link dies).
    if apply:
        result["pruned"] = prune_broken(repo_root)

    # 2. THE BRAIN. Not re-implemented here — see the module docstring.
    c = census(repo_root)

    for row in c.safe_worktrees:
        if len(result["worktrees_removed"]) >= max_per_run:
            result["skipped"].append({"name": row.path, "reason": f"over the --max {max_per_run} cap"})
            continue
        ok, why = is_idle_enough(row.path, min_idle_hours, now, self_path)
        if not ok:
            result["skipped"].append({"name": row.path, "reason": why})
            continue
        if apply:
            log_reaped(repo_root, row.head, row.branch or "(detached)", f"worktree {row.path}")
            _, rc = run(["git", "worktree", "remove", row.path], cwd=str(repo_root))
            if rc != 0:
                result["skipped"].append({"name": row.path, "reason": "git worktree remove refused"})
                continue
        result["worktrees_removed"].append({"path": row.path, "branch": row.branch, "why": why})

    if keep_branches:
        return result

    # 3. BRANCHES. A fresh census ONLY IF a worktree actually went: removing one
    #    frees its branch, so the first census's branch verdicts would be stale,
    #    and a stale verdict here deletes a branch whose folder still holds work.
    #    Conditional because one census costs 3m25s over 30 worktrees — running a
    #    second unconditionally doubled every run to seven minutes for nothing.
    c2 = census(repo_root) if (apply and result["worktrees_removed"]) else c
    for b in c2.safe_branches:
        # A SEPARATE cap. --max exists because removing ONE worktree takes 10-30 s
        # (node_modules + .venv); deleting a branch is a ref write and costs
        # nothing. Sharing the cap meant 170 reapable branches would drain at 10
        # per 6-hour run -- four months to converge on work that takes a second.
        if len(result["branches_deleted"]) >= max_branches:
            result["skipped"].append({"name": b.name, "reason": f"over the --max-branches {max_branches} cap"})
            continue
        if apply:
            log_reaped(repo_root, b.head, b.name, "branch")
            # -D, not -d. Under squash-merge `-d` refuses every shipped branch,
            # correctly: the tip really is not an ancestor. The census replaced
            # that proof with a stronger one (patch-identical to main), and the
            # tip SHA is in REAPED.log above before anything is destroyed.
            _, rc = run(["git", "branch", "-D", b.name], cwd=str(repo_root))
            if rc != 0:
                result["skipped"].append({"name": b.name, "reason": "git branch -D refused"})
                continue
        result["branches_deleted"].append({"name": b.name, "head": b.head})

    return result


# ─────────────────────────────────────────────────────────────────────────────
# THE REMOTE HALF (CI)
#
# A GitHub Action cannot delete a folder on somebody's laptop, and this laptop
# cannot be trusted to be switched on. So the two halves run in different places
# and neither substitutes for the other. Building only the CI half is why every
# previous attempt left the folders behind.
#
# Measured 2026-09-02: 111 remote branches survive despite GitHub's own
# delete-branch-on-merge, because that setting only fires on a merge performed
# through the UI/API for that PR.
# ─────────────────────────────────────────────────────────────────────────────

PROTECTED_BRANCHES = {"main", "master", "develop"}


def stale_remote_branches(
    merged_prs: list[dict],
    live_refs: set[str],
    now: float,
    older_than_days: float,
) -> list[str]:
    """Which remote branches may be deleted. Pure — the drill needs no network.

    A branch qualifies only if a pull request for it MERGED, that merge is older
    than the grace period, and the ref still exists. `gh pr list --state merged`
    is the oracle because `git branch --merged` reports 1 here where GitHub
    reports 343: the repo squash-merges, so a shipped tip is never an ancestor.
    """
    cutoff = now - older_than_days * 86400
    out: set[str] = set()
    for pr in merged_prs:
        br = pr.get("headRefName") or ""
        if not br or br in PROTECTED_BRANCHES or br not in live_refs:
            continue
        try:
            ts = time.mktime(time.strptime(pr.get("mergedAt") or "", "%Y-%m-%dT%H:%M:%SZ"))
        except ValueError:
            continue  # unparseable date -> never delete
        if ts < cutoff:
            out.add(br)
    return sorted(out)


def reap_remote(repo_root: Path, apply: bool, older_than_days: float, cap: int) -> int:
    out, rc = run(
        ["gh", "pr", "list", "--state", "merged", "--limit", "500",
         "--json", "headRefName,mergedAt"],
        cwd=str(repo_root), timeout=60,
    )
    if rc != 0:
        print("gh pr list failed — doing nothing.")
        return 1
    try:
        prs = json.loads(out or "[]")
    except ValueError:
        print("could not parse gh output — doing nothing.")
        return 1
    if not prs:
        # An empty merged-set makes every branch look unshipped, which is safe --
        # but it equally means the query returned nothing, and a reaper that
        # cannot tell those apart is one whose safety is an accident.
        print("gh returned zero merged PRs — refusing to trust that.")
        return 1

    refs, _ = run(["git", "ls-remote", "--heads", "origin"], cwd=str(repo_root), timeout=60)
    live = {ln.split("refs/heads/", 1)[1] for ln in refs.splitlines() if "refs/heads/" in ln}

    targets = stale_remote_branches(prs, live, time.time(), older_than_days)[:cap]
    if not targets:
        print("No stale merged remote branches.")
        return 0
    print(f"{'Deleting' if apply else 'Would delete'} {len(targets)} of {len(live)} remote branches:")
    for br in targets:
        print(f"  - {br}")
        if apply:
            run(["git", "push", "origin", "--delete", br], cwd=str(repo_root), timeout=60)
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# THE DRILL — prove it still refuses, and prove it still ACTS
# ─────────────────────────────────────────────────────────────────────────────


def _drill_idle_floor() -> list[tuple[str, bool, str]]:
    rows: list[tuple[str, bool, str]] = []
    tmp = tempfile.mkdtemp(prefix="reaper-idle-")
    try:
        wt = Path(tmp) / "wt"
        wt.mkdir()
        now = time.time()

        os.utime(wt, (now, now))
        ok, why = is_idle_enough(str(wt), 24.0, now, self_path="/elsewhere")
        rows.append(("idle_floor_refuses_active_worktree", not ok, why))

        old = now - 48 * 3600
        os.utime(wt, (old, old))
        ok, why = is_idle_enough(str(wt), 24.0, now, self_path="/elsewhere")
        # NEGATIVE CONTROL. Without it, an idle floor that refuses EVERYTHING
        # passes the case above and collects nothing — which is exactly how
        # branch_prune.sh looked correct while 98 worktrees piled up behind it.
        rows.append(("idle_floor_allows_stale_worktree", ok, why))

        ok, why = is_idle_enough(str(wt), 24.0, now, self_path=str(wt))
        rows.append(("refuses_the_worktree_it_runs_in", not ok, why))

        ok, why = is_idle_enough(str(wt / "sub" / "deep"), 24.0, now, self_path=str(wt / "sub" / "deep"))
        rows.append(("refuses_when_cwd_is_below_it", not ok, why))

        ok, why = is_idle_enough(str(Path(tmp) / "does-not-exist"), 24.0, now, "/elsewhere")
        rows.append(("unreadable_mtime_reads_as_ACTIVE", not ok, why))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return rows


def _drill_prune_before_remove() -> list[tuple[str, bool, str]]:
    """Pin the ordering claim against real git: `remove` refuses a broken link."""
    tmp = tempfile.mkdtemp(prefix="reaper-prune-")
    try:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        ident = ["-c", "user.email=drill@example.com", "-c", "user.name=drill"]
        run(["git", "init", "-q", "-b", "main", str(repo)])
        (repo / "f.txt").write_text("x", encoding="utf-8")
        run(["git", *ident, "add", "."], cwd=str(repo))
        run(["git", *ident, "commit", "-qm", "init"], cwd=str(repo))

        wt = Path(tmp) / "wt"
        _, rc = run(["git", *ident, "worktree", "add", "-q", "-b", "side", str(wt)], cwd=str(repo))
        if rc != 0:
            return [("prune_fixture", False, "could not create the fixture worktree")]

        # Exactly what a merge does: GitHub deletes the branch, the link breaks.
        (wt / ".git").unlink()

        _, rc_remove = run(["git", "worktree", "remove", "--force", str(wt)], cwd=str(repo))
        pruned = prune_broken(repo)
        after, _ = run(["git", "worktree", "list", "--porcelain"], cwd=str(repo))

        return [
            ("remove_refuses_broken_link", rc_remove != 0,
             "git worktree remove must REFUSE when .git is missing"),
            ("prune_clears_what_remove_cannot", pruned == 1 and _norm(str(wt)) not in _norm(after),
             f"prune_broken() returned {pruned}"),
        ]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _drill_hook_tick() -> list[tuple[str, bool, str]]:
    rows: list[tuple[str, bool, str]] = []
    tmp = tempfile.mkdtemp(prefix="reaper-tick-")
    try:
        stamp = Path(tmp) / "last-run"
        now = time.time()
        rows.append(("throttle_never_run_is_due", is_due(stamp, 6.0, now), "no stamp -> due"))
        stamp.write_text("", encoding="utf-8")
        os.utime(stamp, (now - 3600, now - 3600))
        rows.append(("throttle_recent_skips", not is_due(stamp, 6.0, now), "1h ago -> throttled"))
        os.utime(stamp, (now - 7 * 3600, now - 7 * 3600))
        rows.append(("throttle_old_is_due", is_due(stamp, 6.0, now), "7h ago -> due"))

        quiet = render_last_run({"pruned": 0, "worktrees_removed": [], "branches_deleted": []})
        rows.append(("report_silent_when_nothing_done", quiet == "", f"got {quiet!r}"))
        loud = render_last_run(
            {"pruned": 1, "worktrees_removed": [{"branch": "fix/a"}], "branches_deleted": [{"name": "b"}]}
        )
        rows.append(("report_names_what_it_took", "fix/a" in loud and "pruned 1" in loud, loud))
        err = render_last_run({"error": "offline"})
        rows.append(("report_surfaces_error", "offline" in err, err))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return rows


def _drill_delegation() -> list[tuple[str, bool, str]]:
    """This file must never grow a second opinion about what is SAFE.

    Two classifiers disagreeing is worse than one being wrong, because the
    disagreement is invisible until it deletes something.
    """
    src = Path(__file__).read_text(encoding="utf-8")

    # Parse rather than grep. A text search cannot tell a forbidden CALL from the
    # word in a comment explaining why it is forbidden -- and this docstring is
    # full of those. It matched five false positives on the first run.
    #
    # `git worktree list --porcelain` is deliberately NOT forbidden: prune_broken
    # uses it to COUNT registrations, which decides nothing about safety.
    forbidden = {"status", "cherry", "merge-base", "--merged", "diff", "ls-files"}
    offenders: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "run"):
            continue
        if not (node.args and isinstance(node.args[0], ast.List)):
            continue
        argv = [e.value for e in node.args[0].elts if isinstance(e, ast.Constant)]
        hits = forbidden.intersection(argv)
        if hits:
            offenders.append(f"line {node.lineno}: git {' '.join(sorted(hits))}")

    return [
        (
            "no_second_classifier",
            not offenders,
            f"this file decides SAFE itself ({'; '.join(offenders)}) — delegate to worktree_census",
        ),
        (
            "census_is_actually_imported",
            "from worktree_census import" in src and "= census(" in src,
            "the brain must be imported and called, not reimplemented",
        ),
    ]


def _drill_remote() -> list[tuple[str, bool, str]]:
    now = time.time()
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 30 * 86400))
    fresh = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3600))
    live = {"shipped-old", "shipped-fresh", "main", "still-open"}
    prs = [
        {"headRefName": "shipped-old", "mergedAt": old},
        {"headRefName": "shipped-fresh", "mergedAt": fresh},
        {"headRefName": "main", "mergedAt": old},
        {"headRefName": "already-gone", "mergedAt": old},
        {"headRefName": "no-date", "mergedAt": None},
    ]
    got = stale_remote_branches(prs, live, now, older_than_days=14)
    return [
        # NEGATIVE CONTROL first: a remote reaper that deletes nothing passes
        # every refusal below and is exactly as useless as no reaper at all.
        ("remote_deletes_the_stale_one", got == ["shipped-old"], f"got {got}"),
        ("remote_respects_grace_period", "shipped-fresh" not in got, f"got {got}"),
        ("remote_never_touches_main", "main" not in got, f"got {got}"),
        ("remote_skips_already_deleted", "already-gone" not in got, f"got {got}"),
        ("remote_refuses_unparseable_date", "no-date" not in got, f"got {got}"),
    ]


def drill() -> int:
    rows = (
        _drill_idle_floor()
        + _drill_prune_before_remove()
        + _drill_hook_tick()
        + _drill_remote()
        + _drill_delegation()
    )
    width = max(len(n) for n, _, _ in rows)
    failed = sum(0 if ok else 1 for _, ok, _ in rows)
    for name, ok, note in rows:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name:<{width}}  {'' if ok else note}".rstrip())
    print(f"\n{len(rows) - failed}/{len(rows)} drill cases passed")
    if failed:
        print("DRILL FAILED — the reaper no longer behaves as documented.")
    return 1 if failed else 0


# ─────────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Sweep the worktrees and branches the census has already judged SAFE."
    )
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    ap.add_argument("--drill", action="store_true", help="prove it still refuses")
    ap.add_argument("--hook-tick", metavar="STATE_DIR",
                    help="hook mode: report the last run, exit 0 if due, 3 if throttled")
    ap.add_argument("--throttle-hours", type=float, default=6.0,
                    help="--hook-tick only: minimum gap between runs")
    ap.add_argument("--max", type=int, default=DEFAULT_MAX_PER_RUN,
                    help="cap deletions per run — one worktree takes 10-30 s to remove on "
                         "Windows because of node_modules")
    ap.add_argument("--max-branches", type=int, default=DEFAULT_MAX_BRANCHES,
                    help="cap branch deletions per run — separate from --max because a branch "
                         "delete is a ref write, not a recursive rmdir")
    ap.add_argument("--min-idle-hours", type=float, default=DEFAULT_MIN_IDLE_HOURS,
                    help="never touch a worktree touched more recently than this")
    ap.add_argument("--keep-branches", action="store_true",
                    help="remove worktrees only; leave every branch alone")
    ap.add_argument("--remote", action="store_true",
                    help="CI mode: delete REMOTE branches whose PR merged long ago")
    ap.add_argument("--older-than-days", type=float, default=14.0,
                    help="--remote only: grace period after the merge")
    args = ap.parse_args(argv)

    if args.drill:
        return drill()
    if args.hook_tick:
        return hook_tick(Path(args.hook_tick), args.throttle_hours)

    # The MAIN repo, not this worktree: `--show-toplevel` inside a worktree
    # returns that worktree, and only the main repo can remove or prune others.
    common, rc = run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"])
    if rc != 0 or not common:
        print("Not in a git repository.")
        return 1
    repo_root = Path(common).parent

    if (repo_root / "docs" / "maintenance" / "REAPER-OFF").exists():
        print("REAPER-OFF present — doing nothing.")
        return 0

    if args.remote:
        return reap_remote(repo_root, args.apply, args.older_than_days, args.max)

    # Single-execution lock. A hook-launched run and a manual one must never
    # interleave: both would judge the same worktree SAFE at the same instant.
    lock = repo_root / ".git" / "reaper.lock"
    holding = False
    if args.apply:
        try:
            lock.mkdir()
            holding = True
        except FileExistsError:
            print(f"Another reaper run holds {lock} — exiting.")
            return 0
        except OSError:
            pass

    try:
        res = sweep(
            repo_root=repo_root,
            apply=args.apply,
            max_per_run=args.max,
            max_branches=args.max_branches,
            min_idle_hours=args.min_idle_hours,
            keep_branches=args.keep_branches,
            self_path=os.getcwd(),
        )
    except Exception as exc:  # noqa: BLE001 — a detached sweep must report, not vanish
        payload = {"error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(payload) if args.json else f"reaper failed: {payload['error']}")
        return 1
    finally:
        if holding:
            try:
                lock.rmdir()
            except OSError:
                pass

    if args.json:
        print(json.dumps(res))
        return 0

    verb = "removed" if args.apply else "would remove"
    if res["pruned"]:
        print(f"pruned {res['pruned']} broken worktree registration(s)\n")
    print(f"worktrees {verb}: {len(res['worktrees_removed'])}")
    for w in res["worktrees_removed"]:
        print(f"  - {(w['branch'] or '(detached)'):<44} {w['why']}")
    print(f"\nbranches {'deleted' if args.apply else 'would delete'}: {len(res['branches_deleted'])}")
    for b in res["branches_deleted"][:20]:
        print(f"  - {b['name']}")
    print(f"\nskipped: {len(res['skipped'])}")
    for s in sorted(res["skipped"], key=lambda s: s["reason"])[:20]:
        print(f"  - {str(s['name'])[-52:]:<54} {s['reason']}")
    if not args.apply:
        print("\nDRY RUN — nothing deleted. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
