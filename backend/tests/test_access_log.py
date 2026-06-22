"""Access-log middleware — one structured log line per HTTP request.

The backbone of full-lifecycle logging: every request (any route, even ones
that don't log themselves) emits method / path / status / duration_ms /
request_id on the `job360.access` logger, so a click is always traceable.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware import AccessLogMiddleware, RequestIdMiddleware


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)        # inner — logs the request
    app.add_middleware(RequestIdMiddleware)        # outer — sets request_id first
    @app.get("/ping")
    def ping():
        return {"ok": True}
    @app.get("/boom")
    def boom():
        raise RuntimeError("kaboom")
    return app


def test_access_log_line_emitted(caplog):
    app = _app()
    with caplog.at_level(logging.INFO, logger="job360.access"):
        with TestClient(app) as c:
            r = c.get("/ping")
    assert r.status_code == 200
    recs = [x for x in caplog.records if x.name == "job360.access"]
    assert recs, "no access log line was emitted"
    rec = recs[-1]
    assert rec.method == "GET"
    assert rec.path == "/ping"
    assert rec.status_code == 200
    assert isinstance(rec.duration_ms, (int, float))
    # request_id is set by the outer RequestIdMiddleware and must be on the line.
    assert getattr(rec, "request_id", None)


def test_access_log_records_status_on_exception(caplog):
    app = _app()
    with caplog.at_level(logging.INFO, logger="job360.access"):
        with TestClient(app, raise_server_exceptions=False) as c:
            c.get("/boom")
    recs = [x for x in caplog.records if x.name == "job360.access" and getattr(x, "path", "") == "/boom"]
    assert recs, "exception path produced no access log line"
    assert recs[-1].status_code == 500
