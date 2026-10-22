"""Feedback loop slice 3 - "numbers from what is stored": the `view` argument of stats.

Funnel, velocity, calibration, company history, lesson effect and follow-ups, all
computed from the event log. Every test builds a real history through the API (past
``occurred_at`` through record_event) and asserts real values, not key presence
(hard rule 21). Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


async def _bring(client: AsyncClient, n: int, company: str = "Northwind") -> int:
    ad = {
        "title": f"Data Engineer {n}", "company": company, "location": "Remote",
        "apply_url": f"https://{company.lower().replace(' ', '')}.example/careers/{n}",
        "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
    }
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, **extra: Any) -> dict[str, Any]:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _fit(client: AsyncClient, app_id: int, score: int) -> None:
    resp = await client.put(f"/api/applications/{app_id}/fit", json={
        "score": score, "verdict": "Fixture fit for the numbers tests.", "score_source": "full_ad",
    })
    assert resp.status_code == 200, resp.text


async def _stats(client: AsyncClient, **params: Any) -> Any:
    return await client.get("/api/applications/stats", params=params)


async def _view(client: AsyncClient, view: str, **params: Any) -> dict[str, Any]:
    resp = await _stats(client, view=view, **params)
    assert resp.status_code == 200, resp.text
    return resp.json()


_counter = {"n": 0}


async def _applied(client: AsyncClient, company: str = "Northwind", days_ago: float = 10, score: int | None = None,
                   *events: tuple[str, float]) -> int:
    """Bring + applied `days_ago`, then each (event_type, days_ago) pair."""
    _counter["n"] += 1
    app_id = await _bring(client, _counter["n"], company)
    if score is not None:
        await _fit(client, app_id, score)
    await _event(client, app_id, "applied", occurred_at=_ago(days_ago))
    for etype, ago in events:
        await _event(client, app_id, etype, occurred_at=_ago(ago))
    return app_id


def _set_timezone(user_id: str, zone: str) -> None:
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute("UPDATE users SET timezone = ? WHERE id = ?", (zone, user_id))
    conn.commit()
    conn.close()


async def _second_user_session_cookie(email: str) -> str:
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
    sync_client.close()
    assert cookie
    return cookie


def _session_client(cookie: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie})


# ── 1. funnel ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_funnel_counts_the_furthest_stage_and_a_rejection_is_a_reply(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _applied(client, "A1", 20, None, ("rejected", 10))
        await _applied(client, "A2", 20, None, ("interview_done", 12), ("rejected", 5))
        await _applied(client, "A3", 20, None, ("offer", 12), ("offer_declined", 8))
        await _applied(client, "A4", 20, None, ("offer", 12), ("hired", 8))
        body = await _view(client, "funnel")
        f = body["funnel"]
        assert (f["n"], f["ever_applied"], f["ever_replied"]) == (4, 4, 4)
        assert (f["ever_interview"], f["ever_offer"], f["ever_hired"]) == (3, 2, 1)
        assert f["reply_rate"] == 1.0 and f["interview_rate"] == 0.75 and f["offer_rate"] == 0.5
        assert f["small_sample"] is True and f["without_applied"] == 0
        assert "rejection counts as a reply" in f["note"]
        # the existing overall.reply_rate is NOT changed by rejections
        assert body["overall"]["reply_rate"] == 0.0 and body["overall"]["rejected"] == 2
        assert body["overall"]["small_sample"] is True
        for other in ("velocity", "calibration", "company", "lesson_effect", "followups"):
            assert body[other] is None


@pytest.mark.asyncio
async def test_funnel_two_of_three_and_small_sample_threshold(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _applied(client, "B1", 20, None, ("replied", 10))
        await _applied(client, "B2", 20, None, ("rejected", 10))
        await _applied(client, "B3", 20)
        f = (await _view(client, "funnel"))["funnel"]
        assert f["n"] == 3 and f["ever_replied"] == 2 and f["reply_rate"] == 0.667
        assert f["small_sample"] is True
        for i in range(7):
            await _applied(client, f"B1{i}", 20)
        body = await _view(client, "funnel")
        assert body["funnel"]["n"] == 10 and body["funnel"]["small_sample"] is False
        assert body["overall"]["small_sample"] is False


@pytest.mark.asyncio
async def test_funnel_without_applied_retraction_and_simulation(authenticated_async_context):
    async with authenticated_async_context() as client:
        _counter["n"] += 1
        bare = await _bring(client, _counter["n"], "Bare")
        await _event(client, bare, "replied", occurred_at=_ago(3))
        retracted = await _applied(client, "Retract", 20, None, ("interview_done", 10))
        events = (await client.get(f"/api/applications/{retracted}")).json()["events"]
        iv = next(e for e in events if e["event_type"] == "interview_done")
        await _event(client, retracted, "note", corrects_event_id=iv["id"], detail="logged by mistake")
        fake = await _applied(client, "Fake", 20, None, ("replied", 10))
        await _event(client, fake, "simulation")
        f = (await _view(client, "funnel"))["funnel"]
        assert f["n"] == 1 and f["ever_interview"] == 0 and f["ever_replied"] == 0
        assert f["without_applied"] == 1


# ── 2. velocity ──────────────────────────────────────────────────────────────


def test_p75_matches_the_reference_fixture():
    from src.services.applications import feedback_numbers as fn

    assert fn._p75([3, 6, 20]) == 13
    assert fn._median([2, 4, 10]) == 4 and fn._p75([2, 4, 10]) == 7
    assert fn._median([]) is None and fn._p75([5]) == 5


def _hop(body: dict[str, Any], key: str) -> dict[str, Any]:
    return next(h for h in body["velocity"]["hops"] if h["key"] == key)


@pytest.mark.asyncio
async def test_velocity_median_p75_and_the_honest_exclusions(authenticated_async_context):
    async with authenticated_async_context() as client:
        for i, gap in enumerate((2, 4, 10)):
            await _applied(client, f"V{i}", 30, None, ("replied", 30 - gap))
        await _applied(client, "Vsame", 30, None, ("replied", 30))  # same day
        await _applied(client, "Vwait", 5)  # still waiting
        await _applied(client, "Vghost", 40, None, ("ghosted", 1))  # not waiting
        body = await _view(client, "velocity")
        h = _hop(body, "applied_to_replied")
        assert (h["n"], h["median_days"], h["p75_days"]) == (3, 4.0, 7.0)
        assert h["insufficient_data"] is False and h["same_day_excluded"] == 1
        assert h["still_waiting"] == 1  # Vwait only; Vghost's latest state is ghosted
        assert body["velocity"]["min_n"] == 3 and "left out" in body["velocity"]["note"]
        assert _hop(body, "applied_to_rejected")["still_waiting"] == 0


@pytest.mark.asyncio
async def test_velocity_under_three_hops_has_no_median(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _applied(client, "W1", 20, None, ("replied", 15))
        await _applied(client, "W2", 20, None, ("replied", 10))
        await _applied(client, "W3", 20, None, ("rejected", 10))
        body = await _view(client, "velocity")
        h = _hop(body, "applied_to_replied")
        assert h["n"] == 2 and h["median_days"] is None and h["p75_days"] is None
        assert h["insufficient_data"] is True
        r = _hop(body, "applied_to_rejected")
        assert r["n"] == 1 and r["still_waiting"] == 0
        assert _hop(body, "replied_to_interview")["n"] == 0


@pytest.mark.asyncio
async def test_velocity_counts_days_in_the_users_zone(authenticated_async_context, fixture_user_id):
    applied_at, replied_at = "2026-03-01T10:00:00+00:00", "2026-03-01T12:00:00+00:00"
    async with authenticated_async_context() as client:
        for _ in range(2):
            _counter["n"] += 1
            a = await _bring(client, _counter["n"], "Zone")
            await _event(client, a, "applied", occurred_at=applied_at)
            await _event(client, a, "replied", occurred_at=replied_at)
        utc = _hop(await _view(client, "velocity"), "applied_to_replied")
        assert utc["n"] == 0 and utc["same_day_excluded"] == 2
    _set_timezone(fixture_user_id, "Pacific/Auckland")  # 23:00 Mar 1 -> 01:00 Mar 2
    async with authenticated_async_context() as client:
        nz = _hop(await _view(client, "velocity"), "applied_to_replied")
        assert nz["n"] == 2 and nz["same_day_excluded"] == 0


# ── 3. calibration ───────────────────────────────────────────────────────────


async def _scored(client: AsyncClient, score: int, *events: tuple[str, float], company: str = "Cal") -> int:
    return await _applied(client, company, 30, score, *events)


@pytest.mark.asyncio
async def test_calibration_separating_inverted_and_insufficient(authenticated_async_context):
    async with authenticated_async_context() as client:
        for i in range(5):
            await _scored(client, 55, *((("interview_done", 20),) if i == 0 else (("rejected", 20),)))
        for i in range(5):
            await _scored(client, 92, *((("interview_done", 20),) if i < 4 else (("rejected", 20),)))
        c = (await _view(client, "calibration"))["calibration"]
        low, high = c["bands"][0], c["bands"][3]
        assert (low["band"], low["n"], low["interviews"], low["interview_rate"]) == ("<70", 5, 1, 0.2)
        assert (high["band"], high["n"], high["interviews"], high["interview_rate"]) == (">=90", 5, 4, 0.8)
        assert c["verdict"]["kind"] == "separating" and "90" in c["verdict"]["text"]
        assert "80% vs 20%" in c["verdict"]["text"]
        assert c["resolved"] == 10 and "Advisory only" in c["note"]


@pytest.mark.asyncio
async def test_calibration_inverted(authenticated_async_context):
    async with authenticated_async_context() as client:
        for i in range(5):
            await _scored(client, 40, *((("interview_done", 20),) if i < 4 else (("rejected", 20),)))
        for i in range(5):
            await _scored(client, 95, *((("interview_done", 20),) if i == 0 else (("rejected", 20),)))
        c = (await _view(client, "calibration"))["calibration"]
        assert c["verdict"]["kind"] == "inverted" and "BETTER" in c["verdict"]["text"]


@pytest.mark.asyncio
async def test_calibration_small_bands_in_flight_unscored_ghosted_withdrawn(authenticated_async_context):
    async with authenticated_async_context() as client:
        for _ in range(4):
            await _scored(client, 75, ("rejected", 20))  # a band of 4: rates null, counts shown
        await _scored(client, 0, ("rejected", 20))  # score 0 is a real score -> "<70"
        await _scored(client, 85)  # applied only: in flight
        await _applied(client, "Cal", 30, None, ("rejected", 20))  # no score: unscored
        await _scored(client, 66, ("ghosted", 1))  # ghosted resolves negative
        await _scored(client, 66, ("withdrawn", 5))  # outside
        c = (await _view(client, "calibration"))["calibration"]
        by = {b["band"]: b for b in c["bands"]}
        assert (by["70-79"]["n"], by["70-79"]["interview_rate"], by["70-79"]["offer_rate"]) == (4, None, None)
        assert by["<70"]["n"] == 2  # score 0 and the ghosted 66
        assert c["in_flight"] == 1 and c["unscored"] == 1 and c["resolved"] == 6
        assert c["ghosted_counted_negative"] == 1
        assert c["verdict"]["kind"] == "insufficient" and "6 resolved" in c["verdict"]["text"]


@pytest.mark.asyncio
async def test_calibration_feedback_quotes_keep_the_exact_words(authenticated_async_context):
    words = "Merci pour votre candidature - nous avons choisi un autre profil. 谢谢 ☕"
    async with authenticated_async_context() as client:
        a = await _scored(client, 91, ("rejected", 10))
        await _event(client, a, "rejected", payload={"verbatim_feedback": words}, occurred_at=_ago(9))
        c = (await _view(client, "calibration"))["calibration"]
        assert c["feedback"] == [{"application_id": a, "company": "Cal", "fit_score": 91, "verbatim_feedback": words}]
        # the first rejection carries no words and adds no quote


# ── 4. company ───────────────────────────────────────────────────────────────


def _card(body: dict[str, Any]) -> dict[str, Any]:
    cards = body["company"]["cards"]
    assert len(cards) == 1, cards
    return cards[0]


@pytest.mark.asyncio
async def test_company_silent_pending_answered_and_mixed(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _applied(client, "Acme", 30)
        body = await _view(client, "company")
        card = _card(body)
        assert card["label"] == "silent-on-you" and card["applications"] == 1
        fact = card["facts"][0]
        assert (fact["kind"], fact["application_id"], fact["silent_days"], fact["confidence"]) == ("silent", a, 30, "unconfirmed")
        assert fact["stale"] is False and fact["checkins_sent"] == 0
        assert body["company"]["silence_window_days"] == 28 and "facts, not verdicts" in body["company"]["explanation"]

        await _applied(client, "Acme", 20, None, ("rejected", 5))
        mixed = _card(await _view(client, "company"))
        assert mixed["label"] == "mixed"
        answered = next(f for f in mixed["facts"] if f["kind"] == "answered")
        assert answered["outcome"] == "rejected" and answered["note"] == "a rejection is an answer"


@pytest.mark.asyncio
async def test_company_block_carries_the_application_cap_separately(authenticated_async_context, monkeypatch):
    async with authenticated_async_context() as client:
        await _applied(client, "Acme", 30)
        await _applied(client, "Acme", 25)
        await _applied(client, "Other", 20)
        plain = (await _view(client, "company"))["company"]
        assert plain["applications_truncated"] is False and not plain.get("truncated_hint")
        monkeypatch.setattr(settings, "STATS_MAX_APPLICATIONS", 2)
        # `since` makes the company block do its own second (history) load, which is capped too
        capped = (await _view(client, "company", since=_ago(5)[:10]))["company"]
        assert capped["applications_truncated"] is True
        assert "newest 2 applications" in capped["truncated_hint"]
        assert "truncated" in capped  # the card cap key still exists, separate from the application cap


@pytest.mark.asyncio
async def test_company_pending_is_no_history_and_answered_is_responded_before(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _applied(client, "Pending Ltd", 20)
        card = _card(await _view(client, "company"))
        assert card["label"] == "no-history" and card["facts"] == []
    async with authenticated_async_context() as client:
        await _applied(client, "Reply Ltd", 40, None, ("replied", 30), ("interview_done", 20), ("withdrawn", 5))
        body = await _view(client, "company", company="Reply Ltd")
        card = _card(body)
        assert card["label"] == "responded-before"
        assert card["facts"][0]["outcome"] == "interview"  # withdrawn after an interview still answered


@pytest.mark.asyncio
async def test_company_stale_fact_is_listed_but_ignored_by_the_label(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _applied(client, "Old Co", 400)
        card = _card(await _view(client, "company"))
        assert card["label"] == "no-history"
        assert card["facts"][0]["stale"] is True and card["facts"][0]["silent_days"] == 400


@pytest.mark.asyncio
async def test_company_filter_uses_the_rule1_key_ignores_since_and_never_says_ghost(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _applied(client, "Acme", 40)
        await _applied(client, "Other Corp", 40)
        body = await _view(client, "company", company="ACME Ltd")
        assert [c["company"] for c in body["company"]["cards"]] == ["Acme"]
        assert body["company"]["since_ignored"] is False
        since = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        with_since = await _view(client, "company", company="Acme", since=since)
        assert _card(with_since)["applications"] == 1 and with_since["company"]["since_ignored"] is True
        everything = await _view(client, "company")
        assert {c["company"] for c in everything["company"]["cards"]} == {"Acme", "Other Corp"}
        import json

        assert "ghost" not in json.dumps(everything["company"]).lower()
        assert "risk" not in json.dumps(everything["company"]).lower()


@pytest.mark.asyncio
async def test_company_outreach_does_not_change_the_label(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _applied(client, "Reach Co", 30)
        before = _card(await _view(client, "company"))
        contact = await client.post(f"/api/applications/{a}/contacts", json={"name": "Pat Recruiter"})
        assert contact.status_code in (200, 201), contact.text
        cid = contact.json().get("contact_id") or contact.json().get("id")
        for entry in ("sent", "reply"):
            r = await client.post(f"/api/contacts/{cid}/outreach", json={"entry": entry, "channel": "email"})
            assert r.status_code in (200, 201), r.text
        after = _card(await _view(client, "company"))
        assert before["label"] == after["label"] == "silent-on-you"
        assert before["facts"] == after["facts"]


# ── 5. lesson_effect ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lesson_effect_splits_applications_before_and_after(authenticated_async_context):
    async with authenticated_async_context() as client:
        before_ids = [
            await _applied(client, "L1", 30, None, ("replied", 25)),
            await _applied(client, "L2", 30, None, ("rejected", 25)),
            await _applied(client, "L3", 30),
        ]
        await _event(client, before_ids[0], "lesson", detail="Mention the Kubernetes cert.", occurred_at=_ago(15))
        await _applied(client, "L4", 10, None, ("replied", 5))
        await _applied(client, "L5", 8, None, ("interview_requested", 4))
        body = await _view(client, "lesson_effect")
        le = body["lesson_effect"]
        assert len(le["lessons"]) == 1
        row = le["lessons"][0]
        assert row["lesson"] == "Mention the Kubernetes cert." and row["application_id"] == before_ids[0]
        assert row["before"] == {"n": 3, "replied": 2, "interview": 0, "reply_rate": 0.667, "interview_rate": 0.0,
                                 "small_sample": True, "still_waiting": 1}
        assert row["after"]["n"] == 2 and row["after"]["replied"] == 2 and row["after"]["reply_rate"] == 1.0
        assert row["after"]["interview"] == 1 and row["comparable"] is False
        assert "not proof of cause" in le["note"]


@pytest.mark.asyncio
async def test_lesson_effect_skips_a_removed_lesson(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _applied(client, "L6", 30)
        lesson = await _event(client, a, "lesson", detail="Wrong lesson", occurred_at=_ago(20))
        await _event(client, a, "lesson", detail="Right lesson", occurred_at=_ago(10))
        await _event(client, a, "note", corrects_event_id=lesson["event_id"], detail="removed")
        le = (await _view(client, "lesson_effect"))["lesson_effect"]
        assert [r["lesson"] for r in le["lessons"]] == ["Right lesson"]


# ── 6. followups ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_followups_is_unavailable_until_slice_2_event_types_exist(authenticated_async_context, monkeypatch):
    monkeypatch.setattr(
        settings, "APPLICATION_NOTE_EVENT_TYPES",
        tuple(t for t in settings.APPLICATION_NOTE_EVENT_TYPES if t not in settings.FEEDBACK_FOLLOWUP_EVENT_TYPES),
    )
    async with authenticated_async_context() as client:
        await _applied(client, "F0", 20)
        f = (await _view(client, "followups"))["followups"]
        assert f["available"] is False
        assert all(v is None for k, v in f.items() if k != "available")


@pytest.mark.asyncio
async def test_followups_values(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _applied(client, "F1", 30)
        await _event(client, a, "checkin_sent", payload={
            "to": "hiring@f1.example", "text": "Checking in on my application.", "to_kind": "careers_address",
        })
        await _applied(client, "F2", 20)  # applied 20 days, no check-in: counted
        skipped = await _applied(client, "F3", 20)
        await _event(client, skipped, settings.FEEDBACK_SKIP_EVENT_TYPE, payload={"kind": "checkin", "reason": "no-reply sender"})
        await _applied(client, "F4", 2)  # too recent
        f = (await _view(client, "followups"))["followups"]
        assert f["available"] is True and f["checkins_sent"] == 1 and f["applications_with_checkin"] == 1
        assert f["applied_without_checkin"] == 1  # F2 only: F1 has one, F3 was skipped, F4 is too recent
        assert f["skipped"] == {"checkin": 1} and f["checkin_days"] == 7
        fact = (await _view(client, "company", company="F1"))["company"]["cards"][0]["facts"][0]
        assert fact["confidence"] == "confirmed-by-followups" and fact["checkins_sent"] == 1


def test_followups_counts_from_hand_built_facts(monkeypatch):
    """The pure function, with the slice-2 types switched on: counts only, never "due"."""
    from zoneinfo import ZoneInfo

    from src.services.applications import feedback_numbers as fn

    monkeypatch.setattr(
        settings, "APPLICATION_NOTE_EVENT_TYPES",
        (*settings.APPLICATION_NOTE_EVENT_TYPES, *settings.FEEDBACK_FOLLOWUP_EVENT_TYPES,
         settings.FEEDBACK_SKIP_EVENT_TYPE),
    )
    now = datetime.now(timezone.utc)

    def ev(i: int, t: str, days: float, **payload: Any) -> fn.Ev:
        return fn.Ev(i, t, now - timedelta(days=days), payload)

    apps = [{"id": i, "status": st} for i, st in ((1, "applied"), (2, "applied"), (3, "applied"), (4, "applied"),
                                                    (5, "rejected"), (6, "rejected"), (7, "rejected"))]
    facts = fn.Facts(apps=apps, events={
        1: [ev(1, "applied", 20), ev(2, "checkin_sent", 10)],
        2: [ev(3, "applied", 20)],  # counted: no check-in
        3: [ev(4, "applied", 20), ev(5, "auto_email_skipped", 9, kind="checkin")],
        4: [ev(6, "applied", 2)],  # too recent
        5: [ev(7, "applied", 20), ev(8, "rejected", 5), ev(9, "feedback_asked", 4), ev(10, "feedback_followup_sent", 1)],
        6: [ev(11, "applied", 20), ev(12, "rejected", 5)],  # counted: no ask
        7: [ev(13, "applied", 20), ev(14, "rejected", 5), ev(15, "auto_email_skipped", 4, kind="feedback_request")],
    })
    out = fn.followups(facts, now, ZoneInfo("UTC"))
    assert out == {
        "available": True, "checkins_sent": 1, "feedback_asked": 1, "feedback_followups_sent": 1,
        "applications_with_checkin": 1, "applied_without_checkin": 1, "rejections_without_ask": 1,
        "skipped": {"checkin": 1, "feedback_request": 1}, "checkin_days": 7,
    }


# ── 7. validation ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bad_view_or_company_is_422_and_does_not_spend_the_rate_limit(authenticated_async_context, monkeypatch):
    monkeypatch.setattr(settings, "STATS_MAX_PER_HOUR", 2)
    async with authenticated_async_context() as client:
        bad = await _stats(client, view="bogus")
        assert bad.status_code == 422 and "view must be one of" in bad.text
        assert (await _stats(client, company="Acme")).status_code == 422
        assert (await _stats(client, view="funnel", company="Acme")).status_code == 422
        assert (await _stats(client, view="company", company="   ")).status_code == 422
        too_long = await _stats(client, view="company", company="x" * (settings.STATS_COMPANY_MAX_CHARS + 1))
        assert too_long.status_code == 422 and str(settings.STATS_COMPANY_MAX_CHARS) in too_long.text
        # four refusals above did not spend the two slots
        assert (await _stats(client)).status_code == 200
        assert (await _stats(client, view="funnel")).status_code == 200
        assert (await _stats(client)).status_code == 429


@pytest.mark.asyncio
async def test_no_view_returns_the_old_response_with_every_block_null(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _applied(client, "Plain", 10, None, ("replied", 5))
        body = (await _stats(client)).json()
        for key in ("since", "overall", "by_cv_version", "by_role", "by_country", "by_job_source", "by_channel",
                    "by_contact_found_via", "by_outreach_variant", "score_line", "groups_truncated", "applications_truncated", "computed_at"):
            assert key in body
        for key in ("funnel", "velocity", "calibration", "company", "lesson_effect", "followups"):
            assert body[key] is None
        assert body["overall"]["applied"] == 1 and body["overall"]["replied"] == 1
        assert body["overall"]["reply_rate"] == 1.0 and body["overall"]["small_sample"] is True


@pytest.mark.asyncio
async def test_all_is_every_view_except_all(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _applied(client, "Everything", 30, 80, ("replied", 20))
        body = await _view(client, "all")
        every = [v for v in settings.STATS_VIEWS if v != "all"]
        assert every and all(body[v] is not None for v in every), {v: body[v] for v in every}


# ── 8. IDOR ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_another_users_data_never_appears_in_any_view(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _applied(client, "Secretco", 40, 91, ("rejected", 10))
        await _event(client, a, "lesson", detail="Private lesson", occurred_at=_ago(20))
    cookie = await _second_user_session_cookie("fl-s3-other@example.com")
    async with _session_client(cookie) as other:
        body = await _view(other, "all")
        assert body["funnel"]["n"] == 0 and body["funnel"]["reply_rate"] is None
        assert body["calibration"]["resolved"] == 0 and body["calibration"]["feedback"] == []
        assert body["company"]["cards"] == [] and body["lesson_effect"]["lessons"] == []
        asked = await _view(other, "company", company="Secretco")
        assert asked["company"]["cards"] == []
    # the route takes no user id: an extra query parameter changes nothing
    async with authenticated_async_context() as client:
        resp = await _stats(client, view="funnel", user_id="someone-else")
        assert resp.status_code == 200 and resp.json()["funnel"]["n"] == 1


# ── 9. MCP ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_stats_view_matches_the_route_and_unknown_args_are_refused(authenticated_async_context):
    pytest.importorskip("mcp")
    import json

    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        await _applied(client, "Parity", 30, 85, ("replied", 20))
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
        rest = (await _view(client, "all"))

    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    async with mcp_runtime():
        async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
            result = await mcp.call_tool("stats", {"view": "all"})
            assert not result.is_error, result.content[0].text
            body = json.loads(result.content[0].text)
            def _present(block: Any) -> Any:  # the REST model adds null optionals; the tool returns the raw dict
                if isinstance(block, dict):
                    return {k: _present(v) for k, v in block.items() if v is not None}
                return [_present(v) for v in block] if isinstance(block, list) else block

            for key in ("funnel", "velocity", "calibration", "company", "lesson_effect", "followups"):
                assert _present(body[key]) == _present(rest[key]), key
            assert body["overall"]["applied"] == rest["overall"]["applied"] == 1
            one = await mcp.call_tool("stats", {"view": "company", "company": "Parity"})
            assert not one.is_error and len(json.loads(one.content[0].text)["company"]["cards"]) == 1
            bad = await mcp.call_tool("stats", {"view": "bogus"})
            assert bad.is_error
            unknown = await mcp.call_tool("stats", {"made_up": 1})
            assert unknown.is_error


# ── guards ───────────────────────────────────────────────────────────────────


def test_funnel_rank_is_in_job360_status_names_and_rank_three_stays_empty():
    assert set(settings.FEEDBACK_FUNNEL_RANK) <= set(settings.APPLICATION_STATUS_EVENT_TYPES)
    assert 3 not in settings.FEEDBACK_FUNNEL_RANK.values()
    assert settings.FEEDBACK_FUNNEL_RANK["rejected"] == settings.FEEDBACK_FUNNEL_RANK["replied"] == 2
    assert settings.FEEDBACK_FUNNEL_RANK["offer_declined"] == 5 and settings.FEEDBACK_FUNNEL_RANK["hired"] == 6
    assert settings.STATS_VIEWS[-1] == "all"


def test_the_numbers_module_issues_no_write_and_no_event_name_literal_in_sql():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "src" / "services" / "applications"
            / "feedback_numbers.py").read_text(encoding="utf-8")
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert f"{verb} " not in text.replace("# ", ""), verb
    for name in settings.APPLICATION_STATUS_EVENT_TYPES:
        assert f"'{name}'" not in text, f"event name {name!r} quoted in the module"

