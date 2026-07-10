"""Access-log user_id (3a) + 4xx reason logging (3b)."""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from src.api.errors import register_exception_logging
from src.api.middleware import AccessLogMiddleware, RequestIdMiddleware

# ─── 3a: access log records who ──────────────────────────────────────────────


def _app_user() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/whoami")
    async def whoami(request: Request):
        request.state.user_id = "u-123"  # what require_user does
        return {"ok": True}

    @app.get("/anon")
    async def anon():
        return {"ok": True}

    return app


def test_access_log_records_user_id(caplog):
    with caplog.at_level(logging.INFO, logger="job360.access"):
        with TestClient(_app_user()) as c:
            c.get("/whoami")
    rec = [r for r in caplog.records if r.name == "job360.access" and getattr(r, "path", "") == "/whoami"][-1]
    assert rec.user_id == "u-123"


def test_access_log_user_id_none_when_anonymous(caplog):
    with caplog.at_level(logging.INFO, logger="job360.access"):
        with TestClient(_app_user()) as c:
            c.get("/anon")
    rec = [r for r in caplog.records if r.name == "job360.access" and getattr(r, "path", "") == "/anon"][-1]
    assert rec.user_id is None


# ─── 3b: 4xx reasons logged ──────────────────────────────────────────────────


class _Body(BaseModel):
    x: int


def _app_4xx() -> FastAPI:
    app = FastAPI()
    register_exception_logging(app)

    @app.get("/missing")
    async def missing():
        raise HTTPException(status_code=404, detail="no such thing")

    @app.get("/needs-auth")
    async def needs_auth():
        raise HTTPException(status_code=401, detail="authentication required")

    @app.post("/validate")
    async def validate(b: _Body):
        return {"ok": True}

    return app


def test_4xx_detail_and_validation_logged_but_401_skipped(caplog):
    with caplog.at_level(logging.WARNING, logger="job360.error"):
        with TestClient(_app_4xx(), raise_server_exceptions=False) as c:
            r404 = c.get("/missing")
            r422 = c.post("/validate", json={"x": "not-an-int"})
            r401 = c.get("/needs-auth")

    assert r404.status_code == 404 and r422.status_code == 422 and r401.status_code == 401

    http_errs = {getattr(r, "path", ""): r for r in caplog.records if getattr(r, "event", "") == "http_error"}
    assert "/missing" in http_errs
    assert http_errs["/missing"].detail == "no such thing"
    assert http_errs["/missing"].status_code == 404

    val = [r for r in caplog.records if getattr(r, "event", "") == "validation_error" and getattr(r, "path", "") == "/validate"]
    assert val, "422 validation error not logged"
    assert val[-1].errors  # field-level detail captured

    # 401 is the routine auth check — must NOT be logged (noise control).
    assert "/needs-auth" not in http_errs
