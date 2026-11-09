"""get_application hands the agent the exact inputs a cv_reviewed needs (production 2026-11-06/07: 70 of 70 weak)."""
from __future__ import annotations

from typing import Any

import pytest

from src.services.applications import cv_review, kit
from src.services.profile.models import CVData, UserProfile
from tests.test_cv_review import CV, L1, L2, RAW, _seed, _setup, _stored, sha

CV2 = CV + "- Worked on the warehouse migration and cut storage costs in half.\n"


async def _detail(client: Any, app_id: int, **params: Any) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_review_carries_the_checks_own_sha_and_lines(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        profile = UserProfile(cv_data=CVData(raw_text=RAW, name="Ada Lovelace"))
        for params in ({}, {"with_artifact_text": True}):
            review = (await _detail(client, app_id, **params))["review"]
            assert review == {"artifact_id": cv_id, "sha256": kit.sha256_text(CV), "state": "none", "lines": [
                "- Data engineer at Northwind since 2019.",
                "- Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%."]}
            assert review["lines"] == cv_review.content_lines(CV, profile)
        # a newer version replaces it: older ids and shas are not offered
        newer = int((await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV2})).json()["artifact_id"])
        review = (await _detail(client, app_id))["review"]
        assert (review["artifact_id"], review["sha256"], len(review["lines"])) == (newer, sha(CV2), 3)


@pytest.mark.asyncio
async def test_a_review_built_from_review_is_strong_and_an_older_sha_is_not(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client)
        review = (await _detail(client, app_id))["review"]
        quotes = {cv_review.line_key(x["cv_line"]): x["profile_quote"] for x in (L1, L2)}
        rows = [{"cv_line": ln, "profile_quote": quotes[cv_review.line_key(ln)], "field_path": "cv_data.raw_text"} for ln in review["lines"]]
        good = {"artifact_id": review["artifact_id"], "sha256": review["sha256"], "confidence": "strong",
                "lines": rows, "reviewer": "fresh helper"}
        got = await _stored(client, app_id, good)
        assert (got["confidence"], got["downgraded"], got["covered"], got["content_lines"]) == ("strong", [], 2, 2)
        for bad in ({**good, "sha256": sha("an older text")}, {**good, "sha256": ""}):
            refused = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "cv_reviewed", "payload": bad})
            assert refused.status_code == 422 and "review.sha256 and review.lines" in refused.text
        events = (await _detail(client, app_id))["events"]
        assert [e["payload"]["confidence"] for e in events if e["event_type"] == "cv_reviewed"] == ["strong"]


@pytest.mark.asyncio
async def test_missing_lines_are_named_in_the_reply(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, _ = await _setup(client)
        review = (await _detail(client, app_id))["review"]
        short = {"artifact_id": review["artifact_id"], "sha256": review["sha256"], "confidence": "strong",
                 "lines": [L1], "reviewer": "fresh helper"}
        resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "cv_reviewed", "payload": short})
        assert resp.status_code == 201, resp.text
        assert any("1 of 2 required lines" in w and "review.lines" in w for w in resp.json()["warnings"])


@pytest.mark.asyncio
async def test_no_cv_means_no_review(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        brought = await client.post("/api/jobs/bring", json={
            "title": "Data Engineer", "company": "Acme", "location": "London", "country": "GB", "remote": False,
            "apply_url": "https://acme.example/careers/1", "description": "Build pipelines."})
        assert (await _detail(client, int(brought.json()["application_id"])))["review"] is None


@pytest.mark.asyncio
async def test_review_inputs_failure_never_breaks_the_application_read(
    authenticated_async_context, fixture_user_id, monkeypatch, caplog
):
    """Opus review P2 (2026-11-09): a profile/CV-lines failure leaves review null and logs it; the read still works."""
    from src.services.applications import cv_review

    async def boom(*_a, **_k):
        raise RuntimeError("profile unavailable")

    monkeypatch.setattr(cv_review, "inputs", boom)
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        brought = await client.post("/api/jobs/bring", json={
            "title": "Data Engineer", "company": "Acme", "location": "London", "country": "GB", "remote": False,
            "apply_url": "https://acme.example/careers/2", "description": "Build pipelines."})
        assert (await _detail(client, int(brought.json()["application_id"])))["review"] is None
    assert "cv_review_inputs_unavailable" in caplog.text
