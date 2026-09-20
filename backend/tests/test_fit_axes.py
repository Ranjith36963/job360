"""The fit picture's axes (2026-09-20): the AGENT names a few dimensions and
puts a role/you number on each; Job360 stores the list on the fit slot,
carries it in the ``fit_judged`` event, and hands it back unchanged to the
web and to the MCP tool. It names nothing and scores nothing (VISION rule 4).
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from src.services.applications.spine import SpineError, validate_axes

_AD = {
    "title": "AI Engineer",
    "company": "QuantCo",
    "location": "London",
    "apply_url": "https://jobs.lever.co/quantco-/1",
    "description": "RAG, Python, LLM APIs.",
}

_AXES = [
    {"name": "LLM depth", "role": 90, "you": 75},
    {"name": "Production ML", "role": 70, "you": 80},
    {"name": "Leadership", "role": 30, "you": 45},
    {"name": "London base", "role": 100, "you": 60},
]


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


# ── the pure check ────────────────────────────────────────────────────────────


def test_none_and_empty_mean_no_picture():
    assert validate_axes(None) == []
    assert validate_axes([]) == []


def test_axes_come_back_normalised_and_in_order():
    out = validate_axes([{"name": "  LLM depth ", "role": 90.0, "you": 75}] + _AXES[1:])
    assert out[0] == {"name": "LLM depth", "role": 90, "you": 75}
    assert [a["name"] for a in out] == [a["name"] for a in _AXES]


@pytest.mark.parametrize(
    ("axes", "fragment"),
    [
        (_AXES[:2], "between 3 and 8"),
        ([{"name": f"a{i}", "role": 1, "you": 1} for i in range(9)], "between 3 and 8"),
        ([{"name": "", "role": 1, "you": 1}] + _AXES[1:], "needs a name"),
        ([{"name": "x" * 41, "role": 1, "you": 1}] + _AXES[1:], "APPLICATION_FIT_AXIS_NAME_MAX_CHARS"),
        ([{"name": "llm depth", "role": 1, "you": 1}] + _AXES, "duplicate"),
        ([{"name": "a", "role": 101, "you": 1}] + _AXES[1:], "0..100"),
        ([{"name": "a", "role": 1, "you": -1}] + _AXES[1:], "0..100"),
        ([{"name": "a", "role": 1.5, "you": 1}] + _AXES[1:], "whole number"),
        ([{"name": "a", "role": True, "you": 1}] + _AXES[1:], "whole number"),
        ([{"name": "a", "role": 1}] + _AXES[1:], "whole number"),
        (["not an object"] + _AXES[1:], "object"),
    ],
)
def test_shapes_job360_cannot_draw_are_refused(axes, fragment):
    with pytest.raises(SpineError) as exc:
        validate_axes(axes)
    assert exc.value.status_code == 422
    assert fragment in exc.value.detail


def test_the_bounds_are_live_settings(monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "APPLICATION_FIT_AXES_MAX", 3)
    with pytest.raises(SpineError):
        validate_axes(_AXES)  # four axes, cap now three
    assert len(validate_axes(_AXES[:3])) == 3


# ── the route: stored, carried, handed back (rule 21: non-default values) ─────


@pytest.mark.asyncio
async def test_axes_ride_the_slot_and_the_event(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await client.put(
            f"/api/applications/{app_id}/fit",
            json={"score": 68, "verdict": "good", "axes": _AXES},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["fit"]["axes"] == _AXES

        detail = (await client.get(f"/api/applications/{app_id}")).json()
        alignment = (await client.get(f"/api/applications/{app_id}/alignment")).json()
    assert detail["fit"]["axes"] == _AXES, "the slot holds the picture"
    assert alignment["fit"]["axes"] == _AXES, "the fit picture endpoint hands it to the web"
    judged = [e for e in detail["events"] if e["event_type"] == "fit_judged"]
    assert len(judged) == 1 and judged[0]["payload"]["axes"] == _AXES, "the event keeps it as history"


@pytest.mark.asyncio
async def test_a_save_without_axes_clears_them_and_stays_silent(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        first = await client.put(f"/api/applications/{app_id}/fit", json={"score": 50, "axes": _AXES})
        assert first.status_code == 200, first.text
        second = await client.put(f"/api/applications/{app_id}/fit", json={"score": 55, "verdict": "re-read"})
        assert second.status_code == 200, second.text
        detail = (await client.get(f"/api/applications/{app_id}")).json()
    assert detail["fit"]["score"] == 55
    assert detail["fit"]["axes"] == [], "the slot is one record — no axes this time means none"
    judged = [e for e in detail["events"] if e["event_type"] == "fit_judged"]
    assert [e["payload"]["axes"] for e in judged] == [_AXES, []], "both pictures stay in the log"


@pytest.mark.asyncio
async def test_the_route_refuses_what_it_cannot_draw(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        too_few = await client.put(f"/api/applications/{app_id}/fit", json={"axes": _AXES[:2]})
        out_of_range = await client.put(
            f"/api/applications/{app_id}/fit", json={"axes": [{"name": "a", "role": 101, "you": 1}] + _AXES[1:]}
        )
        extra_key = await client.put(
            f"/api/applications/{app_id}/fit",
            json={"axes": [{"name": "a", "role": 1, "you": 1, "weight": 2}] + _AXES[1:]},
        )
        detail = (await client.get(f"/api/applications/{app_id}")).json()
    assert too_few.status_code == 422
    assert out_of_range.status_code == 422
    assert extra_key.status_code == 422
    assert detail["fit"] is None, "a refused save writes nothing"


# ── the MCP tool: same route, same picture ───────────────────────────────────


@pytest.mark.asyncio
async def test_the_agent_tool_stores_and_reads_the_same_axes(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    from src.api import mcp_server

    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        server = mcp_server.build_server()
        save = server._tool_manager.get_tool("save_fit")
        read = server._tool_manager.get_tool("get_application")
        assert save is not None and read is not None
        mcp_server._current_user.set(mcp_server.CurrentUser(id=fixture_user_id, email="e2e@example.com"))
        try:
            saved = await save.fn(app_id, score=68, verdict="good", axes=_AXES)
            detail = await read.fn(app_id)
        finally:
            mcp_server._current_user.set(None)
    assert saved["fit"]["axes"] == _AXES
    assert detail["fit"]["axes"] == _AXES
