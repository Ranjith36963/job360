"""Fixes for the application-spine review findings (docs/plans/2026-09-04-
application-spine/spec.md build). Each test pins ONE finding so it stays
fixed:

B1 — migration 0037 backfills `status` from `stage` but leaves a legacy
     application with no stage_history/receipt/tailored-doc row with ZERO
     events; the next real event's status recompute then defaults to
     'considering', wiping the real status.
B2 — `birth_application` used to omit `stage`, so a freshly-brought
     (`considering`) job read as `applied` on the legacy `/api/pipeline` and
     could be nagged about by reminders.
B3 — `parse_occurred_at` kept the caller's UTC offset; the TEXT column is
     ordered lexically, so mixed offsets can sort out of real chronological
     order.
B4 — `whats_new`'s cursor used `recorded_at >= since AND id > after_id` as
     two INDEPENDENT predicates, so an event with a lower id than the cursor
     boundary but a newer `recorded_at` was skipped forever.
B5 — `corrects_event_id` was accepted without checking the target event
     exists and belongs to the SAME application.
B6 — `record_receipt` hardcoded `profile_version=None` instead of stamping
     the caller's current profile version, unlike the legacy receipts route.

Helpers are imported from ``tests.test_application_spine`` (frozen, not
edited) rather than duplicated.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone

import pytest

from tests.test_application_spine import _AD_2, _bring, _get_application, _record_event, _record_receipt

_NOW = "2026-01-01T00:00:00Z"


# ═══════════════════════════════════════════════════════════════════════════
# B1 — a legacy application with no fold-generated events keeps its status
# ═══════════════════════════════════════════════════════════════════════════


async def _seed_pre_migration_offer_app(db_path: str) -> None:
    """A single legacy `applications` row at stage='offer' with NO matching
    `application_stage_history` / `application_receipts` / `tailored_documents`
    row — the exact B1 scenario: after the fold, this row's `status` is
    backfilled to 'offer' (step 3) but gets no event at all from steps 5-7.
    """
    from migrations import runner
    from src.repositories import pg as _pg
    from src.repositories.database import JobDatabase

    db = JobDatabase(db_path)
    await db.init_db()
    await db.close()
    await runner.up(db_path, target="0036_oauth")

    async with _pg.connect(db_path) as conn:
        conn.row_factory = _pg.Row
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            ("b1-user", "b1@example.com", "!", _NOW),
        )
        await conn.execute(
            "INSERT INTO applications (user_id, job_id, stage, notes, created_at, updated_at) "
            "VALUES (?, ?, 'offer', '', ?, ?)",
            ("b1-user", 900_100, _NOW, _NOW),
        )
        await conn.commit()

    await runner.up(db_path)


@pytest.mark.asyncio
async def test_b1_a_legacy_offer_survives_its_first_new_event(tmp_path):
    from src.repositories import pg as _pg
    from src.services.applications import spine

    db_path = str(tmp_path / "b1.db")
    await _seed_pre_migration_offer_app(db_path)

    async with _pg.connect(db_path) as conn:
        conn.row_factory = _pg.Row
        cur = await conn.execute(
            "SELECT id, status FROM applications WHERE user_id = ? AND job_id = ?", ("b1-user", 900_100)
        )
        row = await cur.fetchone()
        assert row["status"] == "offer", "step 3's backfill must still land 'offer'"
        application_id = row["id"]

        # Sanity: the fold's synthetic backfill (B1 fix) really did give this
        # application an event — otherwise this test would pass for the WRONG
        # reason (an empty log replaying to the 'considering' default would
        # never even be exercised by the assertion below).
        cur = await conn.execute(
            "SELECT COUNT(*) FROM application_events WHERE application_id = ?", (application_id,)
        )
        assert (await cur.fetchone())[0] > 0, "B1 fix: a legacy row with no fold history must still get ONE event"

        from src.repositories.database import JobDatabase

        db = JobDatabase.from_connection(db_path, conn)
        result = await spine.append_event(
            db, user_id="b1-user", application_id=application_id, event_type="artifact_saved",
            occurred_at=datetime.now(timezone.utc).isoformat(), recorded_by="test",
            payload={"artifact_id": 1, "kind": "cv", "version_no": 1},
        )
        assert result["status"] == "offer", (
            "B1: appending a note-family-adjacent event on a legacy app with "
            "no fold history must NOT reset status to the 'considering' default"
        )

        cur = await conn.execute("SELECT status FROM applications WHERE id = ?", (application_id,))
        assert (await cur.fetchone())["status"] == "offer"

    with contextlib.suppress(Exception):
        await _pg.drop_schema(db_path)


# ═══════════════════════════════════════════════════════════════════════════
# B2 — a brought job is 'considering' on the spine, never 'applied'
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_b2_a_brought_job_shows_considering_not_applied(authenticated_async_context):
    """The Kanban `/api/pipeline` this originally pinned went with the
    mission sweep; `GET /api/applications` is the one surface left to read
    a freshly-brought job's status back from."""
    async with authenticated_async_context() as client:
        brought = await _bring(client)
        job_id = brought["job"]["id"]

        apps = await client.get("/api/applications")
        assert apps.status_code == 200, apps.text
        row = next(a for a in apps.json()["applications"] if a["job_id"] == job_id)
        assert row["status"] == "considering", (
            "B2: birth_application must write status='considering' explicitly — "
            "the column's 'applied' default must never leak through"
        )


# ═══════════════════════════════════════════════════════════════════════════
# B3 — occurred_at is normalised to UTC so mixed offsets sort correctly
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_b3_mixed_offsets_sort_by_real_time_not_by_string(authenticated_async_context):
    async with authenticated_async_context() as client:
        brought = await _bring(client)
        app_id = brought["application_id"]

        # 2026-01-01T17:30:00Z, written under a +05:30 offset. Its RAW string
        # ("...T23:00:00+05:30") is lexically GREATER than the second event's
        # raw string below, even though it happened EARLIER in real time —
        # the exact "+05:30 case" the review named.
        earlier_utc_odd_offset = "2026-01-01T23:00:00+05:30"
        # 2026-01-01T20:00:00Z under +00:00 — later in real time, but its raw
        # string sorts BEFORE the one above under naive lexical comparison.
        later_utc_zulu_offset = "2026-01-01T20:00:00+00:00"

        await _record_event(client, app_id, "note", detail="earlier-real-time", occurred_at=earlier_utc_odd_offset)
        await _record_event(client, app_id, "note", detail="later-real-time", occurred_at=later_utc_zulu_offset)

        detail = await _get_application(client, app_id)
        assert detail.status_code == 200, detail.text
        notes = [e["detail"] for e in detail.json()["events"] if e["event_type"] == "note"]
        assert notes == ["earlier-real-time", "later-real-time"], (
            "B3: the timeline must order by the real instant, not by the "
            "caller's original offset string"
        )
        # And the stored strings themselves are canonical +00:00, not the
        # caller's raw offset (pg.py's own convention — see spine.py comment).
        raw = [e["occurred_at"] for e in detail.json()["events"] if e["event_type"] == "note"]
        assert all(o.endswith("+00:00") for o in raw), raw


# ═══════════════════════════════════════════════════════════════════════════
# B4 — whats_new's cursor is a REAL keyset pair, not two independent AND
# predicates
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_b4_a_lower_id_recorded_after_the_cursor_boundary_is_not_skipped(
    authenticated_async_context, fixture_user_id
):
    from src.core import settings
    from src.repositories import pgsync

    async with authenticated_async_context() as client:
        brought = await _bring(client)
        app_id = brought["application_id"]

    conn = pgsync.connect(str(settings.DB_PATH))
    baseline = conn.execute("SELECT MAX(id) FROM application_events").fetchone()[0]

    boundary_id = baseline + 2
    late_id = baseline + 1  # LOWER id than the boundary
    boundary_recorded_at = "2030-01-01T00:10:00+00:00"
    late_recorded_at = "2030-01-01T00:20:00+00:00"  # NEWER than the boundary

    # Explicit ids, deliberately out of natural auto-increment order — this
    # makes the review's "a concurrently-committed event with a lower id is
    # skipped forever" scenario deterministic instead of timing-dependent: a
    # transaction that grabbed a low sequence value but took longer to commit
    # than one that grabbed a higher value produces exactly this shape.
    conn.execute(
        "INSERT INTO application_events "
        "(id, user_id, application_id, event_type, detail, payload, occurred_at, recorded_at, recorded_by) "
        "VALUES (?, ?, ?, 'note', 'boundary-row', '{}', ?, ?, 'test')",
        (boundary_id, fixture_user_id, app_id, boundary_recorded_at, boundary_recorded_at),
    )
    conn.execute(
        "INSERT INTO application_events "
        "(id, user_id, application_id, event_type, detail, payload, occurred_at, recorded_at, recorded_by) "
        "VALUES (?, ?, ?, 'note', 'late-lower-id-row', '{}', ?, ?, 'test')",
        (late_id, fixture_user_id, app_id, late_recorded_at, late_recorded_at),
    )
    conn.commit()
    conn.close()

    async with authenticated_async_context() as client:
        # Simulates a client that already consumed a page ending exactly at
        # the boundary row (since=boundary's recorded_at, after_id=boundary's id).
        resp = await client.get(
            "/api/whats-new", params={"since": boundary_recorded_at, "after_id": boundary_id}
        )
        assert resp.status_code == 200, resp.text
        details = [e["detail"] for e in resp.json()["events"]]
        assert "late-lower-id-row" in details, (
            "B4: an event recorded strictly AFTER the cursor boundary must "
            "never be skipped just because its id is lower than the boundary's"
        )
        # And walking forward from this page never re-shows the boundary itself.
        assert "boundary-row" not in details


# ═══════════════════════════════════════════════════════════════════════════
# B5 — corrects_event_id must name a real event on the SAME application
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_b5_corrects_event_id_must_exist_on_the_same_application(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]

        made_up = await _record_event(client, app_id, "note", detail="oops", corrects_event_id=999_999_999)
        assert made_up.status_code == 422, made_up.text

        other_app_id = (await _bring(client, _AD_2))["application_id"]
        other_event = await _record_event(client, other_app_id, "note", detail="lives on the other application")
        assert other_event.status_code == 201, other_event.text
        other_event_id = other_event.json()["event_id"]

        cross_application = await _record_event(
            client, app_id, "note", detail="nice try", corrects_event_id=other_event_id
        )
        assert cross_application.status_code == 422, (
            "B5: corrects_event_id naming a REAL event on a DIFFERENT "
            "application must still be refused"
        )

        real_event = await _record_event(client, app_id, "note", detail="the mistake")
        assert real_event.status_code == 201, real_event.text
        real_event_id = real_event.json()["event_id"]

        fix = await _record_event(client, app_id, "note", detail="corrected", corrects_event_id=real_event_id)
        assert fix.status_code == 201, "a corrects_event_id naming a real event on THIS application must succeed"

        detail = await _get_application(client, app_id)
        by_id = {e["id"]: e for e in detail.json()["events"]}
        assert by_id[real_event_id]["superseded"] is True


# ═══════════════════════════════════════════════════════════════════════════
# B6 — record_receipt stamps the caller's current profile version
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_b6_record_receipt_stamps_the_current_profile_version(authenticated_async_context, monkeypatch):
    import src.services.profile.storage as storage

    monkeypatch.setattr(storage, "current_profile_version_id", lambda user_id: 777)

    async with authenticated_async_context() as client:
        app_id = (await _bring(client))["application_id"]
        receipt = await _record_receipt(client, app_id, channel="email")
        assert receipt.status_code == 201, receipt.text
        receipt_id = receipt.json()["receipt_id"]

    from src.core import settings
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    row = conn.execute(
        "SELECT profile_version FROM application_receipts WHERE id = ?", (receipt_id,)
    ).fetchone()
    conn.close()
    assert row[0] == 777, (
        "B6: record_receipt must stamp the CALLER's current profile version, "
        "not hardcode None (the legacy /receipts/{job_id} route has always done this)"
    )
