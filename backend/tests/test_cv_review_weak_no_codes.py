"""2026-11-15, production apps 874 and 886: a reviewer claimed "weak" while Job360's own checks found no problem
(``downgraded`` [] and ``where`` {}); both CVs were really strong. The reply now says so, and the stored
confidence stays what it was (a hint, never an override)."""
from __future__ import annotations

import pytest

from src.services.applications import cv_review
from tests.test_cv_review import CV, L1, L2, _event, _review, _seed, _setup

HINT = "Job360 found no problem in this review: every line it checked holds."


def _hints(resp) -> list[str]:
    return [w for w in resp.json()["warnings"] if HINT in w]


@pytest.mark.asyncio
async def test_weak_claim_without_codes_gets_the_hint_and_stays_weak(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        resp = await _event(client, app_id, "cv_reviewed", _review(cv_id, CV, confidence="weak"))
        assert resp.status_code == 201, resp.text
        hints = _hints(resp)
        assert len(hints) == 1, resp.json()["warnings"]
        assert "record this same CV again as strong" in hints[0] and "costs no try" in hints[0]
        events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
        stored = next(e for e in events if e["id"] == resp.json()["event_id"])["payload"]
        # 2026-11-18 (apps 1048, 1049): every weak store names a code; a reviewer-only weak is ``reviewer_weak``
        assert (stored["confidence"], stored["claimed"], stored["downgraded"]) == ("weak", "weak", ["reviewer_weak"])


@pytest.mark.asyncio
async def test_weak_claim_with_codes_and_strong_claims_get_no_hint(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        bad = [{**L1, "profile_quote": "Led a team of 12 engineers at Mars."}, L2]
        resp = await _event(client, app_id, "cv_reviewed", _review(cv_id, CV, lines=bad, confidence="weak"))
        assert resp.status_code == 201, resp.text
        events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
        stored = next(e for e in events if e["id"] == resp.json()["event_id"])["payload"]
        assert stored["downgraded"], "the fixture must produce a code"
        assert not _hints(resp)
        strong = await _event(client, app_id, "cv_reviewed", _review(cv_id, CV))
        assert strong.status_code == 201, strong.text
        assert not _hints(strong)


def test_the_hint_is_a_pure_function_of_the_stored_review():
    assert cv_review.no_codes_warning({"claimed": "weak", "confidence": "weak", "downgraded": []}) is not None
    assert cv_review.no_codes_warning({"claimed": "weak", "confidence": "weak", "downgraded": ["reviewer_weak"]}) \
        == cv_review.NO_CODES_HINT
    assert cv_review.no_codes_warning({"claimed": "weak", "confidence": "weak", "downgraded": ["jd_gap_claimed"]}) is None
    assert cv_review.no_codes_warning({"claimed": "strong", "confidence": "strong", "downgraded": []}) is None
    assert cv_review.no_codes_warning({"claimed": "strong", "confidence": "weak", "downgraded": ["x"]}) is None
