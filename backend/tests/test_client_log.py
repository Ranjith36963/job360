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


_TOKEN_URL = "https://job360.uk/auth/magic?token=abc123#frag-secret999"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def audit_capture():
    cap = _Capture()
    lg = logging.getLogger("job360.audit")
    lg.addHandler(cap)
    yield cap
    lg.removeHandler(cap)


def _app_as_user() -> FastAPI:
    from src.api.auth_deps import CurrentUser, optional_user

    app = _app()
    app.dependency_overrides[optional_user] = lambda: CurrentUser(id="user-fake-1", email="u@example.test")
    return app


def _everything(records: list[logging.LogRecord]) -> str:
    return " ".join(repr(r.__dict__) for r in records)


def test_client_url_query_and_fragment_never_logged(caplog, audit_capture):
    body = {
        "level": "error",
        "message": f"failed at {_TOKEN_URL}",
        "url": _TOKEN_URL,
        "stack": f"Error\n at {_TOKEN_URL}",
        "context": _TOKEN_URL,
    }
    with caplog.at_level(logging.INFO, logger="job360.client"):
        with TestClient(_app_as_user()) as c:
            assert c.post("/api/client-log", json=body).status_code == 204
    dump = _everything(caplog.records) + _everything(audit_capture.records)
    assert "abc123" not in dump
    assert "secret999" not in dump
    assert audit_capture.records[-1].client_url == "https://job360.uk/auth/magic"


def test_anonymous_client_log_never_reaches_audit_log(caplog, audit_capture):
    with caplog.at_level(logging.INFO, logger="job360.client"):
        with TestClient(_app()) as c:
            assert c.post("/api/client-log", json={"level": "error", "message": "anon boom"}).status_code == 204
    assert audit_capture.records == []
    assert [r for r in caplog.records if getattr(r, "event", "") == "client_event"]


def test_authenticated_audit_row_has_masked_ip(audit_capture):
    with TestClient(_app_as_user()) as c:
        assert c.post("/api/client-log", json={"level": "error", "message": "auth boom"}).status_code == 204
    assert len(audit_capture.records) == 1
    rec = audit_capture.records[0]
    assert rec.user_id == "user-fake-1"
    assert rec.client_ip.startswith("ip_")
    assert "testclient" not in rec.client_ip


def test_relative_client_url_query_is_stripped_too(audit_capture):
    with TestClient(_app_as_user()) as c:
        body = {"level": "error", "message": "boom", "url": "/auth/magic?token=rel123#x"}
        assert c.post("/api/client-log", json=body).status_code == 204
    assert "rel123" not in _everything(audit_capture.records)
    assert audit_capture.records[-1].client_url == "/auth/magic"
