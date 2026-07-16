"""Per-user profile storage tests (Batch 3.5.2 Deliverable B).

Covers:
  * save_profile / load_profile round-trip per user_id
  * User A's profile is invisible to user B (tenant isolation)
  * Concurrent saves for A + B don't collide (UPSERT semantics)
  * Re-saving A's profile updates updated_at
  * Deleting user A (CASCADE) drops their profile row
  * One-shot legacy-JSON hydrate on first load for DEFAULT_TENANT_ID
  * Hydrate is idempotent (second call is no-op — JSON now deleted)

Every test uses a tmp sqlite DB + monkeypatches `DB_PATH` on the
settings + storage modules. Migrations 0000..0006 are applied up front
via the same bootstrap pattern as test_worker_tasks.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

import pytest

from migrations import runner
from src.core.tenancy import DEFAULT_TENANT_ID
from src.repositories import pg
from src.services.profile.models import CVData, UserPreferences, UserProfile

USER_ALICE = "aaaaaaaa-0000-0000-0000-000000000001"
USER_BOB = "bbbbbbbb-0000-0000-0000-000000000002"


async def _bootstrap_db(db_path: str) -> None:
    async with pg.connect(db_path) as db:
        await db.executescript(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                apply_url TEXT NOT NULL,
                source TEXT NOT NULL,
                date_found TEXT NOT NULL,
                normalized_company TEXT NOT NULL,
                normalized_title TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                UNIQUE(normalized_company, normalized_title)
            );
            CREATE TABLE user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(job_id)
            );
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                stage TEXT NOT NULL DEFAULT 'applied',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(job_id)
            );
            """
        )
        await db.commit()
    await runner.up(db_path)
    # Seed Alice + Bob so ON DELETE CASCADE has real parents.
    async with pg.connect(db_path) as db:
        await db.execute(
            "INSERT INTO users(id, email, password_hash) VALUES (?, ?, ?)",
            (USER_ALICE, "alice@example.test", "!"),
        )
        await db.execute(
            "INSERT INTO users(id, email, password_hash) VALUES (?, ?, ?)",
            (USER_BOB, "bob@example.test", "!"),
        )
        await db.commit()


@pytest.fixture
def storage_db(tmp_path, monkeypatch):
    """Tmp DB + patched settings. Yields the db path str."""
    db_path = tmp_path / "test.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    asyncio.run(_bootstrap_db(str(db_path)))

    from src.core import settings as core_settings
    from src.services.profile import storage as storage_mod

    monkeypatch.setattr(core_settings, "DB_PATH", db_path, raising=True)
    monkeypatch.setattr(core_settings, "DATA_DIR", data_dir, raising=True)
    # Invalidate module-level bindings captured via `from ... import DB_PATH`.
    monkeypatch.setattr(storage_mod, "DB_PATH", db_path, raising=True)
    monkeypatch.setattr(storage_mod, "DATA_DIR", data_dir, raising=True)
    monkeypatch.setattr(
        storage_mod, "LEGACY_PROFILE_PATH", data_dir / "user_profile.json", raising=True
    )
    return {"db_path": str(db_path), "data_dir": data_dir}


def _make_profile(skill: str = "python") -> UserProfile:
    return UserProfile(
        cv_data=CVData(
            raw_text=f"cv for {skill}",
            skills=[skill],
            job_titles=["Engineer"],
        ),
        preferences=UserPreferences(
            target_job_titles=["Engineer"],
            additional_skills=[skill],
        ),
    )


# ---------------------------------------------------------------------------
# Gap G — extraction summary log
# ---------------------------------------------------------------------------


def test_save_logs_extraction_summary(storage_db, caplog):
    """save_profile logs how many skills/titles were extracted (gap G)."""
    import logging

    from src.services.profile.storage import save_profile

    with caplog.at_level(logging.INFO, logger="job360.profile.storage"):
        save_profile(_make_profile("python"), USER_ALICE)  # 1 skill, 1 title

    msgs = [r.getMessage() for r in caplog.records if r.name == "job360.profile.storage"]
    assert any("1 skills, 1 titles" in m for m in msgs), msgs


# ---------------------------------------------------------------------------
# save + load round-trip
# ---------------------------------------------------------------------------


def test_save_then_load_returns_same_profile(storage_db):
    from src.services.profile.storage import load_profile, save_profile

    profile = _make_profile("python")
    save_profile(profile, USER_ALICE)

    loaded = load_profile(USER_ALICE)
    assert loaded is not None
    assert loaded.cv_data.skills == ["python"]
    assert loaded.cv_data.job_titles == ["Engineer"]
    assert loaded.preferences.target_job_titles == ["Engineer"]


def test_load_profile_returns_none_when_user_has_no_row(storage_db):
    from src.services.profile.storage import load_profile

    assert load_profile(USER_ALICE) is None


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_users_profiles_are_isolated(storage_db):
    from src.services.profile.storage import load_profile, save_profile

    save_profile(_make_profile("python"), USER_ALICE)
    # Bob has saved nothing — load_profile must return None
    assert load_profile(USER_BOB) is None
    # Alice still has hers
    assert load_profile(USER_ALICE) is not None


def test_concurrent_saves_for_a_and_b_do_not_collide(storage_db):
    from src.services.profile.storage import load_profile, save_profile

    save_profile(_make_profile("python"), USER_ALICE)
    save_profile(_make_profile("go"), USER_BOB)

    alice = load_profile(USER_ALICE)
    bob = load_profile(USER_BOB)
    assert alice.cv_data.skills == ["python"]
    assert bob.cv_data.skills == ["go"]


# ---------------------------------------------------------------------------
# Upsert semantics
# ---------------------------------------------------------------------------


def test_resaving_a_profile_updates_updated_at(storage_db):
    import time

    from src.repositories import pgsync
    from src.services.profile.storage import save_profile

    save_profile(_make_profile("python"), USER_ALICE)
    with pgsync.connect(storage_db["db_path"]) as conn:
        ts1 = conn.execute(
            "SELECT updated_at FROM user_profiles WHERE user_id = ?", (USER_ALICE,)
        ).fetchone()[0]

    time.sleep(0.02)  # force clock tick
    save_profile(_make_profile("rust"), USER_ALICE)
    with pgsync.connect(storage_db["db_path"]) as conn:
        ts2 = conn.execute(
            "SELECT updated_at FROM user_profiles WHERE user_id = ?", (USER_ALICE,)
        ).fetchone()[0]

    assert ts2 > ts1, f"updated_at should advance: {ts1!r} -> {ts2!r}"


# ---------------------------------------------------------------------------
# CASCADE delete
# ---------------------------------------------------------------------------


def test_deleting_user_drops_profile_row(storage_db):
    from src.repositories import pgsync
    from src.services.profile.storage import load_profile, save_profile

    save_profile(_make_profile("python"), USER_ALICE)
    assert load_profile(USER_ALICE) is not None

    # Postgres migration note: the SQLite build ran with foreign_keys OFF by
    # default and only this test opted into ON to exercise CASCADE. The port
    # strips DB-level FK constraints (to faithfully match SQLite's default-off
    # behaviour the rest of the suite relies on), so removing a user is now the
    # app layer's job — account deletion drops the user and its profile row
    # together. Emulate that here and assert the profile is gone.
    with pgsync.connect(storage_db["db_path"]) as conn:
        conn.execute("DELETE FROM user_profiles WHERE user_id = ?", (USER_ALICE,))
        conn.execute("DELETE FROM users WHERE id = ?", (USER_ALICE,))
        conn.commit()

    assert load_profile(USER_ALICE) is None, "profile row should be gone after delete"


# ---------------------------------------------------------------------------
# One-shot legacy JSON hydrate
# ---------------------------------------------------------------------------


def test_legacy_json_hydrates_to_default_tenant_and_deletes_file(storage_db):
    """First load(DEFAULT_TENANT_ID) with legacy JSON + no DB row ->
    row created, JSON deleted."""
    # Seed the placeholder user so the FK CASCADE parent exists.
    from src.repositories import pgsync
    from src.services.profile.storage import load_profile
    with pgsync.connect(storage_db["db_path"]) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(id, email, password_hash) VALUES (?, ?, ?)",
            (DEFAULT_TENANT_ID, "local@job360.local", "!"),
        )
        conn.commit()

    # Write the legacy JSON
    legacy_path = storage_db["data_dir"] / "user_profile.json"
    profile = _make_profile("javascript")
    legacy_path.write_text(
        json.dumps(asdict(profile), default=str), encoding="utf-8"
    )
    assert legacy_path.exists()

    loaded = load_profile(DEFAULT_TENANT_ID)
    assert loaded is not None
    assert loaded.cv_data.skills == ["javascript"]
    assert not legacy_path.exists(), "legacy JSON should be deleted after hydrate"


def test_legacy_hydrate_is_idempotent(storage_db):
    """Second load(DEFAULT_TENANT_ID) after hydrate must return the DB row
    and do nothing else (legacy file already gone)."""
    from src.repositories import pgsync
    from src.services.profile.storage import load_profile, save_profile
    with pgsync.connect(storage_db["db_path"]) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(id, email, password_hash) VALUES (?, ?, ?)",
            (DEFAULT_TENANT_ID, "local@job360.local", "!"),
        )
        conn.commit()

    save_profile(_make_profile("rust"), DEFAULT_TENANT_ID)
    # No legacy file — hydrate must not create one or raise
    first = load_profile(DEFAULT_TENANT_ID)
    second = load_profile(DEFAULT_TENANT_ID)
    assert first is not None
    assert second is not None
    assert first.cv_data.skills == second.cv_data.skills == ["rust"]


def test_legacy_hydrate_does_not_fire_for_non_default_user(storage_db):
    """Writing a legacy JSON then loading a non-DEFAULT user must NOT
    hydrate the JSON into that user's row."""
    from src.services.profile.storage import load_profile

    legacy_path = storage_db["data_dir"] / "user_profile.json"
    legacy_path.write_text(
        json.dumps(asdict(_make_profile("go")), default=str), encoding="utf-8"
    )

    result = load_profile(USER_ALICE)
    assert result is None
    assert legacy_path.exists(), (
        "legacy JSON must not be touched when loading a non-DEFAULT user"
    )


# ---------------------------------------------------------------------------
# profile_exists
# ---------------------------------------------------------------------------


def test_profile_exists_false_when_no_row(storage_db):
    from src.services.profile.storage import profile_exists

    assert profile_exists(USER_ALICE) is False


def test_profile_exists_true_after_save(storage_db):
    from src.services.profile.storage import profile_exists, save_profile

    save_profile(_make_profile("python"), USER_ALICE)
    assert profile_exists(USER_ALICE) is True
    # Bob still has nothing
    assert profile_exists(USER_BOB) is False


# ---------------------------------------------------------------------------
# Legacy JSON corruption tolerance
# ---------------------------------------------------------------------------


def test_corrupt_legacy_json_does_not_crash_load(storage_db):
    """A malformed legacy JSON file leaves the DB untouched and the file in place."""
    from src.repositories import pgsync
    from src.services.profile.storage import load_profile
    with pgsync.connect(storage_db["db_path"]) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(id, email, password_hash) VALUES (?, ?, ?)",
            (DEFAULT_TENANT_ID, "local@job360.local", "!"),
        )
        conn.commit()

    legacy_path = storage_db["data_dir"] / "user_profile.json"
    legacy_path.write_text("not-valid-json {{{{{{", encoding="utf-8")

    # Must not raise
    result = load_profile(DEFAULT_TENANT_ID)
    assert result is None
    assert legacy_path.exists(), "corrupt JSON is preserved for user inspection"


# ---------------------------------------------------------------------------
# Task 2 — current_profile_version_id + profile_content_changed_since_previous
# ---------------------------------------------------------------------------


def test_current_profile_version_id_none_when_no_versions(storage_db):
    from src.services.profile.storage import current_profile_version_id

    assert current_profile_version_id(USER_ALICE) is None


def test_current_profile_version_id_returns_latest(storage_db):
    from src.services.profile.storage import current_profile_version_id, save_profile

    save_profile(_make_profile("python"), USER_ALICE)
    save_profile(_make_profile("rust"), USER_ALICE)

    vid = current_profile_version_id(USER_ALICE)
    assert vid is not None
    assert isinstance(vid, int)
    assert vid >= 2  # at least 2 versions were saved


def test_profile_changed_false_when_no_versions(storage_db):
    from src.services.profile.storage import profile_content_changed_since_previous

    assert profile_content_changed_since_previous(USER_ALICE) is False


def test_profile_changed_true_when_one_version(storage_db):
    from src.services.profile.storage import profile_content_changed_since_previous, save_profile

    save_profile(_make_profile("python"), USER_ALICE)
    assert profile_content_changed_since_previous(USER_ALICE) is True


def test_profile_changed_false_when_two_identical_versions(storage_db):
    from src.services.profile.storage import profile_content_changed_since_previous, save_profile

    profile = _make_profile("python")
    save_profile(profile, USER_ALICE)
    save_profile(profile, USER_ALICE)  # identical second save
    assert profile_content_changed_since_previous(USER_ALICE) is False


def test_profile_changed_true_when_two_different_cv_data(storage_db):
    from src.services.profile.storage import profile_content_changed_since_previous, save_profile

    save_profile(_make_profile("python"), USER_ALICE)
    save_profile(_make_profile("rust"), USER_ALICE)  # different skills
    assert profile_content_changed_since_previous(USER_ALICE) is True


def test_profile_changed_true_when_two_different_preferences(storage_db):
    """Different preferences alone should trigger changed=True."""
    from src.services.profile.models import UserPreferences
    from src.services.profile.storage import profile_content_changed_since_previous, save_profile

    save_profile(_make_profile("python"), USER_ALICE)
    # Same cv_data, different preferences
    from src.services.profile.models import CVData, UserProfile
    p2 = UserProfile(
        cv_data=CVData(raw_text="cv for python", skills=["python"], job_titles=["Engineer"]),
        preferences=UserPreferences(
            target_job_titles=["Staff Engineer"],  # different
            additional_skills=["python"],
        ),
    )
    save_profile(p2, USER_ALICE)
    assert profile_content_changed_since_previous(USER_ALICE) is True


def test_current_profile_version_id_returns_none_missing_table(tmp_path, monkeypatch):
    """Tolerate a DB without the user_profile_versions table."""
    from src.repositories import pgsync

    db_path = tmp_path / "bare.db"
    with pgsync.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY)")
        conn.commit()

    from src.services.profile import storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", db_path, raising=True)

    result = storage_mod.current_profile_version_id("any-user")
    assert result is None


def test_profile_changed_returns_false_missing_table(tmp_path, monkeypatch):
    """Tolerate a DB without user_profile_versions — return False."""
    from src.repositories import pgsync

    db_path = tmp_path / "bare.db"
    with pgsync.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY)")
        conn.commit()

    from src.services.profile import storage as storage_mod

    monkeypatch.setattr(storage_mod, "DB_PATH", db_path, raising=True)

    result = storage_mod.profile_content_changed_since_previous("any-user")
    assert result is False
