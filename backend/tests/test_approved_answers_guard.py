"""Owner 2026-11-19: an assistant never removes an answer the user approved.

At 10:42 an assistant redrafting 21 work-status guesses sent the whole ``user_info.answers`` list with only
those 21; the 71 answers the user had approved fell out of the live profile (164 -> 21)."""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

_APPROVED = [
    {"question": "May we contact you for 2 years?", "answer": "Yes", "approved": True, "topic": "consent.contact_2y"},
    {"question": "Why do you like remote work?", "answer": "Deep focus", "approved": True,
     "topic": "why_remote"},
]
_DRAFT = {"question": "Are you legally allowed to work in NL?", "answer": "needs sponsorship", "approved": False,
          "topic": "right_to_work.NL.work_authorization", "country": "NL"}


def _answers(edit: dict[str, Any]) -> dict[str, Any]:
    return {"edits": [{"path": "user_info.answers", **edit}]}


@pytest.mark.asyncio
async def test_an_assistant_can_never_drop_an_answer_the_user_approved(authenticated_async_context):
    from src.api.main import app

    async with authenticated_async_context() as client:
        saved = await client.patch("/api/profile", json=_answers({"value": _APPROVED}))
        assert saved.status_code == 200, saved.text
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as agent:
        # The 10:42 write: the whole list, holding only the new guess.
        whole = await agent.patch("/api/profile", json=_answers({"value": [_DRAFT]}))
        assert whole.status_code == 422, whole.text
        assert "would remove or change 2 answer(s) the user approved" in whole.text
        dropped = await agent.patch("/api/profile", json=_answers({"op": "remove_at", "index": 0}))
        assert dropped.status_code == 422, dropped.text
        # Review of this fix: turning an approved answer into a draft (step one of a two-call removal).
        flip = await agent.patch("/api/profile", json=_answers({"op": "upsert", "value": {
            **_APPROVED[0], "answer": "No", "approved": False}}))
        assert flip.status_code == 422, flip.text
        # Second review: the text of an approved answer changed while it stays approved.
        reword = await agent.patch("/api/profile", json=_answers({"op": "upsert", "value": {
            **_APPROVED[0], "answer": "No"}}))
        assert reword.status_code == 422, reword.text
        # ...unless it is the user's own word from the chat, relayed (source user_said).
        relayed = await agent.patch("/api/profile", json=_answers({"op": "upsert", "source": "user_said",
                                                                    "value": {**_APPROVED[1], "answer": "Two weeks"}}))
        assert relayed.status_code == 200, relayed.text
        # The right way: one upsert; every approved answer stays.
        upsert = await agent.patch("/api/profile", json=_answers({"op": "upsert", "value": _DRAFT}))
        assert upsert.status_code == 200, upsert.text
        # Resending the whole list WITH every approved answer is still fine.
        full = (await agent.get("/api/profile")).json()["user_info"]["answers"]
        assert {r["topic"] for r in full} == {"consent.contact_2y", "why_remote",
                                              "right_to_work.NL.work_authorization"}
        again = await agent.patch("/api/profile", json=_answers({"value": full}))
        assert again.status_code == 200, again.text
    async with authenticated_async_context() as client:
        mine = await client.patch("/api/profile", json=_answers({"op": "remove_at", "index": 0}))
        assert mine.status_code == 200, "the user removes their own answer"


@pytest.mark.asyncio
async def test_the_guard_matches_one_to_one_on_the_saved_values(authenticated_async_context):
    """Review of this fix: each approved answer needs its own match (one kept never covers two), and a
    country written out ("Netherlands") is judged after it is stored as NL, so a correct resend is never
    refused. (The store itself refuses the same question twice, so exact twins cannot be stored.)"""
    from src.api.main import app

    twins = [{"question": "Notice period?", "answer": "1 month", "approved": True, "company": "Acme"},
             {"question": "Visa?", "answer": "needs sponsorship", "approved": True, "country": "NL"}]
    async with authenticated_async_context() as client:
        assert (await client.patch("/api/profile", json=_answers({"value": twins}))).status_code == 200
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as agent:
        spelled = [twins[0], {**twins[1], "country": "Netherlands"}]
        ok = await agent.patch("/api/profile", json=_answers({"value": spelled}))
        assert ok.status_code == 200, ok.text
        one = await agent.patch("/api/profile", json=_answers({"value": [twins[0]]}))
        assert one.status_code == 422 and "would remove or change 1 answer(s)" in one.text, one.text


@pytest.mark.asyncio
async def test_a_draft_twin_or_a_company_spelling_never_locks_the_assistant_out(authenticated_async_context):
    """Second review: a guess stored ahead of an approved answer to the same topic, or the company written
    "Acme" for a saved "Acme Ltd", is not a drop - an unchanged resend still saves."""
    from src.api.main import app

    stored = [{"question": "Need sponsorship in GB?", "answer": "No", "approved": False,
               "topic": "sponsorship.GB.yes_no", "country": "GB"},
              {"question": "Need sponsorship in GB?", "answer": "No", "approved": True,
               "topic": "sponsorship.GB.yes_no"},
              {"question": "Why Acme?", "answer": "Their product.", "approved": True, "company": "Acme Ltd"}]
    async with authenticated_async_context() as client:
        saved = await client.patch("/api/profile", json=_answers({"value": stored}))
        assert saved.status_code == 200, saved.text
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as agent:
        current = (await agent.get("/api/profile")).json()["user_info"]["answers"]
        same = await agent.patch("/api/profile", json=_answers({"value": current}))
        assert same.status_code == 200, same.text
        renamed = [{**r, "company": "Acme"} if r.get("company") else r for r in current]
        ok = await agent.patch("/api/profile", json=_answers({"value": renamed}))
        assert ok.status_code == 200, ok.text
