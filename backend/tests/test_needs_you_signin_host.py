"""Needs you groups sign-in steps by site (diagnosis D, 2026-11-13).

Production: 62 queued jobs waited on a sign-in / create-account / code step across ~23 hosts (hirist.tech 11,
wellfound.com 7, reed.co.uk 5...), and Needs you showed one card per job. One sign-in on a site clears every
job on it, so those jobs are ONE item ``{host, word, app_ids}``, counted once, and the web-only
``POST /api/sites/{host}/signed-in`` records, for each of the user's jobs with an open sign-in-family block on
that host, ``unblocked`` {user_did_it, "signed in on <host>"} and ``site_account`` {host}; their asks close.
A CAPTCHA or "finish on the site" stays per job. Real Postgres, real HTTP doors.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.repositories import pgsync


def _sql(statement: str, params: tuple = ()) -> list[Any]:
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        cur = conn.execute(statement, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
    return rows


async def _bring(client: AsyncClient, n: int, url: str = "") -> int:
    ad = {
        "title": f"Engineer {n}", "company": f"Company{n}", "location": "Pune", "country": "IN", "remote": False,
        "apply_url": url or f"https://co{n}.example/careers/{n}", "description": "Build things.",
    }
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _block(client: AsyncClient, app_id: int, reason: str, host: str = "") -> None:
    payload = {"reason": reason, "step": "open_form", **({"page_host": host} if host else {})}
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "blocked", "payload": payload})
    assert resp.status_code == 201, resp.text


async def _count(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/needs-you-count")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _open_asks(app_id: int) -> int:
    rows = _sql(
        "SELECT COUNT(*) FROM application_asks WHERE application_id = ? AND answered_at IS NULL "
        "AND withdrawn_at IS NULL", (app_id,),
    )
    return int(rows[0][0])


def _events(app_id: int, event_type: str) -> list[dict[str, Any]]:
    rows = _sql(
        "SELECT payload, detail FROM application_events WHERE application_id = ? AND event_type = ? ORDER BY id",
        (app_id, event_type),
    )
    return [{**(json.loads(r[0]) if isinstance(r[0], str) else dict(r[0] or {})), "_detail": r[1]} for r in rows]


async def _hirist(client: AsyncClient) -> tuple[list[int], int]:
    """Four jobs waiting on a sign-in-family step on hirist.tech (two spellings, three reasons) + one CAPTCHA."""
    jobs = [await _bring(client, n, f"https://www.hirist.tech/j/{n}") for n in range(1, 6)]
    await _block(client, jobs[0], "login_needed", "hirist.tech")
    await _block(client, jobs[1], "login_needed", "www.hirist.tech")
    await _block(client, jobs[2], "account_creation", "hirist.tech")
    await _block(client, jobs[3], "otp_needed", "www.hirist.tech")
    await _block(client, jobs[4], "captcha", "hirist.tech")
    return jobs[:4], jobs[4]


@pytest.mark.asyncio
async def test_four_hirist_jobs_are_one_item(authenticated_async_context):
    async with authenticated_async_context() as client:
        signin, captcha = await _hirist(client)
        body = await _count(client)
        assert body["site_signins"] == [{"host": "hirist.tech", "word": "sign in", "app_ids": sorted(signin)}]
        assert body["job_ids"] == [captcha], "a CAPTCHA stays one card per job"
        assert body["job_ids_by_kind"] == {"site_step": [captcha]}
        assert body["n"] == 2, "four sign-ins on one site count ONCE, plus the CAPTCHA job"
        assert body["other_ask_ids"] == [], "the grouped jobs' sign-in asks are not pushed into Other"
        assert body["jobs"] == body["by_actor"]["you"] == 5 and body["by_kind"] == {"site_step": 5}


@pytest.mark.asyncio
async def test_www_is_folded_and_the_job_link_names_a_block_with_no_page_host(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _bring(client, 1, "https://wellfound.com/jobs/1")
        b = await _bring(client, 2, "https://www.wellfound.com/jobs/2")
        await _block(client, a, "login_needed", "www.wellfound.com")
        await _block(client, b, "login_needed")  # no page_host: the job's own link names the site
        body = await _count(client)
        assert body["site_signins"] == [{"host": "wellfound.com", "word": "sign in", "app_ids": sorted([a, b])}]
        assert body["job_ids"] == [] and body["n"] == 1


@pytest.mark.asyncio
async def test_signed_in_unblocks_every_job_on_the_host_and_closes_their_asks(authenticated_async_context):
    async with authenticated_async_context() as client:
        signin, captcha = await _hirist(client)
        elsewhere = await _bring(client, 9, "https://www.reed.co.uk/jobs/9")
        await _block(client, elsewhere, "login_needed", "reed.co.uk")
        assert all(_open_asks(i) == 1 for i in [*signin, captcha, elsewhere])

        resp = await client.post("/api/sites/www.hirist.tech/signed-in")
        assert resp.status_code == 201, resp.text
        assert resp.json() == {"host": "hirist.tech", "unblocked": sorted(signin)}
        for app_id in signin:
            done = _events(app_id, "unblocked")
            assert [(e["resolution"], e["note"], e["assistant"]) for e in done] == [
                ("user_did_it", "signed in on hirist.tech", "web")
            ]
            assert [(e["host"], e["by"]) for e in _events(app_id, "site_account")] == [("hirist.tech", "web")]
            assert _open_asks(app_id) == 0, "the sign-in ask closed with its block"
        assert _events(captcha, "unblocked") == [] and _open_asks(captcha) == 1, "a CAPTCHA is never cleared here"
        assert _events(elsewhere, "unblocked") == [] and _open_asks(elsewhere) == 1, "another site stays"
        body = await _count(client)
        assert body["site_signins"] == [{"host": "reed.co.uk", "word": "sign in", "app_ids": [elsewhere]}]
        assert body["job_ids"] == [captcha]
        again = await client.post("/api/sites/hirist.tech/signed-in")
        assert again.status_code == 409, "nothing on hirist.tech waits on a sign-in any more"


@pytest.mark.asyncio
async def test_another_users_jobs_are_untouched(authenticated_async_context):
    from fastapi.testclient import TestClient

    from src.api.main import app

    async with authenticated_async_context() as client:
        mine = await _bring(client, 1, "https://hirist.tech/j/1")
        await _block(client, mine, "login_needed", "hirist.tech")
        sync_client = TestClient(app)
        email = "signin-stranger@example.com"
        assert sync_client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"}).status_code == 201
        _sql("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
        lr = sync_client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
        cookies = {"job360_session": str(lr.cookies.get("job360_session"))}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies) as other:
            theirs = await _bring(other, 2, "https://hirist.tech/j/2")
            await _block(other, theirs, "login_needed", "hirist.tech")
            resp = await client.post("/api/sites/hirist.tech/signed-in")
            assert resp.status_code == 201 and resp.json()["unblocked"] == [mine]
            assert _events(theirs, "unblocked") == [] and _events(theirs, "site_account") == []
            assert _open_asks(theirs) == 1
            assert (await _count(other))["site_signins"] == [
                {"host": "hirist.tech", "word": "sign in", "app_ids": [theirs]}
            ]


@pytest.mark.asyncio
async def test_a_host_that_is_not_a_host_is_refused(authenticated_async_context):
    async with authenticated_async_context() as client:
        assert (await client.post("/api/sites/not a host/signed-in")).status_code == 422
        assert (await client.post("/api/sites/localhost/signed-in")).status_code == 422


def test_signed_in_route_is_session_only_with_auth_first():
    from src.api.auth_deps import require_session_user, require_user
    from src.api.routes.asks import router

    route = next(r for r in router.routes if getattr(r, "path", "") == "/sites/{host}/signed-in")
    assert [d.dependency for d in route.dependencies] == [require_user]
    assert any(d.call is require_session_user for d in route.dependant.dependencies)
