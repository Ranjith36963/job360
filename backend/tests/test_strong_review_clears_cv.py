"""A strong review of the exact CV counts as "the CV is approved" everywhere it is shown (owner 2026-11-09).

With ``assistant_settings.cv_auto_approve`` on, the newest stored ``cv_reviewed`` of the newest CV (id + sha256) being
``strong`` removes ``approve_cv`` from the application's blockers, the Needs-you "Approve CV" card and its count, and
the batch's waiting-on-you tally (so the batch is due). One rule, ``kit.reviews_clearing``. A weak review, a review of
an older version, a CV the save checks flag, and the setting off all still need the user. Helpers are copied.
"""
from __future__ import annotations

import hashlib
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.profile.models import AssistantSettings, CVData, UserProfile

pytestmark = pytest.mark.usefixtures("rules5_review_checks")  # rules-6 checks: test_cv_review_benchmark.py

RAW = (
    "Ada Lovelace\nEXPERIENCE\nData engineer at Northwind since 2019.\n"
    "Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.\n"
)
CV = (
    "Ada Lovelace\nada@example.com | +44 7700 900123 | linkedin.com/in/ada\nEXPERIENCE\n"
    "- Data engineer at Northwind since 2019.\n"
    "- Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.\n"
)
# One row per review line (rules 5): n = its number in review.lines, the reviewer's verdict, verbatim profile quotes.
L1 = {"n": 1, "verdict": "supported", "evidence": ["Data engineer at Northwind since 2019."]}
L2 = {"n": 2, "verdict": "supported", "evidence": ["Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%."]}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text=RAW, name="Ada Lovelace")), user_id, source_action="cv_upload")
    save_assistant_settings(user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "seed")


async def _setup(client: AsyncClient, *, cv_auto: bool = True, cv: str = CV, auto: bool = True) -> tuple[int, int]:
    edits = [{"path": "assistant_settings.submit_mode", "value": "auto_when_sure"}] if auto else []
    edits += [{"path": "assistant_settings.cv_auto_approve", "value": True}] if cv_auto else []
    if edits:
        assert (await client.patch("/api/profile", json={"edits": edits})).status_code == 200
    brought = await client.post("/api/jobs/bring", json={
        "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "queue": True,
        "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines. Python, Snowflake.",
    })
    assert brought.status_code == 200, brought.text
    app_id = int(brought.json()["application_id"])
    return app_id, await _save_cv(client, app_id, cv)


async def _save_cv(client: AsyncClient, app_id: int, text: str) -> int:
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": text})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["artifact_id"])


async def _review(client: AsyncClient, app_id: int, cv_id: int, text: str = CV, confidence: str = "strong") -> str:
    payload = {"artifact_id": cv_id, "sha256": sha(text), "confidence": confidence, "lines": [L1, L2],
               "reviewer": "fresh helper"}
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "cv_reviewed", "payload": payload})
    assert resp.status_code == 201, resp.text
    events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
    return next(e for e in events if e["id"] == resp.json()["event_id"])["payload"]["confidence"]


async def _state(client: AsyncClient, app_id: int) -> dict[str, Any]:
    row = next(a for a in (await client.get("/api/applications")).json()["applications"] if a["id"] == app_id)
    cards = (await client.get("/api/cv-to-approve")).json()["items"]
    due = (await client.get("/api/schedule/due")).json()
    return {
        "blockers": [b["kind"] for b in row["blockers"]],
        "card": any(c["application_id"] == app_id for c in cards),
        "waiting": due["waiting_on_you"], "hold": due["batch_hold"],
        "batch_due": any(d["duty"] == "batch" for d in due["due"]),
    }


def _needs_you(s: dict[str, Any]) -> bool:
    return "approve_cv" in s["blockers"] and s["card"] and s["waiting"] == 1 and s["hold"] == "waiting_on_you"


@pytest.mark.asyncio
async def test_a_strong_review_of_the_current_cv_clears_every_place_it_is_shown(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        assert _needs_you(await _state(client, app_id)) and not (await _state(client, app_id))["batch_due"]
        count_before = (await client.get("/api/needs-you-count")).json()["n"]
        assert await _review(client, app_id, cv_id) == "strong"
        s = await _state(client, app_id)
        assert "approve_cv" not in s["blockers"] and s["card"] is False
        assert (s["waiting"], s["hold"], s["batch_due"]) == (0, None, True)
        assert (await client.get("/api/needs-you-count")).json()["n"] == count_before - 1


@pytest.mark.asyncio
async def test_a_weak_review_still_needs_the_user(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        assert await _review(client, app_id, cv_id, confidence="weak") == "weak"
        assert _needs_you(await _state(client, app_id))


@pytest.mark.asyncio
async def test_a_review_of_an_older_version_or_sha_does_not_clear_the_newer_cv(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, v1 = await _setup(client)
        assert await _review(client, app_id, v1) == "strong"
        assert "approve_cv" not in (await _state(client, app_id))["blockers"]
        v2 = CV.replace("- Data engineer", "* Data engineer")
        await _save_cv(client, app_id, v2)
        assert _needs_you(await _state(client, app_id)), "the review names v1, not the newest CV"


@pytest.mark.asyncio
async def test_a_cv_the_save_checks_flag_stays_with_the_user(authenticated_async_context, fixture_user_id):
    flagged = CV + "- Cut costs by 94% across 12 clusters.\n"  # numbers the profile never says
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client, cv=flagged)
        assert await _review(client, app_id, cv_id, flagged) == "weak"  # cv_checks_found downgrades it
        assert _needs_you(await _state(client, app_id))


@pytest.mark.asyncio
async def test_the_setting_off_never_clears(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client, cv_auto=False)
        assert await _review(client, app_id, cv_id) == "strong"
        assert _needs_you(await _state(client, app_id))


@pytest.mark.asyncio
async def test_the_setting_turned_off_again_stops_the_review_counting(
    authenticated_async_context, fixture_user_id
):
    from src.services.profile.storage import load_profile

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        assert await _review(client, app_id, cv_id) == "strong"
        assert "approve_cv" not in (await _state(client, app_id))["blockers"]
        off = await client.patch("/api/profile", json={"edits": [
            {"path": "assistant_settings.cv_auto_approve", "value": False}]})
        assert off.status_code == 200, off.text
        assert load_profile(fixture_user_id).assistant_settings.cv_auto_approve is False
        assert _needs_you(await _state(client, app_id))


def test_every_place_that_shows_or_counts_the_cv_approval_uses_the_one_shared_rule():
    """Defence in depth: the blocker, the Needs-you card (and so the batch's due count and the stats tally, which
    read ``ready.cv_to_approve``) and the receipt gate all call ``kit.reviews_clearing``; none re-implements it."""
    import inspect

    from src.services.applications import blockers, kit, ready
    from src.services.profile import assistant_settings, schedule

    assert "kit.reviews_clearing(" in inspect.getsource(blockers._fill)
    assert "kit.reviews_clearing(" in inspect.getsource(ready.cv_to_approve)
    assert "reviews_clearing(" in inspect.getsource(kit.cv_cleared_by)
    assert "ready.cv_to_approve(" in inspect.getsource(schedule._waiting_on_you)
    assert "reviews_clearing(" in inspect.getsource(kit._gate_cv_review)  # check_submit's cv_review fact
    assert "_gate_cv_review(" in inspect.getsource(kit.gate_facts)
    for mod in (blockers, ready, schedule):
        assert "cv_reviewed" not in inspect.getsource(mod), f"{mod.__name__} must not read reviews itself"
    rules = inspect.getsource(assistant_settings)
    assert "newest_review(" not in rules and "review_state(" not in rules  # the gate rule reads the fact only


@pytest.mark.asyncio
async def test_a_strong_review_of_a_cv_whose_stored_checks_flag_unsupported_claims_does_not_clear(
    authenticated_async_context, fixture_user_id
):
    """The helper's own unsupported branch: the API would downgrade such a review, so the checks are written
    straight onto the stored CV after a real strong review."""
    import json

    from src.api.dependencies import get_db

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        assert await _review(client, app_id, cv_id) == "strong"
        assert "approve_cv" not in (await _state(client, app_id))["blockers"]
        db = await get_db()
        for code in ("unsupported_numbers", "unsupported_terms"):
            await db._db.execute(
                "UPDATE application_artifacts SET checks_json = ? WHERE id = ? AND user_id = ?",
                (json.dumps([{"code": code, "detail": "x"}]), cv_id, fixture_user_id),
            )
            await db._db.commit()
            assert _needs_you(await _state(client, app_id)), code


@pytest.mark.asyncio
async def test_confirm_mode_still_shows_approve_cv_even_with_a_strong_review(
    authenticated_async_context, fixture_user_id
):
    """The gate uses a strong review only in auto mode (confirm mode asks every submit), so the cards agree."""
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client, auto=False)
        assert await _review(client, app_id, cv_id) == "strong"
        s = await _state(client, app_id)  # no batch runs in confirm mode, so only the list and the card apply
        assert "approve_cv" in s["blockers"] and s["card"] is True


@pytest.mark.asyncio
async def test_the_clearing_is_logged_at_the_gate_not_on_every_read(
    authenticated_async_context, fixture_user_id, caplog
):
    import logging

    def hits() -> list[logging.LogRecord]:
        return [r for r in caplog.records if r.getMessage() == "cv_cleared_by_review"]

    caplog.set_level(logging.INFO)
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        fit = await client.put(f"/api/applications/{app_id}/fit", json={"score": 80, "verdict": "ok", "score_source": "full_ad"})
        assert fit.status_code == 200, fit.text
        form = "https://careers.northwind.example/apply/7"
        for event_type, payload in (("form_filled", {"form_url": form, "fields_count": 1}),
                                    ("form_swept", {"fields_checked": 1, "fixed": 0})):
            assert (await client.post(f"/api/applications/{app_id}/events",
                                      json={"event_type": event_type, "payload": payload})).status_code == 201
        assert await _review(client, app_id, cv_id) == "strong"
        caplog.clear()
        for path in ("/api/applications", "/api/cv-to-approve", "/api/schedule/due", "/api/needs-you-count"):
            assert (await client.get(path)).status_code == 200
        assert hits() == [], "a read path must not log the clearing"
        gate = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": form})
        assert gate.status_code == 200 and gate.json()["reason"] != "cv_not_seen", gate.text
        got = hits()
        assert got and {(r.application_id, r.artifact_id, r.reason) for r in got} == {(app_id, cv_id, "strong_review")}
