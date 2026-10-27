"""One canonical host, and LinkedIn denied at the egress proxy (browser routes PR 0; security C1, C2, M4).

IDNA maps look-alike characters onto plain ones: ``www.ⓛinkedin.com`` and ``www.linkedin。com`` connect to
``www.linkedin.com``. Every host test therefore compares ``guard.canonical_host``, which refuses any host that
needs mapping, and the egress proxy (Chromium's only way out) refuses every LinkedIn host and every host it
cannot name. Offline: a fake resolver, a fake ``open_connection``, a loopback site.
"""
from __future__ import annotations

import asyncio
import socket
from typing import Any
from urllib.parse import urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.routes import browser as browser_routes
from src.services.applications import own_browser, spine
from src.services.browser_agent import routing
from src.services.browser_agent.egress import BLOCK_HEADER, BLOCK_MARK, LINKEDIN_REFUSED, EgressProxy
from src.services.fetch.guard import canonical_host, is_linkedin_host

TRICKS = [
    "https://www.ⓛinkedin.com/jobs/view/1",  # ⓛ
    "https://www.linkedin。com/jobs/view/1",  # ideographic full stop
    "https://www.linked­in.com/",  # soft hyphen
    "https://www.lin​kedin.com/",  # zero-width space
    "https://lnkd.in/abc",
    "https://media.licdn.com/x.png",
    "https://LINKEDIN.COM.",
    "https://linkedin.com:443/feed",
]
NOT_LINKEDIN = ["https://notlinkedin.com", "https://linkedin.com.evil.io", "https://evil.io/?u=linkedin.com"]


# ── the pure host test ───────────────────────────────────────────────────────


@pytest.mark.parametrize("url", TRICKS[4:])
def test_a_real_linkedin_host_keeps_the_linkedin_reason(url):
    assert routing.names_linkedin(url) is True
    assert routing.browser_for(url=url) == routing.Route("own_browser", "linkedin_own_browser")


@pytest.mark.parametrize("url", TRICKS[:4])
def test_every_trick_fails_closed_to_the_own_browser_as_link_unclear(url):
    assert routing.is_linkedin(url) is True  # the safety test still fails closed
    assert routing.names_linkedin(url) is False  # but it is not called LinkedIn
    assert routing.browser_for(url=url) == routing.Route("own_browser", "link_unclear")


@pytest.mark.parametrize("url", NOT_LINKEDIN)
def test_a_lookalike_by_substring_is_not_linkedin(url):
    assert routing.is_linkedin(url) is False
    assert routing.browser_for(url=url) == routing.Route("job360", "default")


@pytest.mark.parametrize("url", TRICKS[:4])
def test_a_host_that_needs_idna_mapping_has_no_canonical_host(url):
    assert canonical_host(url) is None


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://LINKEDIN.COM.", "linkedin.com"),
        ("https://linkedin.com:443/feed", "linkedin.com"),
        ("https://Jobs.Acme.example/apply?x=1", "jobs.acme.example"),
        ("http://93.184.216.34/", "93.184.216.34"),
        ("https://user@jobs.acme.example/", None),
        ("ftp://jobs.acme.example/", None),
        ("", None),
    ],
)
def test_canonical_host(url, host):
    assert canonical_host(url) == host


def test_is_linkedin_host_is_exact_or_a_subdomain():
    assert all(is_linkedin_host(h) for h in ("linkedin.com", "uk.linkedin.com", "lnkd.in", "static.licdn.com"))
    assert not any(is_linkedin_host(h) for h in ("notlinkedin.com", "linkedin.com.evil.io", "xlnkd.in", ""))


def test_no_link_still_routes_to_job360s_browser():
    assert routing.is_linkedin("") is False
    assert routing.browser_for(url="") == routing.Route("job360", "default")


# ── the egress proxy: the real door ──────────────────────────────────────────


async def _through_proxy(request: bytes, resolve: Any, open_connection: Any) -> tuple[bytes, EgressProxy]:
    proxy = EgressProxy(resolve, open_connection)
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(request)
        await writer.drain()
        head = await asyncio.wait_for(reader.read(4096), 10)
        writer.close()
    finally:
        await proxy.close()
    return head, proxy


def _never(calls: list[str]) -> tuple[Any, Any]:
    async def resolve(host, port=0, family=socket.AF_UNSPEC):
        calls.append(f"resolve {host}")
        return [{"host": "93.184.216.34", "port": port, "family": socket.AF_INET}]

    async def open_connection(address, port):
        calls.append(f"open {address}")
        raise OSError("must not connect")

    return resolve, open_connection


def _refused(head: bytes) -> bool:
    return head.startswith(b"HTTP/1.1 403") and f"{BLOCK_HEADER}: {BLOCK_MARK}".encode() in head


@pytest.mark.asyncio
async def test_a_connect_to_linkedin_is_refused_before_any_lookup_or_connection():
    calls: list[str] = []
    resolve, open_connection = _never(calls)
    head, proxy = await _through_proxy(
        b"CONNECT www.linkedin.com:443 HTTP/1.1\r\nHost: www.linkedin.com:443\r\n\r\n", resolve, open_connection
    )
    assert _refused(head), head
    assert calls == []  # never resolved, never opened
    assert proxy.denied == [f"www.linkedin.com: {LINKEDIN_REFUSED}"]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", TRICKS)
async def test_every_trick_is_refused_at_the_egress(url):
    calls: list[str] = []
    resolve, open_connection = _never(calls)
    parts = urlsplit(url)
    target = parts.netloc if parts.port else f"{parts.netloc}:443"
    head, _ = await _through_proxy(f"CONNECT {target} HTTP/1.1\r\n\r\n".encode(), resolve, open_connection)
    assert _refused(head), head
    assert not [c for c in calls if c.startswith("open")]


@pytest.mark.asyncio
async def test_a_plain_http_request_to_linkedin_is_refused_too():
    calls: list[str] = []
    resolve, open_connection = _never(calls)
    head, _ = await _through_proxy(
        b"GET http://lnkd.in/abc HTTP/1.1\r\nHost: lnkd.in\r\n\r\n", resolve, open_connection
    )
    assert _refused(head), head
    assert calls == []


@pytest.mark.asyncio
async def test_a_host_that_only_looks_like_linkedin_still_goes_out():
    async def site(reader, writer):
        writer.close()

    server = await asyncio.start_server(site, "127.0.0.1", 0)
    site_port = server.sockets[0].getsockname()[1]
    opened: list[str] = []

    async def resolve(host, port=0, family=socket.AF_UNSPEC):
        return [{"host": "93.184.216.34", "port": port, "family": socket.AF_INET}]

    async def open_connection(address, port):
        opened.append(address)
        return await asyncio.open_connection("127.0.0.1", site_port)

    try:
        head, _ = await _through_proxy(b"CONNECT notlinkedin.com:443 HTTP/1.1\r\n\r\n", resolve, open_connection)
    finally:
        server.close()
    assert head.startswith(b"HTTP/1.1 200"), head
    assert opened == ["93.184.216.34"]


# ── the own-browser queue: a trick link is Easy Apply ────────────────────────


@pytest.mark.asyncio
async def test_a_trick_job_link_waits_in_the_own_browser_as_link_unclear_and_is_not_easy_apply(authenticated_async_context):
    from datetime import datetime, timezone

    from src.api.main import app

    user_id = authenticated_async_context.fixture_user_id
    trick = "https://www.ⓛinkedin.com/jobs/view/4001"
    async with authenticated_async_context() as client:
        ad = {
            "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB",
            "remote": False, "description": "Build the pipelines.",
        }
        resp = await client.post("/api/jobs/bring", json={**ad, "apply_url": trick})
        assert resp.status_code == 200, resp.text
        app_id = int(resp.json()["application_id"])
        async with browser_routes._db() as db:  # noqa: SLF001
            cur = await db._db.execute("SELECT job_url FROM applications WHERE id = ?", (app_id,))  # noqa: SLF001
            stored = str(dict(await cur.fetchone())["job_url"])
        assert routing.is_linkedin(stored), stored  # whatever bring kept, it still fails closed
        assert not routing.names_linkedin(stored), stored  # ...but it is not LinkedIn
        queued = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "queued"})
        assert queued.status_code == 201, queued.text
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
        ) as agent:
            got = await agent.post("/api/own-browser/take", json={"session": "desk"})
        assert got.status_code == 200, got.text
        task = got.json()["task"]
        assert got.json()["status"] == "task" and task["application_id"] == app_id
        assert task["browser"] == {
            "kind": "own_browser", "reason": "link_unclear",
            "label": "This link looks broken or disguised: your own browser checks it",
        }
        async with browser_routes._db() as db:  # noqa: SLF001
            await spine.append_event(
                db, user_id=user_id, application_id=app_id, event_type="submit_attempted", payload={},
                occurred_at=spine.parse_occurred_at(None), recorded_by="web",
            )
            await db._db.commit()  # noqa: SLF001
            assert await own_browser.easy_apply_today(db, user_id, datetime.now(timezone.utc)) == 0  # not LinkedIn: no cap


@pytest.mark.parametrize("url", ["https://not a link", "https://a b.com/x", "https://bad_host.com/", "https://-x.com/"])
def test_a_host_no_resolver_could_look_up_has_no_canonical_name(url):
    assert canonical_host(url) is None
    assert routing.is_linkedin(url) is True  # fails closed: the own browser, never Job360's


def test_plain_hosts_and_ip_literals_keep_their_canonical_name():
    assert canonical_host("https://jobs.acme-corp.co.uk/x") == "jobs.acme-corp.co.uk"
    assert canonical_host("https://8.8.8.8/x") == "8.8.8.8"
