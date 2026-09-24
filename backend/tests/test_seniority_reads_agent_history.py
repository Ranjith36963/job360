"""The experience level reads the work history the user's assistant writes.

Since decision 28 the assistant writes dated positions with ``update_profile``
(slice B2). Those live in the OVERLAY (``profile_edits``), which extraction
never sees — so ``experience_level_inferred``, computed at extraction time off
the STORED profile, stayed wrong or blank after the assistant wrote the real
history.

The fix computes the inferred level off the EFFECTIVE profile (stored +
overlay) in the one read door, ``edits.apply_overlay_rows``, whenever the
overlay carries one of its inputs. Nothing is stored twice.

Precedence: a level the user set (``preferences.experience_level``) wins; the
inferred one is only the fallback. Rule 29: no usable history means NO level,
never a default like "junior".
"""
from __future__ import annotations

import io
import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

# One plainly-titled role held for exactly eight years (96 months). No tier
# word in the title, and closed dates so the answer never depends on today:
# seniority._YEARS_BANDS puts 6 <= years < 10 in rank 3, "senior".
EIGHT_YEARS: list[dict[str, Any]] = [
    {
        "company": "Acme Ltd",
        "title": "Software Engineer",
        "dates": "Jan 2016 – Dec 2023",
        "location": "London",
        "bullets": ["Built the payments platform"],
    },
]


def _seed_profile(user_id: str, *, typed: str = "", inferred: str = "") -> None:
    """A stored (base) profile, as an upload leaves it."""
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    profile = UserProfile(
        cv_data=CVData(
            raw_text="Python engineer.\nSkills: Python, SQL",
            name="Ada Lovelace",
            skills=["Python", "SQL"],
        ),
        preferences=UserPreferences(experience_level=typed, experience_level_inferred=inferred),
    )
    save_profile(profile, user_id, source_action="cv_upload")


async def _write_positions(client: AsyncClient, positions: list[dict[str, Any]]):
    resp = await client.patch(
        "/api/profile", json={"edits": [{"path": "cv_data.cv_positions", "value": positions}]}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["profile"]


# ═══════════════════════════════════════════════════════════════════════════
# (a) the assistant's history sets the level
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_assistant_history_sets_the_senior_band(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        before = (await client.get("/api/profile")).json()
        assert before["preferences"]["experience_level_inferred"] == ""

        after_write = await _write_positions(client, EIGHT_YEARS)
        assert after_write["preferences"]["experience_level_inferred"] == "senior"

        body = (await client.get("/api/profile")).json()
        assert body["preferences"]["experience_level_inferred"] == "senior"
        # the user's own field stays theirs: empty, never filled in by inference
        assert body["preferences"]["experience_level"] == ""
        assert body["summary"]["experience_level"] == ""

    from src.services.profile.storage import load_profile

    base = load_profile(fixture_user_id, with_overlay=False)
    assert base is not None
    assert base.preferences.experience_level_inferred == "", "derived at read time, never stored twice"


# ═══════════════════════════════════════════════════════════════════════════
# (b) no assistant history -> exactly today's behaviour
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_no_assistant_history_leaves_the_stored_level(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id, inferred="mid")
        body = (await client.get("/api/profile")).json()
        assert body["preferences"]["experience_level_inferred"] == "mid"

        # an unrelated agent edit does not recompute anything
        resp = await client.patch(
            "/api/profile", json={"edits": [{"path": "cv_data.skills", "value": ["Python"]}]}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["profile"]["preferences"]["experience_level_inferred"] == "mid"


# ═══════════════════════════════════════════════════════════════════════════
# (c) the user's own level wins
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_users_explicit_level_beats_the_inferred_one(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id, typed="junior")
        body = await _write_positions(client, EIGHT_YEARS)
        # the inference fills its own field and never overwrites the user's
        assert body["preferences"]["experience_level"] == "junior"
        assert body["preferences"]["experience_level_inferred"] == "senior"
        assert body["summary"]["experience_level"] == "junior", "typed wins; inferred is a fallback"

        # a user who later types a level keeps it over the agent's history
        resp = await client.post("/api/profile", data={"preferences": json.dumps({"experience_level": "mid"})})
        assert resp.status_code == 200, resp.text
        again = (await client.get("/api/profile")).json()
        assert again["preferences"]["experience_level"] == "mid"
        assert again["summary"]["experience_level"] == "mid"
        assert again["preferences"]["experience_level_inferred"] == "senior"


# ═══════════════════════════════════════════════════════════════════════════
# (d) a CV re-upload does not lose it
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cv_reupload_keeps_the_level_from_the_assistants_history(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    import src.api.routes.profile as profile_route

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        await _write_positions(client, EIGHT_YEARS)

        monkeypatch.setattr(
            profile_route, "extract_text",
            lambda path: "Grace Hopper\nSummary\nCompiler pioneer.\nSkills: COBOL, Fortran\n",
        )
        up = await client.post(
            "/api/profile/cv", files={"cv": ("new_cv.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"), "application/pdf")}
        )
        assert up.status_code == 200, up.text
        assert up.json()["preferences"]["experience_level_inferred"] == "senior"

        body = (await client.get("/api/profile")).json()
        assert body["preferences"]["experience_level_inferred"] == "senior"


# ═══════════════════════════════════════════════════════════════════════════
# (e) rule 29 — no usable history, no level, never a default
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "positions",
    [
        [],
        # a title with no tier word and no dates: nothing to read, so nothing said
        [{"company": "Acme", "title": "Engineer", "dates": "", "location": "", "bullets": []}],
    ],
)
async def test_empty_history_means_no_level(authenticated_async_context, fixture_user_id, positions):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        body = await _write_positions(client, positions)
        assert body["preferences"]["experience_level_inferred"] == ""
        assert body["preferences"]["experience_level"] == ""


def test_overlay_recompute_never_invents_a_level():
    """Unit-level: the read door itself, no HTTP."""
    from src.services.profile import edits
    from src.services.profile.models import UserProfile

    profile = UserProfile()
    rows = [{"path": "cv_data.cv_positions", "value": [], "set_by": "token:x", "set_at": "t"}]
    edits.apply_overlay_rows(profile, rows)
    assert profile.preferences.experience_level_inferred == ""

    rows = [{"path": "cv_data.cv_positions", "value": EIGHT_YEARS, "set_by": "token:x", "set_at": "t"}]
    edits.apply_overlay_rows(profile, rows)
    assert profile.preferences.experience_level_inferred == "senior"


# ═══════════════════════════════════════════════════════════════════════════
# Clearing / replacing an input takes the level it implied with it
# (CodeRabbit on #630: a STORED inference outlived the history behind it)
# ═══════════════════════════════════════════════════════════════════════════

# 48 months, no tier word -> "mid" (3 <= years < 6 in seniority._YEARS_BANDS).
LINKEDIN_FOUR_YEARS: list[dict[str, Any]] = [
    {"title": "Engineer", "company": "Initech", "start": "Jan 2020", "end": "Dec 2023"},
]


def _seed_stored_level(user_id: str, *, cv_positions: list, linkedin_positions: list, inferred: str) -> None:
    """A base profile whose STORED inferred level came from its own history."""
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    profile = UserProfile(
        cv_data=CVData(
            raw_text="Python engineer.\nSkills: Python, SQL",
            skills=["Python", "SQL"],
            cv_positions=list(cv_positions),
            linkedin_positions=list(linkedin_positions),
            linkedin_raw_text="LinkedIn export text" if linkedin_positions else "",
        ),
        preferences=UserPreferences(experience_level_inferred=inferred),
    )
    save_profile(profile, user_id, source_action="cv_upload")


@pytest.mark.asyncio
async def test_clearing_the_cv_clears_the_level_it_implied(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_stored_level(fixture_user_id, cv_positions=EIGHT_YEARS, linkedin_positions=[], inferred="senior")
        assert (await client.get("/api/profile")).json()["preferences"]["experience_level_inferred"] == "senior"

        resp = await client.post("/api/profile/clear", data={"section": "cv"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["preferences"]["experience_level_inferred"] == "", "no history left, no level (rule 29)"
        body = (await client.get("/api/profile")).json()
        assert body["preferences"]["experience_level_inferred"] == ""


@pytest.mark.asyncio
async def test_clearing_the_cv_keeps_the_level_the_remaining_history_implies(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        _seed_stored_level(
            fixture_user_id, cv_positions=EIGHT_YEARS, linkedin_positions=LINKEDIN_FOUR_YEARS, inferred="senior"
        )
        resp = await client.post("/api/profile/clear", data={"section": "cv"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["preferences"]["experience_level_inferred"] == "mid"


@pytest.mark.asyncio
async def test_clearing_the_cv_after_an_agent_write_clears_the_level(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_stored_level(fixture_user_id, cv_positions=[], linkedin_positions=[], inferred="senior")
        await _write_positions(client, EIGHT_YEARS)
        resp = await client.post("/api/profile/clear", data={"section": "cv"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["preferences"]["experience_level_inferred"] == ""


@pytest.mark.asyncio
async def test_clearing_linkedin_clears_the_level_it_implied(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_stored_level(fixture_user_id, cv_positions=[], linkedin_positions=LINKEDIN_FOUR_YEARS, inferred="mid")
        resp = await client.post("/api/profile/clear", data={"section": "linkedin"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["preferences"]["experience_level_inferred"] == ""


@pytest.mark.asyncio
async def test_a_new_cv_does_not_inherit_the_old_cvs_level(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    import src.api.routes.profile as profile_route

    async with authenticated_async_context() as client:
        _seed_stored_level(fixture_user_id, cv_positions=EIGHT_YEARS, linkedin_positions=[], inferred="senior")
        monkeypatch.setattr(
            profile_route, "extract_text",
            lambda path: "Grace Hopper\nSummary\nCompiler pioneer.\nSkills: COBOL, Fortran\n",
        )
        up = await client.post(
            "/api/profile/cv", files={"cv": ("new_cv.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"), "application/pdf")}
        )
        assert up.status_code == 200, up.text
        assert up.json()["preferences"]["experience_level_inferred"] == ""


# ═══════════════════════════════════════════════════════════════════════════
# (f) the web profile and MCP get_profile agree
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_web_and_mcp_show_the_same_level(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await client.post("/api/tokens", json={"name": "agent"})
        assert resp.status_code == 201, resp.text
        token = resp.json()["token"]

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    async with mcp_runtime():
        async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
            result = await mcp.call_tool(
                "update_profile", {"edits": [{"path": "cv_data.cv_positions", "value": EIGHT_YEARS}]}
            )
            assert not result.is_error, result.content[0].text
            got = json.loads((await mcp.call_tool("get_profile", {})).content[0].text)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as web:
        web_body = (await web.get("/api/profile")).json()

    assert got["experience_level_inferred"] == "senior"
    assert web_body["preferences"]["experience_level_inferred"] == got["experience_level_inferred"]
    assert web_body["summary"]["experience_level"] == got["experience_level"] == ""
