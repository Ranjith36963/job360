#!/usr/bin/env python3
"""After a rename, nothing may still point at the old path.

WHY THIS EXISTS
---------------
Wave 1 of the harness/product split moved 109 files and rewrote references in 107
others. A move like that fails in exactly one way: something still points at the
old location, nobody notices, and months later a runbook sends you to a file that
has not existed since August.

The usual reach is a compatibility shim -- leave the old path working too. That is
the debt this repo said no to. A shim means BOTH paths are correct forever, so the
split never finishes and the next reader cannot tell which one is real. A stale
reference must BREAK, loudly, once.

WHY IT ASKS THIS QUESTION AND NOT A BIGGER ONE
----------------------------------------------
The first version of this guard checked something broader and more obvious-sounding:
"every repo path mentioned anywhere must exist". Run against this repo it produced
461 findings, and reading them was the lesson:

    backend/src/foo.py        a template placeholder
    backend/audit.out         a file CI generates at run time
    scripts/mypy_ratchet.py   correct -- it is relative to `working-directory: backend`
    backend/frontend          the words "backend/frontend only" in a sentence
    2030.md -> chat.py        a plan describing files not built yet

None of those are broken. A guard with that signal-to-noise gets muted within a
week, and a muted guard is worse than no guard because the repo still looks
protected. So this asks the far narrower question it can actually answer with
certainty: git says these paths were RENAMED; does anything still name the old one?

Zero judgement, zero heuristics, no list to maintain. Every future wave of the
split is covered automatically because the rename set comes from git, not from a
constant in this file.

NO SENTINELS
------------
If the rename set cannot be computed, this exits 2 -- never 0. A checker that
reports "clean" because it could not look is worse than none: you act on it.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

# Archived text is a historical record. Rewriting it would falsify it, and
# demanding its links resolve would make history un-archivable.
ARCHIVE_PREFIXES = ("docs/_archive/", "docs/archive/")


class CannotMeasureError(RuntimeError):
    """The rename set or the file list is unavailable, so no verdict is possible."""


def _git(args: list[str], repo: Path) -> str:
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=repo,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise CannotMeasureError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:200]}")
    return proc.stdout


def renames(repo: Path, base: str, head: str) -> list[tuple[str, str]]:
    """Every (old, new) path git detected as a rename between base and head."""
    out = _git(["diff", "--name-status", "--find-renames", f"{base}...{head}"], repo)
    pairs: list[tuple[str, str]] = []
    for line in out.split("\n"):
        if not line.startswith("R"):
            continue
        parts = line.split("\t")
        if len(parts) == 3:
            pairs.append((parts[1], parts[2]))
    return pairs


def stale(repo: Path, base: str, head: str) -> list[tuple[str, str]]:
    """Every (old_path, file_still_naming_it). Empty means the move finished."""
    pairs = renames(repo, base, head)
    if not pairs:
        return []
    tracked = [f for f in _git(["ls-files"], repo).split("\n") if f]
    if not tracked:
        raise CannotMeasureError("git ls-files returned nothing -- refusing to report clean")

    found: list[tuple[str, str]] = []
    for old, _new in pairs:
        for rel in tracked:
            if rel.startswith(ARCHIVE_PREFIXES):
                continue
            p = repo / rel
            if not p.is_file():
                continue
            try:
                body = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary or unreadable: cannot contain a text reference
            if old in body:
                found.append((old, rel))
    return found


def _drill() -> int:
    """Break it on purpose. The case that matters most is whether it can go blind."""
    cases: list[tuple[str, bool]] = []

    def check(name: str, got: object, want: object) -> None:
        ok = got == want
        cases.append((name, ok))
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"   got={got!r}"))

    def repo(initial: dict[str, str], then: dict[str, str]) -> Path:
        """A repo with `initial` committed, then replaced wholesale by `then`."""
        tmp = Path(tempfile.mkdtemp(prefix="stale-drill-"))

        def run(*a: str) -> None:
            subprocess.run(a, cwd=tmp, capture_output=True, text=True, check=True)

        run("git", "init", "-q")
        run("git", "config", "user.email", "drill@local")
        run("git", "config", "user.name", "drill")
        for name, body in initial.items():
            fp = tmp / name
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(body, encoding="utf-8")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "before")
        run("git", "branch", "-M", "base")
        run("git", "checkout", "-qb", "work")
        for name in list(initial):
            (tmp / name).unlink()
        for name, body in then.items():
            fp = tmp / name
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(body, encoding="utf-8")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "after")
        return tmp

    print("stale_path_check.py --drill")

    body = "x" * 200  # long enough that git scores it a rename, not add+delete

    # 1. Moved, and the pointer was updated. Clean.
    r = repo({"docs/a.md": body, "README.md": "see docs/a.md\n"},
             {"docs/product/a.md": body, "README.md": "see docs/product/a.md\n"})
    check("a move with its references updated is clean", stale(r, "base", "work"), [])

    # 2. Moved, and a pointer was FORGOTTEN. This is the whole point.
    r = repo({"docs/a.md": body, "README.md": "see docs/a.md\n"},
             {"docs/product/a.md": body, "README.md": "see docs/a.md\n"})
    got = stale(r, "base", "work")
    check("a forgotten reference is caught", len(got), 1)
    check("...and it names the old path and the offending file", got[0], ("docs/a.md", "README.md"))

    # 3. Nothing moved -> nothing to say. It must not invent findings.
    r = repo({"docs/a.md": body}, {"docs/a.md": body + "more\n"})
    check("no renames -> no findings", stale(r, "base", "work"), [])

    # 4. Archived history may keep pointing at what it always pointed at.
    r = repo({"docs/a.md": body, "docs/_archive/old.md": "see docs/a.md\n"},
             {"docs/product/a.md": body, "docs/_archive/old.md": "see docs/a.md\n"})
    check("archived text is exempt", stale(r, "base", "work"), [])

    # 5. THE ONE THAT MATTERS. Unable to measure -> raise, never report clean.
    raised = False
    try:
        stale(Path(tempfile.mkdtemp(prefix="notarepo-")), "base", "work")
    except CannotMeasureError:
        raised = True
    check("cannot measure -> raises, never reports clean", raised, True)

    passed = sum(1 for _, ok in cases if ok)
    print(f"\n{passed}/{len(cases)}")
    return 0 if passed == len(cases) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fail if anything still names a renamed path.")
    ap.add_argument("--repo", default=".", help="repository root")
    ap.add_argument("--base", default="origin/main", help="revision before the move")
    ap.add_argument("--head", default="HEAD", help="revision after the move")
    ap.add_argument("--drill", action="store_true", help="break the checker on purpose")
    args = ap.parse_args(argv)

    if args.drill:
        return _drill()

    try:
        found = stale(Path(args.repo).resolve(), args.base, args.head)
    except CannotMeasureError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2  # UNMEASURABLE -- deliberately not 1, so it cannot read as "clean"

    if not found:
        print("no stale references to renamed paths")
        return 0
    print(f"{len(found)} stale reference(s) to renamed path(s):", file=sys.stderr)
    for old, rel in found:
        print(f"  {rel}  still names  {old}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
