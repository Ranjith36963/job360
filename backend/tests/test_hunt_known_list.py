"""The hunt's known list comes from Job360, not from hand copies (2026-11-17 hunt run).

The run's helpers paged list_applications and copied 966 rows into known.txt by hand; nothing proved the copy
whole. ``whats_due`` now gives an assistant one short-lived signed link per form; the link answers the user's
known jobs as plain text with the line counts to check against. Real routes on the real Postgres.
"""
from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings

_ADS = [
    {"title": "Data Engineer", "company": "Northwind | Labs", "location": "London",
     "apply_url": "https://careers.northwind.example/jobs/1", "description": "Build the pipelines. " * 5},
    {"title": "ML Engineer", "company": "Contoso", "location": "Remote",
     "apply_url": "https://jobs.contoso.example/ml/2", "description": "Train the models. " * 5},
    {"title": "Analytics Engineer", "company": "Fabrikam", "location": "Paris",
     "apply_url": "", "description": "Model the data. " * 5},
]


def _app():
    from src.api.main import app

    return app


async def _agent(client: AsyncClient) -> AsyncClient:
    token = (await client.post("/api/tokens", json={"name": "scheduler"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


def _anon() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test")


def _path(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


async def _bring_all(client: AsyncClient) -> list[int]:
    ids = []
    for ad in _ADS:
        resp = await client.post("/api/jobs/bring", json=ad)
        assert resp.status_code == 200, resp.text
        ids.append(resp.json()["application_id"])
    test_job = {**_ADS[0], "title": "Practice Job", "apply_url": "https://practice.example/1", "test": True}
    assert (await client.post("/api/jobs/bring", json=test_job)).status_code == 200
    return ids


@pytest.mark.asyncio
async def test_whats_due_gives_an_agent_the_known_list_and_the_files_match_it(authenticated_async_context, caplog):
    caplog.set_level(logging.INFO)
    async with authenticated_async_context() as client:
        ids = await _bring_all(client)
        assert (await client.get("/api/schedule/due")).json().get("known_list") is None, "the website gets no link"
        async with await _agent(client) as agent:
            known = (await agent.get("/api/schedule/due", params={"duties": "hunt"})).json()["known_list"]
            assert (await agent.get("/api/schedule/due", params={"duties": "inbox"})).json().get("known_list") is None
    assert known["total"] == 3 and known["urls"] == 2, known
    assert urlsplit(known["urls_url"]).hostname == urlsplit(settings.SITE_BASE_URL).hostname
    async with _anon() as anon:
        urls = await anon.get(_path(known["urls_url"]))
        ct = await anon.get(_path(known["ct_url"]))
    assert urls.status_code == 200 and urls.headers["content-type"].startswith("text/plain")
    assert "no-store" in urls.headers["cache-control"]
    assert urls.text.splitlines() == ["https://careers.northwind.example/jobs/1", "https://jobs.contoso.example/ml/2"]
    assert ct.text.splitlines() == [
        f"Northwind | Labs ||| Data Engineer ||| {ids[0]}", f"Contoso ||| ML Engineer ||| {ids[1]}",
        f"Fabrikam ||| Analytics Engineer ||| {ids[2]}",
    ]
    served = [r for r in caplog.records if r.getMessage() == "known_list_served"]
    assert sorted(r.form for r in served) == ["ct", "urls"] and {r.lines for r in served} == {2, 3}
    token = urlsplit(known["urls_url"]).path.rsplit("/", 1)[-1]
    server = [r for r in caplog.records if r.name != "httpx"]  # httpx is this test's own client
    leaks = [(r.name, r.getMessage()) for r in server if token in r.getMessage() or token in str(r.__dict__)]
    assert leaks == [], "the token is never logged by the server"


@pytest.mark.asyncio
async def test_a_known_list_link_reads_only_its_own_user(authenticated_async_context):
    from tests._signin import async_sign_in

    async with authenticated_async_context() as client:
        await _bring_all(client)
        async with await _agent(client) as agent:
            mine = (await agent.get("/api/schedule/due")).json()["known_list"]
    async with _anon() as other:
        await async_sign_in(other, "known-list-b@example.com")
        assert (await other.post("/api/jobs/bring", json={**_ADS[1], "company": "Only B",
                                                          "apply_url": "https://b.example/9"})).status_code == 200
        async with await _agent(other) as agent_b:
            theirs = (await agent_b.get("/api/schedule/due")).json()["known_list"]
    async with _anon() as anon:
        assert (await anon.get(_path(theirs["urls_url"]))).text.splitlines() == ["https://b.example/9"]
        assert "https://b.example/9" not in (await anon.get(_path(mine["urls_url"]))).text
        # Another user's id under this user's signature, and a session-cookie signature: refused, never resolved.
        mine_token = urlsplit(mine["urls_url"]).path.rsplit("/", 1)[-1]
        their_token = urlsplit(theirs["urls_url"]).path.rsplit("/", 1)[-1]
        forged = their_token.split(".", 1)[0] + "." + mine_token.split(".", 1)[1]
        assert (await anon.get(f"/api/known-list/{forged}")).status_code == 404
        from src.services.auth.sessions import _signer as session_signer

        cookie_like = session_signer("test-secret-" + "z" * 40).sign(b"abc").decode()
        assert (await anon.get(f"/api/known-list/{cookie_like}")).status_code == 404
        assert (await anon.get("/api/known-list/not-a-token")).status_code == 404


@pytest.mark.asyncio
async def test_an_expired_known_list_link_is_gone(authenticated_async_context, fixture_user_id, caplog):
    from src.services.applications import known_list

    caplog.set_level(logging.INFO)
    async with authenticated_async_context():
        signer = known_list._signer()
        signer.get_timestamp = lambda: int(time.time()) - (settings.KNOWN_LIST_TTL_MINUTES * 60 + 5)
        old = signer.sign(fixture_user_id.encode()).decode()
        async with _anon() as anon:
            resp = await anon.get(f"/api/known-list/{old}")
    assert resp.status_code == 410 and "whats_due" in resp.json()["detail"]
    refused = [r for r in caplog.records if r.getMessage() == "known_list_refused"]
    assert [r.reason for r in refused] == ["expired"]
