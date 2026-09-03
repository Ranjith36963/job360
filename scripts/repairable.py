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
    norm = path.replace("\\", "/").lstrip("./")
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", help="repo-relative paths; omit to read stdin")
    ap.add_argument("--drill", action="store_true", help="prove this guard can go red")
    ap.add_argument("--paths-only", action="store_true",
                    help="print just the blocked paths, one per line — for shell pipelines")
    args = ap.parse_args()

    if args.drill:
        return drill()

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
    print(f"\n{len(cases) - bad}/{len(cases)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
