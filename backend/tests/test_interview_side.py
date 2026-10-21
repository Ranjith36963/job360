"""Feedback loop slice 4 (interview side): interview_session / process_friction /
assessment events, the offer figures, the silence note, the three artifact kinds,
the digest / friction / assessments stats views and doctor's silent_after_interview.

Value-presence (hard rule #21): every assertion reads a stored or returned VALUE.
Helpers are copied, never imported from another test module (schema isolation)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings

_BANNED = ("illegal", "unlawful", "broke the law", "discrimination occurred")


def _ad(n: int, company: str = "Northwind") -> dict[str, Any]:
    return {
        "title": f"Data Engineer {n}", "company": company, "location": "Remote",
        "apply_url": f"https://{company.lower()}.example/careers/{n}",
        "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
    }


async def _bring(client: AsyncClient, n: int = 0, company: str = "Northwind") -> int:
    resp = await client.post("/api/jobs/bring", json=_ad(n, company))
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _post(client: AsyncClient, app_id: int, event_type: str, **extra: Any) -> Any:
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, **extra})


async def _ev(client: AsyncClient, app_id: int, event_type: str, **extra: Any) -> dict[str, Any]:
    resp = await _post(client, app_id, event_type, **extra)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _session(client: AsyncClient, app_id: int, day: str, round_: str = "technical", **payload: Any) -> int:
    body = await _ev(client, app_id, "interview_session", payload={"round": round_, "date": day, **payload})
    return int(body["event_id"])


async def _artifact(client: AsyncClient, app_id: int, kind: str, text: str = "text") -> dict[str, Any]:
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": kind, "text": text})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _sql(query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        cur = conn.execute(query, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
        return [tuple(r) for r in rows]
    finally:
        conn.close()


def _stored(event_id: int) -> dict[str, Any]:
    return json.loads(_sql("SELECT payload FROM application_events WHERE id = ?", (event_id,))[0][0])


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


async def _second_user_client(email: str = "second@example.com") -> AsyncClient:
    from fastapi.testclient import TestClient

    from src.api.main import app

    sync_client = TestClient(app)
    r = sync_client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"})
    assert r.status_code == 201, r.text
    _sql("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
    lr = sync_client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    assert lr.status_code == 200, lr.text
    cookie = sync_client.cookies.get("job360_session")
    sync_client.close()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie})


async def _build(user_id: str, view: str, **kw: Any) -> dict[str, Any]:
    from src.api.dependencies import get_db
    from src.services.applications import interview_counts

    return await interview_counts.build_view(await get_db(), user_id, view, **kw)


# ── round trips ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_interview_session_round_trips_normalised(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        quote = "We’ll “see” what the role becomes — naïve"
        eid = await _session(
            client, app_id, "2026-10-14",
            competency_tags=["System Design ", "SQL", "sql", "  ", "api  design"],
            gaps=[{"topic": " indexing ", "status": "gap"}, {"topic": "caching", "status": "strong"}],
            signals=[{"signal": "scope_ambiguity", "quote": quote}], interviewer_role="  engineering manager ",
        )
    p = _stored(eid)
    assert p["round"] == "technical" and p["date"] == "2026-10-14"
    assert p["source"] == "debrief"  # the stored default
    assert p["competency_tags"] == ["system-design", "sql", "sql", "api-design"]  # duplicates kept
    assert p["gaps"] == [{"topic": "indexing", "status": "gap"}, {"topic": "caching", "status": "strong"}]
    assert p["signals"] == [{"signal": "scope_ambiguity", "quote": quote}]
    assert p["interviewer_role"] == "engineering manager"


@pytest.mark.asyncio
async def test_assessment_unknowns_are_null_not_zero_and_friction_reason_is_stripped(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        a = await _ev(client, app_id, "assessment", payload={"platform": " HackerRank ", "subject": "SQL"})
        f = await _ev(client, app_id, "process_friction", payload={"reason": "  rescheduled twice  "})
        full = await _ev(client, app_id, "assessment", payload={
            "platform": "Codility", "subject": "Python", "threshold_pct": 70.5, "score_pct": 0, "stale_note": "old",
        })
    stored = _stored(a["event_id"])
    assert stored["platform"] == "HackerRank" and stored["subject"] == "SQL"
    assert stored["threshold_pct"] is None and stored["score_pct"] is None
    assert _stored(f["event_id"]) == {"reason": "rescheduled twice"}
    got = _stored(full["event_id"])
    assert got["threshold_pct"] == 70.5 and got["score_pct"] == 0 and got["stale_note"] == "old"


@pytest.mark.asyncio
async def test_offer_figures_and_silence_note_are_stored(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        done = await _ev(client, app_id, "interview_done")
        offer = await _ev(client, app_id, "offer", payload={
            "offer_amount": 55000, "offer_currency": "GBP", "offer_period": "year",
        })
        note = await _ev(client, app_id, "note", payload={
            "silence_confirmed": True, "interview_event_id": done["event_id"],
        })
    assert _stored(offer["event_id"])["offer_amount"] == 55000
    assert _stored(offer["event_id"])["offer_period"] == "year"
    assert _stored(note["event_id"]) == {"silence_confirmed": True, "interview_event_id": done["event_id"]}


# ── every bad shape is a 422 that lists ALL its problems ─────────────────────

_FUTURE = "__FUTURE__"  # replaced with a date 5 days ahead inside the test (not at import time)
_BAD: list[tuple[str, dict[str, Any], list[str]]] = [
    ("interview_session", {"round": "chat", "date": "2026-10-14"}, ["round must be one of"]),
    ("interview_session", {"round": "screen", "date": "2026-02-30"}, ["real calendar date"]),
    ("interview_session", {"round": "screen", "date": _FUTURE}, ["must not be in the future"]),
    ("interview_session", {"round": "screen"}, ["date is required"]),
    ("interview_session", {"round": "screen", "date": "2026-10-14", "signals": [
        {"signal": s, "quote": "q"} for s in
        ("scope_ambiguity", "defensive_closure", "evaluator_gap", "process_signals", "protected_grounds", "x")
    ]}, ["signals has more than 5"]),
    ("interview_session", {"round": "screen", "date": "2026-10-14", "signals": [
        {"signal": "evaluator_gap", "quote": "a"}, {"signal": "evaluator_gap", "quote": "b"},
    ]}, ["appears twice"]),
    ("interview_session", {"round": "screen", "date": "2026-10-14", "signals": [
        {"signal": "evaluator_gap", "quote": "x" * 301}]}, ["quote must be 1 to 300"]),
    ("interview_session", {"round": "screen", "date": "2026-10-14", "gaps": [{"topic": "a", "status": "weak"}]},
     ["status must be one of"]),
    ("interview_session", {"round": "screen", "date": "2026-10-14", "interview_event_id": True},
     ["interview_event_id must be a whole number"]),
    ("assessment", {"platform": "P", "subject": "S", "score_pct": 101}, ["score_pct must be a number from 0 to 100"]),
    ("assessment", {"platform": "P", "subject": "S", "score_pct": "70"}, ["score_pct must be a number"]),
    ("assessment", {"platform": "P", "subject": "S", "score_pct": True}, ["score_pct must be a number"]),
    ("process_friction", {"recruiter": "Jo", "reason": "x"}, ["company level only"]),
    ("offer", {"offer_amount": 50000}, ["offer_currency is required", "offer_period is required"]),
    ("note", {"silence_confirmed": False}, ["silence_confirmed must be true"]),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("event_type", "payload", "expected"), _BAD)
async def test_bad_shapes_are_422_naming_the_problem(authenticated_async_context, event_type, payload, expected):
    future = (datetime.now(timezone.utc) + timedelta(days=5)).date().isoformat()
    payload = {k: (future if v == _FUTURE else v) for k, v in payload.items()}
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _post(client, app_id, event_type, payload=payload)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    for words in expected:
        assert words in detail, detail
    if event_type in ("interview_session", "assessment", "process_friction"):
        assert "Valid example payload:" in detail


@pytest.mark.asyncio
async def test_a_refusal_lists_every_problem_at_once(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        resp = await _post(client, app_id, "interview_session", payload={
            "round": "chat", "source": "dream", "gaps": [{"topic": "a", "status": "weak"}],
        })
    detail = resp.json()["detail"]
    assert resp.status_code == 422
    assert "4 problems" in detail
    for words in ("round must be one of", "date is required", "source must be one of", "status must be one of"):
        assert words in detail


# ── refs, IDOR, duplicates ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refs_must_be_this_users_events_on_this_application(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        other_app = await _bring(client, 2)
        done = await _ev(client, app_id, "interview_done")
        replied = await _ev(client, app_id, "replied")
        foreign_done = await _ev(client, other_app, "interview_done")
        good_transcript = await _artifact(client, app_id, "interview_transcript", "Interviewer: hi")
        cv = await _artifact(client, app_id, "cv", "CV")
        other_transcript = await _artifact(client, other_app, "interview_transcript", "Interviewer: yo")

        async def attempt(**payload: Any) -> Any:
            return await _post(client, app_id, "interview_session", payload={"round": "screen", "date": "2026-10-01", **payload})

        assert (await attempt(interview_event_id=replied["event_id"])).status_code == 422
        assert (await attempt(interview_event_id=foreign_done["event_id"])).status_code == 422
        assert (await attempt(transcript_artifact_id=cv["artifact_id"])).status_code == 422
        assert (await attempt(transcript_artifact_id=other_transcript["artifact_id"])).status_code == 422
        ok = await attempt(
            interview_event_id=done["event_id"], transcript_artifact_id=good_transcript["artifact_id"]
        )
        assert ok.status_code == 201, ok.text
        # the silence note points at an interview event too
        bad_note = await _post(client, app_id, "note", payload={
            "silence_confirmed": True, "interview_event_id": replied["event_id"],
        })
        assert bad_note.status_code == 422

        # another user's interview_done is never a valid ref, and their application is a 404
        second = await _second_user_client()
        async with second:
            theirs = await _bring(second, 9, "Elsewhere")
            their_done = await _ev(second, theirs, "interview_done")
            mine_with_their_ref = await attempt(interview_event_id=their_done["event_id"])
            assert mine_with_their_ref.status_code == 422
            idor = await _post(second, app_id, "interview_session", payload={"round": "screen", "date": "2026-10-01"})
            assert idor.status_code == 404


@pytest.mark.asyncio
async def test_a_duplicate_session_is_refused_naming_the_event_and_a_correction_is_accepted(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        first = await _session(client, app_id, "2026-10-05", "technical", competency_tags=["sql"])
        dup = await _post(client, app_id, "interview_session", payload={"round": "technical", "date": "2026-10-05"})
        assert dup.status_code == 422
        assert f"event {first}" in dup.json()["detail"] and f"corrects_event_id={first}" in dup.json()["detail"]
        # a different round on the same day is a different session
        await _session(client, app_id, "2026-10-05", "behavioral")
        fixed = await _post(
            client, app_id, "interview_session", corrects_event_id=first,
            payload={"round": "technical", "date": "2026-10-05", "competency_tags": ["python"]},
        )
        assert fixed.status_code == 201, fixed.text
        digest = await _build(fixture_user_id, "digest", since="2026-10-05")
    tags = {c["tag"]: c["count"] for c in digest["competency_tag_counts"]}
    assert tags == {"python": 1}  # the corrected event left the digest
    assert digest["sessions"] == 2


@pytest.mark.asyncio
async def test_a_correction_cannot_collide_with_another_session(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        technical = await _session(client, app_id, "2026-10-05", "technical")
        behavioral = await _session(client, app_id, "2026-10-05", "behavioral")
        clash = await _post(
            client, app_id, "interview_session", corrects_event_id=behavioral,
            payload={"round": "technical", "date": "2026-10-05"},
        )
        assert clash.status_code == 422, clash.text
        assert f"event {technical}" in clash.json()["detail"]
        # correcting an event to its own (round, date, source) is not a clash
        same = await _post(
            client, app_id, "interview_session", corrects_event_id=technical,
            payload={"round": "technical", "date": "2026-10-05", "competency_tags": ["sql"]},
        )
        assert same.status_code == 201, same.text


@pytest.mark.asyncio
async def test_silent_list_cap_drops_the_oldest_events_and_says_so(authenticated_async_context, monkeypatch):
    async with authenticated_async_context() as client:
        old_app = await _bring(client, 0, "Coold")
        new_app = await _bring(client, 1, "Conew")
        await _ev(client, old_app, "interview_done", occurred_at=_ago(60))
        await _ev(client, new_app, "interview_done", occurred_at=_ago(40))
        monkeypatch.setattr(settings, "INTERVIEW_VIEW_MAX_EVENTS", 1)
        block = (await client.get("/api/doctor")).json()["silent_after_interview"]
        assert block["truncated"] is True
        assert [i["application_id"] for i in block["items"]] == [new_app]  # the newest survived
        monkeypatch.setattr(settings, "INTERVIEW_VIEW_MAX_EVENTS", 100)
        block = (await client.get("/api/doctor")).json()["silent_after_interview"]
        assert block["truncated"] is False and len(block["items"]) == 2


# ── digest ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_digest_week_tags_recurring_gaps_and_story_bank(authenticated_async_context, fixture_user_id):
    _sql("UPDATE users SET timezone = 'Pacific/Auckland' WHERE id = ?", (fixture_user_id,))
    async with authenticated_async_context() as client:
        a = await _bring(client, 1, "Northwind")
        b = await _bring(client, 2, "Southwind")
        await _session(client, a, "2026-10-04", "screen", competency_tags=["outside-week"])  # Sunday before
        await _session(
            client, a, "2026-10-05", "technical", competency_tags=["sql", "system-design"],
            gaps=[{"topic": "Indexing", "status": "gap"}, {"topic": "caching", "status": "gap"}],
        )
        # a Sunday-23:30 session still belongs to its own (Monday..Sunday) week
        await _ev(client, a, "interview_session", occurred_at="2026-10-11T23:30:00+13:00", payload={
            "round": "behavioral", "date": "2026-10-11", "competency_tags": ["sql"],
            "gaps": [{"topic": "indexing", "status": "solid"}],
        })
        await _session(client, b, "2026-10-06", "screen", competency_tags=["sql", "communication"])
        await _artifact(client, a, "story_bank", "stories v1")
        second_bank = await _artifact(client, a, "story_bank", "stories v2")
        # "now" is Sunday 12:00 UTC = Monday 01:00 in Auckland: the user's week starts that Monday
        now = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
        digest = await _build(fixture_user_id, "digest", now=now)
        assert digest["week"] == {"from": "2026-10-05", "to": "2026-10-11", "timezone": "Pacific/Auckland"}
        assert digest["sessions"] == 3 and digest["companies"] == 2
        assert [x["company"] for x in digest["applications"]] == ["Northwind", "Southwind"]
        assert [r["round"] for r in digest["applications"][0]["rounds"]] == ["technical", "behavioral"]
        assert digest["competency_tag_counts"] == [
            {"tag": "sql", "count": 3}, {"tag": "communication", "count": 1}, {"tag": "system-design", "count": 1},
        ]
        assert digest["recurring_competencies"] == [{"tag": "sql", "count": 3}]  # recurring needs 2
        # indexing closed (a later session said solid); caching is still open
        assert digest["open_gaps"] == [{"application_id": a, "company": "Northwind", "topics": ["caching"]}]
        assert digest["story_bank"]["artifact_id"] == second_bank["artifact_id"]
        assert digest["story_bank"]["version_no"] == 2 and digest["story_bank"]["application_id"] == a
        assert digest["truncated"] is False and "truncated_hint" not in digest
        # the same view through the stats route
        resp = await client.get("/api/applications/stats", params={"view": "digest", "since": "2026-10-05"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["digest"]["sessions"] == 3 and body["friction"] is None and body["assessments"] is None


@pytest.mark.asyncio
async def test_view_truncation_carries_a_hint(authenticated_async_context, fixture_user_id, monkeypatch):
    monkeypatch.setattr(settings, "INTERVIEW_VIEW_MAX_EVENTS", 1)
    async with authenticated_async_context() as client:
        a = await _bring(client)
        await _session(client, a, "2026-10-05", "screen")
        await _session(client, a, "2026-10-06", "technical")
        digest = await _build(fixture_user_id, "digest", since="2026-10-05")
    assert digest["truncated"] is True
    assert digest["truncated_hint"] == "pass since=YYYY-MM-DD to narrow"


@pytest.mark.asyncio
async def test_views_exclude_simulations_retractions_and_other_users(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        real = await _bring(client, 1)
        sim = await _bring(client, 2, "Simco")
        await _ev(client, sim, "simulation")
        await _session(client, real, "2026-10-05", "screen", competency_tags=["real"])
        await _session(client, sim, "2026-10-05", "screen", competency_tags=["simulated"])
        await _ev(client, sim, "assessment", payload={"platform": "Sim", "subject": "S"})
        retracted = await _ev(client, real, "assessment", payload={"platform": "Gone", "subject": "S"})
        await _ev(client, real, "note", corrects_event_id=retracted["event_id"])
        second = await _second_user_client()
        async with second:
            theirs = await _bring(second, 7, "Theirs")
            await _session(second, theirs, "2026-10-05", "screen", competency_tags=["theirs"])
            await _ev(second, theirs, "assessment", payload={"platform": "Theirs", "subject": "S"})
            await _ev(second, theirs, "process_friction", payload={"reason": "theirs"})
        digest = await _build(fixture_user_id, "digest", since="2026-10-05")
        assessments = await _build(fixture_user_id, "assessments")
        friction = await _build(fixture_user_id, "friction")
    assert [c["tag"] for c in digest["competency_tag_counts"]] == ["real"]
    assert assessments["assessments"] == [] and assessments["quality"]["total"] == 0
    assert all(c["company"] != "Simco" and c["company"] != "Theirs" for c in friction["companies"])
    assert all(c["reasons"] != ["theirs"] for c in friction["companies"])


# ── friction + redflags ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_friction_rates_sorting_and_blank_company(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        x = await _bring(client, 1, "Xco")
        y = await _bring(client, 2, "Yco")
        z = await _bring(client, 3, "Zco")
        blank = await _bring(client, 4, "Blankco")
        _sql(
            "UPDATE jobs SET normalized_company = 'unknown' "
            "WHERE id = (SELECT job_id FROM applications WHERE id = ?)", (blank,),
        )
        await _ev(client, x, "interview_done", occurred_at=_ago(20))
        await _ev(client, x, "interview_done", occurred_at=_ago(10))
        await _ev(client, x, "process_friction", payload={"reason": "rescheduled twice"}, occurred_at=_ago(9))
        await _ev(client, y, "process_friction", payload={"reason": "no confirmation"})  # friction without a round
        await _ev(client, y, "process_friction", payload={})
        await _ev(client, blank, "interview_done", occurred_at=_ago(3))
        await _ev(client, z, "note", detail="nothing happened")  # no rounds, no friction: dropped
        view = await _build(fixture_user_id, "friction")
    rows = {c["company"]: c for c in view["companies"]}
    assert "Zco" not in rows
    assert rows["Xco"]["rounds"] == 2 and rows["Xco"]["friction_count"] == 1
    assert rows["Xco"]["friction_rate"] == 0.5 and rows["Xco"]["reasons"] == ["rescheduled twice"]
    assert rows["Yco"]["rounds"] == 0 and rows["Yco"]["friction_rate"] is None  # unknown, never 0
    assert rows["Yco"]["friction_count"] == 2 and rows["Yco"]["reasons"] == ["no confirmation"]
    assert rows["Company not set"]["company_key"] == f"app:{blank}" and rows["Company not set"]["rounds"] == 1
    # friction desc, then rate desc with null last: Yco (2), Xco (1, 0.5), then the no-friction company
    assert [c["company"] for c in view["companies"]] == ["Yco", "Xco", "Company not set"]
    assert "never about a named person" in view["note"]


@pytest.mark.asyncio
async def test_redflag_points_levels_and_analysis_pointer(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        one = await _bring(client, 1, "Oneco")
        many = await _bring(client, 2, "Manyco")
        await _session(client, one, "2026-10-05", "screen", signals=[{"signal": "scope_ambiguity", "quote": "q1"}])
        two_signals = [
            {"signal": "scope_ambiguity", "quote": "we will see"}, {"signal": "defensive_closure", "quote": "next"},
        ]
        await _session(client, many, "2026-10-05", "screen", signals=two_signals)
        await _session(client, many, "2026-10-06", "technical", signals=two_signals)
        analysis = await _artifact(client, many, "redflag_analysis", "pattern across two rounds")
        view = await _build(fixture_user_id, "friction")
    flags = {r["company"]: r for r in view["redflags"]}
    assert flags["Oneco"]["points"] == 1 and flags["Oneco"]["level"] == "none"  # one session: +1
    assert flags["Oneco"]["label"] == "No structural red flags detected" and flags["Oneco"]["analysis"] is None
    assert flags["Oneco"]["signals"][0]["severity"] == "single"
    assert flags["Manyco"]["points"] == 4 and flags["Manyco"]["level"] == "warning"  # two sessions: +2 each
    assert flags["Manyco"]["label"] == "Reconsider"
    assert {s["signal"]: (s["sessions"], s["severity"]) for s in flags["Manyco"]["signals"]} == {
        "scope_ambiguity": (2, "pattern"), "defensive_closure": (2, "pattern"),
    }
    assert flags["Manyco"]["analysis"]["artifact_id"] == analysis["artifact_id"]
    assert [r["company"] for r in view["redflags"]] == ["Manyco", "Oneco"]
    assert _level_for(2) == "caution" and _level_for(3) == "caution" and _level_for(1) == "none"


def _level_for(points: int) -> str:
    from src.services.applications import interview_counts

    return interview_counts._level(points)


# ── assessments ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assessments_outcomes_and_platform_casefold(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _ev(client, app_id, "assessment", occurred_at=_ago(5), payload={
            "platform": "HackerRank", "subject": "SQL", "threshold_pct": 70, "score_pct": 82})
        await _ev(client, app_id, "assessment", occurred_at=_ago(4), payload={
            "platform": " hackerrank ", "subject": "Python", "threshold_pct": 80, "score_pct": 60,
            "stale_note": "questions from 2019"})
        await _ev(client, app_id, "assessment", occurred_at=_ago(1), payload={"platform": "Codility", "subject": "Go"})
        view = await _build(fixture_user_id, "assessments")
    assert [i["subject"] for i in view["assessments"]] == ["Go", "Python", "SQL"]  # newest first
    assert [i["outcome"] for i in view["assessments"]] == ["unknown", "failed", "passed"]
    assert view["assessments"][0]["score_pct"] is None and view["assessments"][0]["threshold_pct"] is None
    assert view["by_platform"]["hackerrank"] == {
        "platform": "HackerRank", "count": 2, "stale_flagged": 1, "passed": 1, "failed": 1, "unknown_outcome": 0,
    }
    assert view["by_platform"]["codility"]["unknown_outcome"] == 1
    assert view["quality"] == {"total": 3, "stale_flagged": 1, "without_score": 1, "without_threshold": 1}


# ── silence after an interview ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_silent_list_rules(authenticated_async_context, fixture_user_id, monkeypatch):
    async with authenticated_async_context() as client:
        ids = {}
        for n, name in enumerate(["s31", "s30", "answered", "mail", "rejected", "s45"]):
            ids[name] = await _bring(client, n, f"Co{name}")
        d31 = await _ev(client, ids["s31"], "interview_done", occurred_at=_ago(31))
        await _ev(client, ids["s30"], "interview_done", occurred_at=_ago(30))  # exactly 30: not yet (strict)
        await _ev(client, ids["answered"], "interview_done", occurred_at=_ago(40))
        await _ev(client, ids["answered"], "replied", occurred_at=_ago(5))
        await _ev(client, ids["mail"], "interview_done", occurred_at=_ago(40))
        await _ev(client, ids["mail"], "note", occurred_at=_ago(5), source={"kind": "email", "message_id": "m-1"})
        await _ev(client, ids["rejected"], "interview_done", occurred_at=_ago(40))
        await _ev(client, ids["rejected"], "rejected", occurred_at=_ago(35))
        await _ev(client, ids["s45"], "interview_done", occurred_at=_ago(45))
        resp = await client.get("/api/doctor")
        assert resp.status_code == 200, resp.text
        block = resp.json()["silent_after_interview"]
        assert block["courtesy_days"] == 30
        assert [(i["application_id"], i["days_silent"], i["confirmed"]) for i in block["items"]] == [
            (ids["s45"], 45, False), (ids["s31"], 31, False),
        ]
        assert block["items"][1]["last_interview_event_id"] == d31["event_id"]
        assert block["items"][0]["company"] == "Cos45"
        assert "not legal advice" in block["disclaimer"]
        # a silence note recorded after the interview marks it confirmed
        await _ev(client, ids["s31"], "note", payload={
            "silence_confirmed": True, "interview_event_id": d31["event_id"]})
        again = (await client.get("/api/doctor")).json()["silent_after_interview"]
        assert [(i["application_id"], i["confirmed"]) for i in again["items"]] == [
            (ids["s45"], False), (ids["s31"], True),
        ]
        # the list is capped
        monkeypatch.setattr(settings, "INTERVIEW_SILENT_MAX_ITEMS", 1)
        capped = (await client.get("/api/doctor")).json()["silent_after_interview"]
        assert [i["application_id"] for i in capped["items"]] == [ids["s45"]]


@pytest.mark.asyncio
async def test_a_future_interview_is_never_silent():
    from src.services.applications import interview_counts

    today = datetime.now(timezone.utc).date()
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    apps = [{
        "application_id": 1, "company": "C", "job_title": "T", "status": "interview_done",
        "events": [{"id": 1, "event_type": "interview_done", "occurred_at": future, "recorded_at": future,
                    "payload": {}, "source_message_id": ""}],
    }]
    assert interview_counts.silent_after_interview(apps, today=today, courtesy_days=30) == []


@pytest.mark.asyncio
async def test_doctor_survives_a_failing_silence_loader(authenticated_async_context, monkeypatch):
    from src.services.applications import interview_counts

    async def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("db gone")

    monkeypatch.setattr(interview_counts, "silent_list", boom)
    async with authenticated_async_context() as client:
        resp = await client.get("/api/doctor")
    assert resp.status_code == 200, resp.text
    block = resp.json()["silent_after_interview"]
    assert block["items"] == [] and block["courtesy_days"] == 30
    assert "not legal advice" in block["disclaimer"]


def test_unknown_view_is_a_value_error():
    import asyncio

    from src.services.applications import interview_counts

    with pytest.raises(ValueError):
        asyncio.run(interview_counts.build_view(None, "u", "funnel"))  # type: ignore[arg-type]


# ── MCP parity ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_inherits_the_route_validation_and_serves_the_views(authenticated_async_context, fixture_user_id):
    pytest.importorskip("mcp")
    from src.api import mcp_server

    tools = mcp_server.build_server()._tool_manager
    user = mcp_server.CurrentUser(
        id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc",
    )
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _session(client, app_id, "2026-10-05", "screen", competency_tags=["sql"])
        bad = {"round": "chat", "date": "2026-10-14"}
        route = await _post(client, app_id, "interview_session", payload=bad)
        token_ctx = mcp_server._current_user.set(user)
        try:
            with pytest.raises(Exception) as err:
                await tools.get_tool("record_event").fn(
                    event_type="interview_session", application_id=app_id, payload=bad
                )
            saved = await tools.get_tool("save_artifact").fn(
                kind="story_bank", text="S: ... T: ... A: ... R: ... reflection", application_id=app_id,
            )
            via_tool = await tools.get_tool("stats").fn(view="digest", since="2026-10-05")
        finally:
            mcp_server._current_user.reset(token_ctx)
        via_route = (await client.get("/api/applications/stats", params={"view": "digest", "since": "2026-10-05"})).json()
    assert route.status_code == 422
    assert str(err.value) == f"422: {route.json()['detail']}"
    assert saved["kind"] == "story_bank" and saved["version_no"] == 1
    assert via_tool["digest"] == via_route["digest"]
    assert via_tool["digest"]["sessions"] == 1


# ── wording guard + docs ─────────────────────────────────────────────────────


def test_no_legal_conclusions_in_recipes_rules_or_fixed_strings():
    root = Path(__file__).resolve().parents[1] / "src"
    from src.services.applications import interview_counts

    texts = [
        (root / "recipes" / "debrief.md").read_text(encoding="utf-8"),
        (root / "recipes" / "rules.md").read_text(encoding="utf-8"),
        (root / "services" / "applications" / "interview_counts.py").read_text(encoding="utf-8"),
        interview_counts.FRICTION_NOTE, interview_counts.SILENT_DISCLAIMER, *interview_counts.REDFLAG_LABELS.values(),
    ]
    for text in texts:
        low = text.lower()
        for banned in _BANNED:
            assert banned not in low, banned


def test_silent_disclaimer_matches_the_doctor_model():
    from src.api.models import SILENT_DISCLAIMER
    from src.services.applications import interview_counts

    assert interview_counts.SILENT_DISCLAIMER == SILENT_DISCLAIMER


def test_interview_event_types_and_kinds_are_registered_and_never_ranked():
    for name in ("interview_session", "process_friction", "assessment"):
        assert name in settings.APPLICATION_NOTE_EVENT_TYPES
        assert name not in settings.APPLICATION_STATUS_EVENT_TYPES
        assert name not in settings.FEEDBACK_FUNNEL_RANK
    for kind in ("story_bank", "interview_transcript", "redflag_analysis"):
        assert kind in settings.APPLICATION_ARTIFACT_KINDS
        assert kind not in settings.APPLICATION_ARTIFACT_ATS_KINDS
    assert settings.STATS_VIEWS[-1] == "all"
    assert {"digest", "friction", "assessments"} <= set(settings.STATS_VIEWS)
