"""Owner 2026-10-25 - the connector check.

The assistant reports which helper tools it has (``report_capabilities``, read off its own tool list);
Job360 shows what to connect from the catalog file. Cards: fix a failing connector first, then a missing
essential (Gmail, the user's own calendar link) until connected or dismissed, then a missing
recommended one - at setup, or later only when a run needed it, at most one new card a week. Optional
ones are never pushed; a dismissed card never comes back. Closed values only; links only https on a
vendor domain. Helpers are defined here, never imported (per-test schema isolation).
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.profile import connectors

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def _report(*cats: str, needed: tuple[str, ...] = (), status: dict[str, str] | None = None) -> dict[str, Any]:
    return {"categories": [{"category": c, "via": "", "status": (status or {}).get(c, "ok")} for c in cats],
            "needed": list(needed), "reported_at": NOW.isoformat(), "reported_by": "cc"}


def _keys(cards: list[dict[str, Any]]) -> list[str]:
    return [c["key"] for c in cards]


# ── The catalog ──────────────────────────────────────────────────────────────


def test_the_catalog_loads_and_every_link_is_https_on_a_vendor_domain():
    cat = connectors.catalog()
    assert set(cat["assistants"]) == set(connectors.ASSISTANTS)
    assert cat["tiers"]["gmail"] == "essential" and cat["tiers"]["calendar"] == "optional"
    assert {cat["tiers"]["job_search"], cat["tiers"]["people_lookup"]} == {"recommended"}
    assert connectors.stale_entries(NOW) == []


@pytest.mark.parametrize(("path", "value", "error"), [
    (("assistants", "claude", "entries", 0, "connect_url"), "http://claude.ai/x", "https on a vendor domain"),
    (("assistants", "claude", "entries", 0, "connect_url"), "https://evil.example/claude.ai", "vendor domain"),
    (("assistants", "claude", "entries", 0, "category"), "dating", "unknown category"),
    (("assistants", "claude", "entries", 0, "tier"), "must_have", "tier must be one of"),
    (("tiers", "gmail"), "vital", "tiers must give"),
])
def test_the_loader_refuses_a_bad_catalog(path, value, error):
    raw = copy.deepcopy(connectors.catalog())
    node = raw
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = value
    with pytest.raises(connectors.CatalogError, match=error):
        connectors.check_catalog(raw)


def test_a_report_holds_closed_values_only():
    cats, needed = connectors.clean_report(
        [{"category": "gmail", "via": "  Gmail  "}, {"category": "people_lookup", "status": "needs_sign_in"}],
        ["job_search", "gmail"],
    )
    assert cats == [{"category": "gmail", "via": "Gmail", "status": "ok"},
                    {"category": "people_lookup", "via": "", "status": "needs_sign_in"}]
    assert needed == ["job_search"]  # what it has is never "needed"
    for bad in ([{"category": "dating"}], [{"category": "gmail", "via": "https://x.example"}],
                [{"category": "gmail", "status": "broken"}]):
        with pytest.raises(ValueError):
            connectors.clean_report(bad, [])


# ── The cards (pure) ─────────────────────────────────────────────────────────


def test_fix_comes_before_add_and_optional_is_never_pushed():
    rep = {"claude": _report("calendar", "people_lookup", status={"people_lookup": "needs_sign_in"})}
    cards = connectors.cards(rep, has_feed=False, state={}, setup_done=False, now=NOW)
    assert _keys(cards) == ["fix:claude:people_lookup", "add:calendar_feed", "add:claude:gmail",
                            "add:claude:job_search"]
    assert cards[0]["title"] == "a people lookup in Claude needs you to sign in again."
    assert cards[2]["suggested"]["name"] == "Gmail" and cards[2]["link"].startswith("https://claude.ai/")


def test_a_people_lookup_card_names_a_free_one_first_and_lists_the_rest():
    [card] = [c for c in connectors.cards({"claude": _report("gmail", "job_search")}, has_feed=True, state={},
                                          setup_done=False, now=NOW) if c["category"] == "people_lookup"]
    assert card["suggested"]["tier"] == "recommended"
    assert {e["name"] for e in card["alternatives"]} >= {"Hunter", "Apollo.io"}
    assert [e["free"] for e in connectors.suggestions("claude", "people_lookup")][0] is True


def test_after_setup_a_recommended_card_needs_a_run_that_needed_it_and_one_new_a_week():
    quiet = connectors.cards({"claude": _report("gmail")}, has_feed=True, state={}, setup_done=True, now=NOW)
    assert quiet == []
    both = {"claude": _report("gmail", needed=("job_search", "people_lookup"))}
    first = connectors.cards(both, has_feed=True, state={}, setup_done=True, now=NOW)
    assert _keys(first) == ["add:claude:job_search"]  # one new suggestion a week
    shown = {"add:claude:job_search": {"first_shown_at": NOW.isoformat(timespec="seconds"), "dismissed_at": None}}
    assert _keys(connectors.cards(both, has_feed=True, state=shown, setup_done=True, now=NOW)) == ["add:claude:job_search"]
    later = connectors.cards(both, has_feed=True, state=shown, setup_done=True, now=NOW + timedelta(days=8))
    assert _keys(later) == ["add:claude:job_search", "add:claude:people_lookup"]


def test_an_essential_card_ignores_the_weekly_cap_and_a_dismissal_is_remembered():
    shown = {"add:claude:job_search": {"first_shown_at": NOW.isoformat(timespec="seconds"), "dismissed_at": None}}
    rep = {"claude": _report(needed=("people_lookup",))}
    assert "add:claude:gmail" in _keys(connectors.cards(rep, has_feed=True, state=shown, setup_done=True, now=NOW))
    gone = {**shown, "add:claude:gmail": {"first_shown_at": "x", "dismissed_at": "y"}}
    assert "add:claude:gmail" not in _keys(connectors.cards(rep, has_feed=True, state=gone, setup_done=True, now=NOW))


# ── Through the real doors ───────────────────────────────────────────────────


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_a_report_is_stored_once_shown_on_the_web_and_dismissed_for_good(authenticated_async_context):
    report = {"assistant": "claude", "categories": [{"category": "job_search", "via": "Indeed"},
                                                     {"category": "people_lookup", "status": "not_on_plan"}]}
    async with authenticated_async_context() as client:
        async with await _agent(client) as agent:
            first = await agent.post("/api/capabilities", json=report)
            assert first.status_code == 200, first.text
            body = first.json()
            assert body["saved"] is True and body["missing_essential"] == ["calendar_feed", "claude:gmail"]
            assert body["failing"] == ["claude:people_lookup:not_on_plan"]
            assert (await agent.post("/api/capabilities", json=report)).json()["saved"] is False  # idempotent
            assert (await agent.get("/api/connectors")).status_code in (401, 403)  # the web view is session only
            refused = await agent.post("/api/capabilities", json={"assistant": "claude",
                                                                   "categories": [{"category": "dating"}]})
            assert refused.status_code == 422
        view = (await client.get("/api/connectors")).json()
        assert [c["key"] for c in view["cards"]] == ["fix:claude:people_lookup", "add:calendar_feed", "add:claude:gmail"]
        assert view["assistants"][0]["connected"][0] == {"category": "job_search", "via": "Indeed", "status": "ok"}
        assert "gmail" in view["assistants"][0]["missing"]
        assert (await client.post("/api/connectors/dismiss", json={"key": "add:claude:gmail"})).status_code == 204
        assert "add:claude:gmail" not in [c["key"] for c in (await client.get("/api/connectors")).json()["cards"]]
        assert (await client.post("/api/connectors/dismiss", json={"key": "drop table"})).status_code == 422
        doctor = (await client.get("/api/doctor")).json()["connectors"]
        assert doctor["failing"] == ["claude:people_lookup:not_on_plan"]


@pytest.mark.asyncio
async def test_the_mcp_tool_reports_like_the_route(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    tools = m.build_server()._tool_manager
    token = m._current_user.set(
        CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc")
    )
    try:
        async with authenticated_async_context() as client:
            out = await tools.get_tool("report_capabilities").fn(
                assistant="chatgpt", categories=[{"category": "gmail", "via": "Gmail"}], needed=["job_search"],
            )
            assert out["saved"] is True and out["missing_recommended"] == ["chatgpt:job_search", "chatgpt:people_lookup"]
            with pytest.raises(Exception):  # noqa: B017 - an assistant outside the closed set is refused
                await tools.get_tool("report_capabilities").fn(assistant="siri", categories=[])
            keys = [c["key"] for c in (await client.get("/api/connectors")).json()["cards"]]
            assert keys == ["add:calendar_feed", "add:chatgpt:job_search", "add:chatgpt:people_lookup"]  # setup
    finally:
        m._current_user.reset(token)
