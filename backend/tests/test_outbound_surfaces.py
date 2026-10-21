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

WHY DISCOVERY DOES NOT START FROM THE GUARD'S IMPORTERS
-------------------------------------------------------
The first version of this file derived the surface list from "who imports
`services.fetch.guard`". That cannot see the hole M2 is actually about: a NEW
module that opens a caller-supplied URL and never imports the guard at all is
simply absent from both sides of the comparison, so the test stays green
(CodeRabbit, #934). Discovery therefore starts from the outbound primitives —
`aiohttp.ClientSession`, `httpx.AsyncClient`, `asyncio.open_connection`,
`async_playwright` — and every module that holds one must be classified below.
An unclassified outbound module fails, which is the only arrangement that makes
the doc's claim true rather than merely plausible.

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

# How a module is allowed to reach the network. `<module>.<attr>` or a bare name.
OUTBOUND_CALLS = {
    ("aiohttp", "ClientSession"),
    ("httpx", "AsyncClient"),
    ("httpx", "Client"),
    ("asyncio", "open_connection"),
    ("requests", "Session"),
    ("requests", "request"),
}
OUTBOUND_NAMES = {"async_playwright", "open_connection"}

# Every module that screens a caller-supplied URL, and what it screens.
# Adding a surface? Add it here, and make sure it imports from services.fetch.guard.
SCREENED_SURFACES = {
    "services/fetch/fetcher.py": "POST /jobs/fetch-url — the web fallback's job-ad fetch",
    "services/browser_agent/safety.py": "the browser agent's `open` action",
    "services/browser_agent/egress.py": "EgressProxy — Chromium's only way out",
    "services/browser_agent/trigger.py": "the user's \"start my assistant\" link, POSTed on Send",
}

# Outbound, but never to a URL a caller chose, so the guard does not apply.
# The reason is the point: it is what a reviewer checks when one of these changes.
NOT_ARBITRARY = {
    "services/analytics.py": "PostHog, one compiled-in endpoint",
    "services/auth/email_sender.py": "the email provider's own API, one compiled-in endpoint",
    "services/profile/github_enricher.py": "api.github.com, one compiled-in host",
    "services/browser_agent/agent.py": "launches Chromium, whose only exit is EgressProxy "
                                       "(and whose `open` URL is screened by safety.py)",
}


def _modules() -> list[Path]:
    """Every module under src/, package markers aside."""
    return sorted(p for p in SRC.rglob("*.py") if p.name != "__init__.py")


def _tree(path: Path) -> ast.Module:
    """The module's AST; every check below reads structure, never text."""
    return ast.parse(path.read_text(encoding="utf-8"))


def _defines(path: Path, names: set[str]) -> set[str]:
    """Names assigned anywhere in `path` — structural, not a text match.

    `ast.walk`, not `tree.body`: a second deny list hidden in an `if` block or
    grown with `+=` is still a second deny list.
    """
    found: set[str] = set()
    for node in ast.walk(_tree(path)):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Name) and t.id in names:
                found.add(t.id)
            elif isinstance(t, (ast.Tuple, ast.List)):
                found.update(e.id for e in t.elts if isinstance(e, ast.Name) and e.id in names)
    return found


def _imports_from_guard(path: Path) -> set[str]:
    """Names `path` imports from services.fetch.guard."""
    found: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("services.fetch.guard"):
            found.update(a.name for a in node.names)
    return found


def _opens_outbound(path: Path) -> set[str]:
    """The outbound primitives `path` calls — found without reference to the guard."""
    found: set[str] = set()
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if (func.value.id, func.attr) in OUTBOUND_CALLS:
                found.add(f"{func.value.id}.{func.attr}")
        elif isinstance(func, ast.Name) and func.id in OUTBOUND_NAMES:
            found.add(func.id)
    return found


def _defined_callables(path: Path) -> set[str]:
    """Functions and classes defined anywhere in `path` (nested ones count: a copy is a copy)."""
    return {
        node.name
        for node in ast.walk(_tree(path))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def test_the_deny_lists_have_exactly_one_home() -> None:
    """One deny list, one home: a new private range must reach every surface at once."""
    homes = {p.relative_to(SRC).as_posix() for p in _modules() if _defines(p, DENY_NAMES)}
    assert homes == {"services/fetch/guard.py"}, (
        "The private/reserved deny lists must live only in services/fetch/guard.py; "
        f"a second copy means a new range reaches only one of them. Found in: {sorted(homes)}"
    )


def test_every_outbound_module_is_classified() -> None:
    """The one that can catch a NEW unguarded surface: it starts from the socket, not the guard."""
    outbound = {
        p.relative_to(SRC).as_posix(): sorted(_opens_outbound(p))
        for p in _modules()
        if _opens_outbound(p)
    }
    unclassified = {m: calls for m, calls in outbound.items()
                    if m not in SCREENED_SURFACES and m not in NOT_ARBITRARY}
    assert not unclassified, (
        "These modules open an outbound connection and are classified nowhere:\n"
        + "\n".join(f"  {m} -> {calls}" for m, calls in sorted(unclassified.items()))
        + "\nIf it opens a URL a caller chose, screen it through services.fetch.guard and add it "
          "to SCREENED_SURFACES. If it only ever talks to one compiled-in endpoint, add it to "
          "NOT_ARBITRARY with that endpoint named. Do not leave it out: rule M2 (hard-rules) "
          "claims every caller-supplied URL is screened, and this test is that claim."
    )


def test_every_screened_surface_is_registered() -> None:
    """The registry IS the inventory rule M2 used to state in prose — both directions."""
    screening = {
        p.relative_to(SRC).as_posix()
        for p in _modules()
        if p != GUARD and _imports_from_guard(p) & DECISIONS
    }
    assert screening == set(SCREENED_SURFACES), (
        "A surface started (or stopped) screening URLs through services/fetch/guard. "
        "Register it in SCREENED_SURFACES and say what caller-supplied URL it opens, so the "
        "next deny-range change reaches every surface.\n"
        f"  registered: {sorted(SCREENED_SURFACES)}\n  in code:    {sorted(screening)}"
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
