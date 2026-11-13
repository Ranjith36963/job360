"""A CV that still needs a review, or a fix it has tries left for, is the ASSISTANT's turn (owner 2026-11-13).

Production 2026-11-13: 209 queued jobs led Needs you with "approve the CV" though 94 had no review yet and 115 a weak
one with tries left; 199 waited on nothing else from the owner. Now such a job's blocker is ``review_cv`` (no
review) or ``fix_cv`` (weak, tries left), actor ``assistant``: it is not on Needs you, not on the Approve CV card,
not in the batch's waiting-on-you tally, and the CV task (``cv_work``) still lists it. A strong review, or a weak
one with no tries left, still reaches the owner as ``approve_cv``. The submit gate is untouched: check_submit still
refuses a CV the user has not approved. Helpers are copied from tests/test_strong_review_clears_cv.py.
"""
from __future__ import annotations

import hashlib
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.profile.models import AssistantSettings, CVData, UserProfile
from tests._cv_review import ATS_RAW, ATS_TAIL, ats_rows, blank_ad

RAW = (
    "Ada Lovelace\nEXPERIENCE\nData engineer at Northwind since 2019.\n"
    "Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.\n" + ATS_RAW
)
CV = (
    "Ada Lovelace\nada@example.com | +44 7700 900123 | linkedin.com/in/ada\nEXPERIENCE\n"
    "- Data engineer at Northwind since 2019.\n"
    "- Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.\n" + ATS_TAIL
)
L1 = {"n": 1, "verdict": "supported", "evidence": ["Data engineer at Northwind since 2019."]}
L2 = {"n": 2, "verdict": "supported", "evidence": ["Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%."]}
APPLY = "https://northwind.example/careers/7"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text=RAW, name="Ada Lovelace")), user_id, source_action="cv_upload")
    save_assistant_settings(user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "seed")


async def _setup(client: AsyncClient, *, cv_auto: bool = True) -> tuple[int, int]:
    """Auto mode, so the batch's waiting-on-you tally is counted (it is 0 by design in confirm mode)."""
    edits = [{"path": "assistant_settings.submit_mode", "value": "auto_when_sure"},
             {"path": "assistant_settings.cv_auto_approve", "value": cv_auto}]
    assert (await client.patch("/api/profile", json={"edits": edits})).status_code == 200
    brought = await client.post("/api/jobs/bring", json={
        "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "queue": True,
        "apply_url": APPLY, "description": "Build the pipelines. Python, Snowflake.",
    })
    assert brought.status_code == 200, brought.text
    app_id = int(brought.json()["application_id"])
    await blank_ad(app_id)
    return app_id, await _save_cv(client, app_id, CV)


async def _save_cv(client: AsyncClient, app_id: int, text: str) -> int:
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": text})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["artifact_id"])


async def _review(client: AsyncClient, app_id: int, cv_id: int, text: str = CV, confidence: str = "strong") -> str:
    payload = {"artifact_id": cv_id, "sha256": sha(text), "confidence": confidence, "lines": [L1, L2, *ats_rows(3)],
               "reviewer": "fresh helper"}
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "cv_reviewed", "payload": payload})
    assert resp.status_code == 201, resp.text
    events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
    return next(e for e in events if e["id"] == resp.json()["event_id"])["payload"]["confidence"]


async def _weak_versions(client: AsyncClient, app_id: int, first_cv: int, n: int) -> None:
    """``n`` CV versions in all, each reviewed weak (the first is ``first_cv``, already saved as ``CV``)."""
    assert await _review(client, app_id, first_cv, confidence="weak") == "weak"
    for i in range(1, n):
        text = CV.replace("Ada Lovelace\n", f"Ada Lovelace{' ' * i}\n", 1)
        cv_id = await _save_cv(client, app_id, text)
        assert await _review(client, app_id, cv_id, text, confidence="weak") == "weak"


async def _state(client: AsyncClient, app_id: int, user_id: str) -> dict[str, Any]:
    from src.api.dependencies import get_db
    from src.services.applications import cv_work

    row = next(a for a in (await client.get("/api/applications")).json()["applications"] if a["id"] == app_id)
    cards = (await client.get("/api/cv-to-approve")).json()["items"]
    due = (await client.get("/api/schedule/due")).json()
    count = (await client.get("/api/needs-you-count")).json()
    work = await cv_work.cv_work(await get_db(), user_id, include_profile=False)
    first = row["blockers"][0] if row["blockers"] else {}
    return {
        "first": (first.get("kind"), first.get("actor")), "kinds": [b["kind"] for b in row["blockers"]],
        "card": any(c["application_id"] == app_id for c in cards), "waiting": due["waiting_on_you"],
        "on_needs_you": app_id in count["job_ids"], "cv_work": [i["application_id"] for i in work["items"]],
    }


def _owners_turn(s: dict[str, Any], app_id: int) -> bool:
    return (s["first"] == ("approve_cv", "you") and s["card"] and s["waiting"] == 1 and s["on_needs_you"]
            and app_id not in s["cv_work"])


def _assistants_turn(s: dict[str, Any], app_id: int, kind: str) -> bool:
    return (s["first"] == (kind, "assistant") and "approve_cv" not in s["kinds"] and not s["card"]
            and s["waiting"] == 0 and not s["on_needs_you"] and app_id in s["cv_work"])


@pytest.mark.asyncio
async def test_a_cv_with_no_review_is_the_assistants_review_not_on_needs_you(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, _cv = await _setup(client)
        s = await _state(client, app_id, fixture_user_id)
        assert _assistants_turn(s, app_id, "review_cv"), s


@pytest.mark.asyncio
async def test_a_weak_review_with_tries_left_is_the_assistants_fix(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        await _weak_versions(client, app_id, cv_id, 3)  # 2 older judged versions: 1 try left
        s = await _state(client, app_id, fixture_user_id)
        assert _assistants_turn(s, app_id, "fix_cv"), s


@pytest.mark.asyncio
async def test_a_weak_review_with_no_tries_left_reaches_the_owner(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        await _weak_versions(client, app_id, cv_id, 4)  # 3 older judged versions: no tries left
        s = await _state(client, app_id, fixture_user_id)
        assert _owners_turn(s, app_id), s


@pytest.mark.asyncio
async def test_a_strong_review_not_auto_cleared_reaches_the_owner(authenticated_async_context, fixture_user_id):
    """cv_auto_approve off: a strong review never clears the CV (kit.reviews_clearing), so the owner approves it."""
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client, cv_auto=False)
        assert await _review(client, app_id, cv_id) == "strong"
        s = await _state(client, app_id, fixture_user_id)
        assert _owners_turn(s, app_id), s


@pytest.mark.asyncio
async def test_check_submit_still_refuses_a_cv_the_user_never_approved(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, _cv = await _setup(client)
        assert (await _state(client, app_id, fixture_user_id))["first"] == ("review_cv", "assistant")
        fit = await client.put(f"/api/applications/{app_id}/fit", json={"score": 80, "verdict": "ok", "score_source": "full_ad"})
        assert fit.status_code == 200, fit.text  # the gate reads a fit score above the seeded line of 50
        for event_type, payload in (("form_filled", {"form_url": APPLY, "fields_count": 1}),
                                    ("form_swept", {"fields_checked": 12, "fixed": 1})):
            resp = await client.post(f"/api/applications/{app_id}/events",
                                     json={"event_type": event_type, "payload": payload})
            assert resp.status_code == 201, resp.text
        got = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": APPLY})
        assert got.status_code == 200, got.text
        assert got.json()["decision"] != "submit" and got.json()["reason"] == "cv_not_seen", got.json()
