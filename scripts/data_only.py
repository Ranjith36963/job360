#!/usr/bin/env python3
"""Did this change touch only a file's DATA, or did it change the LAW?

THE HOLE THIS CLOSES
--------------------
Dry-running the lane split over all 12 open PRs found the cage arguing with
itself. Eight PRs landed in the `owner` lane, and FIVE of them for one reason:

    #346, #345, #344, #343  ->  scripts/drill_registry.py
    #347, #336              ->  scripts/drill_registry.py + merge_cage.py

`drill_registry.py` is owner-lane because it is a guard-of-guards: it enforces
that every guard declares a drill. Letting a machine edit that enforcement is
how a cage is escaped, so the rule is right.

But the file holds TWO different things in one place:

    REGISTRY = { ... }     <- DATA. Every PR that adds a guard must append here.
    def check(...)         <- LAW. How enforcement actually works.

Registering a new guard is the *intended workflow*, so every harness PR touches
this file by construction — and the rule therefore made the harness lane
permanently empty. A rule that fires on 100% of the traffic it governs is not a
safety rule, it is an off switch with extra steps.

WHY NOT JUST SPLIT THE FILE
---------------------------
Moving `REGISTRY` into its own data file is the tidier design, and it is the
wrong move TODAY: five open PRs are editing this exact file, and restructuring it
would hand all five a conflict. The rule is what is broken, not the file.

WHAT THIS DOES INSTEAD
----------------------
Compares the file's syntax tree before and after, ignoring the declared data
node. If everything else is byte-identical as a tree, the change is DATA ONLY and
the file does not drag the PR into the owner lane. Touch one line of the law and
it does, immediately.

    REGISTRY = {...}  changed, def check() identical   ->  data-only  -> harness
    def check() changed at all                         ->  law        -> owner

This is the same principle that closed the last blind spot: a filename could not
tell a logging fix from a deleted auth check, so we asked a test that could.
A filename cannot tell a registration from a rewrite either — so we ask the AST.

THE HOLE THIS OPENS, AND HOW IT IS PLUGGED
------------------------------------------
If declarations are freely mergeable, a PR could register a new guard as `owed`
(known-undrilled) instead of `drilled`, and slide an unfireable guard in through
the fast lane. Data is free; DEBT is not. `.github/merge-policy.yml` carries an
`owed drills` ratchet, so the count of undrilled guards may hold or fall and may
never rise. Adding an `owed` entry fails the ratchet and the PR stops.

Comments and formatting are invisible to an AST, which is correct here: neither
can change what the law does.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path


class NotParseableError(RuntimeError):
    """One of the two revisions is not valid Python. Refuse; never guess."""


def _top_level_shape(source: str, data_name: str) -> list[str]:
    """Every top-level node as a dumped tree, with the data node replaced by a marker.

    The data node's CONTENTS are deliberately discarded — that is the part we are
    agreeing to ignore. Its POSITION is kept, so deleting the declaration block
    entirely still registers as a structural change.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise NotParseableError(f"not valid Python: {exc}") from exc

    shape: list[str] = []
    for node in tree.body:
        is_data = False
        if isinstance(node, ast.Assign):
            is_data = any(
                isinstance(t, ast.Name) and t.id == data_name for t in node.targets
            )
        elif isinstance(node, ast.AnnAssign):
            is_data = isinstance(node.target, ast.Name) and node.target.id == data_name
        shape.append(f"<DATA:{data_name}>" if is_data else ast.dump(node))
    return shape


def is_data_only(before: str, after: str, data_name: str) -> bool:
    """True when the ONLY structural difference is inside `data_name`.

    Returns False if the data node is absent from either side: a change that
    removes the declaration block is a change to the law, and an unknown shape
    must never be optimistically waved through.
    """
    before_shape = _top_level_shape(before, data_name)
    after_shape = _top_level_shape(after, data_name)
    marker = f"<DATA:{data_name}>"
    if marker not in before_shape or marker not in after_shape:
        return False
    return before_shape == after_shape


def git_show(rev: str, path: str, repo: Path) -> str:
    """Read `path` at `rev`. A missing file is empty text, not an error."""
    proc = subprocess.run(
        ["git", "show", f"{rev}:{path}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=repo,
    )
    return proc.stdout if proc.returncode == 0 else ""


def _drill() -> int:  # noqa: C901 - a drill is a list of cases
    """Break it on purpose. Each case is a way this could quietly say yes."""
    cases: list[tuple[str, bool]] = []

    def check(name: str, got: object, want: object) -> None:
        ok = got == want
        cases.append((name, ok))
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"   got={got!r}"))

    print("data_only.py --drill")

    law = "def check(root):\n    return []\n"
    reg_a = 'REGISTRY = {"a": 1}\n'
    reg_b = 'REGISTRY = {"a": 1, "b": 2}\n'

    # 1. The case this exists for: a guard is registered, the law is untouched.
    check("adding a registry entry is data-only", is_data_only(reg_a + law, reg_b + law, "REGISTRY"), True)

    # 2. The case it must never miss: the law changed too.
    check("changing the law is NOT data-only",
          is_data_only(reg_a + law, reg_b + "def check(root):\n    return ['x']\n", "REGISTRY"), False)

    # 3. Law changed while data stayed identical -- the sneakiest shape, because
    #    the PR's title will say "register a guard".
    check("law-only change is NOT data-only",
          is_data_only(reg_a + law, reg_a + "def check(root):\n    return ['x']\n", "REGISTRY"), False)

    # 4. Deleting the declaration entirely is a structural change, not a data one.
    check("deleting the data node is NOT data-only", is_data_only(reg_a + law, law, "REGISTRY"), False)
    check("adding a data node where none existed is NOT data-only",
          is_data_only(law, reg_a + law, "REGISTRY"), False)

    # 5. A NEW function alongside an untouched registry is law, not data.
    check("adding a new function is NOT data-only",
          is_data_only(reg_a + law, reg_a + law + "def sneak():\n    pass\n", "REGISTRY"), False)

    # 6. Comments and blank lines cannot change behaviour, so they stay data-only.
    check("a comment-only edit stays data-only",
          is_data_only(reg_a + law, reg_a + "# harmless\n" + law, "REGISTRY"), True)

    # 7. An unparseable revision must RAISE, never fall through to True.
    raised = False
    try:
        is_data_only(reg_a + law, "def broken(:\n", "REGISTRY")
    except NotParseableError:
        raised = True
    check("a syntax error raises rather than returning True", raised, True)

    # 8. Naming a node that does not exist must not read as "nothing changed".
    check("an unknown data name is never data-only",
          is_data_only(reg_a + law, reg_b + law, "NOT_A_REAL_NAME"), False)

    passed = sum(1 for _, ok in cases if ok)
    print(f"\n{passed}/{len(cases)}")
    return 0 if passed == len(cases) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Is a file's change data-only, or law?")
    ap.add_argument("--path", help="file to judge")
    ap.add_argument("--data-name", help="top-level assignment treated as data")
    ap.add_argument("--base", default="origin/main", help="revision before")
    ap.add_argument("--head", default="HEAD", help="revision after")
    ap.add_argument("--repo", default=".", help="repository root")
    ap.add_argument("--drill", action="store_true", help="break it on purpose")
    args = ap.parse_args(argv)

    if args.drill:
        return _drill()
    if not args.path or not args.data_name:
        ap.error("--path and --data-name are required unless --drill")

    repo = Path(args.repo).resolve()
    before = git_show(args.base, args.path, repo)
    after = git_show(args.head, args.path, repo)
    if not before or not after:
        # File added or deleted outright. Not a data edit; refuse to soften it.
        print(json.dumps({"path": args.path, "data_only": False,
                          "why": "file added or removed, not edited"}))
        return 1
    try:
        verdict = is_data_only(before, after, args.data_name)
    except NotParseableError as exc:
        print(f"::error::{args.path}: {exc}", file=sys.stderr)
        return 2  # UNMEASURABLE -- deliberately not 1, so callers cannot read it as "no"
    print(json.dumps({"path": args.path, "data_only": verdict,
                      "data_name": args.data_name}))
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
