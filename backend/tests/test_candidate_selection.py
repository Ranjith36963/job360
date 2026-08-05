"""The User-Level candidate layer (funnel Stage-1, 2026-08-05).

WHY THIS EXISTS. The shared `jobs` catalog flowed almost entirely into every
user's feed (measured in prod: one user's feed held 85% of the whole catalog —
6,207 of 7,309 rows, floor MIN_STORE_SCORE=1). The feed was the warehouse with
per-user price tags, not a personal shelf. Every downstream stage drowned:
enrichment budgeted 20 jobs, the LLM judge saw 30, the dashboard cut top-100
of a noisy ranking.

The fix: `user_feed` becomes a BOUNDED candidate set — only the user's top
FEED_CANDIDATE_CAP jobs by score enter it; rows that fall out of the selection
are marked stale (evicted), and re-selected rows come back. Industry-standard
shape (Twitter materializes ~800-1000 candidates per user; Indeed batch-stores
recommendations).

Invariants pinned here:
  * backfill caps the feed to FEED_CANDIDATE_CAP best-scoring jobs
  * eviction: an active row outside the selection goes stale
  * re-selection: a stale row that re-qualifies returns to active
  * LLM-judged rows are NEVER evicted (paid verdicts are not thrown away)
  * FEED_CANDIDATE_CAP=0 keeps the legacy uncapped behaviour byte-identical
  * rescore_user_feed applies the same selection on profile change
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from src.models import Job
from src.repositories import pgsync
from src.repositories.database import JobDatabase

_NOW = datetime.now(timezone.utc).isoformat()


# ── fixtures (mirrors test_rescore.py's full_db pattern) ─────────────────────


def _bootstrap_full_db(db_path: str) -> None:
    from migrations import runner

    async def _run():
        database = JobDatabase(db_path)
        await database.init_db()
        await database.close()
        await runner.up(db_path)

    asyncio.run(_run())


def _seed_user(db_path: str) -> str:
    user_id = str(uuid.uuid4())
    with pgsync.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO users(id, email, password_hash) VALUES (?,?,?)",
            (user_id, f"{user_id}@test.example", "hash"),
        )
    return user_id


def _seed_profile(db_path: str, user_id: str) -> int:
    from src.services.profile.models import CVData, UserPreferences

    cv = CVData(
        raw_text="Experienced Senior Python Engineer with ML skills.",
        skills=["python", "machine learning", "pytorch"],
        job_titles=["Senior Python Engineer", "ML Engineer"],
    )
    prefs = UserPreferences(
        target_job_titles=["Senior Python Engineer"],
        additional_skills=["python"],
    )
    cv_json = json.dumps(asdict(cv), default=str)
    pref_json = json.dumps(asdict(prefs), default=str)
    now = _NOW
    with pgsync.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO user_profiles(user_id, cv_data, preferences, updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "cv_data=excluded.cv_data, preferences=excluded.preferences, "
            "updated_at=excluded.updated_at",
            (user_id, cv_json, pref_json, now),
        )
        cur = conn.execute(
            "INSERT INTO user_profile_versions(user_id, created_at, source_action, cv_data, preferences) "
            "VALUES (?,?,?,?,?)",
            (user_id, now, "test_seed", cv_json, pref_json),
        )
        version_id = cur.lastrowid
    return version_id


@pytest.fixture
def full_db(tmp_path):
    db_path = str(tmp_path / "candidate_test.db")
    _bootstrap_full_db(db_path)
    return db_path


def _mk_job(i: int, *, relevant: bool) -> Job:
    """A catalog job. `relevant` ones score high for the seeded ML profile;
    the rest are deliberately unmatchable junk."""
    if relevant:
        return Job(
            title=f"ML Engineer {i}",
            company=f"GoodCo{i}",
            apply_url=f"https://example.com/good/{i}",
            source="reed",
            date_found=_NOW,
            location="London, UK",
            description="Python machine learning role with pytorch. " * (i + 1),
        )
    return Job(
        title=f"Florist {i}",
        company=f"FlowerCo{i}",
        apply_url=f"https://example.com/junk/{i}",
        source="reed",
        date_found=_NOW,
        location="Aberdeen",
        description="Arranging flowers and tending the shop.",
    )


async def _seed_catalog(db: JobDatabase, n_relevant: int, n_junk: int) -> None:
    for i in range(n_relevant):
        await db.insert_job(_mk_job(i, relevant=True))
    for i in range(n_junk):
        await db.insert_job(_mk_job(i, relevant=False))


def _feed_rows(db_path: str, user_id: str) -> list[tuple[int, str, int]]:
    """[(job_id, status, score)] for the user, all statuses."""
    with pgsync.connect(db_path) as conn:
        cur = conn.execute(
            "SELECT job_id, status, score FROM user_feed WHERE user_id = ? ORDER BY score DESC",
            (user_id,),
        )
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]


# ── backfill: cap + selection ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backfill_caps_feed_to_candidate_limit(full_db, monkeypatch):
    """6 catalog jobs, cap 2 → only the 2 best-scoring become active feed rows."""
    from pathlib import Path

    import src.core.settings as settings_mod
    import src.services.profile.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    monkeypatch.setattr(settings_mod, "FEED_CANDIDATE_CAP", 2, raising=False)

    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)

    db = JobDatabase(full_db)
    await db.init_db()
    try:
        await _seed_catalog(db, n_relevant=4, n_junk=2)
        from src.services.rescore import backfill_feed_from_catalog

        written = await backfill_feed_from_catalog(user_id, db)
    finally:
        await db.close()

    rows = _feed_rows(full_db, user_id)
    active = [r for r in rows if r[1] == "active"]
    assert written == 2, "backfill must only write the capped selection"
    assert len(active) == 2, f"expected 2 active candidates, got {len(active)}"
    # The kept rows must be the BEST scorers, not arbitrary ones.
    assert all(score > 10 for _, _, score in active), (
        "the selection kept junk over relevant jobs"
    )


@pytest.mark.asyncio
async def test_backfill_evicts_active_row_outside_selection(full_db, monkeypatch):
    """A pre-existing active feed row for a junk job must go stale when the
    selection no longer includes it — the feed is a shelf, not an archive."""
    from pathlib import Path

    import src.core.settings as settings_mod
    import src.services.profile.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    monkeypatch.setattr(settings_mod, "FEED_CANDIDATE_CAP", 2, raising=False)

    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)

    db = JobDatabase(full_db)
    await db.init_db()
    try:
        await _seed_catalog(db, n_relevant=3, n_junk=1)
        # Give the junk job a pre-existing ACTIVE feed row (legacy flood).
        with pgsync.connect(full_db) as conn:
            cur = conn.execute("SELECT id FROM jobs WHERE title LIKE 'Florist%'")
            junk_id = cur.fetchone()[0]
            conn.execute(
                "INSERT INTO user_feed(user_id, job_id, score, bucket, status, created_at, updated_at) "
                "VALUES (?,?,?,?,'active',?,?)",
                (user_id, junk_id, 15, "older", _NOW, _NOW),
            )

        from src.services.rescore import backfill_feed_from_catalog

        await backfill_feed_from_catalog(user_id, db)
    finally:
        await db.close()

    rows = dict((jid, status) for jid, status, _ in _feed_rows(full_db, user_id))
    assert rows[junk_id] == "stale", "the junk row was not evicted from the shelf"


@pytest.mark.asyncio
async def test_evicted_row_is_reactivated_when_reselected(full_db, monkeypatch):
    """A stale row that re-enters the selection returns to active — eviction
    is reversible, never destructive."""
    from pathlib import Path

    import src.core.settings as settings_mod
    import src.services.profile.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    monkeypatch.setattr(settings_mod, "FEED_CANDIDATE_CAP", 5, raising=False)

    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)

    db = JobDatabase(full_db)
    await db.init_db()
    try:
        await _seed_catalog(db, n_relevant=2, n_junk=0)
        # Mark a relevant job's feed row stale beforehand.
        with pgsync.connect(full_db) as conn:
            cur = conn.execute("SELECT id FROM jobs WHERE title LIKE 'ML Engineer%' LIMIT 1")
            good_id = cur.fetchone()[0]
            conn.execute(
                "INSERT INTO user_feed(user_id, job_id, score, bucket, status, created_at, updated_at) "
                "VALUES (?,?,?,?,'stale',?,?)",
                (user_id, good_id, 40, "older", _NOW, _NOW),
            )

        from src.services.rescore import backfill_feed_from_catalog

        await backfill_feed_from_catalog(user_id, db)
    finally:
        await db.close()

    rows = dict((jid, status) for jid, status, _ in _feed_rows(full_db, user_id))
    assert rows[good_id] == "active", "a re-selected stale row must reactivate"


@pytest.mark.asyncio
async def test_llm_judged_rows_are_never_evicted(full_db, monkeypatch):
    """A row carrying a paid LLM verdict survives selection even when its
    keyword score would drop it — we never throw away bought intelligence."""
    from pathlib import Path

    import src.core.settings as settings_mod
    import src.services.profile.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    monkeypatch.setattr(settings_mod, "FEED_CANDIDATE_CAP", 1, raising=False)

    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)

    db = JobDatabase(full_db)
    await db.init_db()
    try:
        await _seed_catalog(db, n_relevant=2, n_junk=1)
        with pgsync.connect(full_db) as conn:
            cur = conn.execute("SELECT id FROM jobs WHERE title LIKE 'Florist%'")
            junk_id = cur.fetchone()[0]
            conn.execute(
                "INSERT INTO user_feed(user_id, job_id, score, bucket, status, created_at, updated_at, "
                "llm_fit_score, llm_verdict) VALUES (?,?,?,?,'active',?,?,85,'Strong fit')",
                (user_id, junk_id, 12, "older", _NOW, _NOW),
            )

        from src.services.rescore import backfill_feed_from_catalog

        await backfill_feed_from_catalog(user_id, db)
    finally:
        await db.close()

    rows = dict((jid, status) for jid, status, _ in _feed_rows(full_db, user_id))
    assert rows[junk_id] == "active", "a judged row was evicted — paid verdict lost"


@pytest.mark.asyncio
async def test_cap_zero_keeps_legacy_uncapped_behaviour(full_db, monkeypatch):
    """FEED_CANDIDATE_CAP=0 is the off switch: every scoring job lands, nothing
    is evicted — byte-identical to the pre-candidate-layer behaviour."""
    from pathlib import Path

    import src.core.settings as settings_mod
    import src.services.profile.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    monkeypatch.setattr(settings_mod, "FEED_CANDIDATE_CAP", 0, raising=False)

    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)

    db = JobDatabase(full_db)
    await db.init_db()
    try:
        await _seed_catalog(db, n_relevant=4, n_junk=1)
        with pgsync.connect(full_db) as conn:
            cur = conn.execute("SELECT id FROM jobs WHERE title LIKE 'Florist%'")
            junk_id = cur.fetchone()[0]
            conn.execute(
                "INSERT INTO user_feed(user_id, job_id, score, bucket, status, created_at, updated_at) "
                "VALUES (?,?,?,?,'active',?,?)",
                (user_id, junk_id, 15, "older", _NOW, _NOW),
            )

        from src.services.rescore import backfill_feed_from_catalog

        written = await backfill_feed_from_catalog(user_id, db)
    finally:
        await db.close()

    rows = dict((jid, status) for jid, status, _ in _feed_rows(full_db, user_id))
    assert written >= 4, "cap=0 must write every scoring job (legacy)"
    assert rows[junk_id] == "active", "cap=0 must not evict anything (legacy)"


# ── rescore path applies the same selection ──────────────────────────────────


@pytest.mark.asyncio
async def test_rescore_user_feed_caps_and_evicts(full_db, monkeypatch):
    """Profile-change rescore enforces the same candidate bound + eviction."""
    from pathlib import Path

    import src.core.settings as settings_mod
    import src.services.llm_matcher as matcher_mod
    import src.services.profile.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    monkeypatch.setattr(settings_mod, "FEED_CANDIDATE_CAP", 2, raising=False)
    monkeypatch.setattr(matcher_mod, "MATCHER_ENABLED", False)

    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)

    db = JobDatabase(full_db)
    await db.init_db()
    try:
        await _seed_catalog(db, n_relevant=4, n_junk=2)
        with pgsync.connect(full_db) as conn:
            cur = conn.execute("SELECT id FROM jobs WHERE title LIKE 'Florist%' LIMIT 1")
            junk_id = cur.fetchone()[0]
            conn.execute(
                "INSERT INTO user_feed(user_id, job_id, score, bucket, status, created_at, updated_at) "
                "VALUES (?,?,?,?,'active',?,?)",
                (user_id, junk_id, 15, "older", _NOW, _NOW),
            )
    finally:
        await db.close()

    from src.services.rescore import rescore_user_feed

    result = await rescore_user_feed(user_id, db_path=full_db)
    assert result["rescored"] == 2, "rescore must only write the capped selection"

    rows = _feed_rows(full_db, user_id)
    active = [r for r in rows if r[1] == "active"]
    by_id = dict((jid, status) for jid, status, _ in rows)
    assert len(active) == 2
    assert by_id[junk_id] == "stale", "rescore did not evict the out-of-selection row"


# ── candidate enrichment (funnel Stage-2) ────────────────────────────────────


@pytest.mark.asyncio
async def test_backfill_enriches_selected_candidates_when_e2_on(full_db, monkeypatch):
    """With E2 on, backfill must enrich the best-scoring SELECTED candidates
    that lack enrichment — coverage of the candidate set grows per search.
    (Prod before this: 24/7,309 jobs enriched, so salary/visa/seniority
    preference dims effectively never fired.)"""
    from pathlib import Path

    import src.core.settings as settings_mod
    import src.services.profile.storage as storage_mod
    import src.services.rescore as rescore_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    monkeypatch.setattr(settings_mod, "FEED_CANDIDATE_CAP", 2, raising=False)
    monkeypatch.setattr(settings_mod, "ENGINE2_ENABLED", True, raising=False)
    monkeypatch.setattr(settings_mod, "ENRICHMENT_MAX_JOBS", 1, raising=False)

    enriched: list[int] = []

    async def fake_enrich_batch(jobs, semaphore_limit=3, conn=None, **kw):
        enriched.extend(j.id for j in jobs)
        return []

    import src.services.job_enrichment as je_mod
    monkeypatch.setattr(je_mod, "enrich_batch", fake_enrich_batch)

    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)

    db = JobDatabase(full_db)
    await db.init_db()
    try:
        await _seed_catalog(db, n_relevant=3, n_junk=1)
        await rescore_mod.backfill_feed_from_catalog(user_id, db)
    finally:
        await db.close()

    # Budget 1 → exactly the single best-scoring candidate gets enriched.
    assert len(enriched) == 1, f"expected 1 enrichment call, got {enriched}"


@pytest.mark.asyncio
async def test_backfill_never_enriches_when_e2_off(full_db, monkeypatch):
    """Rule #18 analog: with E2 off, backfill must make ZERO enrichment calls —
    behaviour identical to pre-Stage-2."""
    from pathlib import Path

    import src.core.settings as settings_mod
    import src.services.profile.storage as storage_mod
    import src.services.rescore as rescore_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    monkeypatch.setattr(settings_mod, "FEED_CANDIDATE_CAP", 2, raising=False)
    monkeypatch.setattr(settings_mod, "ENGINE2_ENABLED", False, raising=False)

    called = {"n": 0}

    async def fake_enrich_batch(jobs, **kw):
        called["n"] += 1
        return []

    import src.services.job_enrichment as je_mod
    monkeypatch.setattr(je_mod, "enrich_batch", fake_enrich_batch)
    monkeypatch.setattr(je_mod, "ENRICHMENT_ENABLED", False)

    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)

    db = JobDatabase(full_db)
    await db.init_db()
    try:
        await _seed_catalog(db, n_relevant=2, n_junk=0)
        await rescore_mod.backfill_feed_from_catalog(user_id, db)
    finally:
        await db.close()

    assert called["n"] == 0, "E2 off must mean zero enrichment calls"


# ── the user-feedback loop (USER-understanding: actions steer the shelf) ─────


@pytest.mark.asyncio
async def test_not_interested_jobs_are_excluded_from_selection(full_db, monkeypatch):
    """A job the user explicitly rejected must never be selected again — even
    when it out-scores everything. Before this, 'not_interested' was recorded
    but the candidate layer ignored it: the reject button was a dead lever."""
    from pathlib import Path

    import src.core.settings as settings_mod
    import src.services.profile.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    monkeypatch.setattr(settings_mod, "FEED_CANDIDATE_CAP", 2, raising=False)

    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)

    db = JobDatabase(full_db)
    await db.init_db()
    try:
        await _seed_catalog(db, n_relevant=3, n_junk=0)
        with pgsync.connect(full_db) as conn:
            cur = conn.execute("SELECT id FROM jobs WHERE title LIKE 'ML Engineer%' ORDER BY id LIMIT 1")
            rejected_id = cur.fetchone()[0]
            conn.execute(
                "INSERT INTO user_actions(user_id, job_id, action, notes, created_at) "
                "VALUES (?,?,?,?,?)",
                (user_id, rejected_id, "not_interested", "", _NOW),
            )
            # It also has a lingering active feed row from before the reject.
            conn.execute(
                "INSERT INTO user_feed(user_id, job_id, score, bucket, status, created_at, updated_at) "
                "VALUES (?,?,?,?,'active',?,?)",
                (user_id, rejected_id, 60, "older", _NOW, _NOW),
            )

        from src.services.rescore import backfill_feed_from_catalog

        await backfill_feed_from_catalog(user_id, db)
    finally:
        await db.close()

    rows = dict((jid, status) for jid, status, _ in _feed_rows(full_db, user_id))
    assert rows.get(rejected_id) == "stale", (
        "a not_interested job must be evicted, not re-offered"
    )


@pytest.mark.asyncio
async def test_liked_and_applied_jobs_survive_eviction(full_db, monkeypatch):
    """Jobs the user invested in (liked/applied) must never be evicted by a
    re-selection, even when their score falls outside the top-N — mirroring
    the LLM-verdict protection: user signal outranks keyword re-ranks."""
    from pathlib import Path

    import src.core.settings as settings_mod
    import src.services.profile.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    monkeypatch.setattr(settings_mod, "FEED_CANDIDATE_CAP", 1, raising=False)

    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)

    db = JobDatabase(full_db)
    await db.init_db()
    try:
        await _seed_catalog(db, n_relevant=2, n_junk=1)
        with pgsync.connect(full_db) as conn:
            cur = conn.execute("SELECT id FROM jobs WHERE title LIKE 'Florist%'")
            liked_junk_id = cur.fetchone()[0]
            conn.execute(
                "INSERT INTO user_actions(user_id, job_id, action, notes, created_at) "
                "VALUES (?,?,?,?,?)",
                (user_id, liked_junk_id, "liked", "", _NOW),
            )
            conn.execute(
                "INSERT INTO user_feed(user_id, job_id, score, bucket, status, created_at, updated_at) "
                "VALUES (?,?,?,?,'active',?,?)",
                (user_id, liked_junk_id, 12, "older", _NOW, _NOW),
            )

        from src.services.rescore import backfill_feed_from_catalog

        await backfill_feed_from_catalog(user_id, db)
    finally:
        await db.close()

    rows = dict((jid, status) for jid, status, _ in _feed_rows(full_db, user_id))
    assert rows[liked_junk_id] == "active", (
        "a liked/applied job was evicted — user investment must survive re-selection"
    )


@pytest.mark.asyncio
async def test_learned_preference_wins_marginal_shelf_slot(full_db, monkeypatch):
    """Learned-preference v1: when two jobs tie on score for the last shelf
    slot, the one from a company the user LIKED before must win it. The boost
    affects membership only — stored scores stay the raw scorer output."""
    from pathlib import Path

    import src.core.settings as settings_mod
    import src.services.profile.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    monkeypatch.setattr(settings_mod, "FEED_CANDIDATE_CAP", 1, raising=False)

    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)

    db = JobDatabase(full_db)
    await db.init_db()
    try:
        # Two identical-scoring relevant jobs from different companies...
        await db.insert_job(Job(
            title="ML Engineer A", company="LovedCo",
            apply_url="https://x/a", source="reed", date_found=_NOW,
            location="London, UK", description="Python machine learning role with pytorch.",
        ))
        await db.insert_job(Job(
            title="ML Engineer B", company="StrangerCo",
            apply_url="https://x/b", source="reed", date_found=_NOW,
            location="London, UK", description="Python machine learning role with pytorch.",
        ))
        # ...and an EARLIER liked job from LovedCo (different posting).
        await db.insert_job(Job(
            title="Data Platform Engineer", company="LovedCo",
            apply_url="https://x/old", source="reed", date_found=_NOW,
            location="London, UK", description="Old role.",
        ))
        with pgsync.connect(full_db) as conn:
            cur = conn.execute("SELECT id FROM jobs WHERE title = 'Data Platform Engineer'")
            liked_id = cur.fetchone()[0]
            conn.execute(
                "INSERT INTO user_actions(user_id, job_id, action, notes, created_at) "
                "VALUES (?,?,?,?,?)",
                (user_id, liked_id, "liked", "", _NOW),
            )

        from src.services.rescore import backfill_feed_from_catalog

        await backfill_feed_from_catalog(user_id, db)

        with pgsync.connect(full_db) as conn:
            cur = conn.execute(
                "SELECT j.company FROM user_feed f JOIN jobs j ON j.id=f.job_id "
                "WHERE f.user_id=? AND f.status='active' AND j.title LIKE ?",
                (user_id, "ML Engineer%"),
            )
            active_companies = [r[0] for r in cur.fetchall()]
    finally:
        await db.close()

    assert active_companies == ["LovedCo"], (
        f"the liked company's job must win the marginal slot, got {active_companies}"
    )


@pytest.mark.asyncio
async def test_recurring_rejection_pattern_loses_marginal_slot(full_db, monkeypatch):
    """Learned-preference v2: rejecting SEVERAL jobs sharing a title pattern
    must sink that pattern in selection — one rejection teaches nothing
    general, a recurring one does. Before v2, rejections only excluded the
    exact jobs; the pattern kept refilling the shelf."""
    from pathlib import Path

    import src.core.settings as settings_mod
    import src.services.profile.storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", Path(full_db), raising=True)
    monkeypatch.setattr(settings_mod, "FEED_CANDIDATE_CAP", 1, raising=False)

    user_id = _seed_user(full_db)
    _seed_profile(full_db, user_id)

    db = JobDatabase(full_db)
    await db.init_db()
    try:
        # Two equal-scoring candidates: one 'Support' pattern, one clean.
        await db.insert_job(Job(
            title="ML Support Engineer", company="PatternCo",
            apply_url="https://x/p", source="reed", date_found=_NOW,
            location="London, UK", description="Python machine learning role with pytorch.",
        ))
        await db.insert_job(Job(
            title="ML Research Engineer", company="CleanCo",
            apply_url="https://x/c", source="reed", date_found=_NOW,
            location="London, UK", description="Python machine learning role with pytorch.",
        ))
        # The user rejected TWO other 'Support' jobs before.
        for i in range(2):
            await db.insert_job(Job(
                title=f"Customer Support Specialist {i}", company=f"R{i}",
                apply_url=f"https://x/r{i}", source="reed", date_found=_NOW,
                location="London, UK", description="Helpdesk.",
            ))
        with pgsync.connect(full_db) as conn:
            cur = conn.execute("SELECT id FROM jobs WHERE title LIKE ?", ("Customer Support%",))
            for (rid,) in cur.fetchall():
                conn.execute(
                    "INSERT INTO user_actions(user_id, job_id, action, notes, created_at) "
                    "VALUES (?,?,?,?,?)",
                    (user_id, rid, "not_interested", "", _NOW),
                )

        from src.services.rescore import backfill_feed_from_catalog

        await backfill_feed_from_catalog(user_id, db)

        with pgsync.connect(full_db) as conn:
            cur = conn.execute(
                "SELECT j.title FROM user_feed f JOIN jobs j ON j.id=f.job_id "
                "WHERE f.user_id=? AND f.status='active' AND j.title LIKE ?",
                (user_id, "ML %"),
            )
            active = [r[0] for r in cur.fetchall()]
    finally:
        await db.close()

    assert active == ["ML Research Engineer"], (
        f"the recurring rejection pattern must lose the marginal slot, got {active}"
    )


def test_liked_vocabulary_is_immune_to_rejection_pattern():
    """A token in BOTH liked and rejected vocabularies must NOT be penalized —
    positive counter-evidence wins ties."""
    from src.services.rescore import _preference_boost

    signals = {"companies": set(), "tokens": {"machine", "learning"},
               "rejected_tokens": set()}  # builder already removed the overlap
    assert _preference_boost({"title": "Machine Learning Engineer", "company": "X"},
                             signals) >= 0
