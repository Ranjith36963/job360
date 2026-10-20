"""Every surface that opens a caller-supplied URL screens through ONE guard — rule M2.

WHY A TEST AND NOT A DOC SENTENCE
---------------------------------
`.claude/skills/hard-rules/SKILL.md` used to carry the inventory in prose: the
URL fetch behind `POST /jobs/fetch-url` was "the only such surface". That went
stale the day the browser agent merged (#902, 2026-10-19), which added two more
callers of the same guard — and a stale inventory is worse than none, because the
next session adding a deny range reads it and updates one place of three.

So the inventory lives here, where adding a surface fails until it is registered,
and registering it is the moment someone has to say which guard screens it.

WHAT IT COSTS WHEN IT REGRESSES
-------------------------------
A surface that reaches a caller-supplied URL without `services/fetch/guard`
is an SSRF hole into Railway's private network: the metadata service, Postgres
and the backend's own loopback are all one unscreened hop away. A second copy
of the deny list is the same hole on a delay — one copy gets the new range.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
GUARD = SRC / "services" / "fetch" / "guard.py"

# The decision functions. A surface that calls these is screened; one that does
# not, but still opens a caller-supplied URL, is the hole this test exists for.
DECISIONS = {"screen_url", "screen_host", "screen_ip", "verify_peername", "GuardedResolver"}

# The private/reserved network lists. These names must exist in exactly one module:
# two copies means the next range added reaches only one of them.
DENY_NAMES = {"DENY_NETS_V4", "DENY_NETS_V6", "_DENY_V4_CIDRS", "_DENY_V6_CIDRS"}

# Every module that screens a caller-supplied URL, and what it screens.
# Adding a surface? Add it here, and make sure it imports from services.fetch.guard.
SCREENED_SURFACES = {
    "services/fetch/fetcher.py": "POST /jobs/fetch-url — the web fallback's job-ad fetch",
    "services/browser_agent/safety.py": "the browser agent's `open` action",
    "services/browser_agent/egress.py": "EgressProxy — Chromium's only way out",
}


def _modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if p.name != "__init__.py")


def _defines(path: Path, names: set[str]) -> set[str]:
    """Names assigned at module scope in `path` — structural, not a text match."""
    found: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        found.update(t.id for t in targets if isinstance(t, ast.Name) and t.id in names)
    return found


def _imports_from_guard(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("services.fetch.guard"):
            found.update(a.name for a in node.names)
    return found


def _defined_callables(path: Path) -> set[str]:
    """Functions and classes defined anywhere in `path` (nested ones count: a copy is a copy)."""
    return {
        node.name
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def test_the_deny_lists_have_exactly_one_home() -> None:
    homes = {p.relative_to(SRC).as_posix() for p in _modules() if _defines(p, DENY_NAMES)}
    assert homes == {"services/fetch/guard.py"}, (
        "The private/reserved deny lists must live only in services/fetch/guard.py; "
        f"a second copy means a new range reaches only one of them. Found in: {sorted(homes)}"
    )


def test_every_screened_surface_is_registered() -> None:
    """The registry above IS the inventory rule M2 used to state in prose."""
    actual = {
        p.relative_to(SRC).as_posix(): sorted(_imports_from_guard(p) & DECISIONS)
        for p in _modules()
        if p != GUARD and _imports_from_guard(p) & DECISIONS
    }
    assert set(actual) == set(SCREENED_SURFACES), (
        "A surface started (or stopped) screening URLs through services/fetch/guard. "
        "Register it in SCREENED_SURFACES and say what caller-supplied URL it opens, so the "
        "next deny-range change reaches every surface.\n"
        f"  registered: {sorted(SCREENED_SURFACES)}\n  in code:    {sorted(actual)}"
    )


def test_no_surface_reimplements_the_decision() -> None:
    """Screening is imported, never re-derived: no surface defines its own screen_*."""
    offenders = {
        p.relative_to(SRC).as_posix()
        for p in _modules()
        if p != GUARD and _defined_callables(p) & DECISIONS
    }
    assert not offenders, (
        f"These modules define their own copy of a guard decision: {sorted(offenders)}. "
        "Import it from services.fetch.guard instead — two decisions diverge."
    )
