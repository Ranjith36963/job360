"""Owner 2026-11-03: every fill, sweep, submit, block and receipt can say WHICH browser did it, so per-site
success is countable per browser. Real route doors; stored VALUES asserted (rule #21)."""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.core import settings
from src.services.applications import which_browser


async def _bring(client: AsyncClient, n: int) -> int:
    ad = {
        "title": f"Analyst {n}", "company": f"WbCo{n}", "location": "Remote",
        "apply_url": f"https://wb{n}.example/careers/{n}", "description": "SQL, Python, dashboards. Remote.",
    }
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> Any:
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})


async def _payloads(client: AsyncClient, app_id: int) -> dict[str, dict[str, Any]]:
    """The newest payload of each event type on the application."""
    events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
    out: dict[str, dict[str, Any]] = {}
    for ev in sorted(events, key=lambda e: e["id"]):
        out[ev["event_type"]] = ev.get("payload") or {}
    return out


def _fill(host: str, browser: str | None = None) -> dict[str, Any]:
    p: dict[str, Any] = {"form_url": f"https://{host}/apply", "fields_count": 1}
    return {**p, "browser": browser} if browser else p


def test_read_is_unknown_without_the_key_and_job360_for_rows_its_browser_wrote():
    assert which_browser.read({}) == "unknown" and which_browser.read(None) == "unknown"
    assert which_browser.read({"browser": "manual"}) == "manual"
    assert which_browser.read({"browser": "chrome"}) == "unknown"
    for by in ("browser", "refill", "restore"):  # written before the key existed: Job360's own browser
        assert which_browser.read({"by": by}) == "job360"
    assert which_browser.read({"by": "claude-code"}) == "unknown"


@pytest.mark.asyncio
async def test_every_form_event_and_blocked_store_the_browser(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        host = "jobs.wb1.example"
        sent = [
            ("form_checked", {"questions_count": 1, "answered": 1, "missing": 0, "browser": "own_browser"}),
            ("form_filled", _fill(host, "own_browser")),
            ("form_swept", {"fields_checked": 1, "fixed": 0, "browser": "own_browser"}),
            ("submit_attempted", {"browser": "own_browser"}),
        ]
        for event_type, payload in sent:
            resp = await _event(client, app_id, event_type, payload)
            assert resp.status_code == 201, (event_type, resp.text)
        other = await _bring(client, 2)
        resp = await _event(client, other, "blocked", {"reason": "captcha", "page_host": host, "browser": "manual"})
        assert resp.status_code == 201, resp.text
        stored = await _payloads(client, app_id)
        for event_type, _ in sent:
            assert stored[event_type]["browser"] == "own_browser", event_type
        assert stored["form_filled"]["host"] == host  # the event's own validator still ran
        assert (await _payloads(client, other))["blocked"]["browser"] == "manual"


@pytest.mark.asyncio
async def test_a_browser_outside_the_closed_set_is_refused(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 3)
        resp = await _event(client, app_id, "form_filled", _fill("jobs.wb3.example", "chrome"))
        assert resp.status_code == 422
        assert all(b in resp.text for b in which_browser.BROWSERS)
        resp = await _event(client, app_id, "form_filled", _fill("jobs.wb3.example"))
        assert resp.status_code == 201
        assert "browser" not in (await _payloads(client, app_id))["form_filled"]  # not said: reads unknown


class _Pool:
    def __init__(self, keys: list[str]) -> None:
        self.keys = keys

    def user_keys(self, user_id: str) -> list[str]:
        return [k for k in self.keys if k.split("#job:")[0] == user_id]


@pytest.mark.asyncio
async def test_job360_is_checked_against_the_open_browser(authenticated_async_context, monkeypatch, fixture_user_id):
    from src.services.browser_agent import agent

    async with authenticated_async_context() as client:
        app_id = await _bring(client, 4)
        monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", False)
        resp = await _event(client, app_id, "form_filled", _fill("jobs.wb4.example", "job360"))
        assert resp.status_code == 422 and "no page open" in resp.text

        monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
        monkeypatch.setattr(agent, "get_pool", lambda: _Pool([agent.browser_key(fixture_user_id, app_id)]))
        assert (await _event(client, app_id, "form_filled", _fill("jobs.wb4.example", "job360"))).status_code == 201
        assert (await _payloads(client, app_id))["form_filled"]["browser"] == "job360"
        # Not said while this job's own Job360 browser is open: the server knows it was Job360's.
        assert (await _event(client, app_id, "form_filled", _fill("jobs.wb4.example"))).status_code == 201
        assert (await _payloads(client, app_id))["form_filled"]["browser"] == "job360"
        # The caller may still name another browser (the own browser took over after a block).
        assert (await _event(client, app_id, "form_filled", _fill("jobs.wb4.example", "manual"))).status_code == 201
        assert (await _payloads(client, app_id))["form_filled"]["browser"] == "manual"


@pytest.mark.asyncio
async def test_receipt_takes_the_submit_clicks_browser_and_stats_split_by_browser(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _bring(client, 5)
        host = "jobs.wb5.example"
        assert (await _event(client, a, "form_filled", _fill(host, "own_browser"))).status_code == 201
        assert (await _event(client, a, "submit_attempted", {"browser": "own_browser"})).status_code == 201
        resp = await client.post(f"/api/applications/{a}/receipt", json={"channel": "company_site"})
        assert resp.status_code == 201, resp.text
        assert resp.json()["browser"] == "own_browser"
        assert (await _payloads(client, a))["applied"]["browser"] == "own_browser"

        b = await _bring(client, 6)
        assert (await _event(client, b, "form_filled", _fill(host, "manual"))).status_code == 201
        resp = await client.post(f"/api/applications/{b}/receipt", json={"channel": "company_site", "browser": "manual"})
        assert resp.status_code == 201 and resp.json()["browser"] == "manual"
        c = await _bring(client, 7)
        assert (await _event(client, c, "form_filled", _fill(host))).status_code == 201
        blocked = {"reason": "captcha", "page_host": host, "browser": "own_browser"}
        assert (await _event(client, c, "blocked", blocked)).status_code == 201

        bad = await client.post(f"/api/applications/{c}/receipt", json={"browser": "chrome"})
        assert bad.status_code == 422

        body = (await client.get("/api/applications/stats", params={"view": "sites"})).json()
        site = {s["host"]: s for s in body["sites"]["sites"]}[host]
        assert site["by_browser"] == {
            "manual": {"filled": 1, "blocked": 0, "submitted": 1},
            "own_browser": {"filled": 1, "blocked": 1, "submitted": 1},
            "unknown": {"filled": 1, "blocked": 0, "submitted": 0},
        }


def test_server_says_wins_over_the_pool_and_the_caller(monkeypatch):
    """Job360's browser recording its own submit click: stored job360 even with no pool to look in."""
    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", False)
    with pytest.raises(Exception, match="no page open"):
        which_browser.resolve("u1", 1, "job360")
    said = which_browser.SERVER_SAYS.set(which_browser.JOB360)
    try:
        assert which_browser.resolve("u1", 1, None) == "job360"
        assert which_browser.resolve("u1", 1, "manual") == "job360"
    finally:
        which_browser.SERVER_SAYS.reset(said)
    assert which_browser.resolve("u1", 1, None) is None
