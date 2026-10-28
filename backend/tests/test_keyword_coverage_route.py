"""PR 8: the saved fit's jd_requirements feed the advisory ``keyword_coverage`` check on a CV save.
Route level (HTTP), values asserted (rule 21). Helpers copied, never imported across test modules."""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications import kit as kit_service
from src.services.profile.models import CVData, UserPreferences, UserProfile

_AD = {
    "title": "Platform Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/8",
    "description": "Run the platform. Python, Kubernetes, Haskell.",
}
CV_TEXT = "Ada Lovelace\nada@example.com\nSKILLS\nLanguages: Python, Docker\n"
FORM = "https://careers.northwind.example/apply/8"


def _seed_profile(user_id: str) -> None:
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(
                raw_text="Python Docker Kubernetes engineer.", name="Ada Lovelace",
                skills=["Python", "Docker", "Kubernetes"],
            ),
            preferences=UserPreferences(),
        ),
        user_id, source_action="cv_upload",
    )


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _fit(client: AsyncClient, app_id: int, requirements: list[Any]) -> None:
    body = {"score": 80, "verdict": "ok", "score_source": "full_ad", "jd_requirements": requirements}
    resp = await client.put(f"/api/applications/{app_id}/fit", json=body)
    assert resp.status_code == 200, resp.text


async def _save_cv(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV_TEXT})
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_save_reply_carries_keyword_coverage(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _fit(client, app_id, ["Python", {"text": "Kubernetes", "kind": "hard"}, "Haskell"])
        saved = await _save_cv(client, app_id)
        by_code = {c["code"]: c for c in saved["checks"]}
        assert by_code["keyword_coverage"]["severity"] == "info"
        detail = by_code["keyword_coverage"]["detail"]
        assert "covered: Python" in detail
        assert "missing (have it): Kubernetes" in detail
        assert "missing (gap): Haskell" in detail
        assert "Never stuff keywords" in detail


@pytest.mark.asyncio
async def test_keyword_coverage_silent_without_fit(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        saved = await _save_cv(client, app_id)
        assert "keyword_coverage" not in {c["code"] for c in saved["checks"]}
        await _fit(client, app_id, [])  # a fit with no requirements stays silent too
        again = await _save_cv(client, app_id)
        assert "keyword_coverage" not in {c["code"] for c in again["checks"]}


@pytest.mark.asyncio
async def test_keyword_coverage_never_asks_in_check_submit(authenticated_async_context, fixture_user_id):
    assert "keyword_coverage" not in kit_service._UNSUPPORTED_CODES
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = await _bring(client)
        await _fit(client, app_id, ["Haskell"])
        saved = await _save_cv(client, app_id)
        assert "keyword_coverage" in {c["code"] for c in saved["checks"]}
        resp = await client.get(f"/api/applications/{app_id}/submit-check", params={"form_url": FORM})
        assert resp.status_code == 200, resp.text
        assert "keyword_coverage" not in resp.text
