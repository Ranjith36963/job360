"""Railway's deploy healthcheck must point at a route that actually exists.

`backend/railway.json`'s `healthcheckPath` is the one string that decides
whether a deploy takes traffic. Nothing imports it, so a rename of the route
that answers it fails silently on Railway (the deploy never goes healthy)
rather than in CI -- and the only other record of which path it is was prose
in `docs/product/DEPLOY.md`, which drifted: it claimed `/api/readyz` long
after the path moved to `/api/deployz` for the browser-agent deploy hold.

This pins the pair instead of restating it: the configured path must be a
GET route declared in `api/routes/health.py`, mounted under `/api`.
"""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RAILWAY_JSON = REPO / "backend" / "railway.json"
HEALTH_ROUTES = REPO / "backend" / "src" / "api" / "routes" / "health.py"


def _declared_get_paths() -> set[str]:
    """The `@router.get("...")` paths health.py declares (no /api prefix)."""
    body = HEALTH_ROUTES.read_text(encoding="utf-8")
    return set(re.findall(r'@router\.get\(\s*"([^"]+)"', body))


def test_healthcheck_path_is_a_declared_health_route():
    config = json.loads(RAILWAY_JSON.read_text(encoding="utf-8"))
    configured = config["deploy"]["healthcheckPath"]

    # api.main mounts the health router under /api, so every live path carries
    # that prefix; the router itself declares the bare path.
    assert configured.startswith("/api/"), configured
    bare = configured[len("/api") :]

    declared = _declared_get_paths()
    assert bare in declared, (
        f"railway.json healthcheckPath {configured!r} is not a GET route in "
        f"health.py (declared: {sorted(declared)}). A deploy would never go "
        f"healthy."
    )


def test_healthcheck_timeout_leaves_room_for_the_deploy_hold():
    """The probe may 503 while the old process finishes a browser form.

    `health.deployz` holds for at most `DEPLOY_HOLD_MAX_SECONDS`; Railway must
    be willing to wait longer than that, or it kills the deploy mid-hold.
    """
    from src.core import settings

    config = json.loads(RAILWAY_JSON.read_text(encoding="utf-8"))
    timeout = config["deploy"]["healthcheckTimeout"]

    assert timeout > settings.DEPLOY_HOLD_MAX_SECONDS, (
        f"healthcheckTimeout {timeout}s must exceed DEPLOY_HOLD_MAX_SECONDS "
        f"{settings.DEPLOY_HOLD_MAX_SECONDS}s"
    )
