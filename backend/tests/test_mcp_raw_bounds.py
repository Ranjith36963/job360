"""get_profile(include_raw=true) is bounded (15k per section) and pageable (raw_section + raw_offset).

Measured live: ~62k chars in one call. Helpers are copied, never imported across test modules.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp")

LINKEDIN = "".join(chr(97 + (i // 7) % 26) + str(i % 10) for i in range(20_000))[:40_000].ljust(40_000, "x")
SHORT_CV = "Python data engineer."


def _repos(n: int = 60) -> list[dict]:
    return [
        {"name": f"repo{i}", "language": "Python", "stars": i, "readme_excerpt": f"{i}" + "r" * 1199}
        for i in range(n)
    ]


def _seed(user_id: str, *, linkedin: str = "", repos: list | None = None) -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(
                raw_text=SHORT_CV, name="Ada Lovelace", skills=["Python"],
                linkedin_raw_text=linkedin, github_repos_brief=repos or [],
            ),
            preferences=UserPreferences(target_job_titles=["Data Engineer"]),
        ),
        user_id, source_action="cv_upload",
    )


async def _get_profile(authenticated_async_context, fixture_user_id, **seed):
    from src.api import mcp_server

    _seed(fixture_user_id, **seed)
    async with authenticated_async_context():
        manager = mcp_server.build_server()._tool_manager
        mcp_server._current_user.set(mcp_server.CurrentUser(id=fixture_user_id, email="e2e@example.com"))
        try:
            yield manager.get_tool("get_profile").fn
        finally:
            mcp_server._current_user.set(None)


@pytest.fixture
async def big(authenticated_async_context, fixture_user_id):
    async for fn in _get_profile(
        authenticated_async_context, fixture_user_id, linkedin=LINKEDIN, repos=_repos()
    ):
        yield fn


@pytest.fixture
async def short(authenticated_async_context, fixture_user_id):
    async for fn in _get_profile(authenticated_async_context, fixture_user_id):
        yield fn


@pytest.mark.asyncio
async def test_old_call_is_bounded_and_says_how_to_page(big):
    out = await big(include_raw=True)
    raw = out["raw"]
    assert len(json.dumps(raw)) < 70_000 and len(json.dumps(out)) < 70_000
    assert len(raw["linkedin"]) == 15_000 and raw["linkedin"] == LINKEDIN[:15_000]
    assert raw["truncated"] is True
    assert raw["more"]["linkedin"] == {
        "total_chars": 40_000, "returned": 15_000,
        "next": 'get_profile(include_raw=true, raw_section="linkedin", raw_offset=15000)',
    }
    repos = raw["github_repos"]
    assert 0 < len(repos) < 60 and len(json.dumps(repos)) <= 15_000
    assert raw["more"]["github_repos"]["returned"] == len(repos)
    assert f"raw_offset={len(repos)}" in raw["more"]["github_repos"]["next"]
    assert "cv" not in raw["more"]


@pytest.mark.asyncio
async def test_paging_a_section_returns_the_next_chars(big):
    page = (await big(include_raw=True, raw_section="linkedin", raw_offset=15_000))["raw"]
    assert set(page) == {"linkedin", "truncated", "more"}
    assert page["linkedin"] == LINKEDIN[15_000:30_000]
    assert page["more"]["linkedin"]["next"].endswith("raw_offset=30000)")
    last = (await big(include_raw=True, raw_section="linkedin", raw_offset=30_000))["raw"]
    assert last["linkedin"] == LINKEDIN[30_000:] and last["truncated"] is False and "more" not in last


@pytest.mark.asyncio
async def test_paging_repos_uses_a_repo_index(big):
    first = (await big(include_raw=True))["raw"]["github_repos"]
    nxt = (await big(include_raw=True, raw_section="github_repos", raw_offset=len(first)))["raw"]
    assert nxt["github_repos"][0]["name"] == f"repo{len(first)}"


@pytest.mark.asyncio
async def test_short_profile_old_call_is_unchanged(short):
    raw = (await short(include_raw=True))["raw"]
    assert raw == {
        "cv": SHORT_CV, "linkedin": "", "github_bio": "", "github_profile_readme": "",
        "github_repos": [], "truncated": False,
    }


@pytest.mark.asyncio
async def test_invalid_section_is_a_clear_error(short):
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as info:
        await short(include_raw=True, raw_section="resume")
    assert "raw_section must be one of: cv, linkedin" in str(info.value)
    with pytest.raises(ToolError):
        await short(include_raw=True, raw_section="cv", raw_offset=-1)
