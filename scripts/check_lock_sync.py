#!/usr/bin/env python3
"""LOCKFILE SYNC GUARD — say "Missing: X from lock file" before Linux does.

`npm ci` on Linux refuses a package-lock.json in which some package's
`dependencies` cannot be resolved to a lock entry. The cleanup audit (#503)
shipped exactly that: npm on Windows pruned the platform-optional `@emnapi/*`
entries (deps of `@img/sharp-wasm32`), every local check passed against the
already-installed node_modules, and Railway + CI both died at `npm ci` while
the commit gate was green.

Neither npm command is a usable guard on Windows (both measured 2026-09-06):
  * `npm ci --dry-run` returned 0 for that broken lock;
  * `npm install --package-lock-only` REMOVES the entries when node_modules
    is absent and ADDS them when it is present — the answer depends on state.

So this is the platform-independent half of npm's own check, done on the JSON:
  1. every dependency in package.json has a lock entry, and
  2. every `dependencies` / `optionalDependencies` / non-optional
     `peerDependencies` of every lock entry resolves — walking up the
     node_modules nesting the way Node's resolver does.

Exit 0 = in sync · 1 = drift (each missing edge printed) · 2 = cannot read.
Usage: python scripts/check_lock_sync.py [frontend] | --drill
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _resolves(packages: dict[str, dict[str, Any]], from_loc: str, name: str) -> bool:
    """True if `name`, required from lock path `from_loc`, has a lock entry.

    Mirrors Node resolution: look in the requirer's own node_modules, then
    each ancestor's, then the root's.
    """
    base = from_loc
    while True:
        candidate = f"{base}/node_modules/{name}" if base else f"node_modules/{name}"
        if candidate in packages:
            return True
        if not base:
            return False
        idx = base.rfind("/node_modules/")
        base = "" if idx == -1 else base[:idx]


def check(pkg_dir: Path) -> list[str]:
    """Return every unresolved edge; an empty list means the lock is in sync.

    Raises OSError / ValueError when the files cannot be read or parsed —
    the caller turns that into exit 2, never into a green verdict.
    """
    pkg = json.loads((pkg_dir / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((pkg_dir / "package-lock.json").read_text(encoding="utf-8"))
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        raise ValueError("lockfileVersion 2/3 `packages` map missing — unsupported lock")

    problems: list[str] = []

    # 1. root package.json ⇄ lock root entry
    root = packages.get("", {})
    for field in ("dependencies", "devDependencies", "optionalDependencies"):
        for name in pkg.get(field, {}):
            if name not in root.get(field, {}):
                problems.append(f'package.json {field} "{name}" is not in the lock root')
            if not _resolves(packages, "", name):
                problems.append(f'package.json {field} "{name}" has no node_modules/{name} lock entry')

    # 2. every edge inside the lock resolves
    for loc, entry in packages.items():
        if loc == "" or entry.get("link"):
            continue
        meta = entry.get("peerDependenciesMeta", {})
        edges = set(entry.get("dependencies", {})) | set(entry.get("optionalDependencies", {}))
        edges |= {n for n in entry.get("peerDependencies", {}) if not meta.get(n, {}).get("optional")}
        for name in sorted(edges):
            if not _resolves(packages, loc, name):
                problems.append(f"Missing: {name} (required by {loc}) from lock file")
    return problems


def run(pkg_dir: Path) -> int:
    """Check one package directory and print the verdict."""
    try:
        problems = check(pkg_dir)
    except (OSError, ValueError) as exc:
        print(f"check_lock_sync: cannot read {pkg_dir}: {exc}")
        return 2
    if problems:
        print(f"check_lock_sync: {pkg_dir}/package-lock.json is OUT OF SYNC — npm ci on Linux will refuse it:")
        for p in problems:
            print(f"  - {p}")
        print(
            "Fix: regenerate the lock (`npm install --package-lock-only` WITH node_modules "
            "present, or on Linux) and commit it."
        )
        return 1
    print(f"check_lock_sync: {pkg_dir.name}/package-lock.json in sync — every edge resolves.")
    return 0


def drill() -> int:
    """Prove the guard can go RED: break a copy of the real lock the way #503 did.

    Removes the lock entry of a package that something else depends on, then
    demands the guard reports exactly that edge. Also the negative control:
    the untouched lock must stay green.
    """
    src = ROOT / "frontend"
    fails = 0
    if check(src):
        print("[drill] negative control FAILED: the real lock is reported out of sync")
        fails += 1
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "frontend"
        dst.mkdir()
        shutil.copy(src / "package.json", dst / "package.json")
        lock = json.loads((src / "package-lock.json").read_text(encoding="utf-8"))
        packages = lock["packages"]
        # Pick a dependency edge that exists, then delete its target — the
        # exact shape npm on Windows produced (dependant kept, dependency gone).
        victim = next(
            (n for loc, e in packages.items() if loc for n in e.get("dependencies", {})
             if f"node_modules/{n}" in packages),
            None,
        )
        if victim is None:
            print("[drill] cannot find a resolvable edge to break — lock too small?")
            return 1
        del packages[f"node_modules/{victim}"]
        (dst / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
        problems = check(dst)
        hit = [p for p in problems if p.startswith(f"Missing: {victim} ")]
        if hit:
            print(f"[drill] RED as required: deleting node_modules/{victim} -> {len(hit)} missing edge(s)")
        else:
            print(f"[drill] FAILED: deleted node_modules/{victim} and the guard stayed green")
            fails += 1
    print(f"[drill] {2 - fails}/2 checks passed")
    return 1 if fails else 0


def main(argv: list[str]) -> int:
    if "--drill" in argv:
        return drill()
    target = Path(argv[1]) if len(argv) > 1 else ROOT / "frontend"
    return run(target)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
