"""Frontend → server log bridge endpoint (full-lifecycle logging piece D)."""
from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import client_log as cl
from src.services.auth import rate_limit


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    rate_limit.reset_for_tests()
    yield
    rate_limit.reset_for_tests()


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(cl.router, prefix="/api")
    return app


def test_client_log_records_event(caplog):
    with caplog.at_level(logging.INFO, logger="job360.client"):
        with TestClient(_app()) as c:
            r = c.post("/api/client-log", json={"level": "error", "message": "boom in UI", "context": "window.onerror"})
    assert r.status_code == 204
    recs = [x for x in caplog.records if getattr(x, "event", "") == "client_event"]
    assert recs, "no client_event logged"
    assert recs[-1].client_message == "boom in UI"
    assert recs[-1].client_level == "error"
    assert recs[-1].client_context == "window.onerror"


def test_client_log_rejects_empty_message():
    with TestClient(_app()) as c:
        r = c.post("/api/client-log", json={"message": ""})
    assert r.status_code == 422


def test_client_log_rate_limited(caplog):
    with caplog.at_level(logging.INFO, logger="job360.client"):
        with TestClient(_app()) as c:
            # 60 allowed in the window, then dropped — all return 204 either way.
            for _ in range(65):
                assert c.post("/api/client-log", json={"message": "spam"}).status_code == 204
    logged = [x for x in caplog.records if getattr(x, "event", "") == "client_event"]
    assert len(logged) == 60, f"expected 60 logged (cap), got {len(logged)}"
