"""The app's flat route table, on every FastAPI we run on.

WHY THIS FILE EXISTS. Tests used to read ``app.routes`` directly to check what
the API exposes ("no DELETE on receipts", "stats is declared before {id}").
FastAPI 0.141 stopped flattening ``include_router`` into ``app.routes``: each
included router is now one private ``_IncludedRouter`` object with no ``path``,
so ``app.routes`` shrank from 100 entries to 24 -- the docs routes, the raw
``/api/mcp`` Route, and nineteen opaque routers. Every ``for route in
app.routes`` guard then either failed (positive asserts) or, worse, passed
VACUOUSLY (the "no offender" loops saw nothing to object to). Local dev had
0.128 and never saw it; CI installs fresh and did.

``fastapi.routing.iter_route_contexts`` is the public walker that expands
those routers into effective, prefixed routes in declaration order. Older
FastAPI has no such function and no such nesting, so ``app.routes`` is already
the flat table there. This helper hides the difference and refuses to return a
hollow table, so a guard built on it can never go green by seeing nothing.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteRow:
    """One HTTP route: its effective (prefixed) path and its methods."""

    path: str
    methods: frozenset[str]


_SENTINEL = "/api/health"  # exists on every build; a table without it is hollow


def route_table(app) -> list[RouteRow]:
    """Every HTTP route on ``app`` in declaration order, prefixes applied."""
    try:
        from fastapi.routing import iter_route_contexts  # FastAPI >= 0.141
    except ImportError:  # older FastAPI: include_router flattened into app.routes
        walked = list(app.routes)
    else:
        walked = list(iter_route_contexts(app.routes))
    rows = [
        RouteRow(path, frozenset(methods))
        for path, methods in (
            (getattr(r, "path", None), getattr(r, "methods", None)) for r in walked
        )
        if path and methods
    ]
    assert any(row.path == _SENTINEL for row in rows), (
        f"route table is hollow ({len(rows)} rows, no {_SENTINEL}) -- "
        "FastAPI changed how routes are stored again; fix tests/_routes.py, "
        "do not loosen the guard that called it"
    )
    return rows


def route_paths(app) -> list[str]:
    """Just the paths, in declaration order (duplicates kept: one per method set)."""
    return [row.path for row in route_table(app)]
