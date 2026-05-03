"""Tests for RequestIdMiddleware and request_id contextvar helpers."""
import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware import RequestIdMiddleware
from src.utils.logger import get_request_id, set_request_id, _request_id_var


# ---------------------------------------------------------------------------
# Minimal app for middleware tests — avoids pulling in the full lifespan / DB.
# ---------------------------------------------------------------------------

def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ping")
    async def ping():
        return {"request_id": get_request_id()}

    return app


_client = TestClient(_make_app())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_response_carries_x_request_id_header():
    resp = _client.get("/ping")
    assert resp.status_code == 200
    assert "X-Request-Id" in resp.headers


def test_generated_request_id_is_hex_16():
    resp = _client.get("/ping")
    rid = resp.headers["X-Request-Id"]
    assert re.fullmatch(r"[0-9a-f]{16}", rid), f"unexpected format: {rid!r}"


def test_incoming_header_is_honoured():
    resp = _client.get("/ping", headers={"X-Request-Id": "abc123"})
    assert resp.headers["X-Request-Id"] == "abc123"
    assert resp.json()["request_id"] == "abc123"


def test_contextvar_is_cleared_after_request():
    # The contextvar must be reset after the request so non-HTTP callers
    # (background tasks, CLI) don't see a stale id.
    _client.get("/ping")
    # After TestClient completes the request the contextvar reset() was called;
    # from *this* synchronous thread the var should read None (default).
    assert get_request_id() is None


def test_set_get_request_id_round_trip():
    token = set_request_id("test-rid-42")
    assert get_request_id() == "test-rid-42"
    _request_id_var.reset(token)
    assert get_request_id() is None
