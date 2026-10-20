#!/usr/bin/env python3
"""BACKEND LOCK GUARD — what ships is what was tested (added 2026-10-20).

backend/pyproject.toml declares `>=` ranges. Until 2026-10-20 the prod image ran
`pip install .`, which resolves the newest matching versions AT BUILD TIME, and
CI ran `pip install -e ".[dev]"` at a different time. So production could hold
versions no test ever ran, and a hallucinated or typosquatted package name added
to pyproject installed without anyone pinning or hashing it.

Now three hashed locks (pip-compile, Linux / CPython 3.12, the prod image) are
the only install source: the Dockerfile and CI run
`pip install --require-hashes -r <lock>` and then the project with `--no-deps`.
That makes the locks load-bearing, so this script fails when they drift from
pyproject or from each other:

  PINNED     every lock entry is `name==version` with at least one sha256 hash
             (`--require-hashes` would refuse it; say so here first, by name).
  MISSING    a pyproject dependency has no pin in the lock that installs it
             (added to pyproject, lock not regenerated -- `--no-deps` would then
             ship the app without it).
  RANGE      the pinned version does not satisfy the pyproject specifier
             (a floor raised in pyproject, lock not regenerated).
  STALE      the lock pins a package ONLY because pyproject used to declare it
             (`# via job360 (pyproject.toml)` and nothing else) -- removed from
             pyproject, still shipped.
  AGREE      a package in the runtime lock has a different version or hash set
             in the dev or browser lock -- CI would test, or the browser image
             would ship, a different set than the plain prod image.

Regenerate the locks: `cd backend && python3.12 scripts/lock_deps.py` (Linux).
Exit 0 = in sync · 1 = drift (each problem printed) · 2 = cannot read.
Usage: python scripts/check_py_lock.py [backend_dir] | --drill [--break-checker RULE]
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

try:  # CI's Backend job has it (pytest needs it); the chain job may not.
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import Version
except ImportError:  # pip vendors the same library, and pip is always present
    from pip._vendor.packaging.requirements import Requirement  # type: ignore[no-redef]
    from pip._vendor.packaging.utils import canonicalize_name  # type: ignore[no-redef]
    from pip._vendor.packaging.version import Version  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parent.parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# lock file -> the pyproject extras it carries on top of the runtime set.
RUNTIME_LOCK = "requirements.lock.txt"
LOCKS: dict[str, tuple[str, ...]] = {
    RUNTIME_LOCK: (),
    "requirements-dev.lock.txt": ("dev",),
    "requirements-browser.lock.txt": ("browser",),
}
PROJECT_VIA = "job360 (pyproject.toml)"
RULES = ("PINNED", "MISSING", "RANGE", "STALE", "AGREE")

_REQ_LINE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?P<op>==|===|>=|<=|~=|!=|>|<)?(?P<ver>[^\s;\\]*)")
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


@dataclass
class Pin:
    version: str
    hashes: set[str] = field(default_factory=set)
    via: set[str] = field(default_factory=set)
    pinned: bool = True


def parse_lock(text: str) -> dict[str, Pin]:
    """Parse pip-compile output into {canonical name: Pin}. Pure."""
    pins: dict[str, Pin] = {}
    current: Pin | None = None
    in_via = False
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if not raw[0].isspace() and not stripped.startswith("#") and not stripped.startswith("-"):
            m = _REQ_LINE.match(stripped)
            if not m:
                continue
            current = Pin(version=m.group("ver"), pinned=m.group("op") == "==")
            pins[canonicalize_name(m.group("name"))] = current
            current.hashes.update(_HASH.findall(stripped))
            in_via = False
            continue
        if current is None:
            continue
        current.hashes.update(_HASH.findall(stripped))
        if stripped.startswith("# via"):
            rest = stripped[len("# via"):].strip()
            if rest:
                current.via.add(rest)
                in_via = False
            else:
                in_via = True
        elif in_via and stripped.startswith("#   "):
            current.via.add(stripped[1:].strip())
        elif stripped.startswith("#"):
            in_via = False
    return pins


def direct_requirements(pyproject: dict, extras: tuple[str, ...]) -> list[Requirement]:
    """The pyproject requirements one lock must satisfy: runtime plus `extras`."""
    project = pyproject["project"]
    specs = list(project.get("dependencies", []))
    for extra in extras:
        specs += project.get("optional-dependencies", {}).get(extra, [])
    return [Requirement(s) for s in specs]


def check(backend: Path, disabled: frozenset[str] = frozenset()) -> list[str]:
    """Every problem with the locks under `backend`; empty means in sync.

    Raises OSError / ValueError when a file cannot be read or parsed -- the
    caller turns that into exit 2, never into a green verdict.
    """
    pyproject = tomllib.loads((backend / "pyproject.toml").read_text(encoding="utf-8"))
    locks = {name: parse_lock((backend / name).read_text(encoding="utf-8")) for name in LOCKS}
    problems: list[str] = []

    for name, extras in LOCKS.items():
        pins = locks[name]
        if not pins:
            raise ValueError(f"{name} has no requirement lines")
        for pkg, pin in sorted(pins.items()):
            if "PINNED" not in disabled and (not pin.pinned or not pin.hashes):
                problems.append(f"PINNED {name}: {pkg} is not `==`-pinned with a sha256 hash")
        wanted = direct_requirements(pyproject, extras)
        wanted_names = {canonicalize_name(r.name) for r in wanted}
        for req in wanted:
            pkg = canonicalize_name(req.name)
            pin = pins.get(pkg)
            if pin is None:
                if "MISSING" not in disabled:
                    problems.append(f"MISSING {name}: pyproject declares `{req}`, the lock does not pin {pkg}")
                continue
            if "RANGE" not in disabled and req.specifier and not req.specifier.contains(
                    Version(pin.version), prereleases=True):
                problems.append(f"RANGE {name}: pyproject wants `{req}`, the lock pins {pkg}=={pin.version}")
        for pkg, pin in sorted(pins.items()):
            if "STALE" not in disabled and pin.via == {PROJECT_VIA} and pkg not in wanted_names:
                problems.append(f"STALE {name}: pins {pkg} only for pyproject, which no longer declares it")

    runtime = locks[RUNTIME_LOCK]
    for name in LOCKS:
        if name == RUNTIME_LOCK or "AGREE" in disabled:
            continue
        for pkg, pin in sorted(runtime.items()):
            other = locks[name].get(pkg)
            if other is None:
                problems.append(f"AGREE {name}: lacks {pkg}, which {RUNTIME_LOCK} ships")
            elif other.version != pin.version or other.hashes != pin.hashes:
                problems.append(f"AGREE {name}: {pkg}=={other.version}, but {RUNTIME_LOCK} ships {pkg}=={pin.version}"
                                + ("" if other.version != pin.version else " (hashes differ)"))
    return problems


def run(backend: Path) -> int:
    try:
        problems = check(backend)
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as exc:
        print(f"check_py_lock: cannot read {backend}: {exc}")
        return 2
    if problems:
        print(f"check_py_lock: the backend locks are OUT OF SYNC with {backend.name}/pyproject.toml:")
        for p in problems:
            print(f"  - {p}")
        print("Fix: `cd backend && python3.12 scripts/lock_deps.py` (Linux), then commit pyproject + all three locks.")
        return 1
    print(f"check_py_lock: {', '.join(LOCKS)} match pyproject and each other.")
    return 0


# ── the drill: every rule watched going red on a copy of the real files ──────

def _first_direct(pins: dict[str, Pin], wanted: set[str]) -> str:
    return next(p for p, pin in sorted(pins.items()) if pin.via == {PROJECT_VIA} and p in wanted)


def drill(disabled: frozenset[str] = frozenset()) -> int:
    """Break a copy of pyproject + locks once per rule; demand that rule fires.

    The untouched copy is the negative control and must stay green.
    `--break-checker RULE` switches RULE off, so this drill MUST then fail.
    """
    src = ROOT / "backend"
    fails = 0
    if check(src, disabled):
        print("[drill] negative control FAILED: the real locks are reported out of sync")
        fails += 1
    runtime = parse_lock((src / RUNTIME_LOCK).read_text(encoding="utf-8"))
    pyproject_text = (src / "pyproject.toml").read_text(encoding="utf-8")
    runtime_names = {canonicalize_name(r.name)
                     for r in direct_requirements(tomllib.loads(pyproject_text), ())}
    victim = _first_direct(runtime, runtime_names)
    shared = sorted(runtime)[0]

    def deps_edit(text: str, old: str, new: str) -> str:
        if old not in text:
            raise ValueError(f"drill cannot find {old!r} to edit")
        return text.replace(old, new, 1)

    victim_line = next(ln for ln in pyproject_text.splitlines()
                       if re.match(rf'\s*"{re.escape(victim)}[\[<>=~!;" ]', ln, re.IGNORECASE))
    mutations = {
        # a hallucinated name added to pyproject, lock not regenerated
        "MISSING": ("pyproject.toml", lambda t: deps_edit(t, "dependencies = [\n",
                                                        'dependencies = [\n    "requests-fake-9999>=1.0",\n')),
        # a floor raised past the pin
        "RANGE": ("pyproject.toml", lambda t: deps_edit(t, victim_line, f'    "{victim}>=9999",')),
        # a dependency removed from pyproject, still pinned
        "STALE": ("pyproject.toml", lambda t: deps_edit(t, victim_line + "\n", "")),
        # one pin loses its hashes
        "PINNED": (RUNTIME_LOCK, lambda t: re.sub(r"\s*\\\n\s*--hash=sha256:[0-9a-f]{64}", "", t)),
        # the dev lock drifts from the runtime lock
        "AGREE": ("requirements-dev.lock.txt", lambda t: re.sub(
            rf"(?im)^({re.escape(shared)}==)\S+", r"\g<1>0.0.0", t, count=1)),
    }
    expect = {"MISSING": "requests-fake-9999", "RANGE": victim, "STALE": victim, "PINNED": "",
              "AGREE": shared}
    for rule, (fname, mutate) in mutations.items():
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp)
            for f in ("pyproject.toml", *LOCKS):
                shutil.copy(src / f, dst / f)
            (dst / fname).write_text(mutate((dst / fname).read_text(encoding="utf-8")), encoding="utf-8")
            hits = [p for p in check(dst, disabled) if p.startswith(rule + " ") and expect[rule] in p]
            if hits:
                print(f"[drill] {rule}: RED as required -> {hits[0]}")
            else:
                print(f"[drill] {rule}: FAILED -- broke {fname} and the guard stayed green")
                fails += 1
    total = len(mutations) + 1
    print(f"[drill] {total - fails}/{total} checks passed")
    return 1 if fails else 0


def main(argv: list[str]) -> int:
    disabled: frozenset[str] = frozenset()
    if "--break-checker" in argv:
        rule = argv[argv.index("--break-checker") + 1]
        if rule not in RULES:
            print(f"unknown rule {rule!r}; one of {', '.join(RULES)}")
            return 2
        disabled = frozenset({rule})
    if "--drill" in argv:
        return drill(disabled)
    rest = [a for a in argv[1:] if not a.startswith("-")]
    return run(Path(rest[0]) if rest else ROOT / "backend")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
