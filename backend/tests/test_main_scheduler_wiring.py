"""Tests for TieredScheduler wire-up in run_search (Batch 3.5 Deliverable E).

Replaces the Batch 3 `asyncio.gather` block at main.py:356 with a
scheduler dispatch call. Tests verify:
  1. run_search invokes TieredScheduler.tick exactly once with force=True.
  2. Every registered source's fetch_jobs is called exactly once (one-shot CLI).
  3. A circuit-breaker-OPEN source is skipped — its fetch_jobs never fires.

Tests inject fake sources via `_build_sources` monkeypatch to avoid live
HTTP. They do not touch test_main.py's --ignore'd integration path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pytest

from src.models import Job
from src.services.circuit_breaker import BreakerState
from src.services.circuit_breaker import default_registry as _default_registry


class _FakeSource:
    """BaseJobSource-compatible fake for scheduler tests."""

    def __init__(self, name: str, category: str = "ats",
                 result: Optional[list] = None, raises: Optional[Exception] = None):
        self.name = name
        self.category = category
        self._result = result if result is not None else []
        self._raises = raises
        self.fetch_call_count = 0

    async def fetch_jobs(self):
        self.fetch_call_count += 1
        if self._raises:
            raise self._raises
        return list(self._result)


def _make_job(title: str = "Test", company: str = "Co") -> Job:
    return Job(
        title=title,
        company=company,
        apply_url="https://x.test/1",
        source="fake",
        date_found=datetime.now(timezone.utc).isoformat(),
    )


@pytest.fixture
def tmp_db_path(tmp_path, monkeypatch):
    """Redirect DB_PATH to a tmp SQLite and run JobDatabase init.

    Defensive: other test files (test_api_idor.py, test_channels_routes.py)
    populate the dependencies._db singleton and the search.py _runs dict;
    we reset both so run_search inside this fixture starts from a known
    clean state.
    """
    from src.api import dependencies
    from src.api.routes import search as search_route
    from src.core import settings as core_settings

    path = tmp_path / "test.db"
    monkeypatch.setattr(core_settings, "DB_PATH", path, raising=True)
    monkeypatch.setattr(dependencies, "DB_PATH", path, raising=True)
    monkeypatch.setattr(dependencies, "_db", None, raising=False)
    # Reset the module-level _runs dict in search.py (not a leak vector
    # here, but keeps the fixture self-documenting for future readers).
    monkeypatch.setattr(search_route, "_runs", {}, raising=True)
    return str(path)


@pytest.fixture(autouse=True)
def _reset_default_breaker_registry():
    """Isolate tests — default registry is module-global."""
    reg = _default_registry()
    reg._breakers.clear()  # type: ignore[attr-defined]
    yield
    reg._breakers.clear()  # type: ignore[attr-defined]


@pytest.fixture
def fake_profile(monkeypatch):
    """Stub load_profile() so run_search doesn't bail out on 'no profile'.

    Must patch `src.main.load_profile` (the bound reference) — patching
    the storage module alone does NOT work because `main.py` did
    `from src.services.profile.storage import load_profile`.
    """
    from src import main as main_mod
    from src.services.profile.models import (
        CVData,
        SearchConfig,
        UserPreferences,
        UserProfile,
    )

    stub = UserProfile(
        cv_data=CVData(
            raw_text="x", skills=["python"], job_titles=["Engineer"],
        ),
        preferences=UserPreferences(
            target_job_titles=["Engineer"], additional_skills=["python"],
        ),
    )
    # Batch 3.5.2 changed load_profile's signature to take a user_id.
    monkeypatch.setattr(main_mod, "load_profile", lambda uid: stub)
    # `generate_search_config` is also bound in main; return a minimal
    # SearchConfig that won't crash JobScorer init.
    config = SearchConfig(
        job_titles=["Engineer"],
        relevance_keywords=["engineer", "python"],
    )
    monkeypatch.setattr(main_mod, "generate_search_config", lambda p: config)
    return stub


@pytest.mark.asyncio
async def test_run_search_loads_profile_for_given_user(tmp_db_path, monkeypatch):
    """run_search(user_id=X) must load X's profile, not the default tenant's.

    Regression for the per-user search bug (E2E_TEST_REPORT #1): the web
    "New Search" ran profile-less because run_search ignored the logged-in
    user and always loaded DEFAULT_TENANT_ID's (usually empty) profile, so
    a logged-in user could never get personalised results.
    """
    from src import main as main_mod
    from src.services.profile.models import (
        CVData,
        SearchConfig,
        UserPreferences,
        UserProfile,
    )

    captured: dict[str, str] = {}
    stub = UserProfile(
        cv_data=CVData(raw_text="x", skills=["python"], job_titles=["Engineer"]),
        preferences=UserPreferences(
            target_job_titles=["Engineer"], additional_skills=["python"],
        ),
    )

    def fake_load_profile(uid):
        captured["uid"] = uid
        return stub

    monkeypatch.setattr(main_mod, "load_profile", fake_load_profile)
    monkeypatch.setattr(
        main_mod, "generate_search_config",
        lambda p: SearchConfig(job_titles=["Engineer"], relevance_keywords=["engineer"]),
    )
    monkeypatch.setattr(main_mod, "_build_sources", lambda *a, **kw: [])

    await main_mod.run_search(db_path=tmp_db_path, no_notify=True, user_id="user-123")

    assert captured["uid"] == "user-123", (
        f"run_search should load the given user's profile; loaded {captured.get('uid')!r}"
    )


@pytest.mark.asyncio
async def test_run_search_defaults_to_tenant_when_no_user(tmp_db_path, monkeypatch):
    """run_search() with no user_id keeps loading DEFAULT_TENANT_ID (CLI path)."""
    from src import main as main_mod
    from src.services.profile.models import (
        CVData,
        SearchConfig,
        UserPreferences,
        UserProfile,
    )

    captured: dict[str, str] = {}
    stub = UserProfile(
        cv_data=CVData(raw_text="x", skills=["python"], job_titles=["Engineer"]),
        preferences=UserPreferences(
            target_job_titles=["Engineer"], additional_skills=["python"],
        ),
    )
    monkeypatch.setattr(
        main_mod, "load_profile",
        lambda uid: captured.update(uid=uid) or stub,
    )
    monkeypatch.setattr(
        main_mod, "generate_search_config",
        lambda p: SearchConfig(job_titles=["Engineer"], relevance_keywords=["engineer"]),
    )
    monkeypatch.setattr(main_mod, "_build_sources", lambda *a, **kw: [])

    await main_mod.run_search(db_path=tmp_db_path, no_notify=True)

    assert captured["uid"] == main_mod.DEFAULT_TENANT_ID


@pytest.mark.asyncio
async def test_dashboard_feed_isolated_between_two_users(tmp_db_path, monkeypatch):
    """John and Paul each run a real search; each user's dashboard feed contains
    ONLY their own jobs. Proves the per-user write path (run_search -> user_feed)
    AND the per-user read path (get_user_feed_jobs) — the core multi-tenant ask.
    """
    from migrations import runner
    from src import main as main_mod
    from src.repositories.database import JobDatabase
    from src.services.profile.models import (
        CVData,
        SearchConfig,
        UserPreferences,
        UserProfile,
    )

    # Mirror production init order (dependencies.init_db): base tables, then
    # migrations — the per-user `user_feed` table lives in a Batch-2 migration.
    _setup = JobDatabase(tmp_db_path)
    await _setup.init_db()
    await runner.up(tmp_db_path)
    await _setup.close()

    stub = UserProfile(
        cv_data=CVData(raw_text="x", skills=["python"], job_titles=["Engineer"]),
        preferences=UserPreferences(
            target_job_titles=["Engineer"], additional_skills=["python"],
        ),
    )
    monkeypatch.setattr(main_mod, "load_profile", lambda uid: stub)
    monkeypatch.setattr(
        main_mod, "generate_search_config",
        lambda p: SearchConfig(job_titles=["Engineer"], relevance_keywords=["engineer"]),
    )

    # John's search finds a job at "JohnCo"; the "Engineer" title clears the gate.
    monkeypatch.setattr(
        main_mod, "_build_sources",
        lambda *a, **kw: [_FakeSource("s", "ats", result=[_make_job("Engineer", "JohnCo")])],
    )
    await main_mod.run_search(db_path=tmp_db_path, no_notify=True, user_id="john")

    # Paul's search finds a different job at "PaulCo".
    monkeypatch.setattr(
        main_mod, "_build_sources",
        lambda *a, **kw: [_FakeSource("s", "ats", result=[_make_job("Engineer", "PaulCo")])],
    )
    await main_mod.run_search(db_path=tmp_db_path, no_notify=True, user_id="paul")

    # Each user's feed must contain ONLY their own job.
    db = JobDatabase(tmp_db_path)
    await db.init_db()
    try:
        john = await db.get_user_feed_jobs("john", days=7, min_score=0)
        paul = await db.get_user_feed_jobs("paul", days=7, min_score=0)
    finally:
        await db.close()

    assert {j["company"] for j in john} == {"JohnCo"}, f"John saw: {[j['company'] for j in john]}"
    assert {j["company"] for j in paul} == {"PaulCo"}, f"Paul saw: {[j['company'] for j in paul]}"
    assert len(john) == 1 and len(paul) == 1


@pytest.mark.asyncio
async def test_enrichment_runs_after_insert_so_jobs_have_db_ids(tmp_db_path, monkeypatch):
    """B7-2 regression: the jobs handed to enrich_batch must carry their DB id.

    Enrichment used to run BEFORE the jobs were inserted, so every Job had
    ``id is None`` — and enrich_batch's persistence (``getattr(job, "id", None)``)
    silently dropped every result, leaving ``job_enrichment`` permanently empty.
    This pins that the pipeline now enriches AFTER insert with ids attached.
    """
    from migrations import runner
    from src import main as main_mod
    from src.repositories.database import JobDatabase
    from src.services.profile.models import CVData, SearchConfig, UserPreferences, UserProfile

    _setup = JobDatabase(tmp_db_path)
    await _setup.init_db()
    await runner.up(tmp_db_path)
    await _setup.close()

    stub = UserProfile(
        cv_data=CVData(raw_text="x", skills=["python"], job_titles=["Engineer"]),
        preferences=UserPreferences(target_job_titles=["Engineer"], additional_skills=["python"]),
    )
    monkeypatch.setattr(main_mod, "load_profile", lambda uid: stub)
    monkeypatch.setattr(
        main_mod, "generate_search_config",
        lambda p: SearchConfig(job_titles=["Engineer"], relevance_keywords=["engineer"]),
    )
    monkeypatch.setattr(
        main_mod, "_build_sources",
        lambda *a, **kw: [_FakeSource("s", "ats", result=[_make_job("Engineer", "AcmeCo")])],
    )

    # Turn enrichment ON and enrich everything that clears the score filter.
    monkeypatch.setattr(main_mod, "ENRICHMENT_ENABLED", True)
    monkeypatch.setattr(main_mod, "ENRICHMENT_THRESHOLD", 0)

    # Capture the jobs handed to enrich_batch (no live LLM — rule #4).
    captured: dict = {}

    async def fake_enrich_batch(jobs, **kwargs):
        captured["ids"] = [getattr(j, "id", None) for j in jobs]
        return [None] * len(jobs)

    monkeypatch.setattr(main_mod, "enrich_batch", fake_enrich_batch)

    await main_mod.run_search(db_path=tmp_db_path, no_notify=True, user_id="u1")

    assert captured.get("ids"), "enrich_batch should have been called with at least one job"
    assert all(i is not None for i in captured["ids"]), (
        f"every job sent to enrichment must carry its DB id; got {captured['ids']}"
    )


@pytest.mark.asyncio
async def test_job_detail_dims_are_per_user(tmp_db_path, monkeypatch):
    """Two users with DIFFERENT profiles view the SAME job. The detail-page
    8-D breakdown must reflect EACH user's own profile (not the shared
    last-writer-wins `jobs` row), and the overall `match_score` must equal
    that user's stored feed score.

    This is the per-user radar fix: the dashboard overall score is already
    per-user (via `user_feed`), but the detail route used to read the shared
    `jobs` row, so two users matching one job saw identical dims (whoever
    searched last). Proof routes through the real `run_search` store path so
    a realistic description flows fetch -> score -> store -> feed.
    """
    from migrations import runner
    from src import main as main_mod
    from src.api.auth_deps import CurrentUser
    from src.api.routes.jobs import get_job
    from src.repositories.database import JobDatabase
    from src.services.profile import storage as profile_storage
    from src.services.profile.models import CVData, UserPreferences, UserProfile

    # Mirror production init order: base tables, then migrations (user_feed +
    # user_profiles live in Batch-2/0006 migrations).
    _setup = JobDatabase(tmp_db_path)
    await _setup.init_db()
    await runner.up(tmp_db_path)
    await _setup.close()

    # Profile load/save bind module-level DB_PATH at import — redirect to tmp.
    monkeypatch.setattr(profile_storage, "DB_PATH", tmp_db_path, raising=False)

    # Two real, DIFFERENT profiles. Both target "Engineer" (clears the title
    # gate), but Alice lists more of the job's skills than Bob -> their skill
    # dimension for the SAME job must differ.
    alice = UserProfile(
        cv_data=CVData(raw_text="x", skills=["python", "pytorch", "machine learning"],
                       job_titles=["Machine Learning Engineer"]),
        preferences=UserPreferences(
            target_job_titles=["Machine Learning Engineer"],
            additional_skills=["python", "pytorch", "machine learning"],
        ),
    )
    bob = UserProfile(
        cv_data=CVData(raw_text="x", skills=["python"],
                       job_titles=["Machine Learning Engineer"]),
        preferences=UserPreferences(
            target_job_titles=["Machine Learning Engineer"],
            additional_skills=["python"],
        ),
    )
    profile_storage.save_profile(alice, "alice")
    profile_storage.save_profile(bob, "bob")

    # The single job both users will match (same company+title -> one jobs row
    # via INSERT OR IGNORE dedup, so both detail views hit the same job_id).
    shared_job = Job(
        title="Machine Learning Engineer",
        company="SharedCo",
        apply_url="https://x.test/ml",
        source="fake",
        date_found=datetime.now(timezone.utc).isoformat(),
        description="Python, PyTorch and machine learning. Build and ship ML models.",
        location="London, UK",
    )
    monkeypatch.setattr(
        main_mod, "_build_sources",
        lambda *a, **kw: [_FakeSource("s", "ats", result=[shared_job])],
    )

    # Each user runs a REAL search -> jobs row + per-user feed row + dims.
    # run_search loads each user's real profile from the tmp DB (no stub).
    await main_mod.run_search(db_path=tmp_db_path, no_notify=True, user_id="alice")
    await main_mod.run_search(db_path=tmp_db_path, no_notify=True, user_id="bob")

    db = JobDatabase(tmp_db_path)
    await db.init_db()
    try:
        a_rows = await db.get_user_feed_jobs("alice", days=7, min_score=0)
        b_rows = await db.get_user_feed_jobs("bob", days=7, min_score=0)
        assert a_rows, "Alice should have matched the shared job"
        assert b_rows, "Bob should have matched the shared job"
        job_id = a_rows[0]["id"]
        assert b_rows[0]["id"] == job_id, "both users must view the SAME jobs row"
        alice_feed_score = a_rows[0]["match_score"]  # get_user_feed_jobs surfaces feed.score here
        bob_feed_score = b_rows[0]["match_score"]

        alice_resp = await get_job(job_id, db=db, user=CurrentUser(id="alice", email="a@x.test"))
        bob_resp = await get_job(job_id, db=db, user=CurrentUser(id="bob", email="b@x.test"))
    finally:
        await db.close()

    # (a) The per-dimension breakdown is per-user: Alice (more matching skills)
    #     must score the skill dim higher than Bob on the identical job.
    assert alice_resp.skill != bob_resp.skill, (
        "detail dims must be per-user: "
        f"alice.skill={alice_resp.skill}, bob.skill={bob_resp.skill}"
    )
    # (b) The overall match_score on the detail page equals each user's stored
    #     feed score — the dashboard card the user just clicked.
    assert alice_resp.match_score == alice_feed_score, (
        f"alice detail={alice_resp.match_score} feed={alice_feed_score}"
    )
    assert bob_resp.match_score == bob_feed_score, (
        f"bob detail={bob_resp.match_score} feed={bob_feed_score}"
    )


@pytest.mark.asyncio
async def test_run_search_uses_tiered_scheduler(tmp_db_path, fake_profile, monkeypatch):
    """Assert run_search calls TieredScheduler.tick exactly once with force=True."""
    from src import main as main_mod
    from src.services import scheduler as scheduler_mod

    tick_calls: list[dict] = []
    original_tick = scheduler_mod.TieredScheduler.tick

    async def spy_tick(self, now=None, *, force: bool = False):
        tick_calls.append({"force": force, "sources": len(self._sources)})
        return await original_tick(self, now=now, force=force)

    monkeypatch.setattr(scheduler_mod.TieredScheduler, "tick", spy_tick)

    # Replace _build_sources with 2 fakes
    srcs = [
        _FakeSource("fake1", category="ats", result=[_make_job("A")]),
        _FakeSource("fake2", category="rss", result=[_make_job("B")]),
    ]
    monkeypatch.setattr(main_mod, "_build_sources",
                        lambda *a, **kw: srcs)

    await main_mod.run_search(db_path=tmp_db_path, no_notify=True)

    assert len(tick_calls) == 1, f"tick should be called exactly once, got {tick_calls}"
    assert tick_calls[0]["force"] is True
    assert tick_calls[0]["sources"] == 2


@pytest.mark.asyncio
async def test_each_registered_source_called_exactly_once(
    tmp_db_path, fake_profile, monkeypatch
):
    from src import main as main_mod

    srcs = [
        _FakeSource("a", category="ats"),
        _FakeSource("b", category="rss"),
        _FakeSource("c", category="scrapers"),
    ]
    monkeypatch.setattr(main_mod, "_build_sources",
                        lambda *a, **kw: srcs)

    await main_mod.run_search(db_path=tmp_db_path, no_notify=True)

    for s in srcs:
        assert s.fetch_call_count == 1, (
            f"{s.name} fetched {s.fetch_call_count} times, expected 1"
        )


@pytest.mark.asyncio
async def test_breaker_open_source_is_skipped(
    tmp_db_path, fake_profile, monkeypatch
):
    """Pre-trip a breaker for 'flaky' — its fetch_jobs must never fire."""
    from src import main as main_mod

    # Pre-trip breaker for 'flaky'
    reg = _default_registry()
    breaker = reg.get("flaky")
    for _ in range(5):
        breaker.record_failure()
    assert breaker.state == BreakerState.OPEN

    flaky = _FakeSource("flaky", category="ats",
                        raises=RuntimeError("should not be called"))
    healthy = _FakeSource("healthy", category="ats", result=[_make_job("H")])
    monkeypatch.setattr(main_mod, "_build_sources",
                        lambda *a, **kw: [flaky, healthy])

    stats = await main_mod.run_search(db_path=tmp_db_path, no_notify=True)

    assert flaky.fetch_call_count == 0, (
        f"flaky (breaker OPEN) should have been skipped; fetched {flaky.fetch_call_count} times"
    )
    assert healthy.fetch_call_count == 1
    # per_source surface: skipped source should appear as 0 so the summary
    # still covers every registered source, with the breaker state explaining why.
    assert stats["per_source"].get("flaky", 0) == 0
    assert stats["per_source"].get("healthy") == 1
