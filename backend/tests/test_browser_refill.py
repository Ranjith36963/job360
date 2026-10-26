"""Refill the form (owner decision 2026-10-26) and the missing "Finish it here" (application 138).

* Job360's browser records each form step the assistant carried out (a stable locator + the value):
  never a submit click, never a password or one-time-code box.
* ``POST /api/applications/{id}/browser/refill`` (website only) replays that script in Job360's own
  browser, in order, from Postgres (so a restart loses nothing), never clicks a submit, then holds the
  page: the open CAPTCHA ask offers Finish it here again.
* Identical answers keep the user's yes; a failed step makes it stale.
* Root cause of 138: an ask_user CAPTCHA ask superseded the ask the browser's block opened, and
  finish_here only matched the block's own (now closed) ask id.
"""
from __future__ import annotations

import contextlib
import json
from datetime import datetime, timezone
from typing import Any

import pytest
from aiohttp import web

from tests._signin import async_sign_in
from tests.test_browser_agent import _ref, _site_resolver, _to_site, needs_chromium

APPLY = """<!doctype html><html><head><title>Apply - Acme</title></head><body>
<form onsubmit="event.preventDefault(); document.title = 'SUBMITTED'">
<label for="fn">First name</label><input id="fn" required>
<label for="otp">Verification code</label><input id="otp">
<label for="c">Country</label><select id="c"><option value="">Choose</option><option>United Kingdom</option>
  <option>United States</option></select>
<fieldset><legend>Need a visa?</legend><label><input type="radio" name="v" value="y">Yes</label>
  <label><input type="radio" name="v" value="n">No</label></fieldset>
<label><input type="checkbox" id="t"> I agree to the terms</label>
<label for="cv">Resume</label><input id="cv" type="file">
<button type="submit">Submit application</button>
</form></body></html>"""
URL = "http://jobs.example/apply"


@pytest.fixture
async def apply_site():
    async def page(_):
        return web.Response(text=APPLY, content_type="text/html")

    app = web.Application()
    app.router.add_get("/apply", page)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    yield tcp._server.sockets[0].getsockname()[1]  # noqa: SLF001
    await runner.cleanup()


async def _files(kind: str) -> tuple[str, bytes]:
    return "CV.pdf", b"%PDF-1.4\n%fake\n"


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_steps_are_recorded_without_secrets_or_submit_and_replay_after_a_restart(apply_site):
    from src.services.browser_agent.agent import STEPS_KEY, BrowserPool, Gate, run
    from src.services.browser_agent.refill import replay

    async def check(_url: str) -> dict[str, Any]:
        return {"decision": "ask", "reason": "submit_mode_confirm"}

    async def mark() -> None:
        raise AssertionError("never reached: the gate said ask")

    gate = Gate(check=check, mark_attempt=mark, application_id=7)

    def pool() -> BrowserPool:
        return BrowserPool(max_sessions=2, idle_s=600, resolve=_site_resolver(), open_connection=_to_site(apply_site))

    first = pool()
    steps: list[dict[str, Any]] = []
    try:
        page = await run(first, "u1", "open", url=URL, application_id=7)
        assert STEPS_KEY not in page, "opening a page is no step"
        acts = [
            ("type", _ref(page, "First name"), "Grace", ""), ("type", _ref(page, "Verification code"), "123456", ""),
            ("choose", _ref(page, "Country"), "United Kingdom", ""), ("click", _ref(page, "No"), "", ""),
            ("click", _ref(page, "I agree"), "", ""), ("upload", _ref(page, "Resume"), "", "cv"),
            ("click", _ref(page, "Submit application"), "", ""),
        ]
        for action, ref, text, kind in acts:
            out = await run(first, "u1", action, ref=ref, text=text, doc_kind=kind, gate=gate, files=_files,
                            application_id=7)
            steps.extend(out.pop(STEPS_KEY, []))
        scratch = await run(first, "u1", "open", url=URL)  # no job named: nothing is recorded
        typed = await run(first, "u1", "type", ref=_ref(scratch, "First name"), text="Ada")
        assert STEPS_KEY not in typed
    finally:
        await first.shutdown()

    assert [(s["action"], s["value"]) for s in steps] == [
        ("type", "Grace"), ("choose", "United Kingdom"), ("click", ""), ("tick", "yes"), ("upload", "cv"),
    ], "the code box and the submit click are never recorded"
    assert "123456" not in json.dumps(steps)
    assert {s["where"]["host"] for s in steps} == {"jobs.example"}
    assert steps[2]["where"]["name"] == "No" and steps[2]["where"]["group"].startswith("Need a visa")

    # A forged submit step in the script is refused, never clicked.
    forged = {"action": "click", "value": "", "where": {
        "tag": "button", "type": "submit", "role": "", "name": "Submit application", "group": "", "nth": 0,
        "frame": steps[0]["where"]["frame"], "host": "jobs.example"}}
    second = pool()  # the process restarted: a new pool, no page
    try:
        done = await replay(second, "u1", 7, URL, [*steps, forged], files=_files)
        assert done["steps_ok"] == 5, done
        assert [(f["step"], f["action"]) for f in done["steps_failed"]] == [(6, "click")]
        assert done["steps_failed"][0]["reason"] == "submit_needs_application"
        seen = await run(second, "u1", "look", application_id=7)
        by = {e["name"]: e for e in seen["elements"]}
        assert by["First name"]["value"] == "Grace" and by["Country"]["value"] == "United Kingdom"
        assert by["No"]["checked"] is True and by["Yes"]["checked"] is False
        assert next(e for n, e in by.items() if n.startswith("I agree"))["checked"] is True
        assert by["Resume"]["value"] == "CV.pdf"
        assert by["Verification code"]["value"] == "", "nothing new is typed"
        assert seen["title"] == "Apply - Acme", "never submitted"
    finally:
        await second.shutdown()


# ── The route, the DB script and the asks payload (the browser is faked) ──────

FORM = "https://jobs.acme.com/apply/7"
ANSWERS = [{"question": "First name", "answer": "Grace", "source": "profile"}]


def _where(name: str, host: str = "jobs.acme.com") -> dict[str, Any]:
    return {"tag": "input", "type": "", "role": "", "name": name, "group": "", "nth": 0,
            "frame": f"https/{host}//apply/7", "host": host}


class _Ctx:
    async def close(self) -> None:
        return None


@contextlib.asynccontextmanager
async def _db():
    from src.api.dependencies import get_request_db

    async with contextlib.asynccontextmanager(get_request_db)() as db:
        yield db


async def _job(client) -> int:
    brought = await client.post("/api/jobs/bring", json={
        "title": "Engineer", "company": "Acme", "location": "London",
        "description": "Build services. " * 10, "apply_url": FORM,
    })
    return int(brought.json()["application_id"])


@pytest.fixture
def fake_browser(monkeypatch):
    """One pool for the test; ``run`` answers like the real one for a fill, a CAPTCHA on any click."""
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    pool = agent.BrowserPool(max_sessions=3, idle_s=600, resolve=None)  # type: ignore[arg-type]
    monkeypatch.setattr(agent, "get_pool", lambda: pool)

    async def fake_run(p, user_id, action, **kw):
        key = agent.browser_key(user_id, kw.get("application_id"))
        pool._sessions.setdefault(key, agent.Session(context=_Ctx(), page=None))  # noqa: SLF001
        if action == "click":
            return {"status": "needs_you", "reason": "captcha", "detail": "x", "url": FORM}
        names = {"type": [("First name", "Grace"), ("First name", "Grace"), ("Last name", "Hopper")]}
        steps = [{"action": "type", "where": _where(n), "value": v} for n, v in names.get(action, [])]
        return {"status": "ok", agent.STEPS_KEY: steps}

    monkeypatch.setattr(agent, "run", fake_run)
    return pool


async def _filled_and_blocked(client, app_id: int) -> None:
    """The assistant fills (recording the script), records form_filled, then Apply raises a CAPTCHA."""
    out = (await client.post("/api/browser", json={"action": "type", "ref": "e1_ab", "application_id": app_id})).json()
    assert "_fill_steps" not in out, "the script never reaches the assistant"
    fill = {"event_type": "form_filled", "payload": {"form_url": FORM, "fields_count": 2, "answers": ANSWERS}}
    assert (await client.post(f"/api/applications/{app_id}/events", json=fill)).status_code == 201
    blocked = (await client.post("/api/browser", json={"action": "click", "ref": "e2_ab", "application_id": app_id}))
    assert blocked.json()["blocked_recorded"] is True


async def _open_asks(client) -> list[dict[str, Any]]:
    return (await client.get("/api/asks")).json()["asks"]


@pytest.mark.asyncio
async def test_a_superseding_captcha_ask_still_offers_finish_it_here(authenticated_async_context, fake_browser):
    """The root cause on application 138: ask_user's CAPTCHA ask replaced the block's ask (one ask per job);
    the block kept the old ask id, so finish_here never matched the ask on the page."""
    async with authenticated_async_context() as client:
        app_id = await _job(client)
        await _filled_and_blocked(client, app_id)
        again = {"application_id": app_id, "question": "Please solve the CAPTCHA on the form.", "kind": "captcha"}
        assert (await client.post("/api/asks", json=again)).status_code == 201
        (ask,) = [a for a in await _open_asks(client) if a["application_id"] == app_id]
        assert ask["finish_here"] is True and ask["held_seconds"] > 0, ask
        assert ask["browser"]["kind"] == "human" and ask["can_refill"] is True


@pytest.mark.asyncio
async def test_the_application_page_offers_the_same_hand_off_as_needs_you(authenticated_async_context, fake_browser):
    """Owner 2026-10-26: the application page never showed "Finish it here" or "Refill the form" on a CAPTCHA,
    because the detail's asks carried only ``browser``. Both doors now read the same hand-off fields."""
    keys = ("finish_here", "held_seconds", "can_refill", "browser")
    async with authenticated_async_context() as client:
        app_id = await _job(client)
        await _filled_and_blocked(client, app_id)
        (needs_you,) = [a for a in await _open_asks(client) if a["application_id"] == app_id]
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        (page,) = [a for a in detail["asks"] if a["status"] == "open"]
        assert page["id"] == needs_you["id"]
        assert page["finish_here"] is True and page["held_seconds"] > 0 and page["can_refill"] is True, page
        assert {k: page[k] is not None for k in keys} == {k: needs_you[k] is not None for k in keys}
        assert (page["finish_here"], page["can_refill"], page["browser"]) == (
            needs_you["finish_here"], needs_you["can_refill"], needs_you["browser"])


@pytest.mark.asyncio
async def test_refill_after_a_restart_replays_the_db_script_and_brings_finish_it_here_back(
    authenticated_async_context, fake_browser, monkeypatch
):
    from src.services.applications import fill_script, kit
    from src.services.browser_agent import agent, refill

    seen: dict[str, Any] = {}

    async def fake_replay(pool, user_id, application_id, form_url, steps, *, files, load_state=None):
        seen.update(form_url=form_url, steps=steps)
        pool._sessions[agent.browser_key(user_id, application_id)] = agent.Session(context=_Ctx(), page=None)  # noqa: SLF001
        return {"status": "ok", "steps_ok": len(steps), "steps_failed": []}

    monkeypatch.setattr(refill, "replay", fake_replay)
    async with authenticated_async_context() as client:
        app_id = await _job(client)
        await _filled_and_blocked(client, app_id)
        user_id = authenticated_async_context.fixture_user_id
        async with _db() as db:
            stamp = await kit.approval_fingerprint(db, user_id, app_id)
        fake_browser._sessions.clear()  # noqa: SLF001 - a deploy restarted the process: the page is gone
        (ask,) = [a for a in await _open_asks(client) if a["application_id"] == app_id]
        assert ask["finish_here"] is False and ask["can_refill"] is True
        controls = (await client.get(f"/api/applications/{app_id}/controls")).json()
        assert controls["blocked"]["can_refill"] is True

        out = (await client.post(f"/api/applications/{app_id}/browser/refill")).json()
        assert out["status"] == "ok" and out["steps_ok"] == 2 and out["steps_failed"] == [], out
        assert 0 < out["held_seconds"] <= agent.HANDOFF_HOLD_SECONDS
        assert seen["form_url"] == FORM
        # One step per field, in the order typed (the repeated First name kept once).
        assert [(s["where"]["name"], s["value"]) for s in seen["steps"]] == [("First name", "Grace"), ("Last name", "Hopper")]
        (ask,) = [a for a in await _open_asks(client) if a["application_id"] == app_id]
        assert ask["finish_here"] is True and ask["held_seconds"] > 0

        async with _db() as db:
            fill = await fill_script.newest_fill(db, user_id, app_id)
        assert fill["by"] == "refill" and fill["answers"] == ANSWERS
        async with _db() as db:
            app_row = {"id": app_id, "job_company": "Acme"}
            event = {"recorded_at": datetime.now(timezone.utc).isoformat(), "payload": {"fingerprint": stamp}}
            assert await kit.approval_stale(db, user_id, app_row, event, datetime.now(timezone.utc)) is None, (
                "a pure replay of the same answers keeps the user's yes"
            )

        async def half(pool, user_id, application_id, form_url, steps, *, files, load_state=None):
            return {"status": "ok", "steps_ok": 1, "steps_failed": [
                {"step": 2, "action": "type", "field": "Last name", "reason": "not_found"}]}

        monkeypatch.setattr(refill, "replay", half)
        out = (await client.post(f"/api/applications/{app_id}/browser/refill")).json()
        assert out["steps_failed"][0]["reason"] == "not_found"
        async with _db() as db:
            assert await kit.approval_stale(db, user_id, app_row, event, datetime.now(timezone.utc)) == "form_fill", (
                "a form that did not refill fully is not the form the user said yes to"
            )


@pytest.mark.asyncio
async def test_refill_refuses_without_a_script_on_another_host_and_for_someone_elses_job(
    authenticated_async_context, fake_browser, monkeypatch
):
    from httpx import ASGITransport, AsyncClient

    from src.api.main import app
    from src.core import settings
    from src.services.browser_agent import refill

    async def never(*_a, **_k):
        raise AssertionError("refused before any browser work")

    monkeypatch.setattr(refill, "replay", never)
    async with authenticated_async_context() as client:
        app_id = await _job(client)
        out = (await client.post(f"/api/applications/{app_id}/browser/refill")).json()
        assert (out["status"], out["reason"]) == ("refused", "no_script")
        await _filled_and_blocked(client, app_id)
        moved = {"event_type": "form_filled", "payload": {"form_url": "https://other.example/apply", "fields_count": 2}}
        assert (await client.post(f"/api/applications/{app_id}/events", json=moved)).status_code == 201
        out = (await client.post(f"/api/applications/{app_id}/browser/refill")).json()
        assert out["reason"] == "host_mismatch"
        monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", False)
        assert (await client.post(f"/api/applications/{app_id}/browser/refill")).json()["reason"] == "browser_off"
        monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await async_sign_in(other, "someone-else@example.com")
        resp = await other.post(f"/api/applications/{app_id}/browser/refill")
        assert resp.status_code == 404, resp.text  # never the other user's form
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as agent_client:
        resp = await agent_client.post(f"/api/applications/{app_id}/browser/refill")
        assert resp.status_code == 403, "an assistant's token never presses Refill"


@pytest.mark.asyncio
async def test_the_script_keeps_one_step_per_field_in_order_and_only_for_its_owner(authenticated_async_context):
    from src.services.applications import fill_script

    async with authenticated_async_context() as client:
        app_id = await _job(client)
    user_id = authenticated_async_context.fixture_user_id
    steps = [
        {"action": "type", "where": _where("City"), "value": "Lon"},
        {"action": "click", "where": {**_where("London, UK"), "role": "option"}, "value": ""},
        {"action": "type", "where": _where("Phone"), "value": "07000"},
        {"action": "type", "where": _where("Phone"), "value": "07123"},
        {"action": "bogus", "where": _where("X"), "value": "no"},
    ]
    async with _db() as db:
        assert await fill_script.append(db, user_id, app_id, steps) == 4
        script, hosts = await fill_script.load(db, user_id, app_id)
        assert [(s["where"]["name"], s["value"]) for s in script] == [
            ("City", "Lon"), ("London, UK", ""), ("Phone", "07123")]
        assert hosts == {"jobs.acme.com"}
        assert await fill_script.load(db, "someone-else", app_id) == ([], set())
        assert await fill_script.apps_with_script(db, user_id, [app_id, app_id + 1]) == {app_id}
