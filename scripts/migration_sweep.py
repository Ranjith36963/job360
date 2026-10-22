#!/usr/bin/env python3
"""Re-judge every open PR's migration numbers after main gains a migration.

WHY THIS EXISTS (blocker B30). The MIGRATIONS cage in merge_cage.py judges a PR
against live main, but only when the cage runs. A PR queued for auto-merge at
10:00 with 0077 is not re-judged when another 0077 lands at 10:05, and GitHub
would merge it on the old green `merge-cage`. This sweep closes that gap: run on
every push to main that touches backend/migrations, it re-reads main once and,
for every open PR whose new migration now clashes, sets `merge-cage` to failure
on the PR's head and withdraws auto-merge.

It only ever REFUSES. It never merges, queues or approves anything, so it may
run on `push` (gate_wiring_check rule G2 forbids merging on a post-merge
trigger, not refusing). Auto-merge is withdrawn through the GraphQL mutation,
not `gh pr merge --disable-auto`, because the latter is a `gh pr merge` command
and reads to that guard as a merge.

Exit codes: 0 = swept (clashes, if any, were acted on), 1 = could not sweep.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import merge_cage as cage  # noqa: E402 - sibling script, not a package

STATUS_CONTEXT = cage.STATUS_CONTEXT
_DISABLE = ("mutation($id: ID!) { disablePullRequestAutoMerge(input: {pullRequestId: $id}) "
            "{ clientMutationId } }")


def open_prs() -> list[dict]:
    prs: list[dict] = []
    page = 1
    while True:
        batch = json.loads(cage.gh(["api", f"repos/{cage.REPO}/pulls?state=open"
                                           f"&per_page={cage.PER_PAGE}&page={page}"]))
        prs.extend(batch)
        if len(batch) < cage.PER_PAGE:
            return prs
        page += 1


def sweep(dry_run: bool = False) -> int:
    tree = json.loads(cage.gh(["api", f"repos/{cage.REPO}/git/trees/{cage.MAIN_BRANCH}"
                                      f"?recursive=1"]))
    main_tree, truncated = tree.get("tree") or [], bool(tree.get("truncated"))
    clashes = 0
    for pr in open_prs():
        n = pr["number"]
        if ((pr.get("base") or {}).get("ref")) != cage.MAIN_BRANCH:
            continue
        files = json.loads(cage.gh(["api", f"repos/{cage.REPO}/pulls/{n}/files"
                                           f"?per_page={cage.PER_PAGE}"]))
        if len(files) >= cage.PER_PAGE:
            print(f"::warning::PR #{n} changes {cage.PER_PAGE}+ files; only the first page was "
                  f"read here (the cage itself refuses a full page at judge time)")
        reasons = cage.judge_migration_numbers(files, main_tree, truncated)
        if not reasons:
            continue
        clashes += 1
        sha = (pr.get("head") or {}).get("sha") or ""
        desc = reasons[0].split(". Two PRs")[0] + ". " + reasons[0].rsplit("FIX: ", 1)[-1]
        print(f"PR #{n} ({sha[:7]}): {reasons[0]}")
        if dry_run:
            continue
        cage.gh(["api", f"repos/{cage.REPO}/statuses/{sha}", "-f", "state=failure",
                 "-f", f"context={STATUS_CONTEXT}", "-f", f"description={desc[:140]}"])
        if pr.get("auto_merge"):
            cage.gh(["api", "graphql", "-f", f"query={_DISABLE}", "-f", f"id={pr['node_id']}"])
            print(f"  auto-merge withdrawn on #{n}")
    print(f"swept: {clashes} open PR(s) clash with main's migrations")
    return 0


def drill(blind: bool = False) -> int:
    """Replay #1088 then #1006 through a fake GitHub. Must flag only the clash.

    `--blind` turns the judge off; the drill must then go RED (exit 1).
    """
    mig = "backend/migrations/"
    main_tree = [{"path": f"{mig}{s}.{d}.sql", "sha": f"{s}.{d}"}
                 for s in ("0076_email_change_undos", "0077_outreach_variants") for d in ("up", "down")]
    prs = [{"number": 1006, "node_id": "N1006", "auto_merge": {"x": 1}, "base": {"ref": "main"},
            "head": {"sha": "a" * 40}},
           {"number": 2, "node_id": "N2", "auto_merge": {"x": 1}, "base": {"ref": "main"},
            "head": {"sha": "b" * 40}},
           {"number": 3, "node_id": "N3", "auto_merge": None, "base": {"ref": "main"},
            "head": {"sha": "c" * 40}}]
    files = {1006: [{"filename": f"{mig}0077_receipt_answers_from.up.sql", "status": "added", "sha": "x"}],
             2: [{"filename": "docs/README.md", "status": "modified"}],
             3: [{"filename": f"{mig}0078_free.up.sql", "status": "added", "sha": "y"}]}
    calls: list[str] = []

    def fake_gh(a: list[str]) -> str:
        j = " ".join(a)
        calls.append(j)
        if "git/trees/" in j:
            return json.dumps({"truncated": False, "tree": main_tree})
        if "pulls?state=open" in j:
            return json.dumps(prs)
        for n, f in files.items():
            if f"pulls/{n}/files" in j:
                return json.dumps(f)
        return "{}"

    saved_gh, saved_judge = cage.gh, cage.judge_migration_numbers
    try:
        cage.gh = fake_gh
        if blind:
            cage.judge_migration_numbers = lambda *_a, **_k: []
        sweep()
    finally:
        cage.gh, cage.judge_migration_numbers = saved_gh, saved_judge
    failed = [c for c in calls if "state=failure" in c]
    withdrawn = [c for c in calls if "disablePullRequestAutoMerge" in c]
    checks = [
        ("#1006's 0077 gets merge-cage=failure", any("a" * 40 in c for c in failed)),
        ("#1006's auto-merge is withdrawn", any("id=N1006" in c for c in withdrawn)),
        ("the no-migration PR and the free-number PR are left alone", len(failed) == 1),
        ("nothing is merged or queued", not any(" merge" in c and "graphql" not in c for c in calls)),
    ]
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    ok = all(p for _, p in checks)
    print(f"DRILL RESULT: {sum(p for _, p in checks)}/{len(checks)} passed")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if "--drill" in argv:
        return drill(blind="--blind" in argv)
    try:
        return sweep(dry_run="--dry-run" in argv)
    except Exception as exc:  # a sweep that could not look must say so, loudly
        print(f"::error::migration sweep could not run ({type(exc).__name__}: {exc})",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    os.environ.setdefault("GH_REPO", cage.REPO)
    raise SystemExit(main(sys.argv[1:]))
