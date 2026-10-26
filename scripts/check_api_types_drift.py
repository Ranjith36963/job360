#!/usr/bin/env python3
"""API TYPES DRIFT GUARD — the frontend's typed API must match the backend.

`frontend/openapi.json` and `frontend/src/lib/api-types.ts` are GENERATED from
the backend (scripts/gen-api-types.sh). The Playwright specs mock the backend
with hand-copied replies, so when a backend change alters a response shape and
nobody regenerates, every other check stays green and the break ships
(2026-10-25 CI audit: `check:types-drift` existed but no workflow ran it).

This regenerates both files and fails if either changed.

Exit 0 = in step · 1 = drift (fix printed) · 2 = could not generate.
Usage: python scripts/check_api_types_drift.py | --drill [--break-checker]
Needs: the backend importable (locked deps) and `npm ci` done in frontend/.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATED = ("frontend/openapi.json", "frontend/src/lib/api-types.ts")
FIX = ("Frontend API types are out of date with the backend. Run 'npm run gen:types' "
       "in frontend/ and commit frontend/openapi.json + frontend/src/lib/api-types.ts.")


def drifted(repo: Path, paths: tuple[str, ...], blind: bool = False) -> list[str]:
    """The generated paths whose working copy differs from HEAD."""
    if blind:  # --break-checker: a guard that never sees drift
        return []
    out = subprocess.run(["git", "-C", str(repo), "diff", "--name-only", "--", *paths],  # noqa: S603, S607
                         capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line]


def check() -> int:
    gen = subprocess.run(["bash", "scripts/gen-api-types.sh"], cwd=ROOT, check=False)  # noqa: S603, S607
    if gen.returncode != 0:
        print(f"::error::could not regenerate the API types (exit {gen.returncode})")
        return 2
    bad = drifted(ROOT, GENERATED)
    if bad:
        subprocess.run(["git", "-C", str(ROOT), "diff", "--stat", "--", *bad], check=False)  # noqa: S603, S607
        print(f"::error::{FIX}")
        return 1
    print("API types match the backend.")
    return 0


def drill(blind: bool) -> int:
    """Plant drift in a throwaway repo and demand it is named; untouched must pass."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        git = ["git", "-C", tmp, "-c", "user.name=drill", "-c", "user.email=drill@invalid"]
        subprocess.run([*git, "init", "-q"], check=True)  # noqa: S603, S607
        for p in GENERATED:
            f = repo / p
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("generated\n", encoding="utf-8")
        subprocess.run([*git, "add", "-A"], check=True)  # noqa: S603, S607
        subprocess.run([*git, "commit", "-q", "-m", "base"], check=True)  # noqa: S603, S607
        clean = drifted(repo, GENERATED, blind)
        (repo / GENERATED[1]).write_text("generated\nexport type Added = string;\n", encoding="utf-8")
        planted = drifted(repo, GENERATED, blind)
    checks = [
        ("untouched generated files pass", clean == []),
        ("a changed api-types.ts is named", planted == [GENERATED[1]]),
    ]
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return 0 if all(ok for _, ok in checks) else 1


def main(argv: list[str]) -> int:
    if "--drill" in argv:
        return drill(blind="--break-checker" in argv)
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
