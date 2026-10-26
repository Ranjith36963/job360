"""The own-browser queue, service side (owner decisions 2026-10-25): the pure derive, the words, and the
refusal of a caller-written entry. Real rows through the real doors. The route and MCP tests are in
``test_own_browser.py``."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications import own_browser
from src.services.browser_agent import routing

_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines.",
}
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


# ── helpers (copied, never imported from another test module) ────────────────


async def _bring(client: AsyncClient, n: int = 0) -> int:
    resp = await client.post(
        "/api/jobs/bring", json={**_AD, "title": f"Engineer {n}", "apply_url": f"https://northwind.example/c/{n}"}
    )
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _person(client: AsyncClient, n: int = 0, channel: str = "linkedin", text: str = "Hi, a note") -> tuple[int, int]:
    """A contact with one drafted message; returns (contact_id, message_id)."""
    app_id = await _bring(client, n)
    added = await client.post(
        f"/api/applications/{app_id}/contacts",
        json={"name": f"Priya {n}", "role": "Recruiter", "linkedin_url": f"https://www.linkedin.com/in/priya{n}"},
    )
    assert added.status_code == 201, added.text
    contact_id = int(added.json()["contact"]["id"])
    return contact_id, await _draft(client, app_id, contact_id, channel, text)


async def _draft(client: AsyncClient, app_id: int, contact_id: int, channel: str, text: str) -> int:
    resp = await client.post(
        f"/api/applications/{app_id}/artifacts",
        json={"kind": "outreach", "text": text, "contact_id": contact_id, "channel": channel},
    )
    assert resp.status_code == 201, resp.text
    cur = await client.get(f"/api/people?contact_id={contact_id}")
    return int(cur.json()["person"]["outreach"]["messages"][-1]["id"])



@pytest.mark.asyncio
async def test_no_caller_can_write_an_own_browser_entry(authenticated_async_context):
    async with authenticated_async_context() as client:
        contact_id, message_id = await _person(client)
        for entry in own_browser.ENTRIES:
            resp = await client.post(
                f"/api/contacts/{contact_id}/outreach", json={"entry": entry, "channel": "linkedin", "text": str(message_id)}
            )
            assert resp.status_code == 422, (entry, resp.text)


def test_pure_derive_values():
    def row(i: int, entry: str, text: str = "", channel: str = "linkedin", at: datetime = NOW) -> dict[str, Any]:
        return {"id": i, "entry": entry, "channel": channel, "text": text, "recorded_at": at.isoformat()}

    assert own_browser.derive([], NOW) is None
    msg = row(1, "message", "hi")
    assert own_browser.derive([msg], NOW)["state"] == "waiting_on_you"
    ok = [msg, row(2, "send_approved", "1")]
    assert own_browser.derive(ok, NOW)["state"] == "queued"
    claimed = [*ok, row(3, "send_claimed", "desk", at=NOW - timedelta(minutes=14))]
    got = own_browser.derive(claimed, NOW)
    assert got["state"] == "in_progress" and got["claimed_by"] == "desk"
    assert own_browser.derive([*ok, row(3, "send_claimed", "desk", at=NOW - timedelta(minutes=16))], NOW)["state"] == "queued"
    assert own_browser.derive([*claimed, row(4, "send_released", "other")], NOW)["state"] == "queued"
    assert own_browser.derive([*claimed, row(4, "send_declined", "1")], NOW)["state"] == "declined"
    assert own_browser.derive([*ok, row(3, "sent", "")], NOW) is None
    assert own_browser.derive([*ok, row(3, "reply", "")], NOW) is None
    assert own_browser.derive([msg, row(2, "sent", ""), row(3, "message", "again")], NOW)["state"] == "waiting_on_you"
    assert own_browser.derive([row(1, "message", "hi", channel="email")], NOW) is None
    # an approval of an OLDER message does not carry over to the rewritten one
    assert own_browser.derive([msg, row(2, "send_approved", "1"), row(3, "message", "v2")], NOW)["state"] == "waiting_on_you"
    assert own_browser.public_task(None) is None


def test_no_assistant_or_browser_brand_in_any_text():
    texts = [*own_browser.WHY_WORDS.values(), *routing.LABELS.values()]
    for text in texts:
        assert not re.search(r"claude|chatgpt|gemini|atlas|chrome", text, re.I), text
