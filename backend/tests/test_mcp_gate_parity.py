"""Two bugs the review of the MCP slice found — pinned so they stay fixed.

1. Gate parity. MCP tools call route *functions* directly, so a route's
   ``Depends(...)`` chain never runs on the agent surface. Any gate a route
   declares (today: ``require_verified_user`` on the tailor routes) must be
   re-applied by the tool. The parity test reads the gate off each route's
   signature and checks the tool behaves the same, so adding a gate to a route
   without adding it to the tool turns this red.

2. A valid token is never throttled. The failed-bearer limiter is keyed by
   client IP, and behind the Next.js rewrite every agent shares the proxy's IP.
   The lock must apply to junk only — one bad client must not 429 everyone.

Extended for the application spine (docs/plans/2026-09-04-application-spine/
spec.md S11): seven new tools land on ``src.api.routes.applications`` — each
one MUST get a ``TOOL_ROUTES`` row here or ``test_the_parity_table_covers_
every_tool`` turns red, by design. ``record_application`` is NOT a new TOOL
(spec: "the existing tool enriched, not a new one") — its NAME and call
shape (``job_id``, ``channel``, ``note`` still work) are unchanged — but a
review finding (C1) rewired its IMPLEMENTATION off the legacy
``receipts.create_receipt`` onto the new rich
``applications.record_application_receipt`` route, so this row's
(module, function) pair now points there too. None of the eight
(the seven new tools, plus this rewired one) are ``require_verified_user``
(spec: "Every route Depends(require_user)... not require_verified_user —
nothing here spends an LLM call").
"""
from __future__ import annotations

import inspect

import pytest
from httpx import ASGITransport, AsyncClient

import src.core.settings as settings_mod
from src.core import settings
from src.repositories import pgsync

pytest.importorskip("mcp")

JOB = {
    "title": "Senior Python Engineer",
    "company": "Acme Ltd",
    "location": "London",
    "description": "Build FastAPI services with Postgres. " * 8,
}

# tool name → (route module, route function, minimal valid arguments)
TOOL_ROUTES = {
    "get_profile": ("profile", "get_profile", {}),
    "bring_job": ("bring", "bring_job", JOB),
    # Slice 5 (#483) deleted `routes/jobs.py` with the public catalog reads it
    # served. The TOOL is unchanged (same name, same `job_id` argument); its
    # ROUTE moved to `GET /api/applications/job/{job_id}` — per-user, so an id
    # the caller never brought reads as 404 instead of somebody else's paste.
    "get_job": ("applications", "get_job", {"job_id": 987654321}),
    "tailor_documents": ("tailor", "generate", {"job_id": 987654321}),
    "get_tailored_documents": ("tailor", "get_tailored", {"job_id": 987654321}),
    # C1 (application-spine review) — rewired onto the rich receipt route;
    # the tool's own call shape (job_id, channel, note) is unchanged.
    "record_application": ("applications", "record_application_receipt", {"job_id": 987654321}),
    "list_receipts": ("receipts", "list_receipts", {}),
    "get_receipt": ("receipts", "get_receipt", {"receipt_id": 987654321}),
    # Application spine (spec 2026-09-04, S11) — seven new tools, all on the
    # new "applications" route module, none require_verified_user.
    "get_application": ("applications", "get_application", {"application_id": 987654321}),
    "list_applications": ("applications", "list_applications", {}),
    "save_artifact": (
        "applications", "save_artifact", {"application_id": 987654321, "kind": "cv", "text": "x"}
    ),
    "save_fit": ("applications", "save_fit", {"application_id": 987654321}),
    "record_event": (
        "applications", "record_event", {"application_id": 987654321, "event_type": "note"}
    ),
    "whats_new": ("applications", "whats_new", {}),
    "export_history": ("applications", "export_history", {}),
}


def _route_requires_verified(module_name: str, fn_name: str) -> bool:
    import importlib

    from src.api.auth_deps import require_verified_user

    fn = getattr(importlib.import_module(f"src.api.routes.{module_name}"), fn_name)
    for param in inspect.signature(fn).parameters.values():
        dep = getattr(param.default, "dependency", None)
        if dep is require_verified_user:
            return True
    return False


def _unverify(user_id: str) -> None:
    conn = pgsync.connect(str(settings_mod.DB_PATH))
    conn.execute("UPDATE users SET email_verified_at = NULL WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


async def _mint(authenticated_async_context) -> str:
    async with authenticated_async_context() as client:
        resp = await client.post("/api/tokens", json={"name": "agent"})
        assert resp.status_code == 201, resp.text
        return resp.json()["token"]


def _mcp_client(token: str):
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


def test_the_parity_table_covers_every_tool():
    from src.api.mcp_server import build_server

    listed = {t.name for t in build_server()._tool_manager.list_tools()}
    assert listed == set(TOOL_ROUTES), "a new tool needs a row in TOOL_ROUTES"


def test_the_tailor_routes_really_are_gated():
    """If this ever flips, the parity test below is testing nothing."""
    assert _route_requires_verified("tailor", "generate")
    assert _route_requires_verified("tailor", "get_tailored")
    assert not _route_requires_verified("bring", "bring_job")


@pytest.mark.asyncio
async def test_every_tool_applies_the_same_email_gate_as_its_route(authenticated_async_context, monkeypatch):
    from src.api.mcp_server import mcp_runtime

    monkeypatch.delenv("REQUIRE_EMAIL_VERIFICATION", raising=False)
    token = await _mint(authenticated_async_context)
    _unverify(authenticated_async_context.fixture_user_id)

    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            for tool, (module_name, fn_name, args) in TOOL_ROUTES.items():
                res = await mcp.call_tool(tool, args)
                text = "".join(getattr(c, "text", "") for c in res.content)
                gated = "email_not_verified" in text and "403" in text
                expected = _route_requires_verified(module_name, fn_name)
                assert gated == expected, (
                    f"{tool}: route gate={expected} but tool gate={gated}: {text[:200]}"
                )


@pytest.mark.asyncio
async def test_a_valid_token_is_never_throttled_by_someone_elses_junk(authenticated_async_context, monkeypatch):
    monkeypatch.setattr(settings, "API_TOKEN_FAIL_MAX_PER_MIN", 2)
    token = await _mint(authenticated_async_context)

    # Enough junk from this client IP to trip the lock…
    async with _bearer_client("j360_junk") as attacker:
        assert (await attacker.get("/api/auth/me")).status_code == 401
        assert (await attacker.get("/api/auth/me")).status_code == 401
        assert (await attacker.get("/api/auth/me")).status_code == 429

    # …and the real token, from the very same IP, still works.
    async with _bearer_client(token) as agent:
        assert (await agent.get("/api/auth/me")).status_code == 200

    # The lock is on junk only — junk is still refused loudly.
    async with _bearer_client("j360_junk") as attacker:
        assert (await attacker.get("/api/auth/me")).status_code == 429
