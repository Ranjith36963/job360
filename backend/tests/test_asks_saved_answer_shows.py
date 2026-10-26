"""Owner 2026-10-26 - a sensitive answer the user saves on Needs you never "comes back" as the old guess.

On production, editing gender / ethnicity / consent on a Needs-you card and reloading showed the
assistant's older value again. The user's own Needs-you read now shows the answer the user saved
(approved, same topic or wording, same company scope) on the open ask's row, as source memory; an
assistant's read is unchanged. Helpers are defined here, never imported.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

_AD = {"title": "AI Engineer", "company": "AppZen", "location": "London", "apply_url": "https://jobs.lever.co/appzen/1",
       "description": "Build agents. " * 8}


@pytest.mark.asyncio
async def test_the_users_saved_gender_shows_on_the_open_row(authenticated_async_context):
    from src.api.main import app

    async with authenticated_async_context() as client:
        app_id = int((await client.post("/api/jobs/bring", json=_AD)).json()["application_id"])
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        agent = AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                            headers={"Authorization": f"Bearer {token}"})
        async with agent:
            made = await agent.post("/api/asks", json={"question": "AppZen form", "application_id": app_id,
                                                       "kind": "question", "questions": [
                {"key": "gender", "question": "Gender?", "source": "guess", "answer": "Male"},
                {"key": "city", "question": "City?", "source": "guess", "answer": "London"}]})
            assert made.status_code == 201, made.text
            saved = await client.patch("/api/profile", json={"edits": [{"path": "user_info.answers", "op": "upsert",
                "value": {"question": "Gender?", "answer": "Female", "approved": True, "topic": "equality.gender"}}]})
            assert saved.status_code == 200, saved.text
            [mine] = (await client.get("/api/asks")).json()["asks"]
            assert [(q["key"], q["answer"], q["source"]) for q in mine["questions"]] == [
                ("equality.gender", "Female", "memory"), ("location_city", "London", "guess")]
            [theirs] = (await agent.get("/api/asks")).json()["asks"]
            assert theirs["questions"][0]["answer"] == "Male"  # the stored ask itself is unchanged
