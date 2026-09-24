"""The fit picture (2026-09-20): the agent's stored verdict beside which of
the candidate's OWN skills occur in the stored ad. Job360 judges nothing —
``skills_in_text`` is a whole-token string check over two stored strings.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications.alignment import skills_in_text

_AD = {
    "title": "AI Engineer",
    "company": "QuantCo",
    "location": "London",
    "apply_url": "https://jobs.lever.co/quantco-/1",
    "description": "We orchestrate LLM components such as RAG and use Python daily. C++ helps. Node.js is a plus.",
}


def _seed_profile(user_id: str, skills: list[str], extra: list[str] | None = None) -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(cv_data=CVData(raw_text="x", skills=skills), preferences=UserPreferences(additional_skills=extra or [])),
        user_id,
        source_action="cv_upload",
    )


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


# ── the pure check ────────────────────────────────────────────────────────────


def test_whole_token_case_insensitive_match():
    found, missing = skills_in_text(["python", "RAG", "Java", "Kubernetes"], _AD["description"])
    assert found == ["python", "RAG"]
    assert missing == ["Java", "Kubernetes"]


def test_punctuation_skills_and_no_substring_matches():
    text = "C++ and Node.js, not Javascript. Rust rusty."
    found, missing = skills_in_text(["C++", "Node.js", "Java", "Rust", "C"], text)
    assert found == ["C++", "Node.js", "Rust"]
    assert missing == ["Java"]  # "Java" is not a token in "Javascript"; lone "C" is skipped


def test_duplicates_collapse_and_blanks_are_skipped():
    found, missing = skills_in_text(["Python", "python", "", " ", "R"], "python everywhere")
    assert found == ["Python"] and missing == []


def test_cap_is_honoured(monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "ALIGNMENT_MAX_SKILLS", 2)
    found, missing = skills_in_text(["a1", "b2", "c3", "d4"], "a1 c3")
    assert found + missing == ["a1", "b2"]


# ── the route ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_alignment_draws_the_verdict_and_the_skill_split(authenticated_async_context, fixture_user_id):
    _seed_profile(fixture_user_id, ["Python", "RAG", "LangGraph"], extra=["Kubernetes"])
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        fit = await client.put(
            f"/api/applications/{app_id}/fit", json={"score": 68, "verdict": "strong match", "gaps": ["leading university"]}
        )
        assert fit.status_code == 200, fit.text
        resp = await client.get(f"/api/applications/{app_id}/alignment")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    assert body["fit"]["score"] == 68 and body["fit"]["gaps"] == ["leading university"]
    assert body["skills_in_ad"] == ["Python", "RAG"]
    # Order follows the one skill list (skill_tiering.profile_skills): the
    # user's own typed skills are read first, then the CV.
    assert body["skills_not_in_ad"] == ["Kubernetes", "LangGraph"]
    assert body["skills_total"] == 4
    assert body["ad_chars"] == len(_AD["description"])


@pytest.mark.asyncio
async def test_no_fit_and_no_profile_read_as_empty_not_error(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await client.get(f"/api/applications/{app_id}/alignment")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fit"] is None
    assert body["skills_in_ad"] == [] and body["skills_not_in_ad"] == [] and body["skills_total"] == 0


@pytest.mark.asyncio
async def test_a_foreign_application_is_404(authenticated_async_context):
    from tests.test_application_spine import _second_user_session_cookie, _session_client

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        cookie = await _second_user_session_cookie("alignment-second@example.com")
        async with _session_client(cookie) as other:
            resp = await other.get(f"/api/applications/{app_id}/alignment")
    assert resp.status_code == 404


def test_no_mcp_tool_and_no_keyword_list():
    """Rule M2: the agent holds both texts. Rule 28: no skill list of ours."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src"
    assert "alignment" not in (root / "api" / "mcp_server.py").read_text(encoding="utf-8")
    src = (root / "services" / "applications" / "alignment.py").read_text(encoding="utf-8")
    assert "SKILL_TERMS" not in src and "_TO_SKILL" not in src
