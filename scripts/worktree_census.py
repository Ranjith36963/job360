#!/usr/bin/env python3
"""worktree_census.py — the broom. It sorts; it does not sweep.

WHY THIS EXISTS
---------------
Measured on this laptop 2026-08-17: 62 worktrees, 209 local branches. Weekly
branch counts 5, 19, 26, 24, 24, 43, 65 — doubling about every two weeks.
`scripts/branch_prune.sh` has existed for 20 days and has changed nothing,
because of one structural fact:

    GITHUB ACTIONS RUNS IN THE CLOUD AND CAN NEVER SEE D:\\.

So this is not a workflow and must never become one. It is a local command the
owner runs. See RUNNING IT at the bottom of this docstring.

THE MISTAKE THIS FILE EXISTS TO PREVENT
---------------------------------------
The obvious rule is "the branch is merged into main, therefore the folder is
junk". On this machine that rule is wrong in the most expensive direction: 20
worktrees hold uncommitted work and 14 of those sit on branches ALREADY MERGED.
One holds 19 staged files. The obvious rule deletes all 14.

They are two different questions:

    "is the branch merged?"      -> about GIT HISTORY
    "does the folder hold work?" -> about the OWNER'S UNSAVED FILES

Conflating them is the same shape as the ten guards that shipped here unable to
fire: correct-looking code reporting success about a DIFFERENT QUESTION than the
one that mattered. So the ordering below is load-bearing, not stylistic:

    CONTENT IS CHECKED FIRST AND WINS. Merged-ness is only ever a NECESSARY
    condition for SAFE, never a sufficient one.

TWO KINDS OF WORK GIT CANNOT SEE
--------------------------------
`git status --porcelain` prints NOTHING for either of these, so a classifier
that trusts it alone is blind to both:

  * a live `.env` (gitignore:9). Two worktrees on this machine hold real
    secrets.
  * runtime data under `backend/data/`, matched by the bare `data/` rule at
    gitignore:24. 45 of 58 worktrees held some.

Hence VALUABLE_IGNORED below. It deliberately does NOT list `node_modules`,
`.venv`, `__pycache__` or `.next`: those are regenerable, and treating them as
work would mark every worktree KEEP, which is a broom that sorts nothing.

WHAT IS DELIBERATELY NOT REPORTED
---------------------------------
Reclaimed disk space. This machine junctions directories (docker_data.vhdx ->
D:\\DockerData), and on Windows `git worktree remove` deletes an NTFS junction
but not the folder it points at. A byte count would be a confident lie, which is
worse than no number.

RUNNING IT
----------
    python scripts/worktree_census.py                      # print the plan
    python scripts/worktree_census.py --plan-out plan.json # print + save it
    python scripts/worktree_census.py --execute --plan plan.json   # SAFE only

`--execute` re-measures every worktree and REFUSES if anything moved since the
plan was written — otherwise a plan from ten minutes ago can delete a folder the
owner has since started working in. It removes worktrees only; branch deletion
stays with `scripts/branch_prune.sh`, which already keeps the recovery index
(REAPED.log) that makes a bad branch delete undoable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SAFE, KEEP, ASK = "SAFE", "KEEP", "ASK"

# Never SAFE, whatever git history says.
PROTECTED_BRANCHES = {"main", "master", "develop"}

# Content git cannot see, but the owner would miss. Paths are worktree-relative.
# Regenerable caches are NOT here on purpose -- see the docstring.
VALUABLE_IGNORED = (
    ".env",
    ".env.local",
    ".env.production",
    "backend/.env",
    "frontend/.env.local",
    "backend/data",
    "User_info",
)

# `git cherry` walks every commit not upstream. On a branch that diverged long
# ago that is unbounded work, so it is capped: past this many commits ahead we
# decline to guess and say ASK rather than SAFE. Declining is free; a wrong SAFE
# is not.
CHERRY_MAX_COMMITS = 400


# ─────────────────────────────────────────────────────────────────────────────
# rows
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WorktreeRow:
    path: str
    branch: str  # "" when detached
    verdict: str
    reasons: list[str] = field(default_factory=list)
    head: str = ""
    state: str = ""  # fingerprint; --execute refuses when this moves


@dataclass
class BranchRow:
    name: str
    verdict: str
    reasons: list[str] = field(default_factory=list)
    head: str = ""


@dataclass
class Census:
    worktrees: list[WorktreeRow] = field(default_factory=list)
    branches: list[BranchRow] = field(default_factory=list)
    main_ref: str = ""

    @property
    def safe_worktrees(self) -> list[WorktreeRow]:
        return [w for w in self.worktrees if w.verdict == SAFE]

    @property
    def safe_branches(self) -> list[BranchRow]:
        return [b for b in self.branches if b.verdict == SAFE]


# ─────────────────────────────────────────────────────────────────────────────
# git plumbing
# ─────────────────────────────────────────────────────────────────────────────

def _run(cwd: Path, *args: str) -> tuple[int, str]:
    """Run git and return (returncode, stdout).

    encoding+errors are MANDATORY, never `text=True` alone: text=True decodes
    with the Windows locale and dies on an em-dash. That exact bug killed the
    merge cage's reader thread on 3 of 6 PRs, and scripts/encoding_guard.py now
    fails the build for it.
    """
    p = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True,
        encoding="utf-8", errors="replace",
    )
    return p.returncode, p.stdout


def _ok(cwd: Path, *args: str) -> str:
    rc, out = _run(cwd, *args)
    return out if rc == 0 else ""


def _resolve_main_ref(repo: Path) -> str:
    for ref in ("origin/main", "main", "origin/master", "master"):
        rc, _ = _run(repo, "rev-parse", "--verify", "--quiet", ref)
        if rc == 0:
            return ref
    return ""


def _parse_worktree_list(repo: Path) -> list[dict]:
    """Parse `git worktree list --porcelain` into dicts.

    A worktree whose HEAD file is corrupt still appears here, which is why the
    corrupt one on this machine survives `git worktree prune`: the folder exists,
    so git does not consider it stale. The block simply lacks a usable HEAD.
    """
    out = _ok(repo, "worktree", "list", "--porcelain")
    blocks: list[dict] = []
    cur: dict = {}
    for line in out.splitlines():
        if not line.strip():
            if cur:
                blocks.append(cur)
                cur = {}
            continue
        key, _, val = line.partition(" ")
        if key == "worktree":
            if cur:
                blocks.append(cur)
            cur = {"worktree": val}
        elif key == "branch":
            cur["branch"] = val.replace("refs/heads/", "")
        elif key == "HEAD":
            cur["HEAD"] = val
        elif key in ("detached", "bare", "prunable", "locked"):
            cur[key] = val or True
    if cur:
        blocks.append(cur)
    return blocks


def _is_ancestor(repo: Path, rev: str, ref: str) -> bool:
    rc, _ = _run(repo, "merge-base", "--is-ancestor", rev, ref)
    return rc == 0


def _squash_equivalent(repo: Path, rev: str, ref: str) -> bool:
    """True when every commit in `rev` is already upstream by PATCH, not by SHA.

    GitHub's squash-merge rewrites history, so a squash-merged branch is never an
    ancestor of main. `git cherry` compares patch-ids, which is the only git-only
    way to see it -- and git-only matters, because this has to answer offline on
    a laptop, not from the GitHub API.
    """
    rc, ahead = _run(repo, "rev-list", "--count", f"{ref}..{rev}")
    if rc != 0:
        return False
    try:
        n = int(ahead.strip() or "0")
    except ValueError:
        return False
    if n == 0:
        return True
    if n > CHERRY_MAX_COMMITS:
        return False
    rc, out = _run(repo, "cherry", ref, rev)
    if rc != 0:
        return False
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return bool(lines) and all(ln.startswith("-") for ln in lines)


def _valuable_ignored_present(path: Path) -> list[str]:
    """Files the owner would miss that `git status` never mentions."""
    found: list[str] = []
    for rel in VALUABLE_IGNORED:
        p = path / rel
        try:
            if p.is_file() and p.stat().st_size > 0:
                found.append(rel)
            elif p.is_dir() and any(p.iterdir()):
                found.append(rel + "/")
        except OSError:
            # Unreadable is not "absent". Say so rather than assume empty.
            found.append(rel + " (unreadable)")
    return found


def _fingerprint(head: str, porcelain: str, ignored: list[str], locked: bool) -> str:
    h = hashlib.sha256()
    h.update(head.encode())
    h.update(b"\0")
    h.update(porcelain.encode())
    h.update(b"\0")
    h.update("|".join(sorted(ignored)).encode())
    h.update(b"\0")
    h.update(b"L" if locked else b"-")
    return h.hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────────────
# the classifier
# ─────────────────────────────────────────────────────────────────────────────

def classify_worktree(repo: Path, blk: dict, main_ref: str, this_repo: Path) -> WorktreeRow:
    path_s = blk.get("worktree", "")
    path = Path(path_s)
    branch = blk.get("branch", "")
    row = WorktreeRow(path=path_s, branch=branch, verdict=ASK)

    # ── ASK first: states where we cannot honestly answer, or must not touch ──
    if blk.get("bare"):
        row.verdict, row.reasons = KEEP, ["the main/bare repository"]
        return row
    if path.resolve() == this_repo.resolve():
        row.verdict, row.reasons = KEEP, ["this is the repo you are standing in"]
        return row
    if not path.exists():
        row.reasons = ["registered but the folder is gone; `git worktree prune` territory"]
        return row
    if blk.get("locked"):
        note = blk["locked"] if isinstance(blk["locked"], str) else ""
        row.reasons = [f"LOCKED{': ' + note if note else ''} -- another session may be inside it"]
        return row

    rc, head = _run(path, "rev-parse", "--verify", "HEAD")
    head = head.strip()
    if rc != 0 or not head:
        row.reasons = [
            "HEAD is unreadable (corrupt). git refuses to touch it and "
            "`git worktree prune` skips it because the folder still exists"
        ]
        return row
    row.head = head

    rc, porcelain = _run(path, "status", "--porcelain")
    if rc != 0:
        row.reasons = ["`git status` failed here; state unknown"]
        return row

    ignored = _valuable_ignored_present(path)
    row.state = _fingerprint(head, porcelain, ignored, bool(blk.get("locked")))

    # ── KEEP: CONTENT WINS. This block runs BEFORE any merged-ness test, and
    #    that ordering is the whole point of the file. ──────────────────────
    reasons: list[str] = []
    dirty = [ln for ln in porcelain.splitlines() if ln.strip()]
    if dirty:
        staged = sum(1 for ln in dirty if ln[:1] not in (" ", "?"))
        untracked = sum(1 for ln in dirty if ln.startswith("??"))
        modified = len(dirty) - untracked
        bits = []
        if staged:
            bits.append(f"{staged} staged")
        if modified - staged > 0:
            bits.append(f"{modified - staged} modified")
        if untracked:
            bits.append(f"{untracked} untracked")
        reasons.append("uncommitted work: " + ", ".join(bits))
    if ignored:
        reasons.append("holds content git cannot see: " + ", ".join(ignored))

    # ── merged-ness: NECESSARY for SAFE, never sufficient ──────────────────
    if main_ref:
        if _is_ancestor(path, head, main_ref):
            merged_note = f"every commit is already in {main_ref}"
        elif _squash_equivalent(path, head, main_ref):
            merged_note = f"squash-merged: every patch is already in {main_ref}"
        else:
            merged_note = ""
            reasons.append(f"has commits not in {main_ref}")
    else:
        merged_note = ""
        reasons.append("no main ref to compare against")

    if reasons:
        row.verdict, row.reasons = KEEP, reasons
        return row

    row.verdict = SAFE
    row.reasons = ["clean working tree", "nothing git cannot see", merged_note]
    return row


def classify_branches(repo: Path, main_ref: str, worktrees: list[WorktreeRow]) -> list[BranchRow]:
    # `--format` and not the default output: the default prefixes a branch
    # checked out in ANOTHER worktree with `+` (and the current one with `*`).
    # Stripping only `*` silently reads every worktree branch as unmerged --
    # a parser bug that makes the guard pass by accident. It did, once, here.
    names = [n.strip() for n in
             _ok(repo, "branch", "--format=%(refname:short)").splitlines() if n.strip()]
    current = _ok(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()

    held: dict[str, WorktreeRow] = {w.branch: w for w in worktrees if w.branch}

    merged = {n.strip() for n in
              _ok(repo, "branch", "--merged", main_ref,
                  "--format=%(refname:short)").splitlines() if n.strip()} if main_ref else set()

    rows: list[BranchRow] = []
    for name in names:
        head = _ok(repo, "rev-parse", name).strip()
        row = BranchRow(name=name, verdict=KEEP, head=head)

        if name in PROTECTED_BRANCHES:
            row.reasons = ["protected branch"]
            rows.append(row)
            continue
        if name == current:
            row.reasons = ["checked out right here"]
            rows.append(row)
            continue

        wt = held.get(name)
        if wt is not None and wt.verdict != SAFE:
            # Deleting this strands the folder that still holds the work.
            row.reasons = [f"checked out in a {wt.verdict} worktree: {wt.path}"] + wt.reasons[:1]
            rows.append(row)
            continue

        if not main_ref:
            row.reasons = ["no main ref to compare against"]
            rows.append(row)
            continue

        if name in merged or _is_ancestor(repo, name, main_ref):
            row.verdict, row.reasons = SAFE, [f"tip is already in {main_ref}"]
        elif _squash_equivalent(repo, name, main_ref):
            row.verdict, row.reasons = SAFE, [f"squash-merged into {main_ref} (patch-identical)"]
        else:
            rc, ahead = _run(repo, "rev-list", "--count", f"{main_ref}..{name}")
            row.reasons = [f"{ahead.strip() or '?'} commit(s) not in {main_ref}"]
        rows.append(row)
    return rows


def census(repo: Path, main_ref: str | None = None) -> Census:
    repo = Path(repo)
    top = _ok(repo, "rev-parse", "--show-toplevel").strip()
    this_repo = Path(top) if top else repo
    ref = main_ref if main_ref is not None else _resolve_main_ref(repo)

    c = Census(main_ref=ref)
    for blk in _parse_worktree_list(repo):
        c.worktrees.append(classify_worktree(repo, blk, ref, this_repo))
    c.branches = classify_branches(repo, ref, c.worktrees)
    return c


# ─────────────────────────────────────────────────────────────────────────────
# reporting
# ─────────────────────────────────────────────────────────────────────────────

def _print_plan(c: Census) -> None:
    wt, br = c.worktrees, c.branches
    def n(rows, v):
        return sum(1 for r in rows if r.verdict == v)

    print("=" * 78)
    print(f"WORKTREE / BRANCH CENSUS   (compared against {c.main_ref or 'NOTHING -- no main ref!'})")
    print("=" * 78)
    print(f"worktrees: {len(wt):>4}   SAFE {n(wt, SAFE):>3}   KEEP {n(wt, KEEP):>3}   ASK {n(wt, ASK):>3}")
    print(f"branches : {len(br):>4}   SAFE {n(br, SAFE):>3}   KEEP {n(br, KEEP):>3}   ASK {n(br, ASK):>3}")
    print()
    print("NOTHING BELOW HAS BEEN DELETED. This command only sorts.")
    print()

    print("-" * 78)
    print("ASK THE OWNER -- I refuse to guess about these")
    print("-" * 78)
    asks = [r for r in wt if r.verdict == ASK]
    if not asks:
        print("  (none)")
    for r in asks:
        print(f"  {r.path}")
        for why in r.reasons:
            print(f"      {why}")
    print()

    print("-" * 78)
    print("KEEP -- holds work. MERGED-NESS DOES NOT OVERRIDE THIS.")
    print("-" * 78)
    keeps = [r for r in wt if r.verdict == KEEP]
    for r in keeps[:40]:
        print(f"  {r.path}  [{r.branch or 'detached'}]")
        for why in r.reasons:
            print(f"      {why}")
    if len(keeps) > 40:
        print(f"  ... and {len(keeps) - 40} more")
    print()

    print("-" * 78)
    print("SAFE -- clean, nothing hidden, already in main. Commands to run BY HAND:")
    print("-" * 78)
    safe = [r for r in wt if r.verdict == SAFE]
    if not safe:
        print("  (none)")
    for r in safe:
        print(f"  git worktree remove {r.path}")
    print()
    sb = [b for b in br if b.verdict == SAFE]
    print(f"SAFE branches ({len(sb)}). Delete these with the existing reaper, which")
    print("keeps the recovery index (docs/maintenance/REAPED.log):")
    print("  scripts/branch_prune.sh          # dry run")
    print("  scripts/branch_prune.sh --apply  # deletes, logging every tip SHA first")
    print()
    for b in sb[:25]:
        print(f"    {b.name}")
    if len(sb) > 25:
        print(f"    ... and {len(sb) - 25} more")
    print()
    print("No disk-space figure is printed on purpose: on Windows `git worktree")
    print("remove` deletes a junction, not its target, so any byte count would lie.")


def _write_plan(c: Census, out: Path) -> None:
    data = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "main_ref": c.main_ref,
        "safe": [{"path": r.path, "branch": r.branch, "state": r.state, "head": r.head}
                 for r in c.safe_worktrees],
        "safe_branches": [{"name": b.name, "head": b.head} for b in c.safe_branches],
    }
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\nplan written: {out}  ({len(data['safe'])} safe worktree(s))")


def _execute(repo: Path, plan_path: Path) -> int:
    """Remove SAFE worktrees, but only if NOTHING has moved since the plan.

    The refusal is the feature. A plan written ten minutes ago can name a folder
    the owner has since started working in.
    """
    if not plan_path.exists():
        print(f"REFUSING: no plan at {plan_path}. Print a plan first.")
        return 2
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    now = census(repo, plan.get("main_ref") or None)
    live = {r.path: r for r in now.safe_worktrees}

    problems: list[str] = []
    for entry in plan.get("safe", []):
        r = live.get(entry["path"])
        if r is None:
            problems.append(f"{entry['path']}: no longer classified SAFE (or gone)")
        elif r.state != entry.get("state"):
            problems.append(f"{entry['path']}: changed since the plan was printed")
    planned = {e["path"] for e in plan.get("safe", [])}
    for extra in sorted(set(live) - planned):
        problems.append(f"{extra}: became SAFE after the plan; not in it")

    if problems:
        print("REFUSING TO EXECUTE -- the world moved since the plan was printed:")
        for p in problems:
            print(f"  {p}")
        print("\nRe-run without --execute to print a fresh plan, then read it again.")
        return 1

    if not planned:
        print("Nothing SAFE to remove.")
        return 0

    for entry in plan["safe"]:
        rc, _ = _run(repo, "worktree", "remove", entry["path"])
        print(f"  {'removed ' if rc == 0 else 'FAILED  '} {entry['path']}")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# the drill — break it on purpose and demand it refuses
# ─────────────────────────────────────────────────────────────────────────────

def _drill() -> int:
    print("=" * 78)
    print("WORKTREE CENSUS DRILL -- plant work in a merged worktree, demand a refusal")
    print("=" * 78)

    def g(cwd: Path, *a: str) -> None:
        p = subprocess.run(["git", *a], cwd=str(cwd), capture_output=True,
                           encoding="utf-8", errors="replace")
        if p.returncode != 0:
            raise RuntimeError(f"git {' '.join(a)}: {p.stderr}")

    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        origin, repo = root / "o.git", root / "r"
        g(root, "init", "--bare", "-b", "main", str(origin))
        g(root, "clone", str(origin), str(repo))
        g(repo, "config", "user.email", "d@d.d")
        g(repo, "config", "user.name", "d")
        (repo / ".gitignore").write_text("data/\n.env\n", encoding="utf-8")
        g(repo, "add", "-A")
        g(repo, "commit", "-m", "seed")
        g(repo, "push", "-u", "origin", "main")

        wt = root / "wt"
        wt.mkdir()

        def merged(name: str) -> Path:
            p = wt / name
            g(repo, "worktree", "add", "-b", name, str(p))
            (p / f"{name}.txt").write_text("x", encoding="utf-8")
            g(p, "add", "-A")
            g(p, "commit", "-m", name)
            g(repo, "merge", "--no-ff", "-m", f"m {name}", name)
            g(repo, "push", "origin", "main")
            return p

        def verdict_of(p: Path) -> tuple[str, list[str]]:
            for r in census(repo).worktrees:
                if Path(r.path) == p:
                    return r.verdict, r.reasons
            return "MISSING", []

        # NEGATIVE CONTROL 1 — a genuinely clean merged worktree IS safe.
        control = merged("control-clean")
        v, why = verdict_of(control)
        results.append(("NEGATIVE CONTROL: clean+merged worktree is SAFE",
                        v == SAFE, f"got {v} {why}"))

        # Each break below is applied to a worktree that IS merged, so the naive
        # rule would call every one of them safe.
        breaks: list[tuple[str, callable]] = [
            ("modified tracked file",
             lambda p: (p / f"{p.name}.txt").write_text("edited", encoding="utf-8")),
            ("untracked new file",
             lambda p: (p / "scratch.md").write_text("note", encoding="utf-8")),
            ("staged but uncommitted",
             lambda p: ((p / "s.txt").write_text("s", encoding="utf-8"), g(p, "add", "s.txt"))),
            ("a live .env git cannot see",
             lambda p: (p / ".env").write_text("KEY=redacted\n", encoding="utf-8")),
            ("runtime data under backend/data/ git cannot see",
             lambda p: ((p / "backend" / "data").mkdir(parents=True),
                        (p / "backend" / "data" / "jobs.db").write_text("d", encoding="utf-8"))),
        ]
        for i, (label, brk) in enumerate(breaks):
            p = merged(f"broken-{i}")
            before, _ = verdict_of(p)
            brk(p)
            after, why = verdict_of(p)
            ok = before == SAFE and after == KEEP
            results.append((f"{label} -> refuses SAFE",
                            ok, f"was {before}, now {after} {why}"))

        # unmerged, clean
        p = wt / "unmerged"
        g(repo, "worktree", "add", "-b", "unmerged", str(p))
        (p / "u.txt").write_text("u", encoding="utf-8")
        g(p, "add", "-A")
        g(p, "commit", "-m", "u")
        v, why = verdict_of(p)
        results.append(("unmerged branch -> refuses SAFE", v == KEEP, f"got {v} {why}"))

        # locked
        p = merged("locked")
        g(repo, "worktree", "lock", str(p))
        v, why = verdict_of(p)
        results.append(("locked worktree -> ASK, never SAFE", v == ASK, f"got {v} {why}"))

        # corrupt HEAD: the exact 41-zero-byte shape found on
        # .claude/worktrees/wf_888d2a05-67d-3 (verified with od -c).
        p = merged("corrupt")
        (repo / ".git" / "worktrees" / "corrupt" / "HEAD").write_bytes(b"\0" * 41)
        v, why = verdict_of(p)
        results.append(("corrupt HEAD -> ASK", v == ASK, f"got {v} {why}"))

        full = census(repo)
        results.append(("census survives the corrupt worktree and still sorts the rest",
                        len(full.worktrees) >= 9, f"{len(full.worktrees)} rows"))

        # NEGATIVE CONTROL 2 — an unrelated new worktree must not change an
        # existing verdict. A checker that fires at ANY change checks nothing.
        before_v, _ = verdict_of(control)
        merged("unrelated-newcomer")
        after_v, _ = verdict_of(control)
        results.append(("NEGATIVE CONTROL: unrelated worktree does not move the verdict",
                        before_v == after_v == SAFE, f"{before_v} -> {after_v}"))

        # --execute must refuse a plan the world has moved past.
        plan = root / "plan.json"
        main(["--repo", str(repo), "--plan-out", str(plan), "--quiet"])
        (control / "new-idea.md").write_text("wip", encoding="utf-8")
        rc = main(["--repo", str(repo), "--execute", "--plan", str(plan), "--quiet"])
        results.append(("--execute refuses a stale plan",
                        rc != 0 and control.exists(), f"rc={rc}, folder exists={control.exists()}"))

        # --execute must refuse a plan hand-edited to include a KEEP row.
        (control / "new-idea.md").unlink()
        main(["--repo", str(repo), "--plan-out", str(plan), "--quiet"])
        data = json.loads(plan.read_text(encoding="utf-8"))
        victim = wt / "broken-0"
        data["safe"].append({"path": str(victim), "branch": "broken-0", "state": "forged"})
        plan.write_text(json.dumps(data), encoding="utf-8")
        rc = main(["--repo", str(repo), "--execute", "--plan", str(plan), "--quiet"])
        results.append(("--execute refuses a forged SAFE row",
                        rc != 0 and victim.exists(), f"rc={rc}, victim exists={victim.exists()}"))

    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"         {detail}")

    n = sum(1 for _, ok, _ in results if ok)
    print()
    print("=" * 78)
    print(f"DRILL RESULT: {n}/{len(results)} passed")
    if n != len(results):
        print("The broom would have swept up work. Do not ship it.")
        return 1
    print("Every planted piece of work was protected; the clean one stayed sweepable.")
    return 0


# ─────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".", help="repo to census (default: cwd)")
    ap.add_argument("--plan-out", help="write the plan as JSON")
    ap.add_argument("--plan", help="the plan --execute must match")
    ap.add_argument("--execute", action="store_true",
                    help="remove SAFE worktrees only, and only if nothing moved")
    ap.add_argument("--drill", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.drill:
        return _drill()

    repo = Path(args.repo).resolve()

    if args.execute:
        if not args.plan:
            print("REFUSING: --execute needs --plan <file> printed by an earlier run.")
            return 2
        return _execute(repo, Path(args.plan))

    c = census(repo)
    if not args.quiet:
        _print_plan(c)
    if args.plan_out:
        out = Path(args.plan_out)
        _write_plan(c, out) if not args.quiet else out.write_text(
            json.dumps({
                "generated": datetime.now(timezone.utc).isoformat(),
                "main_ref": c.main_ref,
                "safe": [{"path": r.path, "branch": r.branch, "state": r.state, "head": r.head}
                         for r in c.safe_worktrees],
                "safe_branches": [{"name": b.name, "head": b.head} for b in c.safe_branches],
            }, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
