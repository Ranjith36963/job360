"""Tests for FastAPI backend API.

Batch 3.5.4 rehab: routes that require auth (added in Batch 3.5 IDOR
fixes) now use the `authenticated_async_context` fixture from conftest.py.
The 3 always-public endpoints (/health, /status, /sources) stay on the
bare ASGITransport pattern — they don't need auth.
"""

import json
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import dependencies as api_deps
from src.api.main import app
from src.repositories.database import JobDatabase


@pytest.mark.asyncio
async def test_health_returns_ok():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_status_returns_counts(authenticated_async_context):
    # Use the fixture's isolated, fully-migrated DB rather than the ambient
    # data/jobs.db: the bare AsyncClient hit whatever DB_PATH resolved to (a
    # stale local data/jobs.db lacking staleness_state -> 500), and writing to
    # the real dev DB is a data-pollution risk. /api/status is public, so the
    # authenticated client's cookie is simply unused.
    async with authenticated_async_context() as client:
        resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "jobs_total" in data
    assert data["sources_total"] == 46


@pytest.mark.asyncio
async def test_sources_returns_46():
    """2026-06 M6 rotation dropped 4 upstream-dead sources (jobtensor, comeet,
    gov_apprenticeships, aijobs_global), reducing the count from 50 to 46."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/sources")
    assert resp.status_code == 200
    assert len(resp.json()["sources"]) == 46


@pytest.mark.asyncio
async def test_jobs_list_empty(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.get("/api/jobs")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_actions_counts_empty(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.get("/api/actions/counts")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_profile_404_when_none(authenticated_async_context):
    """With no profile row for the authenticated user, GET /profile is 404."""
    async with authenticated_async_context() as client:
        # The fresh fixture-user has no profile row yet, so the real
        # load_profile returns None and the route raises 404 — no need
        # to mock load_profile anymore (Batch 3.5.2 made storage
        # per-user).
        resp = await client.get("/api/profile")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pipeline_counts_empty(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.get("/api/pipeline/counts")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("applied", 0) == 0


@pytest.mark.asyncio
async def test_pipeline_list_empty(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.get("/api/pipeline")
    assert resp.status_code == 200
    assert resp.json()["applications"] == []


@pytest.mark.asyncio
async def test_full_api_workflow(authenticated_async_context):
    """Integration test: health → status → sources → jobs → actions → pipeline → profile."""
    async with authenticated_async_context() as client:
        # Health (public)
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Status (public)
        resp = await client.get("/api/status")
        assert resp.status_code == 200
        assert resp.json()["sources_total"] == 46

        # Sources (public)
        resp = await client.get("/api/sources")
        assert resp.status_code == 200
        assert len(resp.json()["sources"]) == 46

        # Jobs (authed, empty DB)
        resp = await client.get("/api/jobs")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        # Jobs export (authed, empty CSV)
        resp = await client.get("/api/jobs/export")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")

        # Action counts (authed, empty)
        resp = await client.get("/api/actions/counts")
        assert resp.status_code == 200

        # Actions list (authed, empty)
        resp = await client.get("/api/actions")
        assert resp.status_code == 200

        # Pipeline counts (authed, empty)
        resp = await client.get("/api/pipeline/counts")
        assert resp.status_code == 200

        # Pipeline list (authed, empty)
        resp = await client.get("/api/pipeline")
        assert resp.status_code == 200
        assert resp.json()["applications"] == []

        # Pipeline reminders (authed, empty)
        resp = await client.get("/api/pipeline/reminders")
        assert resp.status_code == 200

        # Profile (authed — no row for fixture-user, so 404)
        resp = await client.get("/api/profile")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Step-1 B6 — JobResponse surfaces date-model + enrichment fields, JOIN-once
# prefetch from job_enrichment. RED tests live here; if you change the
# response shape, update lib/types.ts in the frontend in lock-step.
# ---------------------------------------------------------------------------


async def _insert_job_row(db: JobDatabase, **overrides) -> int:
    """Insert a row directly via the active aiosqlite connection — bypasses
    `insert_job` so tests can pin date-model fields and id deterministically.
    Returns the inserted job id."""
    # Use *current* time, not a pinned past date: the list route filters by
    # `first_seen >= now() - days` against the REAL clock, so a hardcoded
    # 2026-04-23 made these rows fall outside the recency window once real time
    # advanced (a time-bomb — passed in Apr/May, failed after). Tests that need
    # a fixed date pass explicit overrides.
    now = datetime.now(timezone.utc).isoformat()
    payload = dict(
        title="ML Engineer",
        company="Acme AI",
        location="London, UK",
        salary_min=70000,
        salary_max=90000,
        description="ML engineer role",
        apply_url="https://example.com/jobs/1",
        source="greenhouse",
        date_found=now,
        match_score=80,
        visa_flag=1,
        experience_level="senior",
        normalized_company="acme ai",
        normalized_title="ml engineer",
        first_seen=now,
        posted_at=now,
        first_seen_at=now,
        last_seen_at=now,
        date_confidence="high",
        date_posted_raw=now,
        staleness_state="active",
    )
    feed_for_user = overrides.pop("feed_for_user", None)
    payload.update(overrides)
    cols = ", ".join(payload.keys())
    placeholders = ", ".join(["?"] * len(payload))
    cur = await db._conn.execute(
        f"INSERT INTO jobs ({cols}) VALUES ({placeholders})",  # noqa: S608 — test helper, cols built from local dict
        tuple(payload.values()),
    )
    await db._conn.commit()
    job_id = cur.lastrowid

    # The dashboard now reads each user's user_feed (multi-tenant isolation), so
    # attach this job to the authenticated test user's feed — otherwise an
    # authenticated GET /api/jobs returns nothing. Defaults to the single
    # non-system user the auth fixture created.
    uid = feed_for_user
    if uid is None:
        from src.core.tenancy import DEFAULT_TENANT_ID

        u = await db._conn.execute(
            "SELECT id FROM users WHERE id != ? AND deleted_at IS NULL ORDER BY rowid DESC LIMIT 1",
            (DEFAULT_TENANT_ID,),
        )
        urow = await u.fetchone()
        uid = urow[0] if urow else None
    if uid is not None:
        from src.services.feed import FeedService

        await FeedService(db._conn).upsert_feed_row(
            user_id=uid, job_id=job_id, score=int(payload.get("match_score", 0) or 0), bucket="7d"
        )
    return job_id


async def _insert_enrichment_row(db: JobDatabase, job_id: int, **overrides) -> None:
    payload = dict(
        title_canonical="Senior ML Engineer",
        category="machine_learning",
        employment_type="full_time",
        workplace_type="hybrid",
        locations=json.dumps(["London"]),
        salary=json.dumps(
            {
                "min": 70000.0,
                "max": 90000.0,
                "currency": "GBP",
                "frequency": "annual",
            }
        ),
        required_skills=json.dumps(["Python", "PyTorch"]),
        preferred_skills=json.dumps(["TensorFlow"]),
        experience_min_years=5,
        experience_level="senior",
        requirements_summary="Senior role",
        language="en",
        employer_type="scaleup",
        visa_sponsorship="yes",
        seniority="senior",
        remote_region=None,
        apply_instructions=None,
        red_flags=json.dumps([]),
    )
    payload.update(overrides)
    cols = ", ".join(["job_id", *payload.keys()])
    placeholders = ", ".join(["?"] * (1 + len(payload)))
    await db._conn.execute(
        f"INSERT INTO job_enrichment ({cols}) VALUES ({placeholders})",  # noqa: S608 — test helper, cols built from local dict
        (job_id, *payload.values()),
    )
    await db._conn.commit()


@pytest.mark.asyncio
async def test_jobs_response_includes_score_dim_breakdown(authenticated_async_context):
    """Step-1.5 S1.1-H — JobResponse must surface the per-dim breakdown
    columns added by migration 0011, not silently default them all to 0.

    This is the value-presence test the Step-1 reviewer never wrote
    (CLAUDE.md rule #21). Schema-presence already passed in Step 1; what
    failed silently was that `_row_to_job_response()` never extracted the
    fields. With migration 0011 + the writer + the serializer all wired,
    a job inserted with role=35/skill=30/etc. must round-trip non-zero.
    """
    db = await api_deps.get_db()
    job_id = await _insert_job_row(
        db,
        match_score=85,
        role=35,
        skill=30,
        seniority_score=4,
        location_score=8,
        recency=6,
        semantic=2,
    )
    async with authenticated_async_context() as client:
        resp = await client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    # The bombshell-fix assertion: at least one dim must be non-zero.
    assert any(
        body.get(dim, 0) > 0
        for dim in (
            "role",
            "skill",
            "seniority_score",
            "experience",
            "credentials",
            "location_score",
            "recency",
            "semantic",
        )
    ), f"all dims defaulted to 0 — serializer regression: {body}"
    # And specifically: the values inserted must round-trip exactly.
    assert body["role"] == 35
    assert body["skill"] == 30
    assert body["seniority_score"] == 4
    assert body["location_score"] == 8
    assert body["recency"] == 6
    assert body["semantic"] == 2
    # Unset dims default to 0 (Pillar 2.9 sentinel for "not scored").
    assert body["experience"] == 0
    assert body["credentials"] == 0
    assert body["penalty"] == 0


@pytest.mark.asyncio
async def test_jobs_response_includes_date_model_fields(authenticated_async_context):
    """B6: GET /jobs/:id surfaces the 5 lifecycle/date columns."""
    pinned = "2026-04-20T08:00:00+00:00"
    db = await api_deps.get_db()
    job_id = await _insert_job_row(
        db,
        posted_at=pinned,
        first_seen_at=pinned,
        last_seen_at=pinned,
        date_confidence="high",
        staleness_state="active",
    )
    async with authenticated_async_context() as client:
        resp = await client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["posted_at"] == pinned
    assert body["first_seen_at"] == pinned
    assert body["last_seen_at"] == pinned
    assert body["date_confidence"] == "high"
    assert body["staleness_state"] == "active"


@pytest.mark.asyncio
async def test_jobs_response_includes_enrichment_when_available(
    authenticated_async_context,
):
    """B6: enrichment row → fields populated on JobResponse."""
    db = await api_deps.get_db()
    job_id = await _insert_job_row(db)
    await _insert_enrichment_row(db, job_id)
    async with authenticated_async_context() as client:
        resp = await client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["title_canonical"] == "Senior ML Engineer"
    assert body["seniority"] == "senior"
    assert body["employment_type"] == "full_time"
    assert body["workplace_type"] == "hybrid"
    assert body["visa_sponsorship"] is True
    assert body["salary_min_gbp"] == 70000
    assert body["salary_max_gbp"] == 90000
    assert body["salary_period"] == "annual"
    assert body["salary_currency_original"] == "GBP"
    assert body["required_skills"] == ["Python", "PyTorch"]
    assert body["nice_to_have_skills"] == ["TensorFlow"]
    assert body["industry"] == "machine_learning"
    assert body["years_experience_min"] == 5


@pytest.mark.asyncio
async def test_jobs_response_enrichment_fields_default_null_when_no_enrichment(
    authenticated_async_context,
):
    """B6: no enrichment row → enrichment fields are null/None."""
    db = await api_deps.get_db()
    job_id = await _insert_job_row(db)
    async with authenticated_async_context() as client:
        resp = await client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["title_canonical"] is None
    assert body["seniority"] is None
    assert body["employment_type"] is None
    assert body["workplace_type"] is None
    assert body["visa_sponsorship"] is None
    assert body["salary_min_gbp"] is None
    assert body["salary_max_gbp"] is None
    assert body["salary_period"] is None
    assert body["salary_currency_original"] is None
    assert body["required_skills"] is None
    assert body["nice_to_have_skills"] is None
    assert body["industry"] is None
    assert body["years_experience_min"] is None


@pytest.mark.asyncio
async def test_jobs_response_no_n_plus_one_for_enrichment(
    authenticated_async_context,
    monkeypatch,
):
    """B6: listing 5 enriched jobs uses ONE joined SELECT for enrichment,
    not one per row. We instrument aiosqlite.Connection.execute and assert
    the count of `SELECT ... FROM job_enrichment` queries is at most 1
    across the whole /api/jobs request."""
    db = await api_deps.get_db()
    ids = []
    for i in range(5):
        jid = await _insert_job_row(
            db,
            apply_url=f"https://example.com/jobs/{i}",
            normalized_company=f"acme {i}",
            normalized_title=f"ml engineer {i}",
        )
        await _insert_enrichment_row(db, jid)
        ids.append(jid)

    enrichment_select_count = {"n": 0}
    original_execute = db._conn.execute

    async def _spy(sql, *args, **kwargs):
        # Count any SELECT that references job_enrichment as a real read
        # (excludes ddl / index / pragma).
        s = sql.lstrip().lower()
        if s.startswith("select") and "job_enrichment" in s.lower():
            enrichment_select_count["n"] += 1
        return await original_execute(sql, *args, **kwargs)

    monkeypatch.setattr(db._conn, "execute", _spy)

    async with authenticated_async_context() as client:
        resp = await client.get("/api/jobs?limit=10")
    assert resp.status_code == 200
    assert resp.json()["total"] == 5
    # JOIN-once: zero or one SELECT touching job_enrichment, NOT five.
    assert enrichment_select_count["n"] <= 1, (
        f"expected ≤1 SELECT touching job_enrichment, got " f"{enrichment_select_count['n']} — N+1 regression"
    )


@pytest.mark.asyncio
async def test_jobs_action_filter_runs_before_pagination(authenticated_async_context):
    """C-2 regression guard: when ?action=liked is set, both `total` and the
    returned page must reflect the action-filtered set, not the unfiltered
    superset paginated then filtered. Pre-fix this returned `total=5` and
    `len(jobs)<limit` because the filter ran inside the page loop."""
    db = await api_deps.get_db()

    # 5 jobs total; mark only 2 as 'liked'
    ids = []
    for i in range(5):
        jid = await _insert_job_row(
            db,
            apply_url=f"https://example.com/jobs/c2-{i}",
            normalized_company=f"acme c2 {i}",
            normalized_title=f"role c2 {i}",
        )
        ids.append(jid)

    async with authenticated_async_context() as client:
        # Use the public action route so we don't have to look up user.id
        for jid in ids[:2]:
            r = await client.post(f"/api/jobs/{jid}/action", json={"action": "liked"})
            assert r.status_code == 200, r.text

        # Filter by action — both `total` and returned `jobs` must equal 2
        resp = await client.get("/api/jobs?action=liked&limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2, f"total must reflect filtered count, got {body['total']}"
    assert len(body["jobs"]) == 2, f"page must equal filtered count, got {len(body['jobs'])}"
    for job in body["jobs"]:
        assert job["action"] == "liked"


# ---------------------------------------------------------------------------
# LLM matcher verdict — Task 5 (funnel→judge plan)
# Rule #21 value-presence: assert real values round-trip, not just key presence.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jobs_response_includes_llm_verdict_values(authenticated_async_context):
    """Rule #21 value-presence: a seeded user_feed verdict comes back on
    GET /jobs with its REAL values, and unjudged jobs return nulls.

    Two jobs are inserted; job B gets an LLM verdict written directly into
    user_feed.  The response must carry the exact values for job B and
    null fields for the unjudged job A.  Because llm_fit_score=93 > job A's
    keyword score (80), job B must sort first (COALESCE ranking).
    """
    from src.core.tenancy import DEFAULT_TENANT_ID

    db = await api_deps.get_db()

    # Job A — unjudged, keyword score 80 (id not needed; just needs to be in the feed)
    await _insert_job_row(
        db,
        match_score=80,
        apply_url="https://example.com/jobs/llm-a",
        normalized_company="acme llm a",
        normalized_title="ml engineer llm a",
    )

    # Job B — lower keyword score (60) but will be LLM-judged at 93
    job_b_id = await _insert_job_row(
        db,
        match_score=60,
        apply_url="https://example.com/jobs/llm-b",
        normalized_company="acme llm b",
        normalized_title="ml engineer llm b",
    )

    # Write the LLM verdict directly into user_feed for job B
    u = await db._conn.execute(
        "SELECT id FROM users WHERE id != ? AND deleted_at IS NULL ORDER BY rowid DESC LIMIT 1",
        (DEFAULT_TENANT_ID,),
    )
    urow = await u.fetchone()
    uid = urow[0]

    await db._conn.execute(
        """UPDATE user_feed
           SET llm_fit_score = 93,
               llm_verdict    = 'strong fit',
               llm_reason     = 'domain + seniority',
               llm_matched_at = datetime('now')
           WHERE user_id = ? AND job_id = ?""",
        (uid, job_b_id),
    )
    await db._conn.commit()

    async with authenticated_async_context() as client:
        resp = await client.get("/api/jobs?limit=50")

    assert resp.status_code == 200
    body = resp.json()

    judged = [j for j in body["jobs"] if j["llm_fit_score"] is not None]
    unjudged = [j for j in body["jobs"] if j["llm_fit_score"] is None]

    # Value-presence: the exact seeded values must appear
    assert judged, "expected at least one judged job in response"
    assert judged[0]["llm_fit_score"] == 93
    assert judged[0]["llm_verdict"] == "strong fit"
    assert judged[0]["llm_reason"] == "domain + seniority"

    # Unjudged jobs must have null fields, not 0 or ""
    assert unjudged, "expected at least one unjudged job in response"
    assert unjudged[0]["llm_fit_score"] is None
    assert unjudged[0]["llm_verdict"] is None
    assert unjudged[0]["llm_reason"] is None

    # Ranking bonus: job B (llm_fit_score=93) must outrank job A (score=80)
    # because COALESCE(llm_fit_score, score) = 93 > 80.
    assert body["jobs"][0]["id"] == job_b_id, (
        f"expected judged job (id={job_b_id}) to rank first; "
        f"got id={body['jobs'][0]['id']}"
    )


# ---------------------------------------------------------------------------
# Rule #21 value-presence: deadline round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jobs_response_includes_deadline_values(authenticated_async_context):
    """Rule #21 value-presence: a job with a deadline phrase in its description
    should surface the correct ISO deadline on GET /api/jobs and GET /api/jobs/{id}.

    The deadline is inserted directly into the jobs table (simulating what the
    ingestion pipeline would write after extract_deadline runs).  The test asserts
    the EXACT value round-trips — not just that the field key exists.
    """
    db = await api_deps.get_db()

    # Insert a job whose description contains a clear closing-date phrase.
    # We also write the extracted value directly (as the pipeline would),
    # so this proves the serializer reads deadline / deadline_source back.
    job_id = await _insert_job_row(
        db,
        match_score=75,
        description="Closing date: 30 September 2026. Great role for ML engineers.",
        apply_url="https://example.com/jobs/deadline-test",
        normalized_company="acme deadline co",
        normalized_title="ml engineer deadline",
        deadline="2026-09-30",
        deadline_source="description",
    )

    async with authenticated_async_context() as client:
        # List route
        list_resp = await client.get("/api/jobs?limit=50")
        assert list_resp.status_code == 200
        list_jobs = list_resp.json()["jobs"]
        target = next((j for j in list_jobs if j["id"] == job_id), None)
        assert target is not None, "inserted job not found in /api/jobs list"
        assert target["deadline"] == "2026-09-30", (
            f"expected deadline '2026-09-30', got {target['deadline']!r}"
        )
        assert target["deadline_source"] == "description", (
            f"expected deadline_source 'description', got {target['deadline_source']!r}"
        )

        # Detail route
        detail_resp = await client.get(f"/api/jobs/{job_id}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["deadline"] == "2026-09-30", (
            f"detail deadline mismatch: got {detail['deadline']!r}"
        )
        assert detail["deadline_source"] == "description"


@pytest.mark.asyncio
async def test_jobs_response_deadline_null_when_not_set(authenticated_async_context):
    """Jobs without a deadline should return null for both fields (not empty string)."""
    db = await api_deps.get_db()
    job_id = await _insert_job_row(
        db,
        match_score=65,
        description="No deadline mentioned here.",
        apply_url="https://example.com/jobs/no-deadline",
        normalized_company="acme nodeadline",
        normalized_title="data engineer nodeadline",
    )
    async with authenticated_async_context() as client:
        resp = await client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deadline"] is None
    assert body["deadline_source"] is None
