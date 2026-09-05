"""tests/_routes.py must see through FastAPI's route nesting on every version.

The guards that use it are "no offender" loops; if the table it returns is
hollow they pass by seeing nothing. So the helper itself is pinned here:
included-router paths must be present, in declaration order, with methods.
"""
from __future__ import annotations

from tests._routes import route_paths, route_table


def test_included_router_routes_are_visible() -> None:
    from src.api.main import app

    rows = route_table(app)
    by_path = {row.path: row.methods for row in rows}
    # One route from an included router (prefix applied) and the raw Route
    # appended in main.py -- both must survive whichever FastAPI is installed.
    assert "GET" in by_path["/api/health"]
    assert {"GET", "POST", "DELETE"} <= by_path["/api/mcp"]
    assert len(rows) > 24, f"only {len(rows)} rows -- included routers were not expanded"


def test_declaration_order_is_kept() -> None:
    from src.api.main import app

    paths = route_paths(app)
    # health.router is included first in main.py; well_known.router near last.
    assert paths.index("/api/health") < paths.index("/.well-known/oauth-authorization-server")
