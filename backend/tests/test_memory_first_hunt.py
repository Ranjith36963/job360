"""Memory first S4 (owner 2026-11-05): the hunt keeps to the user's own score line, and its search brief
reads only Memory and the user's instructions.

* ``below_line`` skip + a fit under ``apply_min_score`` = the job leaves the batch queue (an ``unqueued``
  recorded by Job360, the score kept); ``ask`` / ``auto`` / no line keep the old behaviour.
* An old row queued before its fit landed is left out of the open queue (no CV, no batch duty).
* ``build_search_brief`` never reads a saved form answer.

Job360 never scores: it compares the user's own line with the assistant's own number (rule 4).
Helpers are copied, never imported from another test module (schema isolation).
"""
from __future__ import annotations

import inspect
import json
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.profile import assistant_settings as rules
from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile
from src.services.profile.search_brief import build_search_brief


async def _seed(client: AsyncClient, user_id: str, **settings: Any) -> None:
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id,
        source_action="cv_upload",
    )
    edits = [{"path": f"assistant_settings.{k}", "value": v} for k, v in settings.items()]
    resp = await client.patch("/api/profile", json={"edits": edits})
    assert resp.status_code == 200, resp.text


async def _bring(client: AsyncClient, n: int, queue: bool = True) -> dict[str, Any]:
    resp = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": f"Northwind {n}", "location": "Remote", "queue": queue,
        "apply_url": f"https://northwind.example/careers/{n}", "description": "Build the pipelines. " * 8,
        "origin": "ai_hunt",
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _fit(client: AsyncClient, app_id: int, score: int) -> dict[str, Any]:
    resp = await client.put(
        f"/api/applications/{app_id}/fit", json={"score": score, "verdict": "my read", "score_source": "full_ad"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _queued_ids(client: AsyncClient) -> list[int]:
    resp = await client.get("/api/applications", params={"queued": True})
    assert resp.status_code == 200, resp.text
    return [int(r["id"]) for r in resp.json()["applications"]]


async def _detail(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── pure ──────────────────────────────────────────────────────────────────────


def test_drops_below_line_only_for_skip_with_a_line_and_a_score():
    eff = rules.effective
    assert rules.drops_below_line(40, eff(AssistantSettings(apply_min_score=70, below_line="skip"))) is True
    assert rules.drops_below_line(70, eff(AssistantSettings(apply_min_score=70, below_line="skip"))) is False
    assert rules.drops_below_line(None, eff(AssistantSettings(apply_min_score=70, below_line="skip"))) is False
    assert rules.drops_below_line(40, eff(AssistantSettings(apply_min_score=70, below_line="ask"))) is False
    assert rules.drops_below_line(40, eff(AssistantSettings(apply_min_score=70, below_line="auto"))) is False
    assert rules.drops_below_line(40, eff(AssistantSettings(below_line="skip"))) is False, "no line: don't care"


def test_search_brief_follows_memory_never_a_saved_answer():
    """A saved "UK sponsorship = Yes" and Memory's GB row (no sponsorship needed): the brief follows Memory."""
    info = UserProfile().user_info
    info.right_to_work = {"countries": [
        {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False, "remote_ok": True,
         "onsite": "yes", "instructions": ["Hold the visa question until they ask"]},
    ]}
    info.answers = [
        {"question": "Do you need UK visa sponsorship?", "answer": "Yes", "approved": True, "topic": "sponsorship.GB"},
    ]
    assert "answers" not in inspect.signature(build_search_brief).parameters
    brief = build_search_brief(info.right_to_work, "GB", info.logistics)
    (row,) = brief["rows"]
    assert row["needs_sponsorship"] is False and brief["work_from"] == ["GB"]
    assert all("visa" not in line for line in row["counts_if"]), "Memory says no visa is needed in GB"
    assert row["instructions"] == ["Hold the visa question until they ask"], "the country page's instructions ride"
    text = json.dumps(brief)
    assert "Do you need UK visa sponsorship" not in text and "sponsorship.GB" not in text


def test_an_old_saved_work_from_answer_no_longer_closes_the_gap():
    rtw = {"countries": [{"country": "IN", "work_authorization": "citizen", "remote_ok": True, "onsite": "no"}]}
    saved = [{"question": "Would you work from IN?", "answer": "Yes", "approved": True, "topic": "work_from.IN"}]
    assert "work_from.IN" not in json.dumps(build_search_brief(rtw, "GB", {"answers": saved}))
    gaps = build_search_brief(rtw, "GB", {})["gaps"]
    assert [(g["path"], g["field"]) for g in gaps] == [("user_info.logistics", "willing_to_relocate")]


# ── the queue keeps to the line (real Postgres) ────────────────────────────────


@pytest.mark.asyncio
async def test_skip_below_the_line_leaves_the_queue_recorded_as_skipped_with_its_score(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        await _seed(client, fixture_user_id, apply_min_score=70, below_line="skip")
        low, high = await _bring(client, 1), await _bring(client, 2)
        assert low["queued"] is True and high["queued"] is True
        low_fit = await _fit(client, low["application_id"], 40)
        high_fit = await _fit(client, high["application_id"], 80)
        assert low_fit["line"]["switch"] == "skip" and "took it off your batch queue" in low_fit["line"]["next"]
        assert "took it off" not in high_fit["line"]["next"]
        assert await _queued_ids(client) == [high["application_id"]]
        detail = await _detail(client, low["application_id"])
        assert detail["status"] == "considering" and detail["fit"]["score"] == 40, "the score is kept"
        (dropped,) = [e for e in detail["events"] if e["event_type"] == "unqueued"]
        assert dropped["recorded_by"] == "system" and dropped["payload"] == {"reason": "other"}
        assert dropped["detail"].startswith("Skipped: fit 40 is below your score line (70)")
        # Brought again with queue=true: still not queued.
        again = await _bring(client, 1)
        assert again["application_id"] == low["application_id"] and again["queued"] is False
        assert "below your score line" in again["queue_note"]


@pytest.mark.asyncio
@pytest.mark.parametrize("switch", ["ask", "auto"])
async def test_ask_and_auto_below_the_line_stay_queued(authenticated_async_context, fixture_user_id, switch):
    async with authenticated_async_context() as client:
        await _seed(client, fixture_user_id, apply_min_score=70, below_line=switch)
        brought = await _bring(client, 1)
        fit = await _fit(client, brought["application_id"], 40)
        assert fit["line"]["switch"] == switch and "took it off" not in fit["line"]["next"]
        assert await _queued_ids(client) == [brought["application_id"]]
        events = [e["event_type"] for e in (await _detail(client, brought["application_id"]))["events"]]
        assert "unqueued" not in events


@pytest.mark.asyncio
async def test_no_line_set_changes_nothing(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        await _seed(client, fixture_user_id, below_line="skip")
        brought = await _bring(client, 1)
        fit = await _fit(client, brought["application_id"], 10)
        assert fit["line"]["score_line"] is None and fit["line"]["side"] == "unknown"
        assert await _queued_ids(client) == [brought["application_id"]]


@pytest.mark.asyncio
async def test_an_old_row_queued_below_the_line_gets_no_cv_and_no_batch(authenticated_async_context, fixture_user_id):
    """Queued with fit 35 while below_line was ask (as before S4), then the user's rule is skip: nothing is
    rewritten, but the open queue leaves it out, so the batch is not due and no run writes it a CV."""
    async with authenticated_async_context() as client:
        await _seed(client, fixture_user_id, apply_min_score=70, below_line="ask", submit_mode="auto_when_sure")
        old = await _bring(client, 1)
        await _fit(client, old["application_id"], 35)
        assert await _queued_ids(client) == [old["application_id"]]
        before = (await client.get("/api/schedule/due")).json()
        assert "batch" in [d["duty"] for d in before["due"]], "a queued job with no CV is batch work"
        await _seed(client, fixture_user_id, apply_min_score=70, below_line="skip", submit_mode="auto_when_sure")
        assert await _queued_ids(client) == []
        after = (await client.get("/api/schedule/due")).json()
        assert "batch" not in [d["duty"] for d in after["due"]]
        events = [e["event_type"] for e in (await _detail(client, old["application_id"]))["events"]]
        assert "unqueued" not in events, "an old row is left out on read, never rewritten"
