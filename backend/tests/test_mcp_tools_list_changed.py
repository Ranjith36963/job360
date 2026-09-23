"""A deploy must not need a manual reconnect (2026-09-20).

The bug: `save_fit` grew an optional `axes` parameter, Claude.ai kept answering
from the tool list it had cached at connect time, and only a hand-driven
disconnect/reconnect fixed it.

Both protocol eras are exercised on purpose, because they are different
machines and only one of them is the one real connectors use today:

* **``mode="legacy"``** — the ``initialize`` handshake (2024-11-05 …
  2025-11-25). This is the wire a connector like Claude.ai speaks. Here the SDK
  derives ``tools.listChanged`` from a ``NotificationOptions`` the
  streamable-HTTP path never lets us pass, so it is ``false`` unless we stamp
  it; that "the list never changes" promise is the likeliest reason a client
  caches forever.
* **``mode="2026-07-28"``** — the per-request envelope era, which the official
  SDK client picks by default. There is no ``initialize`` at all and the SDK
  already derives ``listChanged`` from ``subscriptions/listen`` being served.

What the tests pin, end to end over real streamable-HTTP JSON-RPC with the
official ``mcp`` client:

* the handshake result declares ``tools.listChanged``;
* a connected client actually *receives* ``notifications/tools/list_changed``
  on its first ``tools/call``, on both wires, and receives it only once;
* the ``tools/list`` it then fetches really does carry ``save_fit`` with
  ``axes``, and that ``axes`` is accepted (value presence, hard rule #21 — not
  just "the key is in the schema");
* ``serverInfo.version`` is the tool-surface fingerprint, so a deploy that
  changes a tool changes it;
* the ``MCP_ANNOUNCE_TOOLS_CHANGED`` kill switch restores the old silent
  JSON-only transport exactly.

**What they do NOT prove.** That Claude.ai — or any particular client — acts on
the notification. No MCP revision makes the client's re-fetch mandatory: the
2025-06-18 and 2025-11-25 specs put the only normative sentence on the server
("servers that declared the `listChanged` capability SHOULD send a
notification"), and the client re-fetching appears only in a non-normative
sequence diagram. These tests prove the server does everything the protocol
gives it to do. Whether a given client looks is a production observation.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp")

# The two wires, named the way `Client(mode=...)` names them.
HANDSHAKE, MODERN = "legacy", "2026-07-28"

JOB = {
    "title": "Senior Python Engineer",
    "company": "Acme Ltd",
    "location": "London",
    "description": "Build FastAPI services with Postgres. " * 8,
    "apply_url": "https://acme.example/jobs/1",
}

TOOLS_CHANGED = "notifications/tools/list_changed"


async def _mint_token(authenticated_async_context, name: str = "agent") -> str:
    async with authenticated_async_context() as client:
        resp = await client.post("/api/tokens", json={"name": name})
        assert resp.status_code == 201, resp.text
        return resp.json()["token"]


class _Notifications:
    """Every server-to-client message the client session surfaces, by method.

    ``message_handler`` is the SDK client's raw inbound hook — the only way to
    observe a notification that rode a request's own SSE stream.
    """

    def __init__(self) -> None:
        self.methods: list[str] = []

    async def __call__(self, message) -> None:
        root = getattr(message, "root", message)
        method = getattr(root, "method", None)
        if method is not None:
            self.methods.append(str(method))

    def count(self, method: str) -> int:
        return self.methods.count(method)


def _mcp_client(token: str, *, mode: str = "auto", on_message: _Notifications | None = None):
    """The official MCP client wired straight into the FastAPI app (in-process)."""
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return Client(
        streamable_http_client("http://test/api/mcp", http_client=http),
        mode=mode,
        message_handler=on_message,
    )


@pytest.mark.asyncio
async def test_the_handshake_wire_declares_tools_list_changed(authenticated_async_context, monkeypatch):
    """The capability the spec demands before the notification is even legal.

    Measured on the SDK: without the stamp this is ``False`` at every handshake
    revision (2024-11-05 → 2025-11-25) — a client told ``false`` may cache the
    tool list forever, which is the bug this whole file exists for.
    """
    from src.core import settings

    monkeypatch.setattr(settings, "MCP_ANNOUNCE_TOOLS_CHANGED", True)
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context)
    async with mcp_runtime():
        async with _mcp_client(token, mode=HANDSHAKE) as mcp:
            assert mcp.session.protocol_version in {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
            tools_capability = mcp.server_capabilities.tools
            assert tools_capability is not None, "the server serves tools/list"
            assert tools_capability.list_changed is True


@pytest.mark.asyncio
async def test_server_info_version_is_the_tool_surface_fingerprint(authenticated_async_context):
    """``serverInfo.version`` moves when the tool surface moves.

    Prod has no other honest version signal (``/api/health`` returns a
    hardcoded ``"1.0.0"``), so this is how a deploy that changed the tools an
    agent can see becomes observable from outside with one call.
    """
    from src.api.mcp_server import build_server, mcp_runtime, tools_fingerprint

    token = await _mint_token(authenticated_async_context)
    expected = tools_fingerprint(await build_server().list_tools())
    assert expected, "an empty fingerprint would say nothing"

    async with mcp_runtime():
        async with _mcp_client(token, mode=HANDSHAKE) as mcp:
            assert mcp.server_info is not None
            assert mcp.server_info.version == expected

    # It is a function of names + input schemas: change one schema and it must
    # move, or it could never signal a deploy.
    changed = [t.model_copy(deep=True) for t in await build_server().list_tools()]
    save_fit = next(t for t in changed if t.name == "save_fit")
    save_fit.input_schema = {
        **save_fit.input_schema,
        "properties": {k: v for k, v in save_fit.input_schema["properties"].items() if k != "axes"},
    }
    assert tools_fingerprint(changed) != expected

    # A client also sees `description` and `output_schema` from `tools/list`
    # (CodeRabbit, PR #604): a wording-only or output-schema-only deploy must
    # move the fingerprint too, or `serverInfo.version` would lie about it.
    desc_changed = [t.model_copy(deep=True) for t in await build_server().list_tools()]
    bring = next(t for t in desc_changed if t.name == "bring_job")
    bring.description = (bring.description or "") + " (reworded)"
    assert tools_fingerprint(desc_changed) != expected, "a description-only change must move the fingerprint"

    schema_changed = [t.model_copy(deep=True) for t in await build_server().list_tools()]
    bring2 = next(t for t in schema_changed if t.name == "bring_job")
    bring2.output_schema = {**(bring2.output_schema or {}), "title": "changed"}
    assert tools_fingerprint(schema_changed) != expected, "an output-schema-only change must move the fingerprint"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [HANDSHAKE, MODERN])
async def test_first_tool_call_tells_the_client_the_tool_list_changed(authenticated_async_context, mode, monkeypatch):
    """The end-to-end claim: a connected client is told, without reconnecting."""
    from src.core import settings

    monkeypatch.setattr(settings, "MCP_ANNOUNCE_TOOLS_CHANGED", True)
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context)
    seen = _Notifications()
    async with mcp_runtime():
        async with _mcp_client(token, mode=mode, on_message=seen) as mcp:
            # The first request of the connection carries it — for most clients
            # that is `tools/list`, so they heal BEFORE the first tool call
            # rather than after one wrong answer.
            listed = await mcp.list_tools()
            assert seen.count(TOOLS_CHANGED) == 1, (
                "the first request must carry the announcement on its own stream"
            )

            # And the list the client (re-)fetches is the NEW one.
            save_fit = next(t for t in listed.tools if t.name == "save_fit")
            properties = save_fit.input_schema["properties"]
            assert "axes" in properties, sorted(properties)
            assert "axes" not in save_fit.input_schema.get("required", [])

            # Told once, not once per request — the set is per user per process.
            brought = await mcp.call_tool("bring_job", JOB)
            assert not brought.is_error, brought.content[0].text
            again = await mcp.call_tool("bring_job", JOB)
            assert not again.is_error, again.content[0].text
            assert seen.count(TOOLS_CHANGED) == 1

            # Value presence, not schema presence (hard rule #21): the
            # re-fetched schema is usable — `axes` really is accepted.
            application_id = json.loads(brought.content[0].text)["application_id"]
            saved = await mcp.call_tool(
                "save_fit",
                {
                    "application_id": application_id,
                    "verdict": "strong",
                    "reasoning": "Python + FastAPI + Postgres all match.",
                    "axes": [
                        {"name": "Python depth", "role": 90, "you": 85},
                        {"name": "Postgres", "role": 70, "you": 80},
                        {"name": "London", "role": 60, "you": 100},
                    ],
                },
            )
            assert not saved.is_error, saved.content[0].text


@pytest.mark.asyncio
async def test_told_once_per_user_not_once_per_connection(authenticated_async_context, monkeypatch):
    """Reconnecting does not re-announce: the key is the user, for this process.

    A client that drops and re-dials (or a second client on the same account)
    must not be told again — the tool list did not move between those two
    connections. Only a restart, which is what a deploy is, empties the set.
    """
    from src.core import settings

    monkeypatch.setattr(settings, "MCP_ANNOUNCE_TOOLS_CHANGED", True)
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context)
    other_token = await _mint_token(authenticated_async_context, "agent-two")
    first_dial, second_dial = _Notifications(), _Notifications()
    async with mcp_runtime():
        async with _mcp_client(token, mode=HANDSHAKE, on_message=first_dial) as first:
            assert not (await first.call_tool("bring_job", JOB)).is_error
        # Different token, same user, fresh connection: already told, stays quiet.
        async with _mcp_client(other_token, mode=HANDSHAKE, on_message=second_dial) as second:
            assert not (await second.call_tool("bring_job", JOB)).is_error
    assert first_dial.count(TOOLS_CHANGED) == 1
    assert second_dial.count(TOOLS_CHANGED) == 0


@pytest.mark.asyncio
async def test_the_told_once_bookkeeping_is_keyed_by_user_not_by_process():
    """A second user must still be told — dedup is per user, not a process-wide flag.

    Driven directly against the middleware because the DB fixture only ever
    makes one user, and "told once" would pass just as happily with a single
    boolean that silences everybody after the first announcement.
    """
    from src.api.auth_deps import CurrentUser
    from src.api.mcp_server import _AnnounceToolListChanged, _current_user

    sent: list[tuple[str, object]] = []

    class _Session:
        async def send_notification(self, notification, related_request_id=None):
            sent.append((notification.method, related_request_id))

    class _Ctx:
        method = "tools/list"
        request_id = 7
        session = _Session()

    async def _call_next(ctx):
        return {}

    announcer = _AnnounceToolListChanged()
    for user_id in ("user-a", "user-a", "user-b"):
        token = _current_user.set(CurrentUser(id=user_id, email=f"{user_id}@example.com"))
        try:
            await announcer(_Ctx(), _call_next)
        finally:
            _current_user.reset(token)

    assert [method for method, _ in sent] == [TOOLS_CHANGED, TOOLS_CHANGED], "one per user, not per call"
    assert all(request_id == 7 for _, request_id in sent), (
        "must ride this request's own stream — a stateless mount has no standalone channel"
    )


@pytest.mark.asyncio
async def test_a_json_only_client_is_served_json_not_a_406(authenticated_async_context, monkeypatch):
    """Announcing must not break a client that cannot read SSE.

    The announcement rides an SSE response stream, and the SDK 406s an
    SSE-mode request whose ``Accept`` did not include ``text/event-stream``.
    So the endpoint keeps a JSON-only leg and picks by ``Accept`` — which is
    what ``Accept`` is for. Conforming clients (the spec says a client MUST
    accept both) land on the SSE leg and get told; a JSON-only client keeps
    working exactly as it did before, silently.
    """
    from httpx import ASGITransport, AsyncClient

    from src.api.main import app
    from src.core import settings

    monkeypatch.setattr(settings, "MCP_ANNOUNCE_TOOLS_CHANGED", True)
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context)
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "json-only", "version": "1"},
        },
    }
    async with mcp_runtime():
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            json_only = await client.post("/api/mcp", json=body, headers={"Accept": "application/json"})
            assert json_only.status_code == 200, json_only.text
            assert json_only.headers["content-type"].startswith("application/json")
            # The JSON leg has no back-channel, so it must not promise one.
            caps = json_only.json()["result"]["capabilities"]
            assert caps["tools"].get("listChanged") is not True

            # Anything the SDK would serve SSE to must reach the SSE leg —
            # including the wildcards a plain httpx/requests client sends.
            # A substring test for "text/event-stream" would fail every case
            # below but the first.
            for accept in ("application/json, text/event-stream", "*/*", "application/*, text/*"):
                sse = await client.post("/api/mcp", json=body, headers={"Accept": accept})
                assert sse.status_code == 200, f"{accept}: {sse.text}"
                assert sse.headers["content-type"].startswith("text/event-stream"), accept
                assert '"listChanged":true' in sse.text.replace(" ", ""), accept

            # A client that accepts ONLY SSE is a 406 on either leg — the SDK
            # has no SSE-without-JSON mode. Pinned so it stays a documented
            # 406 and never becomes a hang or a 500: it is exactly what this
            # endpoint did before the second leg existed.
            sse_only = await client.post("/api/mcp", json=body, headers={"Accept": "text/event-stream"})
            assert sse_only.status_code == 406, sse_only.text


@pytest.mark.asyncio
async def test_the_kill_switch_restores_the_silent_json_transport(authenticated_async_context, monkeypatch):
    """``MCP_ANNOUNCE_TOOLS_CHANGED=0`` = exactly the pre-2026-09-20 behaviour.

    Asserted on the handshake wire, the one a connector speaks: JSON-only
    responses, no back-channel, and therefore no ``listChanged`` promise — do
    not advertise what the transport cannot deliver.
    """
    from src.core import settings

    monkeypatch.setattr(settings, "MCP_ANNOUNCE_TOOLS_CHANGED", False)
    from src.api.mcp_server import mcp_runtime

    token = await _mint_token(authenticated_async_context)
    seen = _Notifications()
    async with mcp_runtime():
        async with _mcp_client(token, mode=HANDSHAKE, on_message=seen) as mcp:
            tools_capability = mcp.server_capabilities.tools
            assert tools_capability is not None
            assert tools_capability.list_changed is False
            brought = await mcp.call_tool("bring_job", JOB)
            assert not brought.is_error, brought.content[0].text
            assert seen.count(TOOLS_CHANGED) == 0
