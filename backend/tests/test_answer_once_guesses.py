"""Answer once (owner 2026-11-02): every questionnaire row comes with a GUESS already filled in, read off the
profile (links, saved age, education...) or drafted by the assistant. A guess is never verified; no source
field means value "" and the row is still asked (rule #29). Real HTTP doors, VALUE asserts (rule #21)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

GB = {"path": "user_info.right_to_work", "value": {"countries": [
    {"country": "GB", "work_authorization": "visa", "needs_sponsorship": True}]}}
PORTFOLIO, GITHUB, LINKEDIN = "https://ada.dev", "https://github.com/ada", "https://www.linkedin.com/in/ada"
THIS_YEAR = datetime.now(timezone.utc).year


@pytest.fixture(autouse=True)
def _every_row(monkeypatch):
    """The card shows 10 rows a country; these tests look at single rows, so lift the cap."""
    from src.services.profile import ask_once

    monkeypatch.setattr(ask_once, "PER_COUNTRY", 99)


async def _save(client: AsyncClient, *edits: dict[str, Any]) -> None:
    resp = await client.patch("/api/profile", json={"edits": [GB, *edits]})
    assert resp.status_code == 200, resp.text


async def _asked(client: AsyncClient) -> dict[str, dict[str, Any]]:
    resp = await client.get("/api/profile/questionnaire")
    assert resp.status_code == 200, resp.text
    (gb,) = resp.json()["countries"]
    return {q["id"]: q for q in gb["questions"]}


@pytest.mark.asyncio
async def test_website_is_the_portfolio_then_github_then_linkedin(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _save(client, {"path": "cv_data.links", "value": [GITHUB, PORTFOLIO, LINKEDIN]})
        row = (await _asked(client))["website"]
        assert (row["value"], row["from"], row["label"], row["verified"]) == (
            PORTFOLIO, "from your portfolio link", "guessed", False)
        assert row["answer"] == PORTFOLIO and row["set_by"] is None
        await _save(client, {"path": "cv_data.links", "value": [GITHUB]})
        row = (await _asked(client))["website"]
        assert (row["value"], row["from"]) == (GITHUB, "from your GitHub link")
        await _save(client, {"path": "cv_data.links", "value": [LINKEDIN]})
        assert (await _asked(client))["website"]["value"] == LINKEDIN


@pytest.mark.asyncio
async def test_no_links_means_no_guess_and_the_row_is_still_asked(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _save(client)
        row = (await _asked(client))["website"]
        assert (row["value"], row["from"], row["answer"], row["label"]) == ("", "", None, "not_known")


@pytest.mark.asyncio
async def test_over_18_from_a_saved_age_else_from_the_education(authenticated_async_context):
    year = THIS_YEAR - 5
    async with authenticated_async_context() as client:
        await _save(client)
        assert (await _asked(client))["over_18"]["value"] == "", "no age and no education: nothing to guess"
        await _save(client, {"path": "cv_data.education", "value": [f"BSc Physics, Leeds, {year - 3}-{year}"]})
        row = (await _asked(client))["over_18"]
        assert (row["value"], row["from"], row["verified"]) == ("Yes", f"from your education (degree {year})", False)
        await _save(client, {"path": "user_info.answers", "op": "upsert", "value": {
            "question": "How old are you?", "answer": "30", "approved": True, "topic": "age"}})
        row = (await _asked(client))["over_18"]
        assert (row["value"], row["from"]) == ("Yes", "from your saved age (30)")


@pytest.mark.asyncio
async def test_a_recent_degree_alone_does_not_prove_age(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _save(client, {"path": "cv_data.education", "value": [f"BSc Physics, Leeds, {THIS_YEAR - 1}"]})
        assert (await _asked(client))["over_18"]["value"] == ""


@pytest.mark.asyncio
async def test_other_rows_are_read_off_the_profile(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _save(
            client,
            {"path": "user_info.right_to_work", "value": {"countries": [
                {"country": "GB", "work_authorization": "citizen"}]}},
            {"path": "cv_data.location", "value": "Leeds, United Kingdom"},
            {"path": "user_info.logistics", "value": {"notice_period": "1 month"}},
            {"path": "preferences.work_arrangement", "value": "hybrid"},
        )
        rows = await _asked(client)
        got = {k: (r["value"], r["from"]) for k, r in rows.items()}
        assert got["needs_sponsorship.GB"] == ("No", "from your saved right to work in GB")
        assert got["city"] == ("Leeds", "from your CV location")
        assert got["residence_country"] == ("GB", "from your CV location")
        assert got["earliest_start"] == ("After my notice period (1 month)", "from your notice period")
        assert got["office_days.GB"] == ("Yes", "from your work arrangement (hybrid)")
        assert all(not r["verified"] for r in rows.values() if r["from"].startswith("from "))


@pytest.mark.asyncio
async def test_an_assistant_saved_guess_is_prefilled_guessed_and_never_verified(authenticated_async_context):
    from src.api.main import app

    async with authenticated_async_context() as client:
        await _save(client)
        assert (await _asked(client))["travel.GB"]["value"] == ""
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"Authorization": f"Bearer {token}"}) as agent:
            put = await agent.patch("/api/profile", json={"edits": [
                {"path": "user_info.logistics", "value": {"countries": [{"country": "GB", "travel_ok_pct": 25}]},
                 "source": "agent_inferred"}]})
            assert put.status_code == 200, put.text
        row = (await _asked(client))["travel.GB"]
        assert (row["value"], row["from"], row["label"], row["verified"]) == (
            "25", "your assistant's guess", "guessed", False)


@pytest.mark.asyncio
async def test_remembering_a_guessed_row_makes_it_verified(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _save(client, {"path": "cv_data.links", "value": [PORTFOLIO]})
        row = (await _asked(client))["website"]
        resp = await client.post("/api/profile/questionnaire/remember", json={"country": "GB", "items": [
            {"topic": row["topic"], "question": row["question"], "answer": row["value"]}]})
        assert resp.status_code == 200 and resp.json() == {"saved": ["contact.website"]}, resp.text
        assert "website" not in await _asked(client), "answered once: not asked again"
        rows = {r["id"]: r for r in (await client.get("/api/profile/common-questions")).json()["questions"]}
        assert (rows["website"]["answer"], rows["website"]["verified"], rows["website"]["label"]) == (
            PORTFOLIO, True, "memory")
