"""Owner, 2026-10-26 (application 138): in "Finish it here" the user solved the CAPTCHA and pressed Apply
himself; the employer confirmed, but Job360 never noticed. Now the user's own click (or Enter) in the
hand-off view runs the same confirmation detector as the assistant's submit click; when it fires, the
application is recorded as submitted BY THE USER, the block and its CAPTCHA asks close, the proof is kept
and the browser is let go, once. A page the detector does not call a confirmation changes nothing.
"""
from __future__ import annotations

import contextlib
import json

import pytest

from tests.test_browser_handoff import _Ctx, _no_dns

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
THANKS = "https://jobs.acme.com/apply/7/thanks"


class _Mouse:
    async def click(self, x, y):
        return None


class _Keys:
    async def press(self, key):
        return None


class _Page:
    url = THANKS
    mouse = _Mouse()
    keyboard = _Keys()

    def is_closed(self):
        return False


def _fake_browser(monkeypatch, pool, confirmed: dict[str, bool]):
    """A held job browser whose page the real ``live`` acts on; ``look`` is the detector's answer."""
    from src.services.browser_agent import agent

    async def fake_run(p, user_id, action, **kw):
        pool._sessions[agent.browser_key(user_id, kw.get("application_id"))] = agent.Session(  # noqa: SLF001
            context=_Ctx(), page=_Page(),
        )
        return {"status": "needs_you", "reason": "captcha", "detail": "x", "url": "https://jobs.acme.com/apply/7"}

    async def fake_look(s, *, after_action=False):
        return {"status": "ok", **({"confirmation_seen": True} if confirmed["seen"] else {})}

    async def fake_shot(s):
        return {"status": "ok", "url": THANKS, "title": "Thanks", "width": 1280, "height": 900,
                "image": "data:image/jpeg;base64,AAAA"}

    async def fake_capture(page, *, quality):
        return JPEG

    async def no_settle(page):
        return None

    monkeypatch.setattr(agent, "run", fake_run)
    monkeypatch.setattr(agent, "look", fake_look)
    monkeypatch.setattr(agent, "_shot", fake_shot)
    monkeypatch.setattr(agent, "_capture", fake_capture)
    monkeypatch.setattr(agent, "_settle", no_settle)
    monkeypatch.setattr(agent, "get_pool", lambda: pool)


async def _blocked_job(client, n: int) -> int:
    brought = await client.post("/api/jobs/bring", json={
        "title": f"Engineer {n}", "company": "Acme", "location": "London",
        "description": "Build services. " * 10, "apply_url": "https://jobs.acme.com/apply/7",
    })
    app_id = brought.json()["application_id"]
    await client.post("/api/browser", json={"action": "click", "ref": "e1_ab", "application_id": app_id})
    # The app-138 shape: an ask_user CAPTCHA ask superseded the one the block opened.
    again = {"application_id": app_id, "question": "Please solve the CAPTCHA on the form.", "kind": "captcha"}
    assert (await client.post("/api/asks", json=again)).status_code == 201
    return app_id


async def _facts(user_id: str, app_id: int) -> dict:
    from src.api.dependencies import get_request_db
    from src.services.applications import blocked

    async with contextlib.asynccontextmanager(get_request_db)() as db:
        status = (await (await db._db.execute(
            "SELECT status FROM applications WHERE id = ?", (app_id,))).fetchone())["status"]
        receipts = [dict(r) for r in await (await db._db.execute(
            "SELECT recorded_by, channel, note FROM application_receipts WHERE application_id = ?", (app_id,),
        )).fetchall()]
        events = [r["event_type"] for r in await (await db._db.execute(
            "SELECT event_type FROM application_events WHERE application_id = ? ORDER BY id", (app_id,),
        )).fetchall()]
        unblocked = [json.loads(r["payload"]) for r in await (await db._db.execute(
            "SELECT payload FROM application_events WHERE application_id = ? AND event_type = 'unblocked'", (app_id,),
        )).fetchall()]
        open_asks = (await (await db._db.execute(
            "SELECT COUNT(*) AS n FROM application_asks WHERE application_id = ? "
            "AND answered_at IS NULL AND withdrawn_at IS NULL", (app_id,),
        )).fetchone())["n"]
        block = await blocked.open_block(db, user_id, app_id)
    return {"status": status, "receipts": receipts, "events": events, "unblocked": unblocked,
            "open_asks": open_asks, "block": block}


@pytest.mark.asyncio
async def test_the_users_own_submit_in_the_live_view_is_recorded_once(authenticated_async_context, monkeypatch):
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    pool = agent.BrowserPool(max_sessions=2, idle_s=600, resolve=_no_dns)
    _fake_browser(monkeypatch, pool, {"seen": True})

    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        app_id = await _blocked_job(client, 7)
        before = await _facts(user_id, app_id)
        assert before["block"] is not None and before["open_asks"] >= 1 and before["status"] == "considering"

        live = {"action": "click", "x": 600, "y": 700, "mode": "finish", "application_id": app_id}
        out = (await client.post("/api/browser/live", json=live)).json()
        assert out["submitted"] is True, out
        assert agent.PROOF_KEY not in out and "confirmation_seen" not in out  # the picture stays server-side
        after = await _facts(user_id, app_id)
        assert after["status"] == "applied"
        (receipt,) = after["receipts"]
        assert receipt["recorded_by"] == "web" and receipt["channel"] == "company_site"
        assert receipt["note"] == "Submitted by you in Job360's live view; confirmation page seen on jobs.acme.com."
        assert after["block"] is None and [u["resolution"] for u in after["unblocked"]] == ["user_did_it"]
        assert after["open_asks"] == 0  # the block's ask AND the superseding CAPTCHA ask
        assert after["events"].count("proof_screenshot") == 1 and after["events"].count("applied") == 1
        assert pool._sessions[agent.browser_key(user_id, app_id)].held_until is None  # noqa: SLF001 - as Done

        # (b) the confirmation page again (Enter this time): nothing new is written.
        again = (await client.post("/api/browser/live", json={**live, "action": "key", "key": "Enter"})).json()
        assert again["submitted"] is True
        twice = await _facts(user_id, app_id)
        assert len(twice["receipts"]) == 1 and twice["events"] == after["events"]


@pytest.mark.asyncio
async def test_a_live_click_with_no_confirmation_changes_nothing(authenticated_async_context, monkeypatch):
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    pool = agent.BrowserPool(max_sessions=2, idle_s=600, resolve=_no_dns)
    _fake_browser(monkeypatch, pool, {"seen": False})

    async with authenticated_async_context() as client:
        user_id = authenticated_async_context.fixture_user_id
        app_id = await _blocked_job(client, 8)
        before = await _facts(user_id, app_id)
        out = (await client.post("/api/browser/live", json={
            "action": "click", "x": 600, "y": 700, "mode": "finish", "application_id": app_id,
        })).json()
        assert out["status"] == "ok" and "submitted" not in out
        assert await _facts(user_id, app_id) == before
        assert pool._sessions[agent.browser_key(user_id, app_id)].held_until is not None  # noqa: SLF001


@pytest.mark.asyncio
async def test_the_detector_never_runs_on_a_scroll_or_shot(monkeypatch):
    """Only the user's press (click, Enter) can be a submit; the 1.5 s picture refresh never is."""
    from src.services.browser_agent import agent

    pool = agent.BrowserPool(max_sessions=1, idle_s=600, resolve=_no_dns)
    looked: list[str] = []

    async def fake_look(s, *, after_action=False):
        looked.append("look")
        return {"confirmation_seen": True}

    async def fake_shot(s):
        return {"status": "ok", "url": THANKS}

    class _Wheel(_Page):
        class mouse:  # noqa: N801
            @staticmethod
            async def wheel(x, y):
                return None

    monkeypatch.setattr(agent, "look", fake_look)
    monkeypatch.setattr(agent, "_shot", fake_shot)
    pool._sessions = {agent.browser_key("u1", 5): agent.Session(context=_Ctx(), page=_Wheel())}  # noqa: SLF001
    for action in ("shot", "scroll"):
        out = await agent.live(pool, "u1", action, dy=100, finish=True, application_id=5)
        assert "confirmation_seen" not in out
    assert looked == []


@pytest.mark.asyncio
async def test_a_token_still_cannot_use_the_finish_view(authenticated_async_context):
    async with authenticated_async_context() as client:
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    from httpx import ASGITransport, AsyncClient

    from src.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as bot:
        resp = await bot.post("/api/browser/live", json={
            "action": "click", "x": 10, "y": 10, "mode": "finish", "application_id": 1,
        })
        assert resp.status_code == 403
