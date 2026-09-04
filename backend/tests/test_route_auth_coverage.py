"""Every route is auth-classified ON PURPOSE — rules #12 and #25.

WHY THIS FILE EXISTS
--------------------
``tests/test_api_idor.py`` already proves that specific endpoints reject
anonymous callers. But its list is **hand-typed** — 15 (method, path) pairs
against 72 live endpoints. A per-user route added tomorrow is simply absent from
it, and absence is silent: the suite stays green.

That is exactly how this repo's three real IDORs happened (CLAUDE.md rule #25).
The rule was written down and obeyed by everyone who remembered it.

This test enumerates the routers instead of a list. Every route must be one of:

  * protected — its dependency tree reaches ``require_user`` /
    ``require_verified_user`` / ``optional_user``; or
  * on ``PUBLIC_ROUTES`` below — an explicit, justified decision.

Adding a route without doing either fails this test. The failure names the route
and tells you the two ways out, so the default outcome of forgetting is a red
test rather than a data leak.

WHY ROUTERS IN A SUBPROCESS
--------------------------
Two things were learned the expensive way building this, both worth keeping:

1. The first version read ``src.api.main.app`` — a module-level singleton the
   suite mutates (conftest swaps its lifespan, tests install
   ``dependency_overrides``). A security guard whose verdict depends on what ran
   before it is not a guard, so discovery now happens in a clean subprocess:
   correct by construction, ~2 s, cached for the module.

2. ``route.path`` on an APIRouter ALREADY includes that router's prefix. The
   first router-based version composed ``API_PREFIX + router.prefix +
   route.path`` and produced ``/api/auth/auth/register``, so every
   ``/api/auth/*`` path looked non-existent — which read exactly like the
   pollution in (1) and sent me looking for a mutator that did not exist. Only
   main.py's ``/api`` needs adding.

The lesson both share: a guard that reports a wrong answer confidently is more
expensive than one that does not exist, because you act on it.
"""

from __future__ import annotations

import functools
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# Mirrors src/api/main.py: every router is included with prefix="/api".
API_PREFIX = "/api"

# The route modules main.py mounts. Explicit rather than globbed, so adding a new
# route module is a deliberate line here — otherwise the same "absence is silent"
# problem this file exists to solve just reappears one level up.
ROUTE_MODULES = [
    "health",
    "client_log",
    "jobs",
    "actions",
    "profile",
    "search",
    "pipeline",
    "auth",
    "channels",
    "notifications",
    "notification_rules",
    "runs",
    "tailor",
    "tokens",
    "bring",
    "receipts",
    "oauth",
]

# Route modules main.py mounts at the site ROOT (no "/api"). Only the OAuth
# discovery documents live here: a client resolves `/.well-known/...` against
# the bare origin, never under the API path (src/api/main.py, `well_known`).
ROOT_ROUTE_MODULES = [
    "well_known",
]

# ---------------------------------------------------------------------------
# The allowlist. Every entry needs a REASON — that is the whole point.
# ---------------------------------------------------------------------------
PUBLIC_ROUTES: dict[str, str] = {
    # --- infrastructure / platform ---
    "/api/health": "liveness probe — Railway and uptime checks call it anonymously",
    "/api/livez": "Kubernetes-style liveness probe — must answer before auth exists",
    "/api/readyz": "readiness probe — the load balancer calls it with no session",
    # --- authentication: must be reachable BEFORE you have a session ---
    "/api/auth/login": "you cannot be logged in in order to log in",
    "/api/auth/register": "account creation happens before any session exists",
    "/api/auth/logout": "clearing a cookie must work even with a dead session",
    "/api/auth/magic-link/request": "magic-link request — unauthenticated by design",
    "/api/auth/magic-link/consume": "magic-link consumption, authenticated by token",
    "/api/auth/password-reset/request": "reset request — by definition unauthenticated",
    "/api/auth/password-reset/confirm": "reset confirmation, authenticated by its token",
    "/api/auth/verify-email/confirm": "email verification, authenticated by its token",
    # --- deliberately public product surface ---
    "/api/jobs": (
        "shared catalog read via optional_user — sitemap and link-unfurl bots read it "
        "anonymously (routes/jobs.py::list_jobs). Per-user MUTATIONS on jobs are NOT "
        "public and are covered by test_api_idor.py."
    ),
    "/api/client-log": "browser error beacon — fires before or without a session",
    "/api/sources": "the list of 47 job sources; public product info, no per-user data",
    "/api/status": (
        "catalog totals + source counts, no per-user data (routes/health.py:103). "
        "FLAGGED for owner review, not a blocker: it returns the last run_log row "
        "VERBATIM as `last_run`, so any source error string captured there is served "
        "publicly. Worth confirming no source ever records a keyed URL in that field."
    ),
    # --- OAuth 2.1 authorization server (docs/plans/2026-09-03-oauth-mcp/spec.md) ---
    # An MCP client (ChatGPT, Claude.ai) has NO Job360 identity when it starts the
    # flow; these are the endpoints the OAuth specs define as anonymous. Each is
    # rate-limited per IP (spec S6) and the browser-facing steps (consent read +
    # decision, grant list/revoke) stay session-only via require_session_user.
    "/.well-known/oauth-authorization-server": (
        "RFC 8414 discovery document — a client reads it before it has any credential"
    ),
    "/.well-known/oauth-protected-resource": (
        "RFC 9728 protected-resource metadata (root form) — anonymous by definition"
    ),
    "/.well-known/oauth-protected-resource/api/mcp": (
        "RFC 9728 protected-resource metadata for /api/mcp — the URL the 401 "
        "challenge on /api/mcp points at, read before any token exists"
    ),
    "/api/oauth/register": (
        "RFC 7591 dynamic client registration — a public client (`none` auth) "
        "registers before any user is involved; per-IP + global rate limits (spec R2/S6)"
    ),
    "/api/oauth/authorize": (
        "authorization endpoint — validates client + redirect_uri, stores the request "
        "and 302s to the consent page; the LOGIN happens there (spec R3). Nothing is "
        "granted without a session on /api/oauth/authorize/{rid}/decision"
    ),
    "/api/oauth/token": (
        "token endpoint — authenticated by the authorization code + PKCE verifier or "
        "by the refresh token itself, never by a session (spec R5)"
    ),
    "/api/oauth/revoke": (
        "RFC 7009 revocation — authenticated by the token being revoked; always 200 "
        "so it cannot be used as an oracle (spec R8)"
    ),
}

AUTH_MARKERS = {"require_user", "require_verified_user", "optional_user"}


_DISCOVER = r"""
import importlib, json, sys
API_PREFIX = "/api"
AUTH_MARKERS = {"require_user", "require_verified_user", "optional_user"}
# {module name: mount prefix} — "/api" for almost everything, "" for the
# root-mounted discovery documents.
MODULES = json.loads(sys.argv[1])

def dep_names(route):
    names, dep = set(), getattr(route, "dependant", None)
    if dep is None:
        return names
    stack, seen = [dep], set()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        call = getattr(node, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", ""))
        stack.extend(getattr(node, "dependencies", []) or [])
    return names

out = []
for name, prefix in MODULES.items():
    mod = importlib.import_module("src.api.routes." + name)
    router = getattr(mod, "router", None)
    if router is None:
        print(json.dumps({"error": "no router in " + name}))
        raise SystemExit(1)
    for route in router.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        # NB: route.path ALREADY carries the router's own prefix — an APIRouter
        # bakes it in at decoration time. Adding router.prefix again produced
        # "/api/auth/auth/register", which is why every /api/auth/* path looked
        # non-existent. Only main.py's "/api" is missing here.
        out.append({
            "path": prefix + path,
            "verbs": ",".join(sorted(methods - {"HEAD", "OPTIONS"})),
            "protected": bool(dep_names(route) & AUTH_MARKERS),
        })
print(json.dumps(out))
"""


@functools.lru_cache(maxsize=1)
def _all_routes() -> tuple[dict[str, Any], ...]:
    """Route table discovered in a FRESH interpreter.

    Discovery runs in a subprocess on purpose. Both earlier versions of this test
    — one reading ``app.routes``, one reading the routers directly — passed alone
    and FAILED when run after ``tests/test_api_idor.py``, reporting every
    ``/api/auth/*`` path as non-existent. Something in the suite mutates the
    shared module state these objects live in.

    Chasing that mutation is a different bug. What this guard needs is an answer
    that does not depend on what ran before it: a security check whose verdict
    changes with test ORDER is not a check. A clean interpreter gives that by
    construction, for about two seconds, and the result is cached for the module.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            _DISCOVER,
            json.dumps(
                {**{m: API_PREFIX for m in ROUTE_MODULES}, **{m: "" for m in ROOT_ROUTE_MODULES}}
            ),
        ],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "Route discovery subprocess failed — this guard cannot verify "
            "anything until it is fixed. stdout: "
            + proc.stdout[-2000:]
            + " | stderr: "
            + proc.stderr[-2000:]
        )
    return tuple(json.loads(proc.stdout.strip().splitlines()[-1]))


def test_route_table_is_not_empty() -> None:
    """Guard the guard: an empty table would make every check below vacuous."""
    routes = _all_routes()
    assert len(routes) >= 40, (
        f"Only {len(routes)} routes discovered across {len(ROUTE_MODULES)} modules. "
        "Discovery itself is broken — every assertion below would pass vacuously. "
        "Check ROUTE_MODULES against src/api/main.py."
    )


def test_every_route_is_auth_classified() -> None:
    """No route may be neither protected nor explicitly public."""
    unclassified = [
        f"{r['verbs']} {r['path']}"
        for r in _all_routes()
        if r["path"] not in PUBLIC_ROUTES and not r["protected"]
    ]

    assert not unclassified, (
        "These routes are neither auth-protected nor on PUBLIC_ROUTES:\n  "
        + "\n  ".join(sorted(unclassified))
        + "\n\nRule #12: every per-user route must Depends(require_user) and scope "
        "every query by user.id.\nRule #25: never take user_id from the URL, body "
        "or query — derive it from the session cookie.\n\nTwo ways out:\n"
        "  1. add the auth dependency (almost always the right answer), or\n"
        "  2. add the path to PUBLIC_ROUTES in this file WITH a reason.\n"
        "Forgetting is not one of them — that is what this test is for."
    )


def test_public_allowlist_has_no_dead_entries() -> None:
    """An allowlist that outlives its routes stops meaning anything.

    A stale entry is a standing permission to be public, granted to a path that
    may later be reintroduced with completely different semantics.
    """
    live = {r["path"] for r in _all_routes()}
    dead = sorted(p for p in PUBLIC_ROUTES if p not in live)
    assert not dead, (
        "PUBLIC_ROUTES lists paths that no longer exist:\n  "
        + "\n  ".join(dead)
        + "\n\nRemove them. A stale allowlist entry silently pre-approves any "
        "future route that happens to reuse the path."
    )


@pytest.mark.parametrize("path,reason", sorted(PUBLIC_ROUTES.items()))
def test_public_entries_carry_a_reason(path: str, reason: str) -> None:
    """'Public' must be a justified decision, not an empty string."""
    assert len(reason.strip()) >= 15, (
        f"PUBLIC_ROUTES[{path!r}] needs a real reason explaining why anonymous "
        f"access is correct. Got: {reason!r}"
    )
