"""Every route is auth-classified ON PURPOSE — rules #12 and #25.

WHY THIS FILE EXISTS
--------------------
``tests/test_api_idor.py`` already proves that specific endpoints reject
anonymous callers. But its list is **hand-typed** — 15 (method, path) pairs
against 72 live endpoints. A per-user route added tomorrow is simply absent from
it, and absence is silent: the suite stays green.

That is exactly how this repo's three real IDORs happened (CLAUDE.md rule #25).
The rule was written down and obeyed by everyone who remembered it.

This test walks ``app.routes`` instead of a list. Every route must be **one of**:

  * protected — its dependency tree reaches ``require_user`` /
    ``require_verified_user`` / ``optional_user``; or
  * on ``PUBLIC_ROUTES`` below — an explicit, justified decision.

Adding a route without doing either fails this test. The failure names the route
and tells you the two ways out, so the default outcome of forgetting is a red
test rather than a data leak.

``PUBLIC_ROUTES`` is the load-bearing part: it makes "this endpoint is public" a
line someone had to write and justify, instead of the absence of a line.
"""

from __future__ import annotations

from typing import Any

import pytest

# ---------------------------------------------------------------------------
# The allowlist. Every entry needs a REASON — that is the whole point.
# ---------------------------------------------------------------------------
PUBLIC_ROUTES: dict[str, str] = {
    # --- infrastructure / platform ---
    "/api/health": "liveness probe — Railway and uptime checks call it anonymously",
    "/api/livez": "Kubernetes-style liveness probe — must answer before auth exists",
    "/api/readyz": "readiness probe — the load balancer calls it with no session",
    "/docs": "FastAPI interactive API docs (Swagger UI)",
    "/docs/oauth2-redirect": "FastAPI Swagger OAuth2 redirect target",
    "/redoc": "FastAPI ReDoc — the alternative rendering of the same schema",
    "/openapi.json": "OpenAPI schema — the frontend type codegen fetches it",
    # --- authentication: must be reachable BEFORE you have a session ---
    "/api/auth/login": "you cannot be logged in in order to log in",
    "/api/auth/register": "account creation happens before any session exists",
    "/api/auth/logout": "clearing a cookie must work even with a dead session",
    "/api/auth/magic-link/request": "magic-link request — unauthenticated by design",
    "/api/auth/magic-link/consume": "magic-link consumption, authenticated by token",
    "/api/auth/password-reset/request": "reset request — by definition unauthenticated",
    "/api/auth/verify-email/confirm": "email verification, authenticated by its token",
    "/api/auth/password-reset/confirm": "reset confirmation, authenticated by its token",
    # --- deliberately public product surface ---
    "/api/jobs": (
        "shared catalog read via optional_user — sitemap and link-unfurl bots read it "
        "anonymously (routes/jobs.py::list_jobs). Per-user MUTATIONS on jobs are NOT "
        "public and are covered by test_api_idor.py."
    ),
    "/api/client-log": "browser error beacon — fires before or without a session",
    "/api/sources": "the list of 47 job sources; public product information, no per-user data",
    "/api/status": (
        "catalog totals + source counts, no per-user data (routes/health.py:103). "
        "FLAGGED for owner review, not a blocker: it returns the last run_log row "
        "VERBATIM as `last_run`, so any source error string captured there is served "
        "publicly. Worth confirming no source ever records a keyed URL in that field."
    ),
}


def _auth_dependency_names(route: Any) -> set[str]:
    """Every dependency callable name reachable from a route, transitively.

    FastAPI flattens the tree onto ``route.dependant``. We walk it rather than
    reading the signature so a dependency pulled in via ``router(dependencies=)``
    or nested inside another dependency still counts.
    """
    names: set[str] = set()
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return names

    stack = [dependant]
    seen: set[int] = set()
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


AUTH_MARKERS = {"require_user", "require_verified_user", "optional_user"}


def _api_routes() -> list[Any]:
    from src.api.main import app  # imported lazily: heavy module

    out = []
    for r in app.routes:
        path = getattr(r, "path", None)
        if not path or not getattr(r, "methods", None):
            continue
        out.append(r)
    return out


def test_every_route_is_auth_classified() -> None:
    """No route may be neither protected nor explicitly public."""
    unclassified: list[str] = []
    for route in _api_routes():
        path = route.path
        if path in PUBLIC_ROUTES:
            continue
        if _auth_dependency_names(route) & AUTH_MARKERS:
            continue
        methods = ",".join(sorted(route.methods - {"HEAD", "OPTIONS"}))
        unclassified.append(f"{methods} {path}")

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
    may be reintroduced later with completely different semantics.
    """
    live = {r.path for r in _api_routes()}
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
