"""Feedback slice 5 - fit records, reposts among your own brings, and the
``patterns`` / ``skill_gaps`` views of ``stats``.

Every assertion is a hand count of the history the test builds (hard rule 21:
real values, never "the key exists"). Past dates are made by backdating
``applications.created_at`` with a direct UPDATE in the TEST only.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.services.applications import fit_patterns
from src.services.applications.fit_record import fit_view
from src.services.applications.reposts import title_identity_key

_AD = {
    "title": "Data Engineer",
    "company": "Acme",
    "location": "London",
    "apply_url": "https://acme.example/jobs/10101",
    "description": "Build the pipelines. Python, dbt, Snowflake.",
}


async def _bring(client: AsyncClient, **over: Any) -> dict[str, Any]:
    resp = await client.post("/api/jobs/bring", json={**_AD, **over})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _app(client: AsyncClient, n: int) -> int:
    """A distinct job per n (own company + link) so nothing collides."""
    body = await _bring(
        client, title=f"Engineer {n}", company=f"Company{n}", apply_url=f"https://c{n}.example/jobs/{20000 + n}",
    )
    return int(body["application_id"])


async def _fit(client: AsyncClient, app_id: int, **body: Any):
    return await client.put(f"/api/applications/{app_id}/fit", json=body)


async def _event(client: AsyncClient, app_id: int, event_type: str, **extra: Any) -> dict[str, Any]:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _detail(client: AsyncClient, app_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{app_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _sql(sql: str, params: tuple[Any, ...] = ()) -> None:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _backdate(app_id: int, days: float) -> str:
    when = datetime.now(timezone.utc) - timedelta(days=days)
    _sql("UPDATE applications SET created_at = ? WHERE id = ?", (when.isoformat(), app_id))
    return when.date().isoformat()


def _mcp_tools():
    pytest.importorskip("mcp")
    from src.api import mcp_server

    return mcp_server, mcp_server.build_server()._tool_manager


def _as_user(mcp_server, user_id: str):
    return mcp_server._current_user.set(
        mcp_server.CurrentUser(id=user_id, email="e2e@example.com", email_verified=True, auth_via="token",
                               actor_name="cc")
    )


async def _second_user_session_cookie(email: str = "second@example.com") -> str:
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.repositories import pgsync

    sync_client = TestClient(app)
    r = sync_client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"})
    assert r.status_code == 201, r.text
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
    conn.commit()
    conn.close()
    lr = sync_client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    assert lr.status_code == 200, lr.text
    cookie = sync_client.cookies.get("job360_session")
    assert cookie
    sync_client.close()
    return cookie


def _session_client(cookie: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie})


# ── 1. the save_fit contract ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_score_needs_a_reason_and_a_source(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _app(client, 1)
        r = await _fit(client, app_id, score=70)
        assert r.status_code == 422 and "a score needs a reason" in r.text
        r = await _fit(client, app_id, score=70, verdict="   ", score_source="full_ad")
        assert r.status_code == 422 and "a score needs a reason" in r.text
        r = await _fit(client, app_id, score=70, verdict="x" * 141, score_source="full_ad")
        assert r.status_code == 422 and "at most 140" in r.text
        r = await _fit(client, app_id, score=70, verdict="fine")
        assert r.status_code == 422 and "a score needs score_source" in r.text
        r = await _fit(client, app_id, score=70, verdict="fine", score_source="guess")
        assert r.status_code == 422 and "full_ad" in r.text and "listing" in r.text and "owner" in r.text
        r = await _fit(client, app_id, score_source="full_ad")
        assert r.status_code == 422 and "score_source needs a score" in r.text
        # a refused call changed NOTHING: no fit, no event
        detail = await _detail(client, app_id)
        assert detail["fit"] is None
        assert [e for e in detail["events"] if e["event_type"] == "fit_judged"] == []

        reason = "r" * 140
        ok = await _fit(client, app_id, score=70, verdict=reason, score_source="listing")
        assert ok.status_code == 200, ok.text
        assert ok.json()["fit"]["verdict"] == reason and ok.json()["fit"]["score_source"] == "listing"


@pytest.mark.asyncio
async def test_unscored_is_null_never_zero_and_verdict_stays_optional(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _app(client, 1)
        r = await _fit(client, app_id)
        assert r.status_code == 200, r.text
        fit = r.json()["fit"]
        assert fit["score"] is None and fit["score_source"] is None and fit["verdict"] is None
        long_note = "n" * 200
        r = await _fit(client, app_id, verdict=long_note)
        assert r.status_code == 200 and r.json()["fit"]["verdict"] == long_note
        assert (await _fit(client, app_id, verdict="n" * 201)).status_code == 422


# ── 2. gaps with hard / soft kinds ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_gaps_old_shape_and_kinds(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _app(client, 1)
        r = await _fit(client, app_id, gaps=["Kubernetes", "German"])
        assert r.status_code == 200, r.text
        fit = r.json()["fit"]
        assert fit["gaps"] == ["Kubernetes", "German"]
        assert fit["gaps_detail"] == [{"text": "Kubernetes", "kind": None}, {"text": "German", "kind": None}]

        r = await _fit(client, app_id, gaps=["SQL", {"text": "Go", "kind": "hard"}])
        fit = r.json()["fit"]
        assert fit["gaps"] == ["SQL", "Go"]
        assert [g["kind"] for g in fit["gaps_detail"]] == [None, "hard"]

        bad = await _fit(client, app_id, gaps=[{"text": "Go", "kind": "must"}])
        assert bad.status_code == 422 and "hard" in bad.text and "soft" in bad.text
        assert (await _fit(client, app_id, gaps=[{"kind": "hard"}])).status_code == 422
        assert (await _fit(client, app_id, gaps=[{"text": "x", "extra": 1}])).status_code == 422
        assert (await _fit(client, app_id, gaps=[{"text": "g" * 201}])).status_code == 422
        # nothing above replaced the last good save
        assert (await _detail(client, app_id))["fit"]["gaps"] == ["SQL", "Go"]

        # a fit saved before migration 0064 (no detail column) reads back from fit_gaps
        _sql("UPDATE applications SET fit_gaps_detail = NULL WHERE id = ?", (app_id,))
        old = (await _detail(client, app_id))["fit"]
        assert old["gaps_detail"] == [{"text": "SQL", "kind": None}, {"text": "Go", "kind": None}]


# ── 3. the four extras and jd_requirements ──────────────────────────────────

_EXTRAS = {
    "jd_requirements": ["5 years Python", {"text": "Kubernetes", "kind": "hard"}, {"text": "dbt", "kind": "soft"}],
    "archetype": "Platform – Данные",
    "remote_policy": "hybrid",
    "team_size": 120,
    "via": "agency",
}


@pytest.mark.asyncio
async def test_extras_are_stored_echoed_and_kept_in_the_event(authenticated_async_context, fixture_user_id):
    mcp_server, tools = _mcp_tools()
    async with authenticated_async_context() as client:
        app_id = await _app(client, 1)
        r = await _fit(client, app_id, score=64, verdict="decent", score_source="full_ad", **_EXTRAS)
        assert r.status_code == 200, r.text
        want_reqs = [
            {"text": "5 years Python", "kind": None},
            {"text": "Kubernetes", "kind": "hard"},
            {"text": "dbt", "kind": "soft"},
        ]
        detail = await _detail(client, app_id)
        fit = detail["fit"]
        assert fit["jd_requirements"] == want_reqs
        assert (fit["archetype"], fit["remote_policy"], fit["team_size"], fit["via"]) == (
            "Platform – Данные", "hybrid", 120, "agency",
        )
        assert fit["score_source"] == "full_ad"
        judged = [e for e in detail["events"] if e["event_type"] == "fit_judged"]
        payload = judged[0]["payload"]
        assert payload["jd_requirements"] == want_reqs and payload["archetype"] == "Platform – Данные"
        assert (payload["remote_policy"], payload["team_size"], payload["via"], payload["score_source"]) == (
            "hybrid", 120, "agency", "full_ad",
        )
        assert payload["gaps_detail"] == []

        ctx = _as_user(mcp_server, fixture_user_id)
        try:
            via_tool = await tools.get_tool("get_application").fn(app_id)
        finally:
            mcp_server._current_user.reset(ctx)
        assert via_tool["fit"]["jd_requirements"] == want_reqs and via_tool["fit"]["team_size"] == 120

        # a second save without extras clears the slot; the first event keeps them
        r = await _fit(client, app_id, score=64, verdict="decent", score_source="full_ad")
        cleared = r.json()["fit"]
        assert cleared["jd_requirements"] == [] and cleared["archetype"] is None
        assert (cleared["remote_policy"], cleared["team_size"], cleared["via"]) == (None, None, None)
        after = await _detail(client, app_id)
        first_event = [e for e in after["events"] if e["event_type"] == "fit_judged"][0]
        assert first_event["payload"]["team_size"] == 120


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"remote_policy": "office"},
        {"via": "recruiter"},
        {"team_size": 0},
        {"team_size": True},
        {"archetype": "a" * 81},
        {"jd_requirements": [f"req {i}" for i in range(51)]},
    ],
)
async def test_bad_extras_are_refused(authenticated_async_context, body):
    async with authenticated_async_context() as client:
        app_id = await _app(client, 1)
        r = await _fit(client, app_id, **body)
        assert r.status_code == 422, r.text
        assert (await _detail(client, app_id))["fit"] is None


@pytest.mark.asyncio
async def test_alignment_returns_the_same_fit_dict(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _app(client, 1)
        await _fit(client, app_id, score=64, verdict="decent", score_source="owner", gaps=[{"text": "Go", "kind": "hard"}],
                   **_EXTRAS)
        detail = await _detail(client, app_id)
        align = (await client.get(f"/api/applications/{app_id}/alignment")).json()
    assert align["fit"] == detail["fit"]
    assert align["fit"]["gaps_detail"] == [{"text": "Go", "kind": "hard"}]


@pytest.mark.asyncio
async def test_mcp_save_fit_equals_the_route(authenticated_async_context, fixture_user_id):
    mcp_server, tools = _mcp_tools()
    async with authenticated_async_context() as client:
        a = await _app(client, 1)
        b = await _app(client, 2)
        body = dict(score=55, verdict="ok", score_source="listing", gaps=["Go", {"text": "SQL", "kind": "soft"}],
                    **_EXTRAS)
        via_route = (await _fit(client, a, **body)).json()
        ctx = _as_user(mcp_server, fixture_user_id)
        try:
            via_tool = await tools.get_tool("save_fit").fn(b, **body)
        finally:
            mcp_server._current_user.reset(ctx)
    assert via_tool["fit"].keys() == via_route["fit"].keys()
    for key in via_route["fit"]:
        if key not in ("recorded_at", "recorded_by"):
            assert via_tool["fit"][key] == via_route["fit"][key], key
    assert via_tool["fit"]["via"] == "agency" and via_tool["fit"]["team_size"] == 120


# ── 5. save_artifact kind skill_gap_report ──────────────────────────────────


@pytest.mark.asyncio
async def test_skill_gap_report_is_an_artifact_kind(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _app(client, 1)
        url = f"/api/applications/{app_id}/artifacts"
        first = await client.post(url, json={"kind": "skill_gap_report", "text": "schema: 1\nLearn Go."})
        assert first.status_code == 201, first.text
        second = await client.post(url, json={"kind": "skill_gap_report", "text": "schema: 1\nLearn Go and SQL."})
        assert (first.json()["version_no"], second.json()["version_no"]) == (1, 2)
        assert second.json()["kind"] == "skill_gap_report"
        with_ats = await client.post(url, json={"kind": "skill_gap_report", "text": "x", "ats_score": 50})
        assert with_ats.status_code == 422
        assert (await client.post(url, json={"kind": "bogus", "text": "x"})).status_code == 422


# ── 6. repost among the jobs you brought ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_repost_under_another_link_is_flagged(authenticated_async_context, fixture_user_id):
    mcp_server, tools = _mcp_tools()
    async with authenticated_async_context() as client:
        first = await _bring(client)
        assert first["repost"] is None
        first_id = int(first["application_id"])
        seen = _backdate(first_id, 30)
        second = await _bring(client, title="Engineer, Data", apply_url="https://acme.example/jobs/20202")
        assert second["existing"] is False and second["application_id"] != first_id
        assert second["repost"] == {
            "label": "among jobs you brought", "first_seen": seen, "days_since": 30,
            "application_id": first_id, "window_days": 90,
        }
        # the same facts through the MCP tool
        ctx = _as_user(mcp_server, fixture_user_id)
        try:
            out = await tools.get_tool("bring_job").fn(
                title="data ENGINEER!", company="Acme", description="Build the pipelines.",
                apply_url="https://acme.example/jobs/30303",
            )
        finally:
            mcp_server._current_user.reset(ctx)
        assert out["repost"]["first_seen"] == seen and out["repost"]["application_id"] == first_id
        assert out["repost"]["days_since"] == 30


@pytest.mark.asyncio
async def test_the_409_carries_the_repost_sentence(authenticated_async_context):
    async with authenticated_async_context() as client:
        first = await _bring(client)
        seen = _backdate(int(first["application_id"]), 30)
        again = await client.post("/api/jobs/bring", json={**_AD, "apply_url": "https://acme.example/jobs/20202"})
        assert again.status_code == 409, again.text
        assert "Among jobs you brought" in again.json()["detail"]
        assert seen in again.json()["detail"] and "30 days ago" in again.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("days,expected", [(0, False), (91, False), (90, True), (1, True)])
async def test_the_repost_window(authenticated_async_context, days, expected):
    async with authenticated_async_context() as client:
        first = await _bring(client)
        if days:
            _backdate(int(first["application_id"]), days)
        second = await _bring(client, title="Engineer, Data", apply_url="https://acme.example/jobs/20202")
        assert (second["repost"] is not None) is expected
        if expected:
            assert second["repost"]["days_since"] == days


@pytest.mark.asyncio
async def test_no_repost_for_the_same_link_blank_links_or_an_exact_rebring(authenticated_async_context):
    async with authenticated_async_context() as client:
        first = await _bring(client)
        _backdate(int(first["application_id"]), 30)
        # exact re-bring: same title, same link
        assert (await _bring(client))["repost"] is None
        # the same link under a new title reuses the job - not a repost
        retitled = await _bring(client, title="Engineer, Data")
        assert retitled["repost"] is None
        # a blank link on the new bring
        blank = await _bring(client, title="Data Engineer II", apply_url="")
        assert blank["repost"] is None

    async with authenticated_async_context() as client:
        other = await _bring(client, company="Blankco", apply_url="")
        _backdate(int(other["application_id"]), 30)
        later = await _bring(client, company="Blankco", title="Engineer, Data", apply_url="https://blank.example/jobs/50505")
        assert later["repost"] is None


@pytest.mark.asyncio
async def test_a_shared_catalog_link_does_not_mask_a_repost(authenticated_async_context, fixture_user_id):
    """The comparison is against THIS user's own application link, not the
    joined catalog url (which another user's bring may share)."""
    from src.api.dependencies import get_db
    from src.services.applications import reposts

    async with authenticated_async_context() as client:
        first = await _bring(client)
        first_id = int(first["application_id"])
        _backdate(first_id, 30)
        # the shared catalog row carries the link the next bring uses; the user's own
        # application link stays the original one
        _sql("UPDATE jobs SET apply_url = ? WHERE id = (SELECT job_id FROM applications WHERE id = ?)",
             ("https://acme.example/jobs/20202", first_id))
        out = await reposts.repost_of(
            await get_db(), fixture_user_id, company="Acme", title="Engineer, Data",
            apply_url="https://acme.example/jobs/20202",
        )
    assert out is not None and out["application_id"] == first_id


@pytest.mark.asyncio
async def test_applications_older_than_the_window_are_not_even_read(authenticated_async_context):
    async with authenticated_async_context() as client:
        first = await _bring(client)
        _backdate(int(first["application_id"]), 400)
        second = await _bring(client, title="Engineer, Data", apply_url="https://acme.example/jobs/20202")
        assert second["repost"] is None


@pytest.mark.asyncio
async def test_another_users_bring_is_never_a_repost(authenticated_async_context):
    async with authenticated_async_context() as client:
        cookie = await _second_user_session_cookie()
        async with _session_client(cookie) as other:
            theirs = await _bring(other)
            _backdate(int(theirs["application_id"]), 30)
        mine = await _bring(client, title="Engineer, Data", apply_url="https://acme.example/jobs/20202")
        assert mine["repost"] is None


@pytest.mark.asyncio
async def test_a_failing_repost_check_never_fails_a_bring(authenticated_async_context, monkeypatch):
    async with authenticated_async_context() as client:
        first = await _bring(client)
        _backdate(int(first["application_id"]), 30)

        def boom(_company: str) -> str:
            raise RuntimeError("boom")

        monkeypatch.setattr("src.services.applications.kit.normalized_company_key", boom)
        second = await _bring(client, title="Engineer, Data", apply_url="https://acme.example/jobs/20202")
        assert second["repost"] is None


def test_title_identity_key_mirrors_detect_reposts():
    assert title_identity_key("Data Engineer") == title_identity_key("Engineer, Data") == "data engineer"
    assert title_identity_key("Sénior  Engineer (m/f/d)") == "d engineer f m senior"
    assert title_identity_key("Data Data Engineer") == "data engineer"
    assert title_identity_key("  !!!  ") == "!!!"


# ── 7. patterns ─────────────────────────────────────────────────────────────


def _row(app_id: int, status: str = "applied", *, applied: int = 1, **over: Any) -> dict[str, Any]:
    return {
        "id": app_id, "status": status, "applied": applied, "job_remote": None, "fit_recorded_at": None,
        "fit_score": None, "fit_gaps": "[]", "fit_gaps_detail": None, "fit_archetype": None,
        "fit_remote_policy": None, "fit_team_size": None, "fit_via": None, **over,
    }


def _by_key(groups: list[dict[str, Any]]) -> dict[Optional[str], dict[str, Any]]:
    return {g["key"]: g for g in groups}


async def _sent(client: AsyncClient, n: int, *events: str, **fit: Any) -> int:
    """An application with a fit (when given) and the listed status events in order."""
    app_id = await _app(client, n)
    if fit:
        r = await _fit(client, app_id, **fit)
        assert r.status_code == 200, r.text
    for ev in events:
        if ev == "rejected":
            await _event(client, app_id, ev, payload={"reasons": ["Location"]} if n % 2 else {})
        else:
            await _event(client, app_id, ev)
    return app_id


@pytest.mark.asyncio
async def test_patterns_below_the_gate(authenticated_async_context):
    async with authenticated_async_context() as client:
        for n in range(4):
            await _sent(client, n, "applied")
        body = (await client.get("/api/applications/stats", params={"view": "patterns"})).json()
    block = body["patterns"]
    assert block["available"] is False and block["submitted"] == 4 and block["threshold"] == 5
    assert "4/5" in block["text"]
    assert block["overall"] is None and block["signals"] is None and block["by_archetype"] is None
    assert body["skill_gaps"] is None


@pytest.mark.asyncio
async def test_patterns_counts_end_to_end(authenticated_async_context):
    async with authenticated_async_context() as client:
        # Data: replied, rejected(reason), rejected(reason)   Platform: rejected x2   none: applied only
        await _sent(client, 1, "applied", "replied", archetype="Data", team_size=50, via="direct")
        await _sent(client, 3, "applied", "rejected", archetype=" data ", team_size=51, via="direct")
        await _sent(client, 5, "applied", "rejected", archetype="Data", team_size=500, via="agency")
        await _sent(client, 7, "applied", "rejected", archetype="Platform", team_size=501)
        await _sent(client, 9, "applied", "rejected", archetype="Platform")
        await _sent(client, 11, "applied")
        ghost = await _sent(client, 13, "applied")
        await _event(client, ghost, "ghosted")
        withdrawn = await _sent(client, 14, "applied")
        await _event(client, withdrawn, "withdrawn")
        # an applied event that was retracted does not count as sent
        retracted = await _app(client, 15)
        ev = await _event(client, retracted, "applied")
        await _event(client, retracted, "note", detail="removed", corrects_event_id=ev["event_id"])
        # a simulation is a test: counted nowhere
        sim = await _app(client, 16)
        await _event(client, sim, "simulation")
        await _event(client, sim, "applied")
        # never sent
        await _app(client, 17)

        body = (await client.get("/api/applications/stats", params={"view": "patterns"})).json()
    p = body["patterns"]
    assert p["available"] is True
    assert (p["submitted"], p["withdrawn_excluded"]) == (7, 1)
    o = p["overall"]
    assert (o["submitted"], o["positive"], o["negative"], o["awaiting"], o["no_answer"], o["decided"]) == (7, 1, 4, 1, 1, 5)
    assert o["conversion_rate"] == round(1 / 7, 3) and o["decided_rate"] == 0.2 and o["small_sample"] is True

    arch = _by_key(p["by_archetype"])
    assert set(arch) == {"data", "platform", None}
    assert (arch["data"]["submitted"], arch["data"]["positive"], arch["data"]["negative"]) == (3, 1, 2)
    assert arch["data"]["label"] == "Data" and arch["data"]["decided_rate"] == round(1 / 3, 3)
    assert (arch["platform"]["submitted"], arch["platform"]["positive"], arch["platform"]["decided"]) == (2, 0, 2)
    assert arch[None]["label"] == "Not set" and arch[None]["submitted"] == 2
    assert arch[None]["no_answer"] == 1 and arch[None]["decided"] == 0 and arch[None]["decided_rate"] is None
    assert [g["key"] for g in p["by_archetype"]] == ["data", "platform", None]

    team = _by_key(p["by_team_size"])
    assert (team["1-50"]["submitted"], team["51-500"]["submitted"], team["501+"]["submitted"]) == (1, 2, 1)
    assert team[None]["submitted"] == 3
    via = _by_key(p["by_via"])
    assert (via["direct"]["submitted"], via["agency"]["submitted"], via[None]["submitted"]) == (2, 1, 4)

    kinds = {(s["dimension"], s["kind"]): s for s in p["signals"]}
    best = kinds[("archetype", "best")]
    assert (best["key"], best["positive"], best["decided"]) == ("data", 1, 3)
    assert best["points_vs_overall"] == 13 and best["clear_lead"] is True
    assert best["text"] == '"Data": 1 of 3 decided applications advanced (33%); overall 20%.'
    none_adv = kinds[("archetype", "no_advance")]
    assert none_adv["key"] == "platform"
    assert none_adv["text"] == '"Platform": none of 2 decided applications advanced (2 sent).'
    assert not [s for s in p["signals"] if s["key"] is None], "a Not set group is never a signal"
    assert not [s for s in p["signals"] if s["dimension"] == "via" and s["key"] is None]

    reasons = p["rejection_reasons"]
    # rejections of the apps numbered 3, 5, 7 and 9 carry the reason (odd n); " data " is app n=3.
    assert reasons == [{"reason": "Location", "count": 4, "application_ids": reasons[0]["application_ids"]}]
    assert len(reasons[0]["application_ids"]) == 4 and p["reason_base"] == 4
    text = json.dumps(p).lower()
    assert "avoid" not in text and "double down" not in text
    assert "no answer" in p["note"].lower() or "ghosted is no answer" in p["note"].lower()


@pytest.mark.asyncio
async def test_job_remote_true_with_no_policy_groups_as_remote(authenticated_async_context):
    async with authenticated_async_context() as client:
        for n in range(5):
            app_id = await _sent(client, n, "applied")
            _sql("UPDATE applications SET job_remote = ? WHERE id = ?", (n < 3, app_id))
        # a stated policy beats the stored fact
        stated = await _sent(client, 6, "applied", remote_policy="onsite")
        _sql("UPDATE applications SET job_remote = ? WHERE id = ?", (True, stated))
        p = (await client.get("/api/applications/stats", params={"view": "patterns"})).json()["patterns"]
    remote = _by_key(p["by_remote_policy"])
    assert (remote["remote"]["submitted"], remote["onsite"]["submitted"], remote[None]["submitted"]) == (3, 1, 2)


def test_patterns_signal_rules_on_exact_counts():
    # overall 5 decided, 2 advanced (40%). "A": 2 of 4 (50%) = +10 exactly.
    rows = [_row(i, "replied", fit_archetype="A") for i in (1, 2)]
    rows += [_row(i, "rejected", fit_archetype="A") for i in (3, 4)]
    rows += [_row(5, "rejected", fit_archetype="B")]
    out = fit_patterns.patterns(rows, [])
    best = [s for s in out["signals"] if s["kind"] == "best"]
    assert len(best) == 1 and best[0]["key"] == "a"
    assert best[0]["points_vs_overall"] == 10 and best[0]["clear_lead"] is True

    # overall 11 decided, 10 advanced (90.9%). "A" 2 of 2 (100%) = +9: a lead, but not a clear one.
    rows = [_row(1, "replied", fit_archetype="A"), _row(2, "replied", fit_archetype="A")]
    rows += [_row(i, "replied", fit_archetype="B") for i in range(3, 11)]
    rows += [_row(11, "rejected", fit_archetype="B")]
    out = fit_patterns.patterns(rows, [])
    best = [s for s in out["signals"] if s["dimension"] == "archetype" and s["kind"] == "best"][0]
    assert best["key"] == "a" and best["points_vs_overall"] == 9 and best["clear_lead"] is False


def test_a_single_decided_application_makes_no_signal_and_signals_are_capped():
    rows = [_row(1, "replied", fit_archetype="A")] + [_row(i, "applied", fit_archetype="B") for i in range(2, 6)]
    out = fit_patterns.patterns(rows, [])
    assert out["available"] is True and out["signals"] == []

    rows = []
    for i in range(1, 9):
        rows.append(_row(i, "replied", fit_archetype="A", fit_remote_policy="remote", fit_team_size=10, fit_via="direct"))
        rows.append(_row(i + 100, "rejected", fit_archetype="B", fit_remote_policy="hybrid", fit_team_size=900,
                         fit_via="agency"))
    out = fit_patterns.patterns(rows, [])
    assert len(out["signals"]) == 8
    assert {(s["dimension"], s["kind"]) for s in out["signals"]} == {
        (d, k) for d in ("archetype", "remote_policy", "team_size", "via") for k in ("best", "no_advance")
    }


def test_team_size_buckets_and_ghosted_are_exact():
    rows = [_row(i, "applied", fit_team_size=n) for i, n in enumerate((50, 51, 500, 501, None), start=1)]
    out = fit_patterns.patterns(rows, [])
    team = {g["key"]: g["submitted"] for g in out["by_team_size"]}
    assert team == {"1-50": 1, "51-500": 2, "501+": 1, None: 1}
    ghost = fit_patterns.patterns([_row(i, "ghosted") for i in range(1, 6)], [])
    assert ghost["overall"]["no_answer"] == 5 and ghost["overall"]["negative"] == 0
    assert ghost["overall"]["decided"] == 0 and ghost["overall"]["decided_rate"] is None


def test_rejection_reasons_are_exact_words_counted_once_per_application():
    rows = [_row(i, "rejected") for i in range(1, 6)]
    ev = lambda app, *words: {"application_id": app, "payload": json.dumps({"reasons": list(words)})}  # noqa: E731
    reasons = [ev(1, "Location", " location "), ev(2, "location"), ev(3, "Seniority"), ev(99, "Location")]
    out = fit_patterns.patterns(rows, reasons)
    assert out["rejection_reasons"] == [
        {"reason": "Location", "count": 2, "application_ids": [1, 2]},
        {"reason": "Seniority", "count": 1, "application_ids": [3]},
    ]
    assert out["reason_base"] == 3


@pytest.mark.asyncio
async def test_a_retracted_rejection_reason_is_not_counted(authenticated_async_context):
    async with authenticated_async_context() as client:
        ids = [await _sent(client, n, "applied") for n in range(5)]
        keep = await _event(client, ids[0], "rejected", payload={"reasons": ["Seniority"]})
        gone = await _event(client, ids[1], "rejected", payload={"reasons": ["Seniority"]})
        await _event(client, ids[1], "note", detail="removed", corrects_event_id=gone["event_id"])
        p = (await client.get("/api/applications/stats", params={"view": "patterns"})).json()["patterns"]
    assert keep["event_id"] and p["rejection_reasons"] == [
        {"reason": "Seniority", "count": 1, "application_ids": [ids[0]]}
    ]
    assert p["reason_base"] == 1


# ── 8. skill_gaps ───────────────────────────────────────────────────────────


def _fit_row(app_id: int, score: Optional[int], *gaps: Any) -> dict[str, Any]:
    items = [g if isinstance(g, dict) else {"text": g, "kind": None} for g in gaps]
    return _row(
        app_id, fit_recorded_at="2026-10-01T00:00:00+00:00", fit_score=score,
        fit_gaps=json.dumps([i["text"] for i in items]), fit_gaps_detail=json.dumps(items),
    )


def test_skill_gaps_gate_and_tiers():
    assert fit_patterns.skill_gaps([_fit_row(i, 50, "x") for i in range(1, 5)], [])["available"] is False
    gate = fit_patterns.skill_gaps([_fit_row(i, 50, "x") for i in range(1, 5)], [])
    assert gate["scored"] == 4 and gate["threshold"] == 5 and "4/5" in gate["text"] and gate["gaps"] is None

    # five low-fit fits, "terraform" on 3, "spark" on 1 (upskill.mjs self-test shape)
    rows = [_fit_row(1, 40, "Terraform"), _fit_row(2, 50, " terraform "), _fit_row(3, 60, "terraform", "Spark"),
            _fit_row(4, 70), _fit_row(5, 30)]
    out = fit_patterns.skill_gaps(rows, [])
    assert out["available"] is True and (out["fits_read"], out["scored"], out["total_low_fit"]) == (5, 5, 5)
    gaps = {g["key"]: g for g in out["gaps"]}
    assert gaps["terraform"]["tier"] == "Critical" and gaps["terraform"]["low_fit"] == 3
    assert gaps["terraform"]["applications"] == 3 and gaps["terraform"]["application_ids"] == [1, 2, 3]
    assert gaps["terraform"]["gap"] == "Terraform"
    assert gaps["spark"]["tier"] == "Low" and gaps["spark"]["low_fit"] == 1
    assert [g["key"] for g in out["gaps"]] == ["terraform", "spark"]


def test_skill_gaps_weights_known_skills_and_markers():
    rows = [_fit_row(1, 60, {"text": "Go", "kind": "hard"}), _fit_row(2, None, {"text": "Go", "kind": "soft"}),
            _fit_row(3, 90, "Go", "Python"), _fit_row(4, 85), _fit_row(5, 95), _fit_row(6, 70, "Python")]
    out = fit_patterns.skill_gaps(rows, ["python"])
    assert out["scored"] == 5 and out["fits_read"] == 6 and out["excluded_as_known"] == 1
    assert [g["key"] for g in out["gaps"]] == ["go"], "a known skill is not a gap"
    go = out["gaps"][0]
    # score 60 -> (100-60)/20 = 2.0; unscored -> 1.0; score 90 -> 0.5
    assert go["weighted"] == 3.5 and go["applications"] == 3 and go["low_fit"] == 1
    assert (go["hard"], go["soft"], go["unmarked"]) == (1, 1, 1)


@pytest.mark.asyncio
async def test_skill_gaps_end_to_end_with_profile_skills(authenticated_async_context, fixture_user_id):
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(cv_data=CVData(raw_text="Python.", name="Ada", skills=["Python"])),
        fixture_user_id, source_action="cv_upload",
    )
    async with authenticated_async_context() as client:
        for n, score in enumerate((40, 50, 60, 70, 85), start=1):
            app_id = await _app(client, n)
            gaps: list[Any] = [{"text": "Terraform", "kind": "hard"}] if n <= 3 else []
            gaps.append("Python")
            r = await _fit(client, app_id, score=score, verdict="read", score_source="full_ad", gaps=gaps)
            assert r.status_code == 200, r.text
        body = (await client.get("/api/applications/stats", params={"view": "skill_gaps"})).json()
    assert body["patterns"] is None
    sg = body["skill_gaps"]
    assert sg["available"] is True and sg["scored"] == 5 and sg["total_low_fit"] == 4
    assert sg["excluded_as_known"] == 1
    assert [g["key"] for g in sg["gaps"]] == ["terraform"]
    t = sg["gaps"][0]
    assert (t["applications"], t["low_fit"], t["hard"], t["tier"]) == (3, 3, 3, "Critical")
    assert t["weighted"] == round((60 + 50 + 40) / 20, 2)


@pytest.mark.asyncio
async def test_skill_gaps_known_includes_typed_additional_skills(authenticated_async_context, fixture_user_id):
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python.", name="Ada", skills=["Python"]),
            preferences=UserPreferences(additional_skills=["Terraform"]),
        ),
        fixture_user_id, source_action="cv_upload",
    )
    async with authenticated_async_context() as client:
        for n, score in enumerate((40, 50, 60, 70, 85), start=1):
            app_id = await _app(client, n)
            r = await _fit(client, app_id, score=score, verdict="read", score_source="full_ad", gaps=["Terraform"])
            assert r.status_code == 200, r.text
        sg = (await client.get("/api/applications/stats", params={"view": "skill_gaps"})).json()["skill_gaps"]
    assert sg["gaps"] == [] and sg["excluded_as_known"] == 1


# ── 9/10. isolation and the dispatcher ──────────────────────────────────────


@pytest.mark.asyncio
async def test_other_users_fits_and_reasons_never_appear(authenticated_async_context):
    async with authenticated_async_context() as client:
        cookie = await _second_user_session_cookie()
        async with _session_client(cookie) as other:
            for n in range(6):
                app_id = await _app(other, n)
                await _fit(other, app_id, score=30, verdict="b", score_source="owner",
                           gaps=["b-only-gap"], archetype="b-only-arch")
                await _event(other, app_id, "applied")
                await _event(other, app_id, "rejected", payload={"reasons": ["b-only-reason"]})
        mine_ids = [await _sent(client, n, "applied") for n in range(100, 106)]
        for i in mine_ids[:5]:
            await _fit(client, i, score=40, verdict="a", score_source="full_ad", gaps=["a-gap"])
        body = (await client.get("/api/applications/stats", params={"view": "all"})).json()
    flat = json.dumps([body["patterns"], body["skill_gaps"]]).lower()
    assert "b-only" not in flat
    assert body["patterns"]["submitted"] == 6 and body["patterns"]["rejection_reasons"] == []
    assert [g["key"] for g in body["skill_gaps"]["gaps"]] == ["a-gap"]


@pytest.mark.asyncio
async def test_the_dispatcher_adds_blocks_and_validates_first(authenticated_async_context):
    async with authenticated_async_context() as client:
        plain = (await client.get("/api/applications/stats")).json()
        assert plain["patterns"] is None and plain["skill_gaps"] is None
        both = (await client.get("/api/applications/stats", params={"view": "all"})).json()
        assert both["patterns"]["available"] is False and both["skill_gaps"]["available"] is False
        only = (await client.get("/api/applications/stats", params={"view": "skill_gaps"})).json()
        assert only["patterns"] is None and only["skill_gaps"]["scored"] == 0

        assert "patterns" in settings.STATS_VIEWS and "skill_gaps" in settings.STATS_VIEWS
        assert settings.STATS_VIEWS[-1] == "all"
        # a bad view is refused BEFORE a rate-limit slot is spent
        for _ in range(settings.STATS_MAX_PER_HOUR + 2):
            assert (await client.get("/api/applications/stats", params={"view": "bogus"})).status_code == 422
        assert (await client.get("/api/applications/stats", params={"view": "patterns"})).status_code == 200
        wrong = await client.get("/api/applications/stats", params={"view": "patterns", "company": "Acme"})
        assert wrong.status_code == 422


@pytest.mark.asyncio
async def test_mcp_stats_views_equal_the_route(authenticated_async_context, fixture_user_id):
    mcp_server, tools = _mcp_tools()

    fence = re.compile(r"<<untrusted-text ([0-9a-f]{8})>>\n(.*)\n<<end untrusted-text \1>>", re.S)

    def strip(value: Any) -> Any:
        # Gap-audit #15: the tool fences the employer's exact words (`_untrusted`); the route stays raw.
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if v is not None and k != "_untrusted"}
        if isinstance(value, list):
            return [strip(v) for v in value]
        if isinstance(value, str) and (m := fence.fullmatch(value)):
            return m.group(2)
        return value

    async with authenticated_async_context() as client:
        for n in range(5):
            app_id = await _sent(client, n, "applied", "rejected" if n % 2 else "replied")
            await _fit(client, app_id, score=50 + n, verdict="r", score_source="full_ad",
                       gaps=[{"text": "Go", "kind": "hard"}], archetype="Data")
        ctx = _as_user(mcp_server, fixture_user_id)
        try:
            for view, key in (("patterns", "patterns"), ("skill_gaps", "skill_gaps")):
                via_tool = await tools.get_tool("stats").fn(view=view)
                via_route = (await client.get("/api/applications/stats", params={"view": view})).json()
                assert via_tool[key]["available"] is True
                assert strip(via_tool[key]) == strip(via_route[key])
                if view == "patterns":
                    reason = via_tool[key]["rejection_reasons"][0]
                    assert reason["_untrusted"] == ["reason"] and fence.fullmatch(reason["reason"])
                    assert via_route[key]["rejection_reasons"][0]["reason"] == "Location"
        finally:
            mcp_server._current_user.reset(ctx)


def test_fit_view_is_none_without_a_fit():
    assert fit_view({"fit_recorded_at": None}) is None
