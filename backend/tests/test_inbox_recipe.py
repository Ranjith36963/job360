"""The Gmail check's rules live in Job360, not the task prompt (owner 2026-11-17).

Root cause it guards: a scheduled Gmail run passed every open application (390+) to
``mark_inbox_checked`` in one call; ``services/applications/inbox.mark_checked`` refused
more than ``INBOX_CHECK_MAX_APPLICATIONS`` (200) with a 422, so the cursor only moved when
the prompt spelt out "chunks of 200" - and it broke again the day the rules left the prompt.
Now Job360 splits the list itself, the inbox duty points at its own recipe, and the recipe
carries every rule the prompt carried. Real doors (HTTP), values asserted (rule #21).
Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.api.routes.recipes import RECIPE_NAMES, load_recipe
from src.core import settings

AD = {"title": "Applied AI Engineer", "company": "Acme", "location": "Remote",
      "description": "Agents, RAG and evals. " * 10, "apply_url": "https://jobs.lever.co/acme/1"}


async def _bring(client: AsyncClient, n: int) -> list[int]:
    ids = []
    for i in range(n):
        resp = await client.post("/api/jobs/bring", json={**AD, "title": f"Applied AI Engineer {i}", "apply_url": f"https://jobs.lever.co/acme/{i}"})
        assert resp.status_code == 200, resp.text
        ids.append(int(resp.json()["application_id"]))
    return ids


async def _rows(client: AsyncClient) -> dict[int, Any]:
    rows = (await client.get("/api/applications", params={"limit": 200})).json()["applications"]
    return {r["id"]: r for r in rows}


@pytest.mark.asyncio
async def test_more_ids_than_the_statement_size_move_in_one_call(authenticated_async_context, monkeypatch):
    # 2 ids per statement and 5 applications = the 390-ids-at-200 failure in miniature.
    monkeypatch.setattr(settings, "INBOX_CHECK_MAX_APPLICATIONS", 2)
    async with authenticated_async_context() as client:
        ids = await _bring(client, 5)
        resp = await client.post("/api/applications/inbox-checked", json={"application_ids": ids})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["application_ids"] == sorted(ids)
        rows = await _rows(client)
        assert {rows[i]["inbox_checked_at"] for i in ids} == {body["inbox_checked_at"]}


def test_the_inbox_duty_runs_its_own_recipe():
    assert settings.SCHEDULE_DUTY_RECIPES["inbox"] == "inbox"
    assert "inbox" in RECIPE_NAMES


def test_the_inbox_recipe_carries_every_rule_the_prompt_carried():
    text = load_recipe("inbox").text
    for rule in (
        'whats_due(duties=["inbox"])',                       # claim, always with duties
        "after:<the day BEFORE search.gmail_after",          # the date search, a day back
        "Never `after:<unix seconds>`",                      # Gmail returns nothing for seconds
        "newer_than:1d -in:sent -in:drafts",                 # the empty-result sanity check
        "`nextPageToken`",                                   # paging by token only
        "even with exactly 50 threads",                      # a full page with no token is the last
        "Over 200 threads",
        "search.email_senders", "search.companies", "search.skip_message_ids",
        "match_email(sender, thread_id, subject)",
        "under `unmatched`", "no `kind`",
        "interview_scheduled", "a Gmail DRAFT only, never sent",
        "3 or more outcome emails",                          # the lesson rule
        "ALL of `search.application_ids` in ONE call",       # the cursor, never hand-chunked
        "exactly 3 lines",
        "never call `get_application_kit`".lower(),
    ):
        assert rule.lower() in text.lower(), rule
