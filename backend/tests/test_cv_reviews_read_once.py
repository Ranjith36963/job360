"""The menu badge reads a user's ``cv_reviewed`` events ONCE per request (production 2026-11-17: ``b_reviews`` lap
94-107 ms on a large account; ``kit.reviews_clearing`` and ``kit.assistant_cv_turns`` each selected and
JSON-parsed the same large payloads, #1581 added the second read).

The shared read covers both exactly: both queries had the same filters (``user_id``, ``event_type``,
``application_id IN``) and the same order (``id DESC``); each function keeps only its own applications' rows in
Python, so nothing else about either result changes. Without ``rows=`` both still read for themselves.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications import cv_review, kit
from src.services.profile.models import AssistantSettings, CVData, UserProfile
from tests._cv_review import ATS_RAW, ATS_TAIL, ats_rows, blank_ad

RAW = (
    "Ada Lovelace\nEXPERIENCE\nData engineer at Northwind since 2019.\n"
    "Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.\n" + ATS_RAW
)
CV = (
    "Ada Lovelace\nada@example.com | +44 7700 900123 | linkedin.com/in/ada\nEXPERIENCE\n"
    "- Data engineer at Northwind since 2019.\n"
    "- Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.\n" + ATS_TAIL
)
L1 = {"n": 1, "verdict": "supported", "evidence": ["Data engineer at Northwind since 2019."]}
L2 = {"n": 2, "verdict": "supported", "evidence": ["Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%."]}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _bring(client: AsyncClient, n: int) -> tuple[int, int]:
    brought = await client.post("/api/jobs/bring", json={
        "title": f"Data Engineer {n}", "company": "Northwind", "location": "London", "country": "GB", "queue": True,
        "apply_url": f"https://northwind.example/careers/{n}", "description": "Build the pipelines. Python, Snowflake.",
    })
    assert brought.status_code == 200, brought.text
    app_id = int(brought.json()["application_id"])
    await blank_ad(app_id)
    saved = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV})
    assert saved.status_code == 201, saved.text
    return app_id, int(saved.json()["artifact_id"])


async def _review(client: AsyncClient, app_id: int, cv_id: int, confidence: str) -> None:
    payload = {"artifact_id": cv_id, "sha256": _sha(CV), "confidence": confidence, "lines": [L1, L2, *ats_rows(3)],
               "reviewer": "fresh helper"}
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "cv_reviewed", "payload": payload})
    assert resp.status_code == 201, resp.text


async def _raw_event(user_id: str, app_id: int, payload: str) -> None:
    from src.api import dependencies as api_deps

    db = await api_deps.get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db._db.execute(
        "INSERT INTO application_events (user_id, application_id, event_type, detail, payload, occurred_at, "
        "recorded_at, recorded_by) VALUES (?, ?, ?, '', ?, ?, ?, 'test')",
        (user_id, app_id, cv_review.EVENT_TYPE, payload, now, now),
    )
    await db._db.commit()


async def _fixture(client: AsyncClient, user_id: str) -> dict[int, dict[str, Any]]:
    """Five queued jobs, each with an unseen CV: older weak then newer strong, older strong then newer weak, a
    malformed payload on top of a strong one, a weak only, and none. Plus a cv_reviewed row of ANOTHER user."""
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text=RAW, name="Ada Lovelace")), user_id, source_action="cv_upload")
    save_assistant_settings(user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "seed")
    edits = [{"path": "assistant_settings.submit_mode", "value": "auto_when_sure"},
             {"path": "assistant_settings.cv_auto_approve", "value": True}]
    assert (await client.patch("/api/profile", json={"edits": edits})).status_code == 200
    cvs: dict[int, dict[str, Any]] = {}
    plan = {0: ("weak", "strong"), 1: ("strong", "weak"), 2: ("strong", "MALFORMED"), 3: ("weak",), 4: ()}
    for n, steps in plan.items():
        app_id, cv_id = await _bring(client, n)
        cvs[app_id] = {"artifact_id": cv_id, "sha": _sha(CV)}
        for step in steps:
            if step == "MALFORMED":
                await _raw_event(user_id, app_id, "{not json")
                await _raw_event(user_id, app_id, json.dumps(["a", "list"]))
            else:
                await _review(client, app_id, cv_id, step)
    # Another user's strong review of the same application id never counts for this user.
    from src.api import dependencies as api_deps

    db = await api_deps.get_db()
    other = "00000000-0000-0000-0000-00000000dead"
    await db._db.execute(
        "INSERT INTO users (id, email, password_hash) VALUES (?, ?, 'x')", (other, "other-reads-once@example.com"))
    await db._db.commit()
    first = next(iter(cvs))
    await _raw_event(other, first, json.dumps({
        "artifact_id": cvs[first]["artifact_id"], "sha256": _sha(CV), "confidence": "strong", "lines": []}))
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM application_events WHERE user_id = ? AND event_type = 'cv_reviewed'", (other,))
    assert (await cur.fetchone())[0] == 1  # the other-user case really runs
    return cvs


async def _legacy_clearing(db: Any, user_id: str, cvs: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """The selection ``reviews_clearing`` made before the shared read (its own query, its own parse)."""
    app_ids = sorted(cvs)
    cur = await db._db.execute(
        "SELECT id, application_id, event_type, payload, recorded_at, recorded_by FROM application_events "  # noqa: S608
        f"WHERE user_id = ? AND event_type = ? AND application_id IN ({','.join('?' for _ in app_ids)}) ORDER BY id DESC",
        (user_id, cv_review.EVENT_TYPE, *app_ids),
    )
    newest: dict[int, dict[str, Any]] = {}
    for r in await cur.fetchall():
        ev = dict(r)
        ev["payload"] = kit._json(ev.get("payload"))  # noqa: SLF001
        app_id, want = int(ev["application_id"]), cvs[int(ev["application_id"])]
        if app_id not in newest and cv_review.review_valid(ev["payload"], want["artifact_id"], want["sha"]):
            newest[app_id] = ev
    return {a: ev for a, ev in newest.items() if ev["payload"].get("confidence") == cv_review.STRONG}


async def _legacy_turns(db: Any, user_id: str, cvs: dict[int, dict[str, Any]]) -> dict[int, str]:
    """The result ``assistant_cv_turns`` gave before the shared read (its own query, its own parse)."""
    app_ids = sorted(cvs)
    cur = await db._db.execute(
        "SELECT application_id, payload FROM application_events "  # noqa: S608
        f"WHERE user_id = ? AND event_type = ? AND application_id IN ({','.join('?' for _ in app_ids)}) ORDER BY id DESC",
        (user_id, cv_review.EVENT_TYPE, *app_ids),
    )
    by_app: dict[int, list[dict[str, Any]]] = {}
    for app_id, raw in await cur.fetchall():
        by_app.setdefault(int(app_id), []).append({"payload": kit._json(raw)})  # noqa: SLF001
    out: dict[int, str] = {}
    for a in app_ids:
        evs, want = by_app.get(a, []), cvs[a]
        newest = next((e for e in evs if cv_review.review_valid(e["payload"], want["artifact_id"], want["sha"])), None)
        if newest is None:
            out[a] = kit.REVIEW_CV
        elif newest["payload"].get("confidence") != cv_review.STRONG:
            used = cv_review.judged_versions(evs, a) - {want["artifact_id"]}
            if cv_review.MAX_WEAK - len(used) > 0:
                out[a] = kit.FIX_CV
    return out


@pytest.mark.asyncio
async def test_needs_you_count_reads_the_cv_reviews_once(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.repositories import pg

    async with authenticated_async_context() as client:
        await _fixture(client, fixture_user_id)
        real = pg.Connection.execute
        seen: list[str] = []

        async def counting(self: Any, sql: str, params: Any = ()) -> Any:
            if "FROM application_events" in sql and cv_review.EVENT_TYPE in tuple(params):
                seen.append(sql)
            return await real(self, sql, params)

        monkeypatch.setattr(pg.Connection, "execute", counting)
        resp = await client.get("/api/needs-you-count")
        assert resp.status_code == 200, resp.text
    assert len(seen) == 1, seen


@pytest.mark.asyncio
async def test_shared_rows_give_what_each_function_gave_alone(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps

    async with authenticated_async_context() as client:
        cvs = await _fixture(client, fixture_user_id)
        db = await api_deps.get_db()
        ids = list(cvs)
        subsets = [cvs, {i: cvs[i] for i in ids[:2]}, {i: cvs[i] for i in ids[1:4]}, {ids[4]: cvs[ids[4]]}, {}]
        for sub in subsets:
            want_turns = await _legacy_turns(db, fixture_user_id, sub) if sub else {}
            assert await kit.assistant_cv_turns(db, fixture_user_id, sub) == want_turns
            rows = await kit.read_cv_reviews(db, fixture_user_id, ids)  # a SUPERSET, as the badge reads it
            assert await kit.assistant_cv_turns(db, fixture_user_id, sub, rows=rows) == want_turns
            want_clear = await _legacy_clearing(db, fixture_user_id, sub) if sub else {}
            alone = await kit.reviews_clearing(db, fixture_user_id, sub, auto_only=False)
            shared = await kit.reviews_clearing(db, fixture_user_id, sub, auto_only=False, rows=rows)
            assert alone == shared
            assert {a: e for a, e in alone.items()} == {a: e for a, e in want_clear.items() if a in alone}
        # the fixture really exercises both verdicts, a tries-left weak, no review, and a malformed payload
        turns = await _legacy_turns(db, fixture_user_id, cvs)
        assert (turns[ids[1]], turns[ids[3]], turns[ids[4]]) == (kit.FIX_CV, kit.FIX_CV, kit.REVIEW_CV)
        assert ids[0] not in turns
        assert ids[0] in await _legacy_clearing(db, fixture_user_id, cvs)


@pytest.mark.asyncio
async def test_needs_you_count_json_is_unchanged_by_the_shared_read(
    authenticated_async_context, fixture_user_id, monkeypatch
):
    async with authenticated_async_context() as client:
        await _fixture(client, fixture_user_id)
        shared = await client.get("/api/needs-you-count")
        assert shared.status_code == 200, shared.text
        real_clearing, real_turns = kit.reviews_clearing, kit.assistant_cv_turns

        async def clearing(*a: Any, **kw: Any) -> Any:
            kw.pop("rows", None)
            return await real_clearing(*a, **kw)

        async def turns(*a: Any, **kw: Any) -> Any:
            kw.pop("rows", None)
            return await real_turns(*a, **kw)

        monkeypatch.setattr(kit, "reviews_clearing", clearing)
        monkeypatch.setattr(kit, "assistant_cv_turns", turns)
        alone = await client.get("/api/needs-you-count")
        assert alone.status_code == 200, alone.text
        assert shared.json() == alone.json()
