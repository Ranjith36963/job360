#!/usr/bin/env python3
"""cage_replay.py — run the merge cage against REAL pull requests, properly.

WHY THIS FILE HAD TO EXIST
--------------------------
`merge_cage.py --replay` used to loop `decide(pr)` with no baseline. The RATCHET
cage returns `not_checked` without one, and `not_checked` blocks, so the answer
was 0/N by construction — for every N, on every repo, forever. It looked like a
measurement. Verified 2026-08-17 by running it: 27 real PRs judged, 0 allowed,
`no baseline was given` present on all 27.

A tree-scoped ratchet cannot be judged from one checkout. It needs two, per PR:

    BASE   the PR's base commit          -> the numbers to compare against
    MERGE  refs/pull/<N>/merge           -> the tree that would actually ship

and a third thing that is not a checkout at all: the RULES, which must come from
somewhere the PR cannot edit. That is why the cage is invoked from ITS OWN
checkout with `--tree <the merge checkout>` rather than by running the copy of
merge_cage.py that happens to be inside the PR.

WHAT IT DOES NOT DO
-------------------
It never merges, never pushes, never writes to `main`, and never touches the
checkout it is run from. All git writes land in a scratch worktree under the
system temp directory, which is removed at the end. `--drill` asserts all of
that against a real repository, including that HEAD here is unmoved after a run.

USAGE
    python scripts/cage_replay.py --open                 # every open PR
    python scripts/cage_replay.py --merged 16            # the last 16 merged PRs
    python scripts/cage_replay.py 337 338                # named PRs
    python scripts/cage_replay.py --merged 16 --json out.json
    python scripts/cage_replay.py --drill

READING THE OUTPUT
    On a MERGED PR the owner already said yes and production is fine, so REFUSE
    is a disagreement the cage has to justify. On an OPEN PR there is no ground
    truth; the verdict is just the verdict.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

ROOT = Path(__file__).resolve().parent.parent
CAGE = ROOT / "scripts" / "merge_cage.py"
REPO = os.environ.get("GH_REPO", "Ranjith36963/job360")

# merge_cage's typed exit codes. 0 and 1 are verdicts; everything else is the
# cage failing to judge, which must never be silently counted as "refused".
EXIT_ALLOW, EXIT_REFUSE = 0, 1


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    """Every subprocess in this file goes through here.

    `encoding="utf-8", errors="replace"` is not decoration: `text=True` alone
    decodes with the Windows locale, and the cage's reason strings are full of
    em-dashes. That combination has crashed this harness before.
    """
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)


def git(args: list[str], cwd: Path | None = None) -> str:
    p = run(["git", *args], cwd=cwd)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])} failed: {(p.stderr or p.stdout)[:300]}")
    return p.stdout.strip()


def gh(args: list[str]) -> str:
    p = run(["gh", *args], timeout=180)
    if p.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {p.stderr.strip()[:300]}")
    return p.stdout.strip()


@dataclass
class Judgement:
    pr: int
    state: str = "?"
    title: str = ""
    base_ref: str = ""
    verdict: str = "NOT JUDGED"
    exit_code: int | None = None
    reasons: list[str] = field(default_factory=list)
    cages: list[dict] = field(default_factory=list)
    note: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict == "ALLOW"

    def failing_cages(self) -> list[str]:
        """Which cages refused — and NEVER an empty list when nothing was judged.

        A cage that broke writes no verdict file, so `self.cages` is empty and a
        naive comprehension returns `[]`, which in a table reads exactly like
        "no cage objected". That is this repo's signature failure wearing a new
        coat, so the absent case is spelled out instead.
        """
        if self.verdict not in ("ALLOW", "REFUSE"):
            return [f"NO VERDICT ({self.verdict})"]
        if not self.cages:
            return ["NO CAGE REPORTED — this is not a clean bill of health"]
        return [c["name"] for c in self.cages if c["status"] != "pass"]


def pr_facts(pr: int) -> dict:
    """The three SHAs that matter, straight from the API.

    `merge_commit_sha` is what merge_cage compares its own checkout against, so
    it is read from the SAME endpoint the cage reads — not from `gh pr view`,
    which reports a different field for merged PRs and would silently make the
    ground check compare two unrelated commits.
    """
    data = json.loads(gh(["api", f"repos/{REPO}/pulls/{pr}"]))
    return {
        "state": "merged" if data.get("merged_at") else data.get("state", "?"),
        "title": data.get("title") or f"PR #{pr}",
        "base_ref": ((data.get("base") or {}).get("ref")) or "",
        "base_sha": ((data.get("base") or {}).get("sha")) or "",
        "merge_sha": data.get("merge_commit_sha") or "",
        # `mergeable` is a THIRD state, not a bool: null means GitHub has not
        # finished computing the test merge yet. Reading null as "it conflicts"
        # would report a wrong cause for a PR that is perfectly fine, and this
        # repo has paid ten times for a guard that named the wrong question.
        "mergeable": data.get("mergeable"),
        "mergeable_state": data.get("mergeable_state") or "",
    }


def fetch(sha_or_ref: str) -> None:
    """Bring a commit into the local object store. Read-only against origin."""
    run(["git", "fetch", "--quiet", "origin", sha_or_ref], cwd=ROOT, timeout=300)


def have(sha: str) -> bool:
    return bool(sha) and run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=ROOT).returncode == 0


def checkout(tree: Path, sha: str) -> None:
    git(["checkout", "--quiet", "--detach", sha], cwd=tree)
    # Belt and braces: a stale file left behind by the previous PR's tree would
    # make a ratchet measure a mixture of two commits and never say so.
    git(["clean", "-qfdx"], cwd=tree)


def measure(tree: Path) -> str:
    """Ratchet values for `tree`, measured BY THIS CHECKOUT'S CAGE.

    The rules and the reading code come from ROOT; only the numbers come from
    `tree`. That is the whole point of --tree: a PR cannot supply the judge that
    judges it.
    """
    p = run([sys.executable, str(CAGE), "--measure", "--tree", str(tree)], cwd=ROOT)
    if p.returncode != 0:
        raise RuntimeError(f"--measure failed on {tree}: {(p.stderr or p.stdout)[:300]}")
    return p.stdout.strip().splitlines()[-1]


def judge(pr: int, tree: Path, baseline: str) -> Judgement:
    with tempfile.TemporaryDirectory(prefix="cage_verdict_") as vd:
        vj = Path(vd) / "verdict.json"
        p = run([sys.executable, str(CAGE), str(pr), "--baseline", baseline,
                 "--tree", str(tree), "--verdict-json", str(vj)], cwd=ROOT)
        cages: list[dict] = []
        if vj.exists():
            cages = json.loads(vj.read_text(encoding="utf-8")).get("cages", [])
    out = (p.stdout or "") + (p.stderr or "")
    j = Judgement(pr=pr, exit_code=p.returncode)
    j.cages = cages
    j.reasons = [ln.strip()[2:].strip() for ln in out.splitlines() if ln.strip().startswith("* ")]
    if p.returncode == EXIT_ALLOW:
        j.verdict = "ALLOW"
    elif p.returncode == EXIT_REFUSE:
        j.verdict = "REFUSE"
    else:
        # NOT a refusal. A cage that could not judge has said nothing, and
        # counting it as "refused" would let a broken cage look like a strict
        # one — the exact confusion the typed exit codes exist to prevent.
        j.verdict = f"CAGE BROKE (exit {p.returncode})"
        j.note = out.strip()[-400:]
    return j


def judge_one(pr: int, tree: Path) -> Judgement:
    """One PR, end to end: resolve, check out the base, measure, check out the
    merge tree, judge."""
    facts = pr_facts(pr)
    j = Judgement(pr=pr, state=facts["state"], title=facts["title"], base_ref=facts["base_ref"])

    merge_sha = facts["merge_sha"]
    if not merge_sha and facts["mergeable"] is None:
        # GitHub computes the test merge lazily; the FIRST read of a cold PR can
        # legitimately return null for both. Ask once more before saying anything
        # about the PR, because "I asked too early" and "it conflicts" are
        # different facts and only one of them is the PR's fault.
        facts = pr_facts(pr)
        merge_sha = facts["merge_sha"]
    if not merge_sha:
        j.verdict = "CANNOT JUDGE"
        if facts["mergeable"] is False:
            j.note = (f"this PR does not merge cleanly (`mergeable_state: "
                      f"{facts['mergeable_state']}`), so GitHub has no merge commit and there "
                      f"is no tree that would ship. Nothing was measured. FIX: resolve the "
                      f"conflict, then re-judge.")
        else:
            j.note = ("GitHub still reports no merge commit and no mergeability after a "
                      "second read. Nothing was measured — this is not a clean bill of "
                      "health. FIX: re-run once GitHub has computed the test merge.")
        return j

    for ref in (merge_sha, f"pull/{pr}/merge", f"pull/{pr}/head", facts["base_sha"]):
        if have(merge_sha) and have(facts["base_sha"]):
            break
        fetch(ref)
    if not have(merge_sha):
        j.verdict = "CANNOT JUDGE"
        j.note = (f"could not fetch the merge commit {merge_sha[:10]} — GitHub drops "
                  f"`refs/pull/<N>/merge` for some closed PRs. Nothing was measured.")
        return j

    base_sha = git(["rev-parse", f"{merge_sha}^1"], cwd=ROOT)

    try:
        checkout(tree, base_sha)
        baseline = measure(tree)
        checkout(tree, merge_sha)
        out = judge(pr, tree, baseline)
    except Exception as exc:  # a runner that dies mid-sweep must say which PR
        j.verdict = "CANNOT JUDGE"
        j.note = f"{type(exc).__name__}: {exc}"
        return j

    out.state, out.title, out.base_ref = j.state, j.title, j.base_ref
    return out


# ─────────────────────────────────────────────────────────────────────────────


def scratch_tree(where: Path) -> Path:
    """A detached worktree of THIS repo, for checkouts. Never `main`, never here."""
    tree = where / "cage_tree"
    git(["worktree", "add", "--detach", "--quiet", str(tree), "HEAD"], cwd=ROOT)
    return tree


def drop_tree(tree: Path) -> None:
    run(["git", "worktree", "remove", "--force", str(tree)], cwd=ROOT)
    run(["git", "worktree", "prune"], cwd=ROOT)


def sweep(prs: list[int]) -> list[Judgement]:
    here_before = git(["rev-parse", "HEAD"], cwd=ROOT)
    results: list[Judgement] = []
    with tempfile.TemporaryDirectory(prefix="cage_replay_") as td:
        tree = scratch_tree(Path(td))
        try:
            for pr in prs:
                j = judge_one(pr, tree)
                results.append(j)
                mark = {"ALLOW": "ALLOW ", "REFUSE": "REFUSE"}.get(j.verdict, j.verdict)
                top = (j.reasons or [j.note or "-"])[0]
                bad = ",".join(j.failing_cages()) or "-"
                print(f"  #{j.pr:>4} {j.state:<6} {mark:<14} {bad:<26} {top[:70]}")
        finally:
            drop_tree(tree)
    here_after = git(["rev-parse", "HEAD"], cwd=ROOT)
    if here_before != here_after:  # pragma: no cover - a bug, not a state
        raise RuntimeError(f"the runner moved its own checkout {here_before} -> {here_after}")
    return results


def report(results: list[Judgement]) -> None:
    merged = [r for r in results if r.state == "merged"]
    agree = [r for r in merged if r.allowed]
    broke = [r for r in results if r.verdict.startswith("CAGE BROKE")]
    print()
    print(f"judged {len(results)}; ALLOW {sum(1 for r in results if r.allowed)}; "
          f"cage broke on {len(broke)}")
    if merged:
        print(f"AGREEMENT WITH GROUND TRUTH: {len(agree)}/{len(merged)} merged PRs "
              f"({100 * len(agree) / len(merged):.0f}%) — the owner shipped every one of "
              f"these and production is fine, so a REFUSE here is a claim the cage owes an "
              f"argument for.")
    print("This is a measurement, not a target. Widening ALLOW to raise it would manufacture "
          "the decay the ratchets exist to catch.")


# ─────────────────────────────────────────────────────────────────────────────
# The drill. Every guard in this repo must declare one, and it must be a REAL
# input end to end — this harness has already been burned by a self-test that
# grepped its own source and stayed green after the code it guarded was deleted.
# ─────────────────────────────────────────────────────────────────────────────


def self_drill() -> int:
    print("DRILL — cage_replay against a real throwaway repository.")
    print("=" * 72)
    results: list[tuple[str, bool, str]] = []

    def ok(name: str, passed: bool, detail: str = "") -> None:
        results.append((name, passed, "" if passed else detail))

    # 1. THIS RUNNER MUST HAVE NO MERGE SURFACE AT ALL. Asserted through the real
    #    CLI, not by grepping the source: a grep-based self-test in this repo
    #    stayed fully green after the only authorisation call it guarded was
    #    deleted.
    for flag in ("--merge", "--push", "--approve"):
        p = run([sys.executable, str(__file__), "1", flag])
        ok(f"`{flag}` is rejected — this runner has no way to merge or push",
           p.returncode != 0 and "unrecognized arguments" in (p.stderr + p.stdout),
           f"exit {p.returncode}: {(p.stderr + p.stdout)[:150]}")

    # 2. A REAL REPOSITORY, a real merge, a real two-checkout measurement.
    with tempfile.TemporaryDirectory(prefix="cage_replay_drill_") as td:
        repo = Path(td) / "repo"
        repo.mkdir()
        git(["init", "--quiet", "-b", "main"], cwd=repo)
        git(["config", "user.email", "drill@example.com"], cwd=repo)
        git(["config", "user.name", "drill"], cwd=repo)
        (repo / "count.txt").write_text("5\n", encoding="utf-8")
        git(["add", "-A"], cwd=repo)
        git(["commit", "--quiet", "-m", "base"], cwd=repo)
        base = git(["rev-parse", "HEAD"], cwd=repo)
        (repo / "count.txt").write_text("3\n", encoding="utf-8")
        git(["commit", "--quiet", "-am", "better"], cwd=repo)
        head = git(["rev-parse", "HEAD"], cwd=repo)

        # measure() must read the tree it is POINTED AT, not the tree it runs in.
        # That is the entire premise of --tree; if it silently read `here` the
        # baseline and the judgement would be the same numbers and every ratchet
        # would compare a value to itself.
        probe = ROOT / "scripts" / "merge_cage.py"
        saved_env = os.environ.get("GH_REPO")
        try:
            work = Path(td) / "work"
            git(["worktree", "add", "--detach", "--quiet", str(work), base], cwd=repo)
            m_base = run([sys.executable, str(probe), "--measure", "--tree", str(work)], cwd=ROOT)
            checkout(work, head)
            m_head = run([sys.executable, str(probe), "--measure", "--tree", str(work)], cwd=ROOT)
            # Both are the real cage's real ratchets over a tree that has none of
            # them, so both must report `absent` — and `absent`, crucially, must
            # not read as a number.
            both_absent = all(
                json.loads(p.stdout.strip().splitlines()[-1])[name]["status"] == "absent"
                for p in (m_base, m_head)
                for name in json.loads(p.stdout.strip().splitlines()[-1])
            )
            ok("--tree measures the tree it is pointed at, and a tree with no ratchet "
               "reports ABSENT rather than a number",
               m_base.returncode == 0 and m_head.returncode == 0 and both_absent,
               f"base={m_base.stdout.strip()[:120]} head={m_head.stdout.strip()[:120]}")
            run(["git", "worktree", "remove", "--force", str(work)], cwd=repo)
        finally:
            if saved_env is not None:
                os.environ["GH_REPO"] = saved_env

    # 3. THE RUNNER MUST NOT MOVE ITS OWN CHECKOUT. `sweep` raises if it does;
    #    prove HEAD here is where it was after a real (empty) sweep.
    before = git(["rev-parse", "HEAD"], cwd=ROOT)
    empty = sweep([])
    after = git(["rev-parse", "HEAD"], cwd=ROOT)
    ok("a sweep leaves this checkout exactly where it found it",
       before == after and empty == [], f"{before[:10]} -> {after[:10]}")

    # 4. A CAGE THAT COULD NOT JUDGE IS NOT A REFUSAL. Counting exit 3 as REFUSE
    #    would let a broken cage be read as a strict one — the whole reason the
    #    exit codes are typed.
    j = Judgement(pr=1, verdict="CAGE BROKE (exit 3)")
    ok("`cage broke` is not counted as a refusal and not counted as an allow",
       not j.allowed and j.verdict != "REFUSE", f"verdict={j.verdict} allowed={j.allowed}")
    ok("a PR with no verdict never renders as `no cage objected`",
       j.failing_cages() and "NO VERDICT" in j.failing_cages()[0]
       and Judgement(pr=1, verdict="REFUSE").failing_cages() ==
       ["NO CAGE REPORTED — this is not a clean bill of health"],
       f"an empty cage list rendered as {j.failing_cages()}")

    # 5. NEGATIVE CONTROL — a normal ALLOW really does read as agreement, or the
    #    agreement number can only ever go one way.
    good = Judgement(pr=2, state="merged", verdict="ALLOW")
    ok("NEGATIVE CONTROL (an ALLOW on a merged PR counts as agreement)",
       good.allowed and good.state == "merged", "an allow did not register")

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
        print("This runner did not do something it claims to do. Do not trust its numbers.")
        return 1
    print("It measures the tree it is pointed at, it cannot merge, and it left main alone.")
    return 0


def main(argv: list[str] | None = None) -> int:
    # allow_abbrev=False, AND THIS WAS FOUND BY THIS FILE'S OWN DRILL ON ITS
    # FIRST RUN. argparse abbreviates by default, so `--merge` was accepted as a
    # unique prefix of `--merged` and the drill asserting "this runner has no
    # merge surface" went red for the right reason: the surface existed, spelled
    # by accident. On a repo where `main` auto-deploys, a flag that means
    # something else than it says is not a typo, it is a trapdoor.
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    ap.add_argument("prs", nargs="*", type=int, help="PR numbers to judge")
    ap.add_argument("--open", action="store_true", help="judge every open PR")
    ap.add_argument("--merged", type=int, metavar="N", help="judge the last N merged PRs")
    ap.add_argument("--json", metavar="FILE", help="write the full result set here")
    ap.add_argument("--drill", action="store_true", help="prove this runner does what it says")
    args = ap.parse_args(argv)

    if args.drill:
        return self_drill()

    prs = list(args.prs)
    if args.open:
        prs += [int(p["number"]) for p in
                json.loads(gh(["pr", "list", "--state", "open", "--limit", "100",
                               "--json", "number"]))]
    if args.merged:
        prs += [int(p["number"]) for p in
                json.loads(gh(["pr", "list", "--state", "merged", "--limit", str(args.merged),
                               "--json", "number"]))]
    prs = sorted(set(prs), reverse=True)
    if not prs:
        ap.error("nothing to judge — pass PR numbers, --open or --merged N")

    print(f"REPLAY — {len(prs)} PR(s), two checkouts each (base for the baseline, "
          f"refs/pull/<N>/merge for the tree that would ship).")
    print(f"The RULES come from {ROOT} and never from the PR.")
    results = sweep(prs)
    report(results)

    if args.json:
        Path(args.json).write_text(json.dumps(
            [{"pr": r.pr, "state": r.state, "title": r.title, "base": r.base_ref,
              "verdict": r.verdict, "exit": r.exit_code, "reasons": r.reasons,
              "cages": r.cages, "note": r.note}
             for r in results], indent=1), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
