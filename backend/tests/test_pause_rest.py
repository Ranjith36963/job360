"""Pause stops the assistant on the REST door too (owner decision 2026-10-26: "Pause stops everything").

An assistant holding a ``j360_`` token or an OAuth grant can call ``/api/...`` directly instead of MCP. While
the user is paused, every route in ``mcp_server.PAUSE_REFUSED_ROUTES`` answers 423 with the MCP refusal to a
bearer call and writes nothing; reads and the wind-down routes keep working; the user's own web session is
never paused; a resume (or another user's pause) changes nothing for anyone else. Helpers are copied, never
imported, apart from the MCP parity table (``TOOL_ROUTES``) this file holds the REST table against.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import pkgutil
import secrets
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

import src.core.settings as settings_mod
from src.repositories import pgsync

PAUSE = "assistant_settings.paused_until"
JOB = {
    "title": "Senior Python Engineer",
    "company": "Acme Ltd",
    "location": "London",
    "description": "Build FastAPI services with Postgres. " * 8,
    "apply_url": "https://acme.example/jobs/1",
}
TABLES = (
    "jobs", "applications", "application_events", "application_artifacts", "application_receipts",
    "application_contacts", "application_asks", "inbox_items", "contact_outreach", "user_templates",
)

# Token-reachable write routes that stay OPEN while paused, each with its reason. A new write route an
# assistant can reach must land here or in PAUSE_REFUSED_ROUTES (test_every_token_write_route_picks_a_side).
PAUSE_OPEN_ROUTES = {
    ("PATCH", "profile.update_profile"): "update_profile: how the user pauses, or asks to resume",
    ("POST", "profile.upload_cv"): "the user's own profile, like update_profile",
    ("POST", "profile.upsert_preferences"): "the user's own profile, like update_profile",
    ("POST", "profile.upsert_profile"): "the user's own profile, like update_profile",
    ("POST", "profile.upload_linkedin"): "the user's own profile, like update_profile",
    ("POST", "profile.upload_github"): "the user's own profile, like update_profile",
    ("POST", "profile.restore_version"): "the user's own profile, like update_profile",
    ("POST", "receipts.create_receipt"): "record_application: a submit made before the pause",
    ("POST", "applications.record_application_receipt"): "record_application: a submit made before the pause",
    ("POST", "applications.download_application_artifact"): "renders a stored version; writes nothing",
    ("POST", "tailor.download"): "renders a stored version; writes nothing",
    ("POST", "proof.create_proof_link"): "get_proof_upload_link: proof of a past submit",
    ("POST", "asks.answer_ask"): "answer_ask: the user's own answer, or a withdraw",
    ("POST", "asks.withdraw_ask"): "answer_ask withdraw: winding down (ask_withdrawn)",
    ("POST", "asks.confirm_ask"): "web session only inside the route (403 for any assistant)",
    ("POST", "own_browser.release"): "own_browser_queue release: gives a claimed task back",
    ("POST", "schedule.mark_done"): "mark_done: closing a run",
    ("POST", "doctor.report_missing_tool"): "report_missing_tool: a read-side report",
    ("POST", "connectors.report_capabilities"): "report_capabilities: a read-side report",
    ("POST", "auth.verify_email_request"): "account housekeeping, not assistant work",
    ("POST", "client_log.client_log"): "the frontend's error log bridge, not assistant work",
}


def _dep_calls(dependant) -> set[Any]:
    calls = {dependant.call}
    for sub in dependant.dependencies:
        calls |= _dep_calls(sub)
    return calls


def _token_routes() -> dict[tuple[str, str], APIRoute]:
    """Every route an assistant's bearer can reach: it needs a user, and not a web session."""
    from src.api import auth_deps, routes
    from src.api.pause_gate import route_key

    out = {}
    for info in pkgutil.iter_modules(routes.__path__):
        router = getattr(importlib.import_module(f"src.api.routes.{info.name}"), "router", None)
        for r in getattr(router, "routes", []):
            if not isinstance(r, APIRoute):
                continue
            calls = _dep_calls(r.dependant)
            authed = calls & {auth_deps.require_user, auth_deps.require_verified_user, auth_deps.optional_user}
            if authed and auth_deps.require_session_user not in calls:
                for method in r.methods:
                    out[route_key(method, r.endpoint)] = r
    return out


# ── The table ───────────────────────────────────────────────────────────────


def test_every_refused_route_is_a_real_token_route():
    from src.api.mcp_server import PAUSE_REFUSED_ROUTES, PAUSE_REFUSED_TOOLS

    reachable = _token_routes()
    assert set(PAUSE_REFUSED_ROUTES) <= set(reachable), set(PAUSE_REFUSED_ROUTES) - set(reachable)
    named = {t for t in PAUSE_REFUSED_ROUTES.values() if t}
    assert named <= PAUSE_REFUSED_TOOLS | {"record_event", "own_browser_queue"}


def test_every_refused_tool_has_its_route_refused():
    """The parity half: a tool PAUSE_REFUSED_TOOLS refuses must have its route in the REST table."""
    from src.api.mcp_server import PAUSE_REFUSED_ROUTES, PAUSE_REFUSED_TOOLS
    from tests.test_mcp_gate_parity import TOOL_ROUTES

    by_route = {key[1]: tool for key, tool in PAUSE_REFUSED_ROUTES.items()}
    for tool in PAUSE_REFUSED_TOOLS | {"record_event", "own_browser_queue"}:
        module, fn, _args = TOOL_ROUTES[tool]
        assert by_route.get(f"{module}.{fn}") == tool, f"{tool}: route {module}.{fn} is not in PAUSE_REFUSED_ROUTES"


def test_every_token_write_route_picks_a_side():
    from src.api.mcp_server import PAUSE_REFUSED_ROUTES

    writes = {k for k in _token_routes() if k[0] != "GET"}
    unclassified = writes - set(PAUSE_REFUSED_ROUTES) - set(PAUSE_OPEN_ROUTES)
    assert not unclassified, f"add these to PAUSE_REFUSED_ROUTES or PAUSE_OPEN_ROUTES: {sorted(unclassified)}"
    assert not set(PAUSE_OPEN_ROUTES) & set(PAUSE_REFUSED_ROUTES)
    assert set(PAUSE_OPEN_ROUTES) <= writes, set(PAUSE_OPEN_ROUTES) - writes


# ── Through the real door ───────────────────────────────────────────────────


def _refused_calls(app_id: int, job_id: int) -> dict[tuple[str, str], tuple[str, str, Any]]:
    """One bearer call per refused route, with a body that would otherwise write or start work."""
    a = f"/api/applications/{app_id}"
    return {
        ("POST", "bring.bring_job"): ("POST", "/api/jobs/bring", {**JOB, "title": "Another role"}),
        ("POST", "bring.fetch_url_route"): ("POST", "/api/jobs/fetch-url", {"url": "https://acme.example/jobs/2"}),
        ("POST", "asks.create_ask"): ("POST", "/api/asks", {"question": "Which CV?", "application_id": app_id}),
        ("POST", "asks.resolve_inbox_item"): ("POST", "/api/inbox-items/1/resolve", {"dismiss": True}),
        ("POST", "applications.match_email"): ("POST", "/api/applications/match-email", {"sender": "r@acme.example"}),
        ("POST", "applications.mark_inbox_checked"): (
            "POST", "/api/applications/inbox-checked", {"application_ids": [app_id]}
        ),
        ("POST", "applications.record_events"): (
            "POST", "/api/applications/record-events",
            {"events": [{"application_id": app_id, "event_type": "note", "detail": "x"}]},
        ),
        ("POST", "applications.record_event"): ("POST", f"{a}/events", {"event_type": "submit_attempted"}),
        ("POST", "applications.save_artifact"): ("POST", f"{a}/artifacts", {"kind": "cv", "text": "A CV."}),
        ("PUT", "applications.save_fit"): ("PUT", f"{a}/fit", {"verdict": "Good fit"}),
        ("PUT", "applications.set_visa"): ("PUT", f"{a}/visa", {"visa_signal": "sponsors"}),
        ("POST", "applications.add_contact"): ("POST", f"{a}/contacts", {"name": "Rita Recruiter"}),
        ("POST", "applications.add_person"): ("POST", "/api/contacts", {"name": "Cold Contact"}),
        ("POST", "applications.add_contacts_batch"): ("POST", "/api/contacts/batch", {"contacts": [{"name": "B"}]}),
        ("PATCH", "applications.update_contact"): ("PATCH", "/api/contacts/1", {"notes": "x"}),
        ("POST", "applications.record_outreach"): ("POST", "/api/contacts/1/outreach", {"text": "Hi"}),
        ("PATCH", "applications.update_job_facts"): ("PATCH", f"{a}/job", {"country": "GB"}),
        ("GET", "applications.outreach_check"): ("GET", "/api/contacts/1/outreach-check?message_id=1", None),
        ("GET", "applications.auto_email_check"): ("GET", f"{a}/auto-email-check?kind=checkin", None),
        ("POST", "applications.check_application_form"): (
            "POST", f"{a}/kit/form-check", {"questions": [{"label": "Name"}]}
        ),
        ("GET", "applications.get_application_kit"): ("GET", f"{a}/kit", None),
        ("POST", "browser.browser_action"): ("POST", "/api/browser", {"action": "status"}),
        ("GET", "own_browser.queue"): ("GET", "/api/own-browser/queue", None),
        ("POST", "own_browser.take"): ("POST", "/api/own-browser/take", {"session": "s1"}),
        ("POST", "templates.save_template"): (
            "POST", "/api/templates", {"kind": "outreach_template", "label": "x", "text": "Hello."}
        ),
        ("PATCH", "tailor.save_edit"): ("PATCH", f"/api/tailor/{job_id}/cv", {"text": "My edit."}),
        ("POST", "tailor.keep"): ("POST", f"/api/tailor/{job_id}/cv/keep", None),
    }


def _counts() -> dict[str, int]:
    conn = pgsync.connect(str(settings_mod.DB_PATH))
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}  # noqa: S608
    finally:
        conn.close()


def _bearer(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


async def _call(client: AsyncClient, method: str, path: str, body: Any):
    return await client.request(method, path, json=body) if body is not None else await client.request(method, path)


async def _pause(client: AsyncClient, value: str) -> None:
    resp = await client.patch("/api/profile", json={"edits": [{"path": PAUSE, "value": value}]})
    assert resp.status_code == 200, resp.text


async def _mint(client: AsyncClient) -> str:
    resp = await client.post("/api/tokens", json={"name": "agent"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


async def _second_user_token() -> str:
    """Register, verify and sign in another user; return their personal token."""
    from src.api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        creds = {"email": "bob@example.com", "password": "s3cretpassword"}
        assert (await other.post("/api/auth/register", json=creds)).status_code == 201
        conn = pgsync.connect(str(settings_mod.DB_PATH))
        conn.execute("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", creds["email"]))
        conn.commit()
        conn.close()
        assert (await other.post("/api/auth/login", json=creds)).status_code == 200
        return await _mint(other)


@pytest.mark.asyncio
async def test_pause_refuses_token_calls_on_rest_not_the_web_session(authenticated_async_context):
    from src.api.mcp_server import PAUSE_REFUSED_ROUTES, PAUSED_MESSAGE

    async with authenticated_async_context() as web:
        token = await _mint(web)
        brought = await web.post("/api/jobs/bring", json=JOB)
        assert brought.status_code == 200, brought.text
        app_id, job_id = brought.json()["application_id"], brought.json()["job"]["id"]
        await _pause(web, "until_resumed")
    bob_token = await _second_user_token()
    calls = _refused_calls(app_id, job_id)
    assert set(calls) == set(PAUSE_REFUSED_ROUTES), "every refused route gets a call here"

    async with _bearer(token) as agent:
        before = _counts()
        for key, (method, path, body) in calls.items():
            resp = await _call(agent, method, path, body)
            assert resp.status_code == 423, (key, resp.status_code, resp.text)
            assert resp.json()["detail"] == {
                "status": "paused", "paused_until": "until_resumed", "message": PAUSED_MESSAGE,
            }, key
        assert _counts() == before, "a refused route wrote something"

        # Reads and the wind-down routes stay open to the token.
        assert (await agent.get("/api/profile")).status_code == 200
        listed = await agent.get("/api/applications")
        assert listed.status_code == 200 and [x["id"] for x in listed.json()["applications"]] == [app_id]
        assert (await agent.get(f"/api/applications/{app_id}")).status_code == 200
        note = await agent.post(f"/api/applications/{app_id}/events", json={"event_type": "note", "detail": "Paused."})
        assert note.status_code == 201, note.text
        release = await agent.post("/api/own-browser/release", json={"session": "s1", "task_id": "x", "reason": "other"})
        assert release.status_code != 423, release.text

    # Another user's token is not paused by this user's pause.
    async with _bearer(bob_token) as bob:
        assert (await bob.post("/api/jobs/bring", json=JOB)).status_code == 200

    # The user's own web session is never paused.
    async with authenticated_async_context() as web:
        art = await web.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "My CV."})
        assert art.status_code == 201, art.text
        assert (await web.post("/api/jobs/bring", json={**JOB, "title": "Web role"})).status_code == 200
        ev = await web.post(f"/api/applications/{app_id}/events", json={"event_type": "fit_judged", "detail": "Mine"})
        assert ev.status_code == 201, ev.text
        assert (await web.get(f"/api/applications/{app_id}/kit")).status_code == 200
        await _pause(web, "")  # resume

    async with _bearer(token) as agent:
        again = await agent.post("/api/jobs/bring", json={**JOB, "title": "After resume"})
        assert again.status_code == 200, again.text
        saved = await agent.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": "A CV."})
        assert saved.status_code == 201, saved.text


@pytest.mark.asyncio
async def test_pause_refuses_an_oauth_bearer_too(authenticated_async_context):
    """An OAuth access token (``j360a_``) is the same bearer path: refused while paused, open after."""
    from src.api.main import app

    redirect = "https://claude.ai/api/mcp/auth_callback"
    verifier = secrets.token_urlsafe(48)[:64]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        reg = await anon.post("/api/oauth/register", json={"redirect_uris": [redirect]})
        assert reg.status_code == 201, reg.text
        client_id = reg.json()["client_id"]
    async with authenticated_async_context() as web:
        params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect,
                  "code_challenge": challenge, "code_challenge_method": "S256", "state": "s"}
        auth = await web.get("/api/oauth/authorize", params=params, follow_redirects=False)
        assert auth.status_code == 302, auth.text
        rid = auth.headers["location"].rstrip("/").rsplit("/", 1)[-1]
        decided = await web.post(f"/api/oauth/authorize/{rid}/decision", json={"approve": True})
        assert decided.status_code == 200, decided.text
        await _pause(web, "until_resumed")
    code = parse_qs(urlsplit(decided.json()["redirect_to"]).query)["code"][0]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        tok = await anon.post("/api/oauth/token", data={
            "grant_type": "authorization_code", "code": code, "client_id": client_id,
            "redirect_uri": redirect, "code_verifier": verifier,
        })
        assert tok.status_code == 200, tok.text
    access = tok.json()["access_token"]
    assert access.startswith("j360a_")

    async with _bearer(access) as agent:
        refused = await agent.post("/api/jobs/bring", json=JOB)
        assert refused.status_code == 423 and refused.json()["detail"]["status"] == "paused", refused.text
        assert (await agent.get("/api/applications")).status_code == 200
    async with authenticated_async_context() as web:
        await _pause(web, "")
    async with _bearer(access) as agent:
        assert (await agent.post("/api/jobs/bring", json=JOB)).status_code == 200


@pytest.mark.asyncio
async def test_a_bad_bearer_on_a_gated_route_is_still_401(authenticated_async_context):
    async with _bearer("j360_junk") as junk:
        resp = await junk.post("/api/jobs/bring", json=JOB)
    assert resp.status_code == 401
