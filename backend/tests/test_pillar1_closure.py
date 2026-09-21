"""Batch 1.3c / 1.8b — Pillar 1 closure patches.

Closes the documented partials in docs/pillar1_progress.md:
  * 1.3c: ESCO normaliser wired into build_skill_entries_from_profile
  * 1.8b: JSON Resume inverse loader (CVData.from_json_resume) — round-trip

DECISION 28 (2026-09-21) removed the third partial this file used to close:
1.7b fed a PDF's section split into the CV LLM prompt as a
``PRE-SEGMENTED SECTIONS`` hint (``cv_parser._build_section_hint``, calling
``llm_provider.llm_extract_validated`` with a ``schemas.CVSchema``). All
three of those names are deleted — ``cv_parser.parse_cv_async`` is
deterministic now (``cv_parser.py``'s module docstring), so there is no
prompt left for a section hint to land in, and the four tests that pinned
that behaviour went with it. ``cv_parser.extract_sections_from_pdf`` itself
survives (still covered by ``test_cv_layout.py``) — only its LLM consumer
is gone.

This file also used to close 1.8 with two rollback tests that stay below
unchanged: they exercise ``storage.restore_profile_version``, which has
nothing to do with the deleted LLM passes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.profile import skill_normalizer
from src.services.profile.models import CVData, UserPreferences, UserProfile

# ── 1.8b: JSON Resume inverse loader ──────────────────────────────


def test_18b_from_json_resume_round_trips_basic_fields():
    original = CVData(
        name="Ada Lovelace",
        headline="Founding Engineer",
        summary="Builds math and machines.",
        location="London",
        skills=["Python", "Algebra"],
        certifications=["ACLS 2022"],
    )
    jr = original.to_json_resume()
    restored = CVData.from_json_resume(jr)
    assert restored.name == "Ada Lovelace"
    assert restored.headline == "Founding Engineer"
    assert restored.summary == "Builds math and machines."
    assert restored.location == "London"
    assert restored.skills == ["Python", "Algebra"]
    assert restored.certifications == ["ACLS 2022"]


def test_18b_from_json_resume_round_trips_linkedin_collections():
    original = CVData(
        linkedin_positions=[{"title": "Eng", "company": "ACME",
                             "start": "2020", "end": "Present",
                             "description": "Built X"}],
        linkedin_languages=[{"language": "English", "proficiency": "Native"}],
        linkedin_projects=[{"title": "Job360", "description": "aggregator",
                            "start": "", "end": "", "url": "https://x"}],
        linkedin_volunteer=[{"role": "Mentor", "organisation": "CFG",
                             "cause": "", "start": "", "end": "", "description": ""}],
        linkedin_industry="Technology",
    )
    jr = original.to_json_resume()
    restored = CVData.from_json_resume(jr)
    assert restored.linkedin_positions[0]["title"] == "Eng"
    assert restored.linkedin_positions[0]["company"] == "ACME"
    assert restored.linkedin_languages[0]["language"] == "English"
    assert restored.linkedin_projects[0]["title"] == "Job360"
    assert restored.linkedin_volunteer[0]["role"] == "Mentor"
    assert restored.linkedin_industry == "Technology"


def test_18b_from_json_resume_preserves_meta_extensions():
    original = CVData(
        career_domain="data_and_ai",
        github_frameworks=["FastAPI", "React"],
        github_topics=["ml", "nlp"],
        github_languages={"Python": 1000},
    )
    jr = original.to_json_resume()
    restored = CVData.from_json_resume(jr)
    assert restored.career_domain == "data_and_ai"
    assert restored.github_frameworks == ["FastAPI", "React"]
    assert restored.github_topics == ["ml", "nlp"]
    assert restored.github_languages == {"Python": 1000}


def test_18b_from_json_resume_handles_empty_and_malformed():
    assert CVData.from_json_resume({}).name == ""
    assert CVData.from_json_resume({"basics": "not a dict"}).name == ""
    assert CVData.from_json_resume(None).skills == []
    # Unknown root keys ignored without crash
    weird = CVData.from_json_resume({"invented": "root key", "work": [{"name": "ok"}]})
    assert weird.linkedin_positions == [{"title": "", "company": "ok",
                                         "start": "", "end": "", "description": ""}]


def test_18b_from_json_resume_accepts_plain_string_skills():
    """Some JSON Resume exports put plain strings in ``skills`` instead of
    the full {name, keywords, level} shape. Loader must accept both."""
    restored = CVData.from_json_resume({"skills": ["Python", "Docker",
                                                   {"name": "Rust"}]})
    assert restored.skills == ["Python", "Docker", "Rust"]


# ── Audit mitigation: ESCO CC BY 4.0 attribution (plan §8 row 1) ──


def test_esco_attribution_in_skill_normalizer_module():
    """The ESCO licence terms require attribution. The module docstring
    must carry it so anyone reading the source sees the credit line.
    """
    doc = skill_normalizer.__doc__ or ""
    assert "ESCO" in doc
    assert "European Union" in doc
    assert "CC BY 4.0" in doc


# `test_esco_attribution_in_build_script` used to assert the same licence line
# on `backend/scripts/build_esco_index.py`. Slice 5 (#483) deleted that script
# (the index it built was never shipped — hard rule #28), so the module
# docstring above is the only place the attribution has to live now.


# ── 1.8b extended: restore_profile_version atomic rollback ────────


@pytest.fixture
def versioned_storage_for_restore(tmp_path: Path, monkeypatch):
    """Reuse the test_profile_versions bootstrap pattern for rollback tests."""
    import asyncio

    from migrations import runner
    from src.core import settings as core_settings
    from src.repositories import pg
    from src.services.profile import storage

    async def _bootstrap():
        db = tmp_path / "t.db"
        async with pg.connect(str(db)) as con:
            await con.executescript(
                """
                CREATE TABLE jobs (id INTEGER PRIMARY KEY, title TEXT, company TEXT,
                                   apply_url TEXT, source TEXT, date_found TEXT,
                                   normalized_company TEXT, normalized_title TEXT,
                                   first_seen TEXT,
                                   UNIQUE(normalized_company, normalized_title));
                CREATE TABLE user_actions (id INTEGER PRIMARY KEY, job_id INTEGER,
                                           action TEXT, notes TEXT DEFAULT '',
                                           created_at TEXT, UNIQUE(job_id));
                CREATE TABLE applications (id INTEGER PRIMARY KEY, job_id INTEGER,
                                           stage TEXT, notes TEXT DEFAULT '',
                                           created_at TEXT, updated_at TEXT,
                                           UNIQUE(job_id));
                """
            )
            await con.commit()
        await runner.up(str(db))
        async with pg.connect(str(db)) as con:
            await con.execute(
                "INSERT INTO users(id,email,password_hash) VALUES (?, ?, ?)",
                ("alice", "a@example.test", "!"),
            )
            await con.execute(
                "INSERT INTO users(id,email,password_hash) VALUES (?, ?, ?)",
                ("bob", "b@example.test", "!"),
            )
            await con.commit()
        return db

    db = asyncio.run(_bootstrap())
    monkeypatch.setattr(core_settings, "DB_PATH", db)
    monkeypatch.setattr(storage, "DB_PATH", db)
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "LEGACY_PROFILE_PATH", tmp_path / "legacy.json")
    return storage


def test_18b_restore_version_reverts_tip_to_prior_snapshot(versioned_storage_for_restore):
    """Plan §10 Batch 1.8 acceptance #3 — rollback to version N."""
    storage = versioned_storage_for_restore

    v1 = UserProfile(cv_data=CVData(name="v1"), preferences=UserPreferences())
    v2 = UserProfile(cv_data=CVData(name="v2"), preferences=UserPreferences())
    v3 = UserProfile(cv_data=CVData(name="v3"), preferences=UserPreferences())
    storage.save_profile(v1, "alice", source_action="cv_upload")
    storage.save_profile(v2, "alice", source_action="user_edit")
    storage.save_profile(v3, "alice", source_action="user_edit")

    # Current tip should be v3
    assert storage.load_profile("alice").cv_data.name == "v3"

    # Find v1's version_id
    versions = storage.list_profile_versions("alice")
    v1_id = versions[-1]["id"]  # oldest
    assert versions[-1]["cv_data"]["name"] == "v1"

    # Restore to v1
    restored = storage.restore_profile_version("alice", v1_id)
    assert restored is not None
    assert restored.cv_data.name == "v1"

    # Tip now reflects v1
    tip = storage.load_profile("alice")
    assert tip.cv_data.name == "v1"

    # Full history preserved (v1 + v2 + v3 + the restore = 4 snapshots)
    after = storage.list_profile_versions("alice", limit=50)
    assert len(after) == 4
    assert after[0]["cv_data"]["name"] == "v1"  # most recent is the restore


def test_18b_restore_version_missing_returns_none(versioned_storage_for_restore):
    storage = versioned_storage_for_restore
    storage.save_profile(
        UserProfile(cv_data=CVData(name="only"), preferences=UserPreferences()),
        "alice",
    )
    assert storage.restore_profile_version("alice", 99999) is None
    # Tip unchanged
    assert storage.load_profile("alice").cv_data.name == "only"


def test_18b_restore_version_cross_tenant_isolation(versioned_storage_for_restore):
    """alice restoring bob's version_id must return None — no cross-tenant leak."""
    storage = versioned_storage_for_restore
    storage.save_profile(
        UserProfile(cv_data=CVData(name="alice-v1"), preferences=UserPreferences()),
        "alice",
    )
    storage.save_profile(
        UserProfile(cv_data=CVData(name="bob-v1"), preferences=UserPreferences()),
        "bob",
    )
    bob_versions = storage.list_profile_versions("bob")
    bob_version_id = bob_versions[0]["id"]

    # alice tries to roll back to bob's version_id
    result = storage.restore_profile_version("alice", bob_version_id)
    assert result is None
    # alice's tip unchanged
    assert storage.load_profile("alice").cv_data.name == "alice-v1"
    # bob's tip unchanged
    assert storage.load_profile("bob").cv_data.name == "bob-v1"
