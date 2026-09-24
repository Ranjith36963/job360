"""Slice B2 (decision 28) — the assistant writes dated work history and projects.

`cv_data.cv_positions` and `cv_data.cv_projects` are editable paths whose value
is a LIST OF RECORDS with a closed key set (`services/profile/edits.py`
`RECORD_SCHEMAS`). A write replaces the whole list, is one more append-only row
in `profile_edits` (the history keeps every earlier version) and — like every
agent edit — lives in the overlay, so no re-extraction can clobber it.
"""
from __future__ import annotations

import copy
import io
import json
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

POSITIONS: list[dict[str, Any]] = [
    {
        "company": "Acme Ltd",
        "title": "Senior Data Engineer",
        "dates": "Jan 2022 – Present",
        "location": "London",
        "bullets": ["Led the payments migration", "Mentored 4 engineers"],
    },
    {
        "company": "Initech",
        "title": "Data Engineer",
        "dates": "2019 – 2021",
        "location": "",
        "bullets": [],
    },
]

PROJECTS: list[dict[str, Any]] = [
    {
        "name": "Job tracker",
        "description": "A FastAPI + Postgres app.\nShips daily.",
        "technologies": ["Python", "FastAPI"],
        "dates": "2023",
    },
]


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    profile = UserProfile(
        cv_data=CVData(
            raw_text="Python data engineer.\nSkills: Python, dbt",
            name="Ada Lovelace",
            skills=["Python", "dbt"],
            # A stale base value: what an OLD era wrote into the blob. The
            # overlay must win over it, and a CV swap clears it.
            cv_positions=[{"company": "Old Co", "title": "Old role", "dates": "2010", "location": "", "bullets": []}],
        ),
        preferences=UserPreferences(),
    )
    save_profile(profile, user_id, source_action="cv_upload")


async def _patch(client: AsyncClient, *edits: dict[str, Any]):
    return await client.patch("/api/profile", json={"edits": list(edits)})


async def _mint_token(client: AsyncClient, name: str = "claude") -> str:
    resp = await client.post("/api/tokens", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _bearer_client(token: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


# ═══════════════════════════════════════════════════════════════════════════
# Valid writes — stored, provenance, whole-list replace, history kept
# ═══════════════════════════════════════════════════════════════════════════


def test_both_paths_are_editable():
    from src.services.profile import edits

    assert "cv_data.cv_positions" in edits.editable_paths()
    assert "cv_data.cv_projects" in edits.editable_paths()


@pytest.mark.asyncio
async def test_valid_write_is_stored_with_provenance_and_versioned(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client, name="claude")
    async with _bearer_client(token) as agent:
        resp = await _patch(
            agent,
            {"path": "cv_data.cv_positions", "value": POSITIONS},
            {"path": "cv_data.cv_projects", "value": PROJECTS},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [a["set_by"] for a in body["applied"]] == ["token:claude", "token:claude"]
        assert body["applied"][0]["value"] == POSITIONS
        assert body["profile"]["cv_detail"]["cv_positions"] == POSITIONS
        assert body["profile"]["cv_detail"]["cv_projects"] == PROJECTS

        # A second write REPLACES the whole list ...
        replacement = [POSITIONS[1]]
        resp2 = await _patch(agent, {"path": "cv_data.cv_positions", "value": replacement})
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["profile"]["cv_detail"]["cv_positions"] == replacement

    async with authenticated_async_context() as client:
        # ... and the history keeps BOTH versions, newest last, nothing rewritten.
        export = (await client.get("/api/applications/export")).json()
        history = [e for e in export["profile_edits"] if e["path"] == "cv_data.cv_positions"]
        assert [e["value"] for e in history] == [POSITIONS, replacement]
        assert all(e["set_by"] == "token:claude" for e in history)


@pytest.mark.asyncio
async def test_missing_keys_are_filled_with_empty_values(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await _patch(
            client,
            {"path": "cv_data.cv_positions", "value": [{"title": "Analyst"}]},
            {"path": "cv_data.cv_projects", "value": [{"name": "Thesis"}]},
        )
        assert resp.status_code == 200, resp.text
        cv = resp.json()["profile"]["cv_detail"]
        assert cv["cv_positions"] == [{"company": "", "title": "Analyst", "dates": "", "location": "", "bullets": []}]
        assert cv["cv_projects"] == [{"name": "Thesis", "description": "", "technologies": [], "dates": ""}]


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        ("Jan 2020 - Present", "Jan 2020 – Present"),
        ("january 2020 to present", "Jan 2020 – Present"),
        ("Sept 2018 — Mar 2020", "Sep 2018 – Mar 2020"),
        ("2019-2021", "2019 – 2021"),
        ("2019 to date", "2019 – Present"),
        ("  2020  ", "2020"),
        ("Mar. 2021 – current", "Mar 2021 – Present"),
        ("", ""),
    ],
)
def test_dates_are_normalised(given: str, stored: str):
    from src.services.profile import edits

    out = edits.validate_edit("cv_data.cv_positions", [{"title": "X", "dates": given}])
    assert out[0]["dates"] == stored


def test_normalised_dates_are_read_by_seniority():
    """The stored form is one the seniority reader already understands."""
    import datetime

    from src.services.profile import edits
    from src.services.profile.seniority import infer_experience_level

    stored = edits.validate_edit(
        "cv_data.cv_positions", [{"title": "Engineer", "dates": "Jan 2010 to present"}]
    )
    assert infer_experience_level(stored, today=datetime.date(2026, 9, 1)) != ""


def test_control_characters_are_stripped():
    from src.services.profile import edits

    out = edits.validate_edit(
        "cv_data.cv_positions",
        [{
            "company": "Acme\x00 Ltd‮",
            "title": "Engineer\r\nEvil",
            "location": "Lon don",
            "bullets": ["Built\x07 things\t fast"],
        }],
    )[0]
    assert out["company"] == "Acme Ltd"
    assert out["title"] == "Engineer Evil"
    assert out["location"] == "Lon don"
    assert out["bullets"] == ["Built things fast"]

    proj = edits.validate_edit(
        "cv_data.cv_projects",
        [{"name": "P\x1b[31m", "description": "line one\r\nline\x00 two ", "technologies": ["Py\x0bthon"]}],
    )[0]
    assert proj["name"] == "P [31m"
    assert proj["description"] == "line one\nline two", "a description keeps its line breaks only"
    assert proj["technologies"] == ["Py thon"]


# ═══════════════════════════════════════════════════════════════════════════
# Invalid shapes — 422, clear message, nothing written
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "value", "needle"),
    [
        # unknown key -> named, with the allowed set
        ("cv_data.cv_positions", [{"title": "X", "start": "2020"}], "start"),
        ("cv_data.cv_projects", [{"name": "X", "url": "https://x"}], "url"),
        # wrong types
        ("cv_data.cv_positions", "Engineer at Acme", "list"),
        ("cv_data.cv_positions", ["Engineer at Acme"], "object"),
        ("cv_data.cv_positions", [{"title": 7}], "title"),
        ("cv_data.cv_positions", [{"title": "X", "bullets": "one line"}], "bullets"),
        ("cv_data.cv_positions", [{"title": "X", "bullets": ["ok", 3]}], "bullets"),
        ("cv_data.cv_projects", [{"name": "X", "technologies": [{"n": 1}]}], "technologies"),
        # a record with nothing to identify it
        ("cv_data.cv_positions", [{"location": "London"}], "title"),
        ("cv_data.cv_projects", [{"description": "stuff"}], "name"),
        # bad dates
        ("cv_data.cv_positions", [{"title": "X", "dates": "last spring"}], "dates"),
        ("cv_data.cv_positions", [{"title": "X", "dates": "2021 – 2019"}], "dates"),
        ("cv_data.cv_positions", [{"title": "X", "dates": "Mar 2020 – Jan 2020"}], "dates"),
        ("cv_data.cv_positions", [{"title": "X", "dates": "Present – 2020"}], "dates"),
        ("cv_data.cv_positions", [{"title": "X", "dates": "2020-01"}], "dates"),
        ("cv_data.cv_projects", [{"name": "X", "dates": "2099"}], "dates"),
    ],
)
async def test_invalid_shapes_are_422(authenticated_async_context, fixture_user_id, path, value, needle):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await _patch(client, {"path": path, "value": value})
        assert resp.status_code == 422, resp.text
        assert path in resp.text
        assert needle in resp.text
        assert (await client.get("/api/profile")).json()["agent_edits"] == [], "a 422 writes nothing"


@pytest.mark.asyncio
async def test_caps_are_422_naming_the_setting(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "PROFILE_EDIT_MAX_RECORDS", 2)
    monkeypatch.setattr(settings, "PROFILE_EDIT_MAX_RECORD_ITEMS", 2)
    monkeypatch.setattr(settings, "PROFILE_EDIT_MAX_ITEM_CHARS", 10)
    monkeypatch.setattr(settings, "PROFILE_EDIT_MAX_BULLET_CHARS", 12)
    monkeypatch.setattr(settings, "PROFILE_EDIT_MAX_CHARS", 30)
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        cases = [
            ("cv_data.cv_positions", [{"title": "a"}] * 3, "PROFILE_EDIT_MAX_RECORDS"),
            ("cv_data.cv_positions", [{"title": "a", "bullets": ["1", "2", "3"]}], "PROFILE_EDIT_MAX_RECORD_ITEMS"),
            ("cv_data.cv_positions", [{"title": "x" * 11}], "PROFILE_EDIT_MAX_ITEM_CHARS"),
            ("cv_data.cv_positions", [{"title": "a", "bullets": ["y" * 13]}], "PROFILE_EDIT_MAX_BULLET_CHARS"),
            ("cv_data.cv_projects", [{"name": "a", "description": "z" * 31}], "PROFILE_EDIT_MAX_CHARS"),
        ]
        for path, value, setting in cases:
            resp = await _patch(client, {"path": path, "value": value})
            assert resp.status_code == 422, f"{setting}: {resp.text}"
            assert setting in resp.text, resp.text
        monkeypatch.setattr(settings, "PROFILE_EDIT_MAX_RECORDS_CHARS", 50)
        resp = await _patch(client, {"path": "cv_data.cv_positions", "value": [{"title": "abc", "company": "defgh"}] * 2})
        assert resp.status_code == 422 and "PROFILE_EDIT_MAX_RECORDS_CHARS" in resp.text, resp.text
        assert (await client.get("/api/profile")).json()["agent_edits"] == []


# ═══════════════════════════════════════════════════════════════════════════
# No clobber — a CV swap and a LinkedIn re-upload leave agent-written
# positions/projects EXACTLY as written (the REAL extractor runs, not a stub)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cv_and_linkedin_reupload_leave_agent_records_exactly_equal(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    import src.api.routes.profile as profile_route
    from src.services.profile.storage import load_profile

    expected_positions = copy.deepcopy(POSITIONS)
    expected_projects = copy.deepcopy(PROJECTS)

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        resp = await _patch(
            client,
            {"path": "cv_data.cv_positions", "value": POSITIONS},
            {"path": "cv_data.cv_projects", "value": PROJECTS},
        )
        assert resp.status_code == 200, resp.text

        # (1) a DIFFERENT CV replaces the old one — reset_cv_owned_fields runs,
        # then the real deterministic extraction.
        monkeypatch.setattr(
            profile_route, "extract_text",
            lambda path: "Grace Hopper\nSummary\nCompiler pioneer.\nSkills: COBOL, Fortran\n",
        )
        up = await client.post(
            "/api/profile/cv", files={"cv": ("new_cv.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"), "application/pdf")}
        )
        assert up.status_code == 200, up.text
        cv = up.json()["cv_detail"]
        assert cv["cv_positions"] == expected_positions
        assert cv["cv_projects"] == expected_projects

        # (2) a LinkedIn export upload — the structural reset + real extraction.
        monkeypatch.setattr(
            profile_route, "extract_linkedin_text",
            lambda path: "Contact\nwww.linkedin.com/in/ada\nTop Skills\nSQL\nExperience\nAcme Ltd\nEngineer\n",
        )
        monkeypatch.setattr(profile_route, "_looks_like_linkedin", lambda text: True)
        li = await client.post(
            "/api/profile/linkedin", files={"file": ("li.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"), "application/pdf")}
        )
        assert li.status_code == 200, li.text

        after = (await client.get("/api/profile")).json()
        assert after["cv_detail"]["cv_positions"] == expected_positions
        assert after["cv_detail"]["cv_projects"] == expected_projects

    loaded = load_profile(fixture_user_id)
    assert loaded is not None
    assert loaded.cv_data.cv_positions == expected_positions
    assert loaded.cv_data.cv_projects == expected_projects
    # the stale base value the seed wrote was cleared by the CV swap — the
    # agent's records are what every reader sees, not a union with it
    base = load_profile(fixture_user_id, with_overlay=False)
    assert base is not None
    assert base.cv_data.cv_positions == []


# ═══════════════════════════════════════════════════════════════════════════
# MCP — the same route function, end to end with a bearer token
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mcp_writes_positions_and_projects(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        token = await _mint_token(client, name="agent")

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    async with mcp_runtime():
        async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
            before = json.loads((await mcp.call_tool("get_profile", {})).content[0].text)
            assert "cv_data.cv_positions" in before["editable_paths"]
            assert "cv_data.cv_projects" in before["editable_paths"]

            result = await mcp.call_tool(
                "update_profile",
                {"edits": [
                    {"path": "cv_data.cv_positions", "value": POSITIONS},
                    {"path": "cv_data.cv_projects", "value": PROJECTS},
                ]},
            )
            assert not result.is_error, result.content[0].text
            body = json.loads(result.content[0].text)
            assert [a["set_by"] for a in body["applied"]] == ["token:agent", "token:agent"]

            after = json.loads((await mcp.call_tool("get_profile", {})).content[0].text)
            assert after["fields"]["cv_data.cv_positions"] == POSITIONS
            assert after["fields"]["cv_data.cv_projects"] == PROJECTS

            bad = await mcp.call_tool(
                "update_profile",
                {"edits": [{"path": "cv_data.cv_positions", "value": [{"title": "X", "start": "2020"}]}]},
            )
            assert bad.is_error and "422" in bad.content[0].text and "start" in bad.content[0].text


def test_mcp_docs_say_positions_and_projects_are_writable():
    """The words an assistant reads must match what the server accepts."""
    import inspect

    from src.api import mcp_server

    src = inspect.getsource(mcp_server)
    assert "not writable" not in src
    assert "cv_data.cv_positions" in mcp_server.INSTRUCTIONS
