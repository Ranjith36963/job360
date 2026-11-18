"""Prod 2026-11-18: two Apply runs claimed the batch (16:29:29, 17:07:55), read kits for about four minutes and
ended with no ``mark_done``. Each 30-minute claim ran out silently: ``whats_due`` still said
``failures_in_row.batch`` 0 and nothing was logged. A claim that runs out with no mark_done now counts once in
``failures_in_row`` (never a back-off: the duty is free at once) and logs ``duty_claim_lapsed``; the batch
recipe makes every exit after a claim call mark_done. Helpers are defined here, never imported.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.routes.recipes import load_recipe
from src.core import settings
from src.services.profile.models import CVData, UserPreferences, UserProfile

HUNT_REPORT = {"sources": {k: {"skipped": {"code": "tool_missing", "detail": "no such tool in this test"}}
                           for k in ("mail", "kit", "connectors", "web", "browser")}}


async def _agent(client: AsyncClient, user_id: str) -> AsyncClient:
    from src.api.main import app
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Python.", name="Ada"),
                             preferences=UserPreferences(target_job_titles=["Data Engineer"])),
                 user_id, source_action="cv_upload")
    on = await client.patch("/api/profile", json={"edits": [{"path": "assistant_settings.hunt_every_hours", "value": 1}]})
    assert on.status_code == 200, on.text
    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _read(client: AsyncClient, *duties: str) -> dict[str, Any]:
    resp = await client.get("/api/schedule/due", params={"duties": list(duties)} if duties else None)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_a_claim_that_runs_out_with_no_mark_done_is_counted_once_and_logged(
    authenticated_async_context, fixture_user_id, monkeypatch, caplog
):
    async with authenticated_async_context() as client:
        async with await _agent(client, fixture_user_id) as agent:
            assert [d["duty"] for d in (await _read(agent, "hunt"))["due"]] == ["hunt"]  # claimed
            monkeypatch.setattr(settings, "DUTY_CLAIM_MINUTES", 0)  # ...and the run died: the claim runs out
            with caplog.at_level(logging.WARNING):
                first = await _read(agent)
            assert first["failures_in_row"]["hunt"] == 1
            assert first["running"] == {}
            assert [d["duty"] for d in first["due"]] == ["hunt"]  # never a back-off: free for the next run
            lines = [r for r in caplog.records if getattr(r, "event", "") == "duty_claim_lapsed"]
            assert [(r.duty, r.reason) for r in lines] == [("hunt", "no_mark_done")]
            assert getattr(lines[0], "claimed_at", "")
            assert (await _read(agent))["failures_in_row"]["hunt"] == 1  # counted once, not on every read
            assert [d["duty"] for d in (await _read(agent, "hunt"))["due"]] == ["hunt"]
            assert (await _read(agent))["failures_in_row"]["hunt"] == 2  # a second lapse in a row
            monkeypatch.setattr(settings, "DUTY_CLAIM_MINUTES", 30)
            assert [d["duty"] for d in (await _read(agent, "hunt"))["due"]] == ["hunt"]
            done = await agent.post("/api/schedule/done", json={"duty": "hunt", "report": HUNT_REPORT})
            assert done.status_code == 200, done.text
            assert (await _read(agent))["failures_in_row"]["hunt"] == 0  # a finished run ends the streak


def test_the_batch_recipe_marks_done_on_every_exit_after_a_claim():
    lines = load_recipe("batch").text.splitlines()
    rule = [ln for ln in lines if "**Every exit after a claim**" in ln]
    assert len(rule) == 1
    for pin in ('`mark_done(duty="batch", outcome, reason)`', "partial, needs_user", "a `stop` or `outside_hours`",
                "done, no_work", "a run out of time"):
        assert pin in rule[0], pin
    claim = next(i for i, ln in enumerate(lines) if ln.startswith("2. **Claim.**"))
    assert lines.index(rule[0]) - claim <= 2  # next to the claim step
    assert len(lines) <= 70
