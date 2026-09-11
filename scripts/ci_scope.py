#!/usr/bin/env python3
"""Decide how much of CI a pull request needs: `docs`, `frontend` or `full`.

WHY THIS EXISTS (harness simplification slice 3, owner decision 2026-09-08)
--------------------------------------------------------------------------
Measured on the last green run of main (2026-09-10): the backend job spends
347s of its 414s running the full pytest suite, and ci-offline.yml runs the
SAME suite again in parallel. A pull request that changes one React component
therefore waits seven minutes for tests it cannot affect. The owner asked for a
fast lane: frontend-only or docs-only diffs get lint + type-check + unit tests
(about two minutes), and anything touching the backend, the schema or the
harness gets the full gate exactly as before.

WHAT THIS IS NOT
----------------
It is NOT the merge lane. `scripts/lane.py` answers "who may merge this and
what must be true first"; this file answers "which steps are worth running".
Both are read by CI; neither reads the other. A frontend-only PR still takes
whatever merge lane its files put it in.

THE ONE RULE: MOST RESTRICTIVE WINS, UNKNOWN IS NOT SAFE
--------------------------------------------------------
    full  >  frontend  >  docs

One backend file in a forty-file docs PR makes the whole PR `full`. A path this
file has no rule for is `full`, and the reason names it. An empty changeset is
`full`. A push to main is `full` without looking -- main is production. Every
refusal here costs a few minutes of compute; a wrong `docs` costs a broken
deploy, and #503 already paid that once.

DEPLOY-SHAPED FRONTEND FILES ARE FULL
-------------------------------------
`frontend/**` is the fast lane EXCEPT the files that decide how the app is built
and shipped: the Dockerfile and .dockerignore (the image Railway builds),
package.json and the lockfile (what gets installed), next.config.* (output
mode, the thing the Dockerfile copies), railway.json, and any .env file. Those
are the files that broke production in #503, and the fast lane skips the exact
step -- the Docker image build -- that would have caught them.

JOB NAMES NEVER CHANGE
----------------------
The ruleset on main requires checks BY NAME (`Backend (Python 3.12)`,
`Frontend (Node 20)`, `offline-suite`, ...), and so does scripts/merge_cage.py.
A job that does not run is a missing required check, and a missing check blocks
the merge. So the fast lane skips STEPS inside those jobs, never the jobs: a
skipped job never reports, a job whose steps were skipped reports success.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import PurePosixPath

SCOPES: tuple[str, ...] = ("full", "frontend", "docs")  # most restrictive first

# Top-level files under frontend/ that change how the app is BUILT or SHIPPED,
# not what it does. Any of these -> full, because the fast lane skips the Docker
# image build and that build is the only step that exercises them.
_DEPLOY_SHAPED_FRONTEND: frozenset[str] = frozenset({
    ".dockerignore",
    "package.json",
    "package-lock.json",
    "railway.json",
})
_DEPLOY_SHAPED_PREFIXES: tuple[str, ...] = ("Dockerfile", "next.config.")


def kind_of(path: str) -> str:
    """Return `docs`, `frontend` or `full` for ONE path. Pure."""
    if not path or path.startswith("/") or ".." in PurePosixPath(path).parts:
        return "full"  # not a repo-relative path -> refuse to guess
    p = PurePosixPath(path)
    if p.name.startswith(".env"):
        return "full"  # an env file anywhere is configuration, never prose
    if p.suffix == ".md" or path.startswith("docs/"):
        return "docs"
    if path.startswith("frontend/"):
        rel = path[len("frontend/"):]
        top_level = "/" not in rel
        if top_level and (rel in _DEPLOY_SHAPED_FRONTEND
                          or rel.startswith(_DEPLOY_SHAPED_PREFIXES)):
            return "full"
        return "frontend"
    return "full"


def classify(files: list[str]) -> dict:
    """Classify a whole changeset. Always says WHICH files decided it."""
    if not files:
        return {"scope": "full", "why": ["no files in the changeset -- refusing to guess"],
                "by_kind": {}}
    by_kind: dict[str, list[str]] = {}
    for f in files:
        by_kind.setdefault(kind_of(f), []).append(f)
    for scope in SCOPES:
        hits = sorted(by_kind.get(scope) or [])
        if hits:
            shown = ", ".join(hits[:4]) + (" ..." if len(hits) > 4 else "")
            return {
                "scope": scope,
                "why": [f"scope `{scope}` decided by {len(hits)} file(s): {shown}"],
                "by_kind": by_kind,
            }
    raise AssertionError("unreachable: every file has a kind")


def changed_files(base: str, head: str) -> list[str] | None:
    """`git diff --name-only base...head`, or None if git could not answer."""
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...{head}"],
            capture_output=True, text=True, check=True, encoding="utf-8", errors="replace",
        ).stdout
    except (subprocess.CalledProcessError, OSError):
        return None
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _emit(verdict: dict, print_scope: bool, summary: bool) -> None:
    """Report the verdict.

    The JSON goes to STDERR so the run log always shows the reasoning, and with
    `--print-scope` STDOUT carries the bare scope word and nothing else, so the
    workflow step can do `scope=$(python ...)` and write `$GITHUB_OUTPUT`
    itself. The write is deliberately in the workflow's own `run:` line and not
    in here: scripts/chain_check.py reads workflow text to prove every
    `steps.X.outputs.Y` a job consumes is actually written by step X, and a
    write hidden inside a Python file is a wire it cannot see.
    """
    print(json.dumps(verdict, indent=2, sort_keys=True), file=sys.stderr)
    if print_scope:
        print(verdict["scope"])
    if summary:
        path = os.environ.get("GITHUB_STEP_SUMMARY")
        if path:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"### CI scope: `{verdict['scope']}`\n\n")
                for line in verdict["why"]:
                    fh.write(f"- {line}\n")
                fh.write("\n`full` runs everything; `frontend` skips backend tests and the "
                         "Docker image build; `docs` runs only the doc gates.\n")


def _drill() -> int:
    """Break the classifier on purpose. A guard nobody has watched go red is a claim."""
    cases: list[tuple[str, bool]] = []

    def check(name: str, got: object, want: object) -> None:
        ok = got == want
        cases.append((name, ok))
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"   got={got!r} want={want!r}"))

    print("ci_scope.py --drill")
    # NEGATIVE CONTROLS FIRST. A classifier that answers `full` to everything
    # passes every refusal below and is exactly as useful as no classifier.
    check("docs-only diff takes the docs lane", classify(["docs/harness/x.md"])["scope"], "docs")
    check("frontend-only diff takes the frontend lane",
          classify(["frontend/src/components/JobCard.tsx"])["scope"], "frontend")

    # PRECEDENCE -- one restrictive file beats any number of safe ones.
    many_docs = [f"docs/note{i}.md" for i in range(40)]
    check("40 docs + 1 backend file is FULL",
          classify([*many_docs, "backend/src/main.py"])["scope"], "full")
    check("docs + frontend is FRONTEND (the wider of the two)",
          classify(["README.md", "frontend/src/lib/api.ts"])["scope"], "frontend")
    check("frontend + one workflow is FULL",
          classify(["frontend/src/app/page.tsx", ".github/workflows/ci.yml"])["scope"], "full")
    check("frontend + one script is FULL",
          classify(["frontend/src/app/page.tsx", "scripts/ci_scope.py"])["scope"], "full")

    # DEPLOY-SHAPED FRONTEND FILES -- the #503 set -- are never fast.
    for deploy in ("frontend/Dockerfile", "frontend/.dockerignore", "frontend/package.json",
                   "frontend/package-lock.json", "frontend/next.config.ts",
                   "frontend/railway.json", "frontend/.env.production"):
        check(f"deploy-shaped file is FULL: {deploy}", classify([deploy])["scope"], "full")
    check("a nested .env is FULL even inside src/",
          classify(["frontend/src/.env.local"])["scope"], "full")
    check("a NESTED package.json is still frontend (only the top-level one ships)",
          classify(["frontend/src/fixtures/package.json"])["scope"], "frontend")

    # DOCS SHAPE -- markdown anywhere, anything under docs/.
    check("root README is docs", classify(["README.md"])["scope"], "docs")
    check("a non-markdown file under docs/ is docs",
          classify(["docs/harness/wires.yml"])["scope"], "docs")
    check("markdown inside frontend/ is docs (cannot affect the build)",
          classify(["frontend/README.md"])["scope"], "docs")
    check("backend CLAUDE.md is docs", classify(["backend/CLAUDE.md"])["scope"], "docs")

    # UNKNOWN IS NOT SAFE.
    check("an unlisted root file is FULL", classify(["foo.txt"])["scope"], "full")
    check("a brand-new top-level dir is FULL", classify(["newthing/x.ts"])["scope"], "full")
    check("an empty changeset is FULL", classify([])["scope"], "full")
    check("a path with .. is FULL", classify(["docs/../backend/x.py"])["scope"], "full")
    check("an absolute path is FULL", classify(["/etc/passwd"])["scope"], "full")

    # THE VERDICT NAMES ITS EVIDENCE.
    v = classify(["docs/a.md", "backend/src/x.py"])
    check("the verdict names the deciding file", "backend/src/x.py" in " ".join(v["why"]), True)

    passed = sum(1 for _, ok in cases if ok)
    print(f"\n{passed}/{len(cases)}")
    if passed != len(cases):
        print("DRILL FAILED -- the scope classifier no longer behaves as documented.")
    return 0 if passed == len(cases) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Decide which CI steps a diff needs.")
    ap.add_argument("files", nargs="*", help="changed paths (alternative to --base/--head)")
    ap.add_argument("--event", default="pull_request",
                    help="GitHub event name; anything but pull_request is full")
    ap.add_argument("--base", help="base revision (with --head: run git diff)")
    ap.add_argument("--head", default="HEAD", help="head revision for --base")
    ap.add_argument("--print-scope", action="store_true",
                    help="stdout carries only the scope word (JSON verdict goes to stderr)")
    ap.add_argument("--summary", action="store_true",
                    help="append a markdown verdict to $GITHUB_STEP_SUMMARY")
    ap.add_argument("--drill", action="store_true", help="break the classifier on purpose")
    args = ap.parse_args(argv)

    if args.drill:
        return _drill()

    if args.event != "pull_request":
        _emit({"scope": "full",
               "why": [f"event `{args.event}` is not a pull request -- main is production, "
                       "so a push, a schedule or a dispatch always runs the full gate"],
               "by_kind": {}}, args.print_scope, args.summary)
        return 0

    files: list[str] | None
    if args.base:
        files = changed_files(args.base, args.head)
        if files is None:
            # FAIL SAFE, LOUDLY. The safe direction is more checks, not fewer --
            # but a warning in the log so a permanently broken diff does not hide
            # the fast lane forever.
            print("::warning::ci_scope: git diff failed -- falling back to the full gate",
                  file=sys.stderr)
            _emit({"scope": "full", "why": ["git diff failed -- refusing to guess"],
                   "by_kind": {}}, args.print_scope, args.summary)
            return 0
    else:
        files = list(args.files)

    _emit(classify(files), args.print_scope, args.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
