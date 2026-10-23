"""The kit's `last_fill` and the Approve CV list (owner decisions 2026-10-23, the ElevenLabs run).

* B: the cloud browser closed overnight while the form waited for the user's yes, and every answer had
  to be retyped field by field. The kit now hands back the newest `form_filled` answers as `last_fill`,
  so the assistant reopens the form and retypes them in one browser `fill`.
* C: the CV was never shown up front, so the send stopped at the end on `cv_not_seen`. Needs you lists
  every open application whose newest CV has no `cv_seen`; a `cv_seen` from chat OR the web clears it,
  for the list and for the gate.
Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

import hashlib
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.profile.models import AssistantSettings, CVData, UserPreferences, UserProfile

CV_TEXT = "Ada Lovelace\nSenior data engineer\nPython, dbt, Snowflake."
FORM = "https://careers.northwind.example/apply/7"
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake.",
}
_AD_2 = {**_AD, "title": "Platform Engineer", "company": "Southwind", "apply_url": "https://southwind.example/c/3"}


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python data engineer.", name="Ada Lovelace", location="London, UK"),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id, source_action="cv_upload",
    )
    save_assistant_settings(
        user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "test_seed"
    )


async def _bring(client: AsyncClient, ad: dict[str, Any] = _AD) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    app_id = int(resp.json()["application_id"])
    fit = await client.put(f"/api/applications/{app_id}/fit", json={"score": 80, "verdict": "ok", "score_source": "full_ad"})
    assert fit.status_code == 200, fit.text
    return app_id


async def _save_cv(client: AsyncClient, app_id: int, text: str = CV_TEXT) -> int:
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": text})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["artifact_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]):
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})


async def _kit(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}/kit")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


# ── B. last_fill ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_kit_hands_back_the_newest_fills_answers_as_last_fill(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        assert (await _kit(client, app_id))["last_fill"] == []   # nothing filled yet
        first = [{"question": "Full name", "answer": "Ada Lovelace", "source": "profile", "key": "contact.name"}]
        assert (await _event(client, app_id, "form_filled", {
            "form_url": FORM, "fields_count": 1, "answers": first,
        })).status_code == 201
        assert (await _kit(client, app_id))["last_fill"] == [{**first[0], "redacted": False}]

        newest = [
            {"question": "Full name", "answer": "Ada King", "source": "profile", "key": "contact.name"},
            {"question": "Why Northwind?", "answer": "I build pipelines.\nEvery day.", "source": "written"},
            # Sensitive and not remembered: stored as a hash (G023), so there is no value to hand back.
            {"question": "Do you need visa sponsorship?", "answer": "No", "source": "guessed"},
        ]
        assert (await _event(client, app_id, "form_filled", {
            "form_url": FORM, "fields_count": 3, "answers": newest,
        })).status_code == 201
        rows = (await _kit(client, app_id))["last_fill"]
        assert [r["question"] for r in rows] == ["Full name", "Why Northwind?", "Do you need visa sponsorship?"]
        assert rows[0]["answer"] == "Ada King" and rows[0]["key"] == "contact.name"
        assert rows[1] == {
            "question": "Why Northwind?", "answer": "I build pipelines.\nEvery day.", "source": "written",
            "key": None, "redacted": False,
        }
        assert rows[2]["answer"] == "" and rows[2]["redacted"] is True


def test_the_recipes_say_how_to_retype_last_fill_and_show_every_cv_first():
    from src.api.routes.recipes import load_recipe

    for name in ("forms", "apply"):
        text = load_recipe(name).text
        assert "last_fill" in text and "fill" in text and "check_submit" in text, name
        assert len(text.splitlines()) <= 70, name
    batch = load_recipe("batch").text
    assert "CV OK" in batch and "where" in batch and "chat" in batch and "Approve CV" in batch
    assert batch.index("CV OK") < batch.index("Work the queue")   # before any form is opened


# ── C. Approve CV ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_cv_lists_unseen_cvs_and_a_chat_or_web_cv_seen_clears_it(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        one, two, bare = await _bring(client), await _bring(client, _AD_2), await _bring(
            client, {**_AD, "title": "ML Engineer", "apply_url": "https://northwind.example/careers/8"},
        )
        cv_one, cv_two = await _save_cv(client, one), await _save_cv(client, two)
        listed = (await client.get("/api/cv-to-approve")).json()["items"]
        assert [r["application_id"] for r in listed] == [two, one]   # newest CV first; no CV, no row
        assert listed[0]["job_title"] == "Platform Engineer" and listed[0]["cv"]["artifact_id"] == cv_two
        assert bare not in [r["application_id"] for r in listed]

        # Chat: the assistant records the user's "CV OK".
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        async with _bearer_client(token) as agent:
            sha = hashlib.sha256(CV_TEXT.encode("utf-8")).hexdigest()
            seen = await _event(agent, one, "cv_seen", {"artifact_id": cv_one, "sha256": sha, "where": "chat"})
            assert seen.status_code == 201, seen.text
        # Web: the user's own click.
        assert (await client.post(f"/api/applications/{two}/cv-seen", json={"artifact_id": cv_two})).status_code == 201
        assert (await client.get("/api/cv-to-approve")).json()["items"] == []
        for app_id in (one, two):
            controls = (await client.get(f"/api/applications/{app_id}/controls")).json()
            assert controls["cv"]["seen"] is not None, app_id   # the gate's cv_not_seen reads this mark

        # A new CV version is unseen again.
        await _save_cv(client, one, CV_TEXT + "\nChanged.")
        assert [r["application_id"] for r in (await client.get("/api/cv-to-approve")).json()["items"]] == [one]


@pytest.mark.asyncio
async def test_the_gate_takes_a_chat_cv_seen_as_well_as_a_web_one(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        assert (await client.patch("/api/profile", json={"edits": [
            {"path": "assistant_settings.submit_mode", "value": "auto_when_sure"},
        ]})).status_code == 200
        app_id = await _bring(client)
        cv_id = await _save_cv(client, app_id)
        assert (await _event(client, app_id, "form_filled", {"form_url": FORM, "fields_count": 1})).status_code == 201
        assert (await _event(client, app_id, "form_swept", {"fields_checked": 1, "fixed": 0})).status_code == 201

        async def reason() -> str:
            resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
            return str(resp.json()["reason"])

        assert await reason() == "cv_not_seen"
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        async with _bearer_client(token) as agent:
            sha = hashlib.sha256(CV_TEXT.encode("utf-8")).hexdigest()
            assert (await _event(agent, app_id, "cv_seen", {
                "artifact_id": cv_id, "sha256": sha, "where": "chat",
            })).status_code == 201
        assert await reason() != "cv_not_seen"


@pytest.mark.asyncio
async def test_approve_cv_is_the_users_own_page_only(authenticated_async_context):
    async with authenticated_async_context() as client:
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
    async with _bearer_client(token) as agent:
        assert (await agent.get("/api/cv-to-approve")).status_code in (401, 403)
