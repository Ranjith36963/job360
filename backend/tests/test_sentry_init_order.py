"""Sentry must initialise at IMPORT time, before the FastAPI app is built.

Found during the 2026-07-24 prod verification: `_init_sentry()` ran inside the
lifespan hook — AFTER `app = FastAPI(...)` was constructed. Starlette builds its
middleware stack when the app object is created, so Sentry's Starlette/FastAPI
tracing integration (which patches at `sentry_sdk.init` time) came too late:
error capture worked (global logging hooks), but **performance tracing could
never emit a single http.server transaction** — confirmed by the Sentry project
containing arq-worker transactions yet zero backend request transactions in its
entire history.

The test runs in a SUBPROCESS so the fake DSN and the initialised SDK cannot
leak into (or send anything from) the rest of the offline suite.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_BACKEND_DIR = str(Path(__file__).resolve().parents[1])

_PROBE = """
import os
os.environ["APP_ENV"] = "production"
os.environ.setdefault("SESSION_SECRET", "x" * 40)
os.environ.setdefault("CHANNEL_ENCRYPTION_KEY", "x" * 40)
os.environ.setdefault("DATABASE_URL", "postgresql://job360:job360dev@localhost:5433/job360")

import src.core.settings as settings
# Fake but well-formed DSN. Import alone must not send anything (Sentry only
# talks to the network when an event/envelope is captured).
settings.SENTRY_DSN = "https://0123456789abcdef0123456789abcdef@o0.ingest.sentry.io/0"

import src.api.main  # noqa: F401 — the import IS the test subject

import sentry_sdk
client = sentry_sdk.get_client()
print("ACTIVE" if client.is_active() else "INACTIVE")
"""


def test_sentry_is_active_at_import_time_in_production() -> None:
    """Importing src.api.main with a DSN + prod env must initialise Sentry
    BEFORE the app object exists — i.e. by the time the import returns.

    Pre-fix this printed INACTIVE: init only happened once the lifespan ran,
    which is too late for the tracing integration.
    """
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"probe crashed:\n{proc.stderr[-2000:]}"
    # EXACT match — "INACTIVE" contains the substring "ACTIVE", so a substring
    # check here silently passes on the broken state (it did, once).
    assert proc.stdout.splitlines()[-1].strip() == "ACTIVE", (
        "Sentry was NOT initialised at import time — the tracing integration "
        f"is dead when init waits for the lifespan. stdout={proc.stdout!r}"
    )
