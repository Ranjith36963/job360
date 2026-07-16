"""Batch 1.8 (Pillar 1) — profile versioning + JSON Resume serializer tests.

Uses a tmp SQLite DB with all migrations applied via the runner. Tests
``save_profile`` writes snapshots, ``list_profile_versions`` reads them
newest-first, retention caps at ``VERSION_RETENTION``, and rollback to
an old version restores prior state. JSON Resume serializer is a pure
CVData method — no DB needed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from migrations import runner
from src.repositories import pg
from src.services.profile.models import CVData, UserPreferences, UserProfile


async def _bootstrap_db(db_path: str) -> None:
    """Mirrors the pattern from test_profile_storage.py: pre-create the
    pre-migration jobs/user_actions/applications tables that 0002 rebuilds,
    then run the full migration chain through 0007.
    """
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
    async with pg.connect(db_path) as db:
        await db.execute(
            "INSERT INTO users(id, email, password_hash) VALUES (?, ?, ?)",
            ("user-1", "u@example.com", "x"),
        )
        await db.commit()


@pytest.fixture
def versioned_storage(tmp_path: Path, monkeypatch):
    """Isolated SQLite with all migrations 0000..0007 applied + one seeded user.

    Patches both ``src.core.settings.DB_PATH`` and
    ``services.profile.storage.DB_PATH`` since the latter is captured
    at module import via ``from … import DB_PATH``.
    """
    from src.core import settings as core_settings
    from src.services.profile import storage

    db = tmp_path / "test.db"
    asyncio.run(_bootstrap_db(str(db)))

    monkeypatch.setattr(core_settings, "DB_PATH", db, raising=True)
    monkeypatch.setattr(storage, "DB_PATH", db, raising=True)
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path, raising=True)
    monkeypatch.setattr(storage, "LEGACY_PROFILE_PATH", tmp_path / "legacy.json", raising=True)
    return storage


# ── save_profile snapshot behaviour ─────────────────────────────────


def test_save_profile_records_initial_snapshot(versioned_storage):
    storage = versioned_storage
    profile = UserProfile(
        cv_data=CVData(name="Ada"),
        preferences=UserPreferences(additional_skills=["Python"]),
    )
    storage.save_profile(profile, user_id="user-1", source_action="cv_upload")

    versions = storage.list_profile_versions("user-1")
    assert len(versions) == 1
    assert versions[0]["source_action"] == "cv_upload"
    assert versions[0]["cv_data"]["name"] == "Ada"


def test_save_profile_appends_new_snapshot_on_update(versioned_storage):
    storage = versioned_storage
    profile = UserProfile(cv_data=CVData(name="Ada"), preferences=UserPreferences())
    storage.save_profile(profile, user_id="user-1", source_action="cv_upload")

    profile.cv_data.name = "Ada Lovelace"
    storage.save_profile(profile, user_id="user-1", source_action="user_edit")

    versions = storage.list_profile_versions("user-1")
    assert len(versions) == 2
    # Newest first — verifies ORDER BY created_at DESC
    assert versions[0]["source_action"] == "user_edit"
    assert versions[0]["cv_data"]["name"] == "Ada Lovelace"
    assert versions[1]["source_action"] == "cv_upload"
    assert versions[1]["cv_data"]["name"] == "Ada"


def test_retention_caps_snapshots_at_configured_limit(versioned_storage, monkeypatch):
    storage = versioned_storage
    monkeypatch.setattr(storage, "VERSION_RETENTION", 3)

    profile = UserProfile(cv_data=CVData(name="A"), preferences=UserPreferences())
    for i in range(6):
        profile.cv_data.name = f"Name v{i}"
        storage.save_profile(profile, user_id="user-1", source_action=f"v{i}")

    versions = storage.list_profile_versions("user-1", limit=50)
    assert len(versions) == 3
    # Newest 3 retained: v5, v4, v3
    assert [v["source_action"] for v in versions] == ["v5", "v4", "v3"]


def test_list_profile_versions_respects_limit_arg(versioned_storage):
    storage = versioned_storage
    profile = UserProfile(cv_data=CVData(name="A"), preferences=UserPreferences())
    for i in range(5):
        profile.cv_data.name = f"n{i}"
        storage.save_profile(profile, user_id="user-1")
    assert len(storage.list_profile_versions("user-1", limit=2)) == 2
    assert len(storage.list_profile_versions("user-1", limit=10)) == 5


def test_snapshots_isolated_per_user(versioned_storage):
    storage = versioned_storage
    from src.repositories import pgsync
    with pgsync.connect(str(storage.DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
            ("user-2", "b@example.com", "x"),
        )
        conn.commit()

    p1 = UserProfile(cv_data=CVData(name="Alice"), preferences=UserPreferences())
    p2 = UserProfile(cv_data=CVData(name="Bob"), preferences=UserPreferences())
    storage.save_profile(p1, user_id="user-1")
    storage.save_profile(p2, user_id="user-2")

    vs1 = storage.list_profile_versions("user-1")
    vs2 = storage.list_profile_versions("user-2")
    assert len(vs1) == 1 and vs1[0]["cv_data"]["name"] == "Alice"
    assert len(vs2) == 1 and vs2[0]["cv_data"]["name"] == "Bob"


def test_snapshot_preserves_full_cvdata_payload(versioned_storage):
    storage = versioned_storage
    profile = UserProfile(
        cv_data=CVData(
            name="Ada",
            skills=["Python", "Docker"],
            github_frameworks=["FastAPI"],
            career_domain="software_engineering",
        ),
        preferences=UserPreferences(additional_skills=["Rust"]),
    )
    storage.save_profile(profile, user_id="user-1", source_action="cv_upload")

    snap = storage.list_profile_versions("user-1")[0]
    assert snap["cv_data"]["skills"] == ["Python", "Docker"]
    assert snap["cv_data"]["github_frameworks"] == ["FastAPI"]
    assert snap["cv_data"]["career_domain"] == "software_engineering"
    assert snap["preferences"]["additional_skills"] == ["Rust"]


# ── JSON Resume serializer ──────────────────────────────────────────


def test_to_json_resume_has_canonical_root_keys():
    cv = CVData(name="Ada", headline="Engineer")
    out = cv.to_json_resume()
    for key in ("basics", "work", "education", "skills", "languages",
                "projects", "volunteer", "certificates", "meta"):
        assert key in out


def test_to_json_resume_maps_linkedin_positions_to_work():
    cv = CVData(
        linkedin_positions=[
            {"title": "Engineer", "company": "ACME", "start": "2020",
             "end": "Present", "description": "Built things"}
        ],
    )
    out = cv.to_json_resume()
    assert out["work"][0]["name"] == "ACME"
    assert out["work"][0]["position"] == "Engineer"
    assert out["work"][0]["startDate"] == "2020"
    assert out["work"][0]["summary"] == "Built things"


def test_to_json_resume_maps_volunteer_schema():
    cv = CVData(linkedin_volunteer=[
        {"role": "Mentor", "organisation": "CFG", "cause": "Education",
         "start": "", "end": "", "description": ""}
    ])
    out = cv.to_json_resume()
    assert out["volunteer"][0]["organization"] == "CFG"
    assert out["volunteer"][0]["position"] == "Mentor"


def test_to_json_resume_meta_carries_provenance_extensions():
    cv = CVData(
        github_frameworks=["FastAPI", "React"],
        career_domain="data_and_ai",
        linkedin_industry="Technology",
    )
    meta = cv.to_json_resume()["meta"]
    assert meta["github_frameworks"] == ["FastAPI", "React"]
    assert meta["career_domain"] == "data_and_ai"
    assert meta["industry"] == "Technology"


def test_to_json_resume_is_json_serialisable():
    """The entire export must round-trip through json.dumps."""
    cv = CVData(
        name="Ada",
        skills=["Python"],
        linkedin_positions=[{"title": "Eng", "company": "A", "start": "", "end": "", "description": ""}],
        linkedin_languages=[{"language": "English", "proficiency": "Native"}],
    )
    serialised = json.dumps(cv.to_json_resume())
    assert "basics" in serialised
    assert "Python" in serialised
    assert "English" in serialised
