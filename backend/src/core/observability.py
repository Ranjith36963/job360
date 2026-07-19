"""Shared observability setup — Sentry init used by BOTH the API and the worker.

docs/fable/09 P0: the ARQ worker process never initialised Sentry, so worker
crashes (e.g. a failed cron) were invisible — Sentry looked "healthy" precisely
where the worst failures happened. This module centralises the prod-gated init +
PII scrubber so the API (`main.py`) and the worker (`workers/settings.py`) share
one implementation instead of the API being the only observed process.
"""

from __future__ import annotations

import os
from typing import Any

# H4 — request headers that must NEVER reach Sentry (credentials / session).
_SENSITIVE_HEADERS = frozenset({"cookie", "authorization", "set-cookie"})


def _strip_password_fields(obj: Any) -> None:
    """Recursively delete any ``password`` key (case-insensitive) in-place."""
    if isinstance(obj, dict):
        for key in [k for k in obj if isinstance(k, str) and k.lower() == "password"]:
            obj.pop(key, None)
        for value in obj.values():
            _strip_password_fields(value)
    elif isinstance(obj, list):
        for item in obj:
            _strip_password_fields(item)


def _scrub_pii(event: Any, _hint: Any) -> Any:
    """Strip auth headers, cookies, request bodies, and password fields before an
    event leaves the box (H4).

    Belt-and-suspenders on top of ``send_default_pii=False``: bodies may hold
    passwords / CV text; the Cookie header holds the session token. Never raises —
    Sentry drops the event if ``before_send`` throws, so fail safe by returning
    the event unmodified on shapes we don't expect.
    """
    try:
        req = event.get("request") if isinstance(event, dict) else None
        if isinstance(req, dict):
            headers = req.get("headers")
            if isinstance(headers, dict):
                for h in [h for h in headers if h.lower() in _SENSITIVE_HEADERS]:
                    headers.pop(h, None)
            req.pop("cookies", None)
            if "data" in req:
                req["data"] = "[redacted]"
        _strip_password_fields(event)
    except Exception:  # noqa: BLE001 — scrubbing must never break error reporting
        return event
    return event


def init_sentry(*, component: str = "api") -> bool:
    """Initialise Sentry error tracking, prod-only. Returns True if it reported.

    Guards on BOTH ``SENTRY_DSN`` being set AND running in a deployed production
    environment (``APP_ENV=production`` or ``RAILWAY_ENVIRONMENT``), so local dev
    and the test suite never report — even with a DSN in the shared ``.env``.

    ``component`` ("api" | "worker") is attached as a tag so worker errors are
    distinguishable from API errors in the same Sentry project.
    """
    import src.core.settings as _settings  # module attr so tests can monkeypatch

    dsn = _settings.SENTRY_DSN
    is_prod = os.environ.get("APP_ENV", "").lower() == "production" or bool(
        os.environ.get("RAILWAY_ENVIRONMENT", "")
    )
    if not dsn or not is_prod:
        return False

    import sentry_sdk
    from sentry_sdk.integrations.logging import ignore_logger

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("RAILWAY_ENVIRONMENT") or "production",
        send_default_pii=False,
        before_send=_scrub_pii,
        traces_sample_rate=0.1,
    )
    sentry_sdk.set_tag("component", component)
    # docs/fable/09 P2 — the /api/client-log bridge logs BROWSER-side events at
    # ERROR level ("job360.client"), and Sentry's logging integration turns any
    # ERROR log into a Sentry event. That flooded the BACKEND error stream with
    # client noise (incl. the synthetic-smoke monitor's own failure posts). Ignore
    # this logger so client logs still reach data/logs/ but never create backend
    # Sentry events. Backend Sentry tracks backend errors; client errors belong to
    # a frontend SDK, not the proxied log bridge.
    ignore_logger("job360.client")
    return True
