"""Global unhandled-exception logging (full-lifecycle logging gap E)."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.errors import register_exception_logging


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_logging(app)

    @app.get("/boom")
    def boom():
        raise RuntimeError("kaboom-xyz")

    return app


def test_unhandled_exception_is_logged_with_traceback(caplog):
    with caplog.at_level(logging.ERROR, logger="job360.error"):
        with TestClient(_app(), raise_server_exceptions=False) as c:
            r = c.get("/boom")
    assert r.status_code == 500
    recs = [x for x in caplog.records if getattr(x, "event", "") == "unhandled_exception"]
    assert recs, "exception was not logged"
    rec = recs[-1]
    assert rec.path == "/boom"
    assert rec.method == "GET"
    # The traceback must be captured (exc_info), and the original error named.
    assert rec.exc_info is not None
    assert "kaboom-xyz" in (rec.error or "")
