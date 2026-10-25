"""Which browser a task uses (owner decisions 2026-10-25): Job360's browser first for everything; the user's own
browser only after Job360's failed (sign-in wall, CAPTCHA or bot check, safety stop); a password, a code or a
CAPTCHA the user must solve is the user's alone. Value asserts (rule #21): real rows through the real doors."""
from __future__ import annotations

import re
from typing import Any

import pytest
from httpx import AsyncClient

from src.api.auth_deps import CurrentUser
from src.api.routes import browser as browser_routes
from src.services.applications import blocked as blocked_service
from src.services.applications import spine
from src.services.browser_agent import routing

_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines.",
}
FORM = "https://careers.example/apply/7"
JOB360 = {"kind": "job360", "label": "I'll use Job360's browser", "reason": "default"}


def test_default_is_job360_for_everything():
    # No URL, no site, no task kind goes in: a form and a LinkedIn task are the same call.
    assert routing.browser_for() == routing.Route("job360", "default")
    assert routing.browser_for(page_stop="", job360_failed="") == routing.Route("job360", "default")
    assert routing.browser_for(page_stop="something_new", job360_failed="site_error") == routing.Route("job360", "default")
    assert routing.label(routing.browser_for()) == JOB360


@pytest.mark.parametrize("failure", sorted(routing.JOB360_FAILURES))
def test_job360_failure_moves_to_own_browser(failure):
    route = routing.browser_for(job360_failed=failure)
    assert route == routing.Route("own_browser", routing.JOB360_FAILURES[failure])
    assert route.reason in routing.ROUTE_REASONS
    assert routing.label(route)["label"] == "Needs your own browser: turn your PC on"


@pytest.mark.parametrize("stop", sorted(routing.HUMAN_STOPS))
def test_human_stops_win(stop):
    for failed in ("", "captcha", "login_required"):
        assert routing.browser_for(page_stop=stop, job360_failed=failed) == routing.Route(
            "human", routing.HUMAN_STOPS[stop]
        )
    assert routing.label(routing.browser_for(page_stop=stop))["label"] == "Only you can do this"


@pytest.mark.parametrize(
    ("kind", "reason", "by_browser", "want"),
    [
        ("upload", "upload_failed", False, ("human", "only_you")),
        ("safety", "safety_block", False, ("human", "only_you")),
        ("safety", "safety_block", True, ("own_browser", "job360_safety_stop")),
        ("login", "otp_needed", False, ("human", "mfa")),
        ("login", "otp_needed", True, ("human", "mfa")),
        ("login", "login_needed", True, ("own_browser", "job360_login_required")),
        ("login", "login_needed", False, ("human", "password")),
        ("login", "account_creation", True, ("human", "password")),
        ("captcha", "captcha", True, ("own_browser", "job360_bot_check")),
        ("captcha", "bot_check", True, ("own_browser", "job360_bot_check")),
        ("captcha", "captcha", False, ("human", "only_you")),
        ("question", "", False, ("job360", "default")),
        ("", "", False, ("job360", "default")),
        ("question", "unknown_question", True, ("job360", "default")),
    ],
)
def test_ask_route_table(kind, reason, by_browser, want):
    route = routing.ask_route(kind, block_reason=reason, block_by_browser=by_browser)
    assert (route.kind, route.reason) == want
    assert route.reason in routing.ROUTE_REASONS


def test_a_captcha_the_user_can_finish_here_is_theirs_alone_until_the_hold_is_gone():
    held = routing.ask_route("captcha", block_reason="captcha", block_by_browser=True, finish_here=True)
    assert (held.kind, held.reason) == ("human", "only_you")
    gone = routing.ask_route("captcha", block_reason="captcha", block_by_browser=True, finish_here=False)
    assert (gone.kind, gone.reason) == ("own_browser", "job360_bot_check")


def _rules_section() -> str:
    from pathlib import Path

    text = (Path(routing.__file__).parents[2] / "recipes" / "rules.md").read_text(encoding="utf-8")
    match = re.search(r"^## Which browser\n(.*?)(?=^## )", text, re.S | re.M)
    assert match, "rules.md has no 'Which browser' section"
    return match.group(1)


def test_no_text_names_an_assistant():
    texts = [*routing.LABELS.values(), browser_routes.HAND_OFF_DETAIL, _rules_section()]
    assert len(_rules_section()) > 400
    for text in texts:
        assert not re.search(r"claude|chatgpt|gemini|atlas|chrome", text, re.I), text


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _browser_block(authenticated_async_context, app_id: int, reason: str) -> None:
    """What Job360's own browser writes when it stops (``by: browser``): never reachable by a caller."""
    user = CurrentUser(id=authenticated_async_context.fixture_user_id, email="u@example.com")
    async with browser_routes._db() as db:  # noqa: SLF001
        await blocked_service.record_browser_block(
            db, user, app_id, {"reason": reason, "step": "Job360 browser", "page_host": "careers.example"}
        )


async def _ask_browser(client: AsyncClient, app_id: int) -> dict[str, Any]:
    asks = [a for a in (await client.get("/api/asks")).json()["asks"] if a["application_id"] == app_id]
    assert len(asks) == 1, asks
    return asks[0]


@pytest.mark.asyncio
async def test_asks_carry_browser(authenticated_async_context):
    async with authenticated_async_context() as client:
        # Four jobs: the same ad is the same application, so vary the title.
        ids = []
        for n in range(4):
            resp = await client.post("/api/jobs/bring", json={**_AD, "title": f"Engineer {n}", "apply_url": f"https://northwind.example/c/{n}"})
            assert resp.status_code == 200, resp.text
            ids.append(int(resp.json()["application_id"]))
        cap_id, login_id, otp_id, plain_id = ids
        await _browser_block(authenticated_async_context, cap_id, "captcha")
        await _browser_block(authenticated_async_context, login_id, "login_needed")
        sent = await client.post(
            f"/api/applications/{otp_id}/events",
            json={"event_type": "blocked", "payload": {"reason": "otp_needed", "step": "code"}},
        )
        assert sent.status_code == 201, sent.text
        plain = await client.post("/api/asks", json={"application_id": plain_id, "question": "Notice period?"})
        assert plain.status_code == 201, plain.text

        cap_ask = await _ask_browser(client, cap_id)
        assert cap_ask["finish_here"] is True  # Job360's browser holds the page: the user solves it right here
        assert cap_ask["browser"] == {"kind": "human", "label": "Only you can do this", "reason": "only_you"}
        assert (await _ask_browser(client, login_id))["browser"] == {
            "kind": "own_browser", "label": "Needs your own browser: turn your PC on",
            "reason": "job360_login_required",
        }
        assert (await _ask_browser(client, otp_id))["browser"] == {
            "kind": "human", "label": "Only you can do this", "reason": "mfa",
        }
        assert (await _ask_browser(client, plain_id))["browser"] == JOB360
        # The application detail carries the same label on its ask.
        detail = (await client.get(f"/api/applications/{cap_id}")).json()
        assert [a["browser"]["reason"] for a in detail["asks"]] == ["only_you"]


@pytest.mark.asyncio
async def test_a_caller_captcha_is_the_users_alone(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await client.post(
            f"/api/applications/{app_id}/events",
            json={"event_type": "blocked", "payload": {"reason": "captcha", "step": "last page"}},
        )
        assert resp.status_code == 201, resp.text
        assert (await _ask_browser(client, app_id))["browser"] == {
            "kind": "human", "label": "Only you can do this", "reason": "only_you",
        }


@pytest.mark.asyncio
async def test_ready_card_browser(authenticated_async_context):
    """A ready card is never one with an open block (`_READY_SQL`), so every card is Job360's browser."""
    async with authenticated_async_context() as client:
        first = await _bring(client)
        resp = await client.post(
            "/api/jobs/bring", json={**_AD, "title": "Engineer 9", "apply_url": "https://northwind.example/c/9"}
        )
        second = int(resp.json()["application_id"])
        for app_id in (first, second):
            filled = await client.post(
                f"/api/applications/{app_id}/events",
                json={"event_type": "form_filled", "payload": {"form_url": FORM, "fields_count": 1}},
            )
            assert filled.status_code == 201, filled.text
        items = (await client.get("/api/ready-to-send")).json()["items"]
        assert {c["application_id"] for c in items} == {first, second}
        assert [c["browser"] for c in items] == [JOB360, JOB360]
        # Job360's browser stopping on a form takes the card off the list instead of relabelling it.
        await _browser_block(authenticated_async_context, second, "login_needed")
        items = (await client.get("/api/ready-to-send")).json()["items"]
        assert [(c["application_id"], c["browser"]) for c in items] == [(first, JOB360)]


@pytest.mark.asyncio
async def test_one_open_block_map_serves_finish_here_and_the_label(authenticated_async_context):
    """`open_blocks` is read once and shared: it agrees with `_open_block_event` for every application,
    and `finish_here_ask_ids` / `browser_for_asks` read the same map to the same answer."""
    async with authenticated_async_context() as client:
        ids = []
        for n in range(5):
            resp = await client.post(
                "/api/jobs/bring", json={**_AD, "title": f"Engineer {n}", "apply_url": f"https://northwind.example/c/{n}"}
            )
            ids.append(int(resp.json()["application_id"]))
        cap_id, login_id, upload_id, cleared_id, none_id = ids
        await _browser_block(authenticated_async_context, cap_id, "captcha")
        await _browser_block(authenticated_async_context, login_id, "login_needed")
        for app_id in (upload_id, cleared_id):
            sent = await client.post(
                f"/api/applications/{app_id}/events",
                json={"event_type": "blocked", "payload": {"reason": "upload_failed", "step": "CV"}},
            )
            assert sent.status_code == 201, sent.text
        user_id = authenticated_async_context.fixture_user_id
        async with browser_routes._db() as db:  # noqa: SLF001
            # A later upload clears an upload_failed block (the same rule as `_open_block_event`).
            await spine.append_event(
                db, user_id=user_id, application_id=cleared_id, event_type="document_uploaded",
                payload={"kind": "cv", "artifact_id": 1}, occurred_at=spine.parse_occurred_at(None), recorded_by="web",
            )
            rows = (await client.get("/api/asks")).json()["asks"]
            blocks = await blocked_service.open_blocks(db, user_id, ids)
            assert set(blocks) == {cap_id, login_id, upload_id}
            for app_id in ids:
                ev = await blocked_service._open_block_event(db, user_id, app_id)  # noqa: SLF001
                assert blocks.get(app_id) == (ev["payload"] if ev else None)
            finish = await blocked_service.finish_here_ask_ids(db, user_id, rows, blocks)
            assert finish == await blocked_service.finish_here_ask_ids(db, user_id, rows)
            labels = await blocked_service.browser_for_asks(db, user_id, rows, blocks, finish)
            assert labels == await blocked_service.browser_for_asks(db, user_id, rows)
            by_app = {r["application_id"]: r for r in rows}
            cap, login = by_app[cap_id], by_app[login_id]
            assert finish == {cap["id"]}
            assert labels[cap["id"]]["kind"] == "human"  # finish_here: solved right now on Needs you
            assert labels[login["id"]]["reason"] == "job360_login_required"
            # Once the hold is gone (not finish_here) the same browser-written CAPTCHA waits for the own browser.
            gone = await blocked_service.browser_for_asks(db, user_id, rows, blocks, set())
            assert gone[cap["id"]] == {
                "kind": "own_browser", "label": "Needs your own browser: turn your PC on", "reason": "job360_bot_check",
            }
            assert none_id not in blocks


@pytest.mark.asyncio
async def test_contact_send_browser(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        views = {}
        for name, channel in (("Priya Shah", "linkedin"), ("Sam Lee", "email")):
            added = await client.post(
                f"/api/applications/{app_id}/contacts",
                json={"name": name, "role": "Recruiter", "email": f"{name[:3].lower()}@northwind.example"},
            )
            cid = added.json()["contact"]["id"]
            assert added.json()["contact"]["send_browser"] is None  # no message yet
            msg = await client.post(
                f"/api/applications/{app_id}/artifacts",
                json={"kind": "outreach", "text": "Hi there", "contact_id": cid, "channel": channel},
            )
            assert msg.status_code == 201, msg.text
            views[channel] = cid
        contacts = {c["id"]: c for c in (await client.get(f"/api/applications/{app_id}")).json()["contacts"]}
        assert contacts[views["linkedin"]]["send_browser"] == JOB360
        assert contacts[views["email"]]["send_browser"] is None
