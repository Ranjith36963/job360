"""A cv_reviewed whose rows sit one line late is refused before it is stored (production 2026-11-13, apps 455, 460:
an agent read the 1-based ``review.held`` as 0-based, so the header rows carried quotes and the bullet rows []; the
server only said no_evidence, the review counted as a try, and 10 CVs went weak). Fix B: ``review.top`` / ``headline``
name the lines that need no quote, so a summary that names a job is seen to need one."""
from __future__ import annotations

import logging
from typing import Any

import pytest

from src.services.applications import cv_records, cv_review, review_rows
from src.services.profile.models import UserProfile
from tests import test_cv_review_line_kinds as lk
from tests.test_cv_review import _setup, _stored
from tests.test_cv_review_held_lines import OWNER_SHAPES, _profile, _seed, _sha

TEXT = OWNER_SHAPES[351]


def _intended(text: str, profile: UserProfile, *, quote_held: tuple[int, ...] = ()) -> list[list[str]]:
    """What a careful reviewer means for line n: [] on a held line, the line's own text elsewhere."""
    held = set(review_rows.held_numbers(text, profile))
    lines = cv_review.content_lines(text, profile)
    return [[ln.strip().lstrip(cv_review._BULLETS + " \t")] if n not in held or n in quote_held else []
            for n, ln in enumerate(lines, 1)]


def _payload(cv_id: int, text: str, evidence: list[list[str]]) -> dict[str, Any]:
    return {"artifact_id": cv_id, "sha256": _sha(text), "confidence": "strong", "reviewer": "fresh helper",
            "lines": [{"n": n, "verdict": "supported", "evidence": ev} for n, ev in enumerate(evidence, 1)]}


async def _state(client: Any, app_id: int) -> tuple[int, int]:
    detail = (await client.get(f"/api/applications/{app_id}", params={"view": "review"})).json()
    events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
    return detail["review"]["tries_left"], sum(e["event_type"] == "cv_reviewed" for e in events)


@pytest.mark.asyncio
async def test_a_review_shifted_by_one_is_refused_and_costs_nothing(authenticated_async_context, fixture_user_id, caplog):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client, cv=TEXT)
        intended = _intended(TEXT, _profile())
        shifted = [[], *intended[:-1]]  # row n carries the review of line n-1
        before = await _state(client, app_id)
        with caplog.at_level(logging.INFO):
            resp = await client.post(f"/api/applications/{app_id}/events",
                                     json={"event_type": "cv_reviewed", "payload": _payload(cv_id, TEXT, shifted)})
        assert resp.status_code == 422, resp.text
        for part in ("shifted by one", "row n reviews review.lines[n-1]", "held lines take evidence []",
                     "nothing was stored", "no try used"):
            assert part in resp.text
        assert f"cv_reviewed_refused reason=rows_shifted application={app_id}" in caplog.text
        assert await _state(client, app_id) == before


@pytest.mark.asyncio
async def test_the_aligned_payload_is_still_accepted(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client, cv=TEXT)
        got = await _stored(client, app_id, _payload(cv_id, TEXT, _intended(TEXT, _profile())))
        assert got["confidence"] == "strong" and got["downgraded"] == []


@pytest.mark.asyncio
async def test_one_quoted_held_line_is_no_shift(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client, cv=TEXT)
        held = review_rows.held_numbers(TEXT, _profile())
        got = await _stored(client, app_id, _payload(cv_id, TEXT, _intended(TEXT, _profile(), quote_held=(held[1],))))
        assert got["rules"] == cv_review.RULES  # stored, not refused


@pytest.mark.asyncio
async def test_review_top_excludes_a_summary_that_names_a_job_and_includes_one_that_does_not(
    authenticated_async_context, fixture_user_id,
):
    from src.services.profile.storage import save_profile
    from tests._cv_review import ats_cv, blank_ad

    plain = ats_cv(lk._cv())
    named = ats_cv(lk._cv(summary="AI engineer with R&D experience at Calnex Solutions and Tidewater."))
    save_profile(lk._profile(), fixture_user_id, source_action="cv_upload")
    async with authenticated_async_context() as client:
        for i, (text, summary_in_top) in enumerate(((plain, True), (named, False))):
            ad = {"title": "AI Product Engineer", "company": "Northwind", "location": "London", "country": "GB",
                  "remote": False, "apply_url": f"https://northwind.example/careers/{i}", "description": "Build agents."}
            app_id = int((await client.post("/api/jobs/bring", json=ad)).json()["application_id"])
            await blank_ad(app_id)
            assert (await client.post(f"/api/applications/{app_id}/artifacts",
                                      json={"kind": "cv", "text": text})).status_code == 201
            review = (await client.get(f"/api/applications/{app_id}", params={"view": "review"})).json()["review"]
            top, headline = cv_records.top_lines(text, lk._profile())
            assert review["top"] == sorted(top) and review["headline"] == headline == 1
            assert (2 in review["top"]) is summary_in_top, review["top"]
            assert set(review["top"]) <= set(review["held"])
            full = (await client.get(f"/api/applications/{app_id}")).json()["review"]
            assert full["top"] == review["top"] and full["headline"] == 1
