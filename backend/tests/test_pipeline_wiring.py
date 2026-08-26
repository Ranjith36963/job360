"""Tests for the Pipeline-page wiring batch.

Three wires that existed as data but were never connected to a reader:

  1. ``applications`` is the ONE truth for "applied" — the job list's
     ``?action=applied`` filter used to read ``user_actions``, which the
     Apply button never wrote, so the filter always returned an empty list.
  2. A pipeline card never learned its job had expired: staleness was checked
     once at creation time and never again.
  3. ``get_tailored_summary_for_jobs`` was written for the Kanban board and
     never called, so the CV/Letter button could not say whether a document
     already existed.

Rule #21 (value-presence, not schema-presence): every assertion here checks a
REAL value produced by a real write, never a serializer default. Each test
would pass trivially against ``= False`` / ``= {}`` defaults if it only
asserted the key was present, so it asserts the non-default instead.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.api import dependencies as api_deps
from src.repositories.database import JobDatabase

# ── helpers ──────────────────────────────────────────────────────────────────


async def _insert_job_row(
    db: JobDatabase,
    *,
    title: str = "Platform Engineer",
    company: str = "Northwind",
    staleness: str = "active",
) -> int:
    """Insert a minimal job row and return its id."""
    now = datetime(2026, 8, 25, 9, 0, 0, tzinfo=timezone.utc).isoformat()
    suffix = title.lower().replace(" ", "_")
    cur = await db._conn.execute(
        """INSERT INTO jobs
           (title, company, location, description, apply_url, source, date_found,
            match_score, visa_flag, experience_level,
            normalized_company, normalized_title, first_seen,
            first_seen_at, last_seen_at, date_confidence, staleness_state)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            title,
            company,
            "Manchester, UK",
            "A test job",
            f"https://example.com/jobs/{suffix}",
            "greenhouse",
            now,
            80,
            0,
            "mid",
            company.lower(),
            title.lower(),
            now,
            now,
            now,
            "high",
            staleness,
        ),
    )
    await db._conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


async def _add_to_feed(db: JobDatabase, user_id: str, job_id: int, score: int = 80) -> None:
    """Put the job in this user's feed.

    NOT optional decoration: for a logged-in caller `GET /api/jobs` reads
    `get_user_feed_jobs` (routes/jobs.py:564), not the shared catalog. A job with no
    feed row is invisible to the list no matter what else is true of it — which is
    also a real bound on the Applied filter, not just a test detail.
    """
    await db._conn.execute(
        "INSERT INTO user_feed (user_id, job_id, score, bucket) VALUES (?, ?, ?, ?)",
        (user_id, job_id, score, "good"),
    )
    await db._conn.commit()


async def _expire_job(db: JobDatabase, job_id: int) -> None:
    """Flip a live job to confirmed_expired, the way the ghost detector does."""
    await db._conn.execute(
        "UPDATE jobs SET staleness_state = 'confirmed_expired' WHERE id = ?",
        (job_id,),
    )
    await db._conn.commit()


async def _insert_tailored_doc(
    db: JobDatabase, user_id: str, job_id: int, doc_kind: str, status: str = "draft"
) -> None:
    now = datetime(2026, 8, 25, 10, 0, 0, tzinfo=timezone.utc).isoformat()
    await db._conn.execute(
        """INSERT INTO tailored_documents
           (user_id, job_id, doc_kind, ai_draft, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, job_id, doc_kind, "draft body", status, now, now),
    )
    await db._conn.commit()


# ── wire 1: applications is the one truth for "applied" ──────────────────────


@pytest.mark.asyncio
async def test_applied_filter_finds_jobs_added_to_the_pipeline(authenticated_async_context):
    """POST /pipeline/{id} → GET /jobs?action=applied returns that job.

    This is the wire that was missing: the Apply button only wrote an
    ``applications`` row, while the filter read ``user_actions``.
    """
    db = await api_deps.get_db()
    applied_id = await _insert_job_row(db, title="Applied Role", company="AppliedCo")
    other_id = await _insert_job_row(db, title="Untouched Role", company="OtherCo")
    uid = authenticated_async_context.fixture_user_id
    await _add_to_feed(db, uid, applied_id)
    await _add_to_feed(db, uid, other_id)

    async with authenticated_async_context() as client:
        resp = await client.post(f"/api/pipeline/{applied_id}")
        assert resp.status_code == 200, resp.text

        resp = await client.get("/api/jobs?action=applied")
        assert resp.status_code == 200, resp.text
        ids = [j["id"] for j in resp.json()["jobs"]]

    assert applied_id in ids, "job added to the pipeline is missing from ?action=applied"
    assert other_id not in ids, "?action=applied leaked a job that was never applied to"


@pytest.mark.asyncio
async def test_applied_flag_is_true_on_the_job_row(authenticated_async_context):
    """The job list marks a pipelined job ``applied: true`` and others false."""
    db = await api_deps.get_db()
    applied_id = await _insert_job_row(db, title="Flagged Role", company="FlagCo")
    other_id = await _insert_job_row(db, title="Unflagged Role", company="PlainCo")
    uid = authenticated_async_context.fixture_user_id
    await _add_to_feed(db, uid, applied_id)
    await _add_to_feed(db, uid, other_id)

    async with authenticated_async_context() as client:
        await client.post(f"/api/pipeline/{applied_id}")
        resp = await client.get("/api/jobs")
        assert resp.status_code == 200, resp.text
        by_id = {j["id"]: j for j in resp.json()["jobs"]}

    assert by_id[applied_id]["applied"] is True
    assert by_id[other_id]["applied"] is False


@pytest.mark.asyncio
async def test_applying_never_erases_a_like(authenticated_async_context):
    """Applying to a liked job keeps the like.

    ``user_actions`` is one row per (user, job) with ``ON CONFLICT DO UPDATE``
    (database.py:848), so writing 'applied' there would overwrite 'liked' and
    silently drop the heart from the card. The pipeline table carries the
    applied fact instead — this test is the guard on that decision.
    """
    db = await api_deps.get_db()
    job_id = await _insert_job_row(db, title="Liked And Applied", company="BothCo")
    await _add_to_feed(db, authenticated_async_context.fixture_user_id, job_id)

    async with authenticated_async_context() as client:
        resp = await client.post(f"/api/jobs/{job_id}/action", json={"action": "liked"})
        assert resp.status_code == 200, resp.text

        resp = await client.post(f"/api/pipeline/{job_id}")
        assert resp.status_code == 200, resp.text

        resp = await client.get("/api/jobs")
        assert resp.status_code == 200, resp.text
        row = next(j for j in resp.json()["jobs"] if j["id"] == job_id)

    assert row["action"] == "liked", "applying wiped the user's like"
    assert row["applied"] is True, "applied flag lost because the like won"


@pytest.mark.asyncio
async def test_liked_filter_still_reads_user_actions(authenticated_async_context):
    """The other two action filters keep their old behaviour."""
    db = await api_deps.get_db()
    liked_id = await _insert_job_row(db, title="Liked Only", company="HeartCo")
    pipelined_id = await _insert_job_row(db, title="Pipelined Only", company="TrackCo")
    uid = authenticated_async_context.fixture_user_id
    await _add_to_feed(db, uid, liked_id)
    await _add_to_feed(db, uid, pipelined_id)

    async with authenticated_async_context() as client:
        await client.post(f"/api/jobs/{liked_id}/action", json={"action": "liked"})
        await client.post(f"/api/pipeline/{pipelined_id}")

        resp = await client.get("/api/jobs?action=liked")
        assert resp.status_code == 200, resp.text
        ids = [j["id"] for j in resp.json()["jobs"]]

    assert ids == [liked_id]


# ── wire 2: a pipeline card learns its job expired ───────────────────────────


@pytest.mark.asyncio
async def test_pipeline_card_flags_a_job_that_expired_after_applying(
    authenticated_async_context,
):
    """Apply while live, job dies later → the card comes back ``expired: true``."""
    db = await api_deps.get_db()
    job_id = await _insert_job_row(db, title="Doomed Role", company="ClosingCo")

    async with authenticated_async_context() as client:
        resp = await client.post(f"/api/pipeline/{job_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["expired"] is False, "a live job must not be flagged expired"

        await _expire_job(db, job_id)

        resp = await client.get("/api/pipeline")
        assert resp.status_code == 200, resp.text
        card = next(a for a in resp.json()["applications"] if a["job_id"] == job_id)

    assert card["expired"] is True, "pipeline card never noticed the job expired"


@pytest.mark.asyncio
async def test_pipeline_card_of_a_live_job_is_not_expired(authenticated_async_context):
    """A job that is merely stale (not confirmed dead) is NOT flagged.

    ``possibly_stale``/``likely_stale`` are guesses from absence; only
    ``confirmed_expired`` comes from a direct URL check (ghost_detection.py:31).
    Flagging a guess as dead would tell the user to stop chasing a live job.
    """
    db = await api_deps.get_db()
    job_id = await _insert_job_row(db, title="Quiet Role", company="SlowCo")

    async with authenticated_async_context() as client:
        await client.post(f"/api/pipeline/{job_id}")
        await db._conn.execute(
            "UPDATE jobs SET staleness_state = 'likely_stale' WHERE id = ?", (job_id,)
        )
        await db._conn.commit()

        resp = await client.get("/api/pipeline")
        card = next(a for a in resp.json()["applications"] if a["job_id"] == job_id)

    assert card["expired"] is False


# ── wire 2b: the closing date survives becoming an application (W-16) ────────


@pytest.mark.asyncio
async def test_the_closing_date_survives_becoming_an_application(
    authenticated_async_context,
):
    """Before applying the card says "Apply by 30 June". After, it said nothing.

    The date was never lost from the database — the pipeline query simply did not
    select it and the response model had nowhere to put it. So a deadline you could
    still act on became invisible the moment you started acting on it.
    """
    db = await api_deps.get_db()
    job_id = await _insert_job_row(db, title="Closing Role", company="DeadlineCo")
    await db._conn.execute(
        "UPDATE jobs SET deadline = ? WHERE id = ?", ("2026-09-30", job_id)
    )
    await db._conn.commit()

    async with authenticated_async_context() as client:
        await client.post(f"/api/pipeline/{job_id}")
        resp = await client.get("/api/pipeline")
        card = next(a for a in resp.json()["applications"] if a["job_id"] == job_id)

    assert card["deadline"] == "2026-09-30", "the closing date vanished on applying"


@pytest.mark.asyncio
async def test_a_job_with_no_deadline_reports_none_rather_than_a_guess(
    authenticated_async_context,
):
    """Most ads state no closing date. None must mean "unknown", never a made-up one."""
    db = await api_deps.get_db()
    job_id = await _insert_job_row(db, title="Open Ended Role", company="NoDateCo")

    async with authenticated_async_context() as client:
        await client.post(f"/api/pipeline/{job_id}")
        resp = await client.get("/api/pipeline")
        card = next(a for a in resp.json()["applications"] if a["job_id"] == job_id)

    assert card["deadline"] is None


# ── wire 3: the Kanban card knows which documents exist ──────────────────────


@pytest.mark.asyncio
async def test_pipeline_card_reports_its_tailored_documents(authenticated_async_context):
    """A generated CV shows up on the card as ``tailored: {"cv": "draft"}``."""
    db = await api_deps.get_db()
    job_id = await _insert_job_row(db, title="Tailored Role", company="DocCo")
    user_id = authenticated_async_context.fixture_user_id

    async with authenticated_async_context() as client:
        await client.post(f"/api/pipeline/{job_id}")
        await _insert_tailored_doc(db, user_id, job_id, "cv", status="kept")
        await _insert_tailored_doc(db, user_id, job_id, "cover_letter", status="draft")

        resp = await client.get("/api/pipeline")
        assert resp.status_code == 200, resp.text
        card = next(a for a in resp.json()["applications"] if a["job_id"] == job_id)

    assert card["tailored"] == {"cv": "kept", "cover_letter": "draft"}


@pytest.mark.asyncio
async def test_pipeline_card_tailored_is_empty_when_nothing_generated(
    authenticated_async_context,
):
    """No documents → an empty map, not a missing key and not a fake entry."""
    db = await api_deps.get_db()
    job_id = await _insert_job_row(db, title="Bare Role", company="EmptyCo")

    async with authenticated_async_context() as client:
        await client.post(f"/api/pipeline/{job_id}")
        resp = await client.get("/api/pipeline")
        card = next(a for a in resp.json()["applications"] if a["job_id"] == job_id)

    assert card["tailored"] == {}


@pytest.mark.asyncio
async def test_tailored_summary_never_leaks_another_users_documents(
    authenticated_async_context,
):
    """Rule #12/#25: the summary is scoped by user.id, not by job_id alone."""
    db = await api_deps.get_db()
    job_id = await _insert_job_row(db, title="Shared Role", company="MultiCo")
    user_id = authenticated_async_context.fixture_user_id

    # A different user generated a CV for the SAME job in the shared catalog.
    other_user = "00000000-0000-0000-0000-0000000000ff"
    await db._conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (other_user, "other@example.com", "x", "2026-08-25T00:00:00Z"),
    )
    await db._conn.commit()
    await _insert_tailored_doc(db, other_user, job_id, "cv", status="kept")

    async with authenticated_async_context() as client:
        await client.post(f"/api/pipeline/{job_id}")
        resp = await client.get("/api/pipeline")
        card = next(a for a in resp.json()["applications"] if a["job_id"] == job_id)

    assert card["tailored"] == {}, "another user's tailored CV leaked onto this card"
    assert user_id != other_user
