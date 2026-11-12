"""What the CV task can read (production dry run 2026-11-12, local app 454).

1. ``jd_terms_dropped`` was near-certain on every fix round: ``review_jd.judge`` requires every earlier must-term of
   the application to come back as a must, but neither ``cv_work`` nor ``get_application(view="review")`` gave the
   earlier terms, so a NEW reviewer picked fresh ones (454 dropped 10). The review block now carries ``jd_terms``:
   the terms of every ``cv_reviewed`` of the application (any CV version), one per term, a must if any review had
   it as one, else ``[]``.
2. The compact profile left out the LinkedIn skills and GitHub languages, so true skills read as gaps. They are in it
   now (empty fields still left out, rule #29), and they are quote sources and skill-book items.
Real doors (HTTP), values asserted (rule #21). Helpers are the CV review tests' own.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.services.applications import cv_review
from src.services.profile.models import CVData, UserProfile
from tests.test_cv_review import _save_cv, _stored
from tests.test_cv_review_benchmark import HONEST, HONEST_JD, _app, _payload, _seed
from tests.test_cv_work import _cv_work, _job

LINKEDIN_SKILLS = ["Prompt engineering", "Vector databases", "OpenAI API", "Gemini API", "NLP", "Linux", "Chroma DB"]
GITHUB_LANGUAGES = {"Python": 120_000, "TypeScript": 40_000}


async def _view(client: Any, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}", params={"view": "review"})
    assert resp.status_code == 200, resp.text
    return resp.json()["review"]


@pytest.mark.asyncio
async def test_jd_terms_are_empty_before_a_review_and_the_judged_terms_after(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _app(client, HONEST)
        assert (await _view(client, app_id))["jd_terms"] == []
        first = await _stored(client, app_id, _payload(cv_id, HONEST, HONEST_JD))
        assert first["jd"]["state"] == "judged"
        want = [{"term": x["term"], "must": x["must"]} for x in HONEST_JD]
        assert first["jd"]["terms"] == want
        assert (await _view(client, app_id))["jd_terms"] == want
        # a new CV version has no review yet; the application's earlier terms still come with it
        await _save_cv(client, app_id, HONEST + "\n")
        fresh = await _view(client, app_id)
        assert fresh["artifact_id"] != cv_id and fresh["state"] == "none" and fresh["jd_terms"] == want


@pytest.mark.asyncio
async def test_a_re_review_that_re_lists_jd_terms_stores_no_jd_terms_dropped(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _app(client, HONEST)
        assert (await _stored(client, app_id, _payload(cv_id, HONEST, HONEST_JD)))["confidence"] == "strong"
        # a NEW reviewer: only what the view gives it, plus its own judgment of each listed term
        listed = (await _view(client, app_id))["jd_terms"]
        judged = {x["term"]: x for x in HONEST_JD}
        second = [{**judged[t["term"]], "must": t["must"]} for t in listed]
        got = await _stored(client, app_id, _payload(cv_id, HONEST, second))
        assert "jd_terms_dropped" not in got["downgraded"] and (got["confidence"], got["downgraded"]) == ("strong", [])


@pytest.mark.asyncio
async def test_jd_terms_keep_a_must_an_earlier_round_dropped_so_re_listing_them_is_strong(
    authenticated_async_context, fixture_user_id
):
    """``judge`` holds a re-review to the musts of EVERY earlier review; ``jd_terms`` must list all of them (checker
    2026-11-12: after a round dropped RAG, the newest event's terms lacked it and every later honest round was weak)."""
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _app(client, HONEST)
        assert (await _stored(client, app_id, _payload(cv_id, HONEST, HONEST_JD)))["confidence"] == "strong"
        shed = [x for x in HONEST_JD if x["term"] != "RAG"]
        assert "jd_terms_dropped" in (await _stored(client, app_id, _payload(cv_id, HONEST, shed)))["downgraded"]
        listed = (await _view(client, app_id))["jd_terms"]
        assert {"term": "RAG", "must": True} in listed
        assert sorted(t["term"] for t in listed) == sorted(x["term"] for x in HONEST_JD)
        judged = {x["term"]: x for x in HONEST_JD}
        third = [{**judged[t["term"]], "must": t["must"]} for t in listed]
        got = await _stored(client, app_id, _payload(cv_id, HONEST, third))
        assert "jd_terms_dropped" not in got["downgraded"] and (got["confidence"], got["downgraded"]) == ("strong", [])


@pytest.mark.asyncio
async def test_cv_work_items_carry_jd_terms(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id = await _job(client, "terms", cv=None)
        item = next(i for i in (await _cv_work(client))["items"] if i["application_id"] == app_id)
        assert item["review"] is None  # no CV yet: no review block
        await _save_cv(client, app_id, HONEST)
        got = next(i for i in (await _cv_work(client))["items"] if i["application_id"] == app_id)
        assert got["review"]["jd_terms"] == []


def _skills_profile() -> UserProfile:
    return UserProfile(cv_data=CVData(name="Ada Lovelace", skills=["Python"], linkedin_skills=list(LINKEDIN_SKILLS),
                                      github_languages=dict(GITHUB_LANGUAGES)))


@pytest.mark.asyncio
async def test_linkedin_skills_and_github_languages_are_in_the_compact_profile(
    authenticated_async_context, fixture_user_id
):
    from src.services.profile.storage import save_profile

    async with authenticated_async_context() as client:
        save_profile(_skills_profile(), fixture_user_id, source_action="cv_upload")
        await _job(client, "skills", cv=None)
        profile = (await _cv_work(client))["profile"]
        assert profile["linkedin_skills"] == LINKEDIN_SKILLS
        assert profile["github_languages"] == ["Python", "TypeScript"]
        # rule #29: an empty field stays out
        save_profile(UserProfile(cv_data=CVData(name="Ada Lovelace", skills=["Python"])), fixture_user_id,
                     source_action="cv_upload")
        bare = (await _cv_work(client))["profile"]
        assert "linkedin_skills" not in bare and "github_languages" not in bare


def test_linkedin_skills_are_quote_sources_and_skill_book_items():
    profile = _skills_profile()
    source, words = cv_review.quote_sources(profile)
    assert cv_review.quote_in_profile("Prompt engineering", source, words)
    assert cv_review.quote_in_profile("TypeScript", source, words)
    book = cv_review.skill_book(profile)
    assert {"prompt engineering", "chroma db", "typescript"} <= book.items
    assert cv_review.list_line_holds("Skills: Prompt engineering, Python", book)
    assert not cv_review.list_line_holds("Skills: Prompt engineering, Kubernetes", book)
