"""MCP endpoint at /api/mcp — the same routes, reached by an agent with a token.

Contract (docs/plans/2026-09-03-mcp-server/spec.md R4): no bearer → 401 with a
``WWW-Authenticate: Bearer`` challenge; eight tools; each tool calls the existing
route function in-process as the token's user; route errors surface as tool
errors carrying the HTTP status and detail; another user's rows are unreachable.

The tests drive the endpoint over real streamable-HTTP JSON-RPC with the
official ``mcp`` client, through httpx2's ASGI transport — no socket, no server.
"""
from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

pytest.importorskip("mcp")

EXPECTED_TOOLS = {
    "get_profile",
    "bring_job",
    "get_job",
    "tailor_documents",
    "get_tailored_documents",
    "record_application",
    "list_receipts",
    "get_receipt",
    # Application spine (docs/plans/2026-09-04-application-spine/spec.md
    # §Tool contracts) — seven new tools, 8 -> 15 total.
    "get_application",
    "list_applications",
    "save_artifact",
    "save_fit",
    "record_event",
    "whats_new",
    "export_history",
}

JOB = {
    "title": "Senior Python Engineer",
    "company": "Acme Ltd",
    "location": "London",
    "description": "Build FastAPI services with Postgres. " * 8,
    "apply_url": "https://acme.example/jobs/1",
}


async def _mint_token(authenticated_async_context, name: str = "agent") -> str:
    async with authenticated_async_context() as client:
        resp = await client.post("/api/tokens", json={"name": name})
        assert resp.status_code == 201, resp.text
        return resp.json()["token"]


def _mcp_client(token: str | None):
    """Official MCP client wired straight into the FastAPI app (in-process)."""
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers=headers
    )
    return Client(streamable_http_client("http://test/api/mcp", http_client=http))


def _payload(result) -> dict:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def _error_text(result) -> str:
    assert result.is_error, "expected a tool error"
    return result.content[0].text


@pytest.mark.asyncio
async def test_no_bearer_is_401_with_a_challenge(authenticated_async_context):
    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context():
        pass
    async with mcp_runtime():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
            resp = await anon.post(
                "/api/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Accept": "application/json, text/event-stream"},
            )
            assert resp.status_code == 401
            assert resp.headers.get("www-authenticate", "").startswith("Bearer")


@pytest.mark.asyncio
async def test_without_the_runtime_the_endpoint_says_503_not_crash(authenticated_async_context):
    from src.api.main import app

    token = await _mint_token(authenticated_async_context)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as agent:
        resp = await agent.post(
            "/api/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert resp.status_code == 503


@pytest.mark.asyncio
async def test_tools_list_is_exactly_the_expected_tools(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            listed = await mcp.list_tools()
            assert {t.name for t in listed.tools} == EXPECTED_TOOLS
            by_name = {t.name: t for t in listed.tools}
            assert set(by_name["bring_job"].input_schema["required"]) == {"title", "company", "description"}
            assert all(t.description for t in listed.tools), "every tool tells the agent what it does"


@pytest.mark.asyncio
async def test_bring_then_read_then_record_then_list_round_trip(authenticated_async_context, fixture_user_id):
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            brought = _payload(await mcp.call_tool("bring_job", JOB))
            job_id = brought["job_id"]
            assert brought["title"] == JOB["title"] and brought["company"] == JOB["company"]
            assert brought["existing"] is False
            assert brought["url"].endswith(f"/jobs/{job_id}")

            again = _payload(await mcp.call_tool("bring_job", JOB))
            assert again["job_id"] == job_id and again["existing"] is True, "same ad twice = same job"

            job = _payload(await mcp.call_tool("get_job", {"job_id": job_id}))
            assert job["job_id"] == job_id and "description" in job

            # C1 (application-spine review) — record_application is rewired onto
            # the rich `POST /applications/{id}/receipt` route; its response is
            # now the R8 shape (receipt_id/event_id/etc, plus job_id echoed back
            # for continuity), not the legacy ReceiptSummary shape.
            receipt = _payload(
                await mcp.call_tool(
                    "record_application", {"job_id": job_id, "channel": "company site", "note": "via MCP"}
                )
            )
            assert receipt["job_id"] == job_id and receipt["sent_at"]
            assert receipt["channel"] == "company site"
            assert receipt["event_id"]

            listed = _payload(await mcp.call_tool("list_receipts", {}))
            assert listed["total"] == 1
            assert listed["receipts"][0]["id"] == receipt["receipt_id"]

            full = _payload(await mcp.call_tool("get_receipt", {"receipt_id": receipt["receipt_id"]}))
            assert full["note"] == "via MCP" and full["channel"] == "company site"

    # The web app sees the same record — one API, every surface.
    async with authenticated_async_context() as client:
        resp = await client.get("/api/receipts")
        assert resp.status_code == 200
        assert [r["id"] for r in resp.json()["receipts"]] == [receipt["receipt_id"]]


@pytest.mark.asyncio
async def test_route_errors_become_tool_errors_with_status_and_detail(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            missing = _error_text(await mcp.call_tool("get_job", {"job_id": 987654321}))
            assert "404" in missing and "not found" in missing.lower()

            no_profile = _error_text(await mcp.call_tool("get_profile", {}))
            assert "404" in no_profile

            not_applied = _error_text(await mcp.call_tool("get_receipt", {"receipt_id": 987654321}))
            assert "404" in not_applied

            bad_input = await mcp.call_tool("bring_job", {"title": "x", "company": "y", "description": ""})
            assert bad_input.is_error


@pytest.mark.asyncio
async def test_another_users_receipt_is_unreachable(authenticated_async_context):
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            job_id = _payload(await mcp.call_tool("bring_job", JOB))["job_id"]
            receipt_id = _payload(await mcp.call_tool("record_application", {"job_id": job_id}))["receipt_id"]

    # A second account, with its own token.
    from src.api.main import app

    other = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    async with other:
        reg = await other.post(
            "/api/auth/register", json={"email": "other@example.com", "password": "an0therS3cret!"}
        )
        assert reg.status_code in (200, 201), reg.text
        login = await other.post(
            "/api/auth/login", json={"email": "other@example.com", "password": "an0therS3cret!"}
        )
        assert login.status_code == 200, login.text
        from src.api import dependencies as api_deps

        db = await api_deps.get_db()
        await db._conn.execute(
            "UPDATE users SET email_verified_at = ? WHERE email = ?",
            ("2026-01-01T00:00:00Z", "other@example.com"),
        )
        await db._conn.commit()
        minted = await other.post("/api/tokens", json={"name": "other agent"})
        assert minted.status_code == 201, minted.text
        other_token = minted.json()["token"]

    async with mcp_runtime():
        async with _mcp_client(other_token) as mcp:
            stolen = await mcp.call_tool("get_receipt", {"receipt_id": receipt_id})
            assert stolen.is_error and "404" in stolen.content[0].text
            mine = _payload(await mcp.call_tool("list_receipts", {}))
            assert mine["total"] == 0


@pytest.mark.asyncio
async def test_record_application_writes_a_rich_receipt_through_the_new_route(authenticated_async_context):
    """C1 (application-spine review) — `record_application` must go through
    `applications.record_application_receipt` (the rich receipt), not the
    legacy `receipts.create_receipt`: a NAMED artifact version and
    `confirmation` passed to the MCP tool must land on the receipt exactly as
    the web's `POST /applications/{id}/receipt` would record them — the old
    legacy route has no `confirmation` or `cv_artifact_id` field at all, so
    this would 422/be silently dropped if the tool still called it.
    """
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            job_id = _payload(await mcp.call_tool("bring_job", JOB))["job_id"]

    async with authenticated_async_context() as client:
        apps = await client.get("/api/applications")
        assert apps.status_code == 200, apps.text
        application_id = apps.json()["applications"][0]["id"]
        saved = await client.post(
            f"/api/applications/{application_id}/artifacts",
            json={"kind": "cv", "text": "my tailored cv"},
        )
        assert saved.status_code == 201, saved.text
        cv_artifact_id = saved.json()["artifact_id"]

    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            receipt = _payload(
                await mcp.call_tool(
                    "record_application",
                    {"job_id": job_id, "confirmation": "REF-12345", "cv_artifact_id": cv_artifact_id},
                )
            )

    assert receipt["confirmation"] == "REF-12345"
    assert receipt["cv_artifact_id"] == cv_artifact_id
    assert receipt["cv_version_no"] == 1

    async with authenticated_async_context() as client:
        detail = await client.get(f"/api/applications/{application_id}")
        assert detail.status_code == 200, detail.text
        receipts = detail.json()["receipts"]
        assert receipts[0]["confirmation"] == "REF-12345"
        assert receipts[0]["cv_artifact_id"] == cv_artifact_id
        # The event log recorded it too — not just the receipts table.
        statuses = [e["event_type"] for e in detail.json()["events"]]
        assert "applied" in statuses
