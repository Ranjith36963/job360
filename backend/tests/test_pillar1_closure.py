"""Batch 1.3c / 1.7b / 1.8b — Pillar 1 closure patches.

Closes the three documented partials in docs/pillar1_progress.md:
  * 1.3c: ESCO normaliser wired into build_skill_entries_from_profile
  * 1.7b: PDF section segmentation fed into the CV LLM prompt
  * 1.8b: JSON Resume inverse loader (CVData.from_json_resume) — round-trip
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.services.profile import cv_parser, skill_entry, skill_normalizer
from src.services.profile.models import CVData, UserPreferences, UserProfile

# ── 1.3c: ESCO normaliser integration ─────────────────────────────


@pytest.fixture
def fake_esco_index(tmp_path: Path):
    """Build a 3-concept fake ESCO index + reset the module singleton."""
    labels = [
        {"uri": "http://esco.example/python", "label": "Python programming", "alt_labels": []},
        {"uri": "http://esco.example/docker", "label": "Container orchestration", "alt_labels": []},
        {"uri": "http://esco.example/rust", "label": "Rust programming", "alt_labels": []},
    ]
    embeddings = np.array(
        [[1.0, 0.0, 0.0],
         [0.0, 1.0, 0.0],
         [0.0, 0.0, 1.0]],
        dtype="float32",
    )
    esco_dir = tmp_path / "esco"
    esco_dir.mkdir()
    (esco_dir / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    np.save(esco_dir / "embeddings.npy", embeddings)
    skill_normalizer.reset_index_for_testing(esco_dir)
    yield esco_dir
    skill_normalizer.reset_index_for_testing()


def test_13c_build_skill_entries_stamps_esco_uri_when_index_available(fake_esco_index):
    """With the ESCO index on disk + encoder available, entries carry esco_uri."""
    # The encoder is patched to return a vector aligned with the python row
    enc = MagicMock()
    enc.encode = MagicMock(return_value=np.array([[0.99, 0.1, 0.0]], dtype="float32"))
    with patch.object(skill_normalizer._INDEX, "_get_encoder", return_value=enc):
        prefs = UserPreferences(additional_skills=["Py"])
        profile = UserProfile(cv_data=CVData(), preferences=prefs)
        entries = skill_entry.build_skill_entries_from_profile(profile)

    assert len(entries) == 1
    assert entries[0].esco_uri == "http://esco.example/python"
    # canonical label replaces the raw surface form
    assert entries[0].name == "Python programming"


def test_13c_build_skill_entries_graceful_when_no_esco_data(tmp_path):
    """When the ESCO index is absent, entries behave like pre-1.3c — no URI, raw name."""
    skill_normalizer.reset_index_for_testing(tmp_path / "nowhere")
    prefs = UserPreferences(additional_skills=["Python"])
    profile = UserProfile(cv_data=CVData(), preferences=prefs)
    entries = skill_entry.build_skill_entries_from_profile(profile)
    skill_normalizer.reset_index_for_testing()

    assert len(entries) == 1
    assert entries[0].esco_uri is None
    assert entries[0].name == "Python"


def test_13c_normalize_false_bypasses_esco_even_when_available(fake_esco_index):
    """Opt-out flag suppresses normalisation regardless of index state."""
    enc = MagicMock()
    enc.encode = MagicMock(return_value=np.array([[0.99, 0.0, 0.0]], dtype="float32"))
    with patch.object(skill_normalizer._INDEX, "_get_encoder", return_value=enc):
        prefs = UserPreferences(additional_skills=["Py"])
        profile = UserProfile(cv_data=CVData(), preferences=prefs)
        entries = skill_entry.build_skill_entries_from_profile(profile, normalize=False)

    assert entries[0].esco_uri is None
    assert entries[0].name == "Py"  # raw form preserved


def test_13c_esco_dedup_via_canonical_label(fake_esco_index):
    """Two surface forms mapping to the same ESCO URI collapse during build
    (same (source, canonical-name) pair dedupes — Batch 1.2 review fix #8
    still holds)."""
    enc = MagicMock()
    enc.encode = MagicMock(return_value=np.array([[0.99, 0.0, 0.0]], dtype="float32"))
    with patch.object(skill_normalizer._INDEX, "_get_encoder", return_value=enc):
        prefs = UserPreferences()
        cv = CVData(skills=["Py", "python programming"])
        profile = UserProfile(cv_data=cv, preferences=prefs)
        entries = skill_entry.build_skill_entries_from_profile(profile)

    cv_entries = [e for e in entries if e.source == "cv_explicit"]
    assert len(cv_entries) == 1
    assert cv_entries[0].name == "Python programming"


# ── 1.7b: section hints in the LLM prompt ─────────────────────────


@pytest.mark.asyncio
async def test_17b_pdf_sections_land_in_prompt(tmp_path):
    """When extract_sections_from_pdf returns a section map, the LLM
    prompt must include a ``PRE-SEGMENTED SECTIONS`` block."""
    from src.services.profile import cv_parser, schemas

    captured: list[str] = []

    async def fake_extract(prompt: str, schema_cls, system: str = "", max_retries: int = 2):
        captured.append(prompt)
        return schemas.CVSchema.model_validate({})

    with patch.object(cv_parser, "extract_text", return_value="RAW CV TEXT"), \
         patch.object(cv_parser, "extract_sections_from_pdf",
                      return_value={
                          "header": "Ada Lovelace",
                          "experience": "Senior Engineer at ACME\nBuilt things",
                          "education": "BSc Maths",
                      }), \
         patch("src.services.profile.llm_provider.llm_extract_validated",
               side_effect=fake_extract):
        fake_pdf = tmp_path / "cv.pdf"
        fake_pdf.write_bytes(b"PDF")
        await cv_parser.parse_cv_async(str(fake_pdf))

    assert len(captured) == 1
    prompt = captured[0]
    assert "PRE-SEGMENTED SECTIONS" in prompt
    assert "[EXPERIENCE]" in prompt
    assert "Senior Engineer at ACME" in prompt
    assert "[EDUCATION]" in prompt


@pytest.mark.asyncio
async def test_17b_non_pdf_skips_section_hint(tmp_path):
    """DOCX / other extensions must NOT trigger extract_sections_from_pdf
    (would waste a pdfplumber open attempt on a non-PDF)."""
    from src.services.profile import cv_parser, schemas

    captured: list[str] = []

    async def fake_extract(prompt: str, schema_cls, system: str = "", max_retries: int = 2):
        captured.append(prompt)
        return schemas.CVSchema.model_validate({})

    with patch.object(cv_parser, "extract_text", return_value="DOCX TEXT"), \
         patch.object(cv_parser, "extract_sections_from_pdf",
                      side_effect=AssertionError("should not be called")), \
         patch("src.services.profile.llm_provider.llm_extract_validated",
               side_effect=fake_extract):
        fake_docx = tmp_path / "cv.docx"
        fake_docx.write_bytes(b"docx")
        await cv_parser.parse_cv_async(str(fake_docx))

    assert "PRE-SEGMENTED SECTIONS" not in captured[0]


@pytest.mark.asyncio
async def test_17b_graceful_when_no_sections_detected(tmp_path):
    """Unreadable PDF → extract_sections returns None → prompt has no hint."""
    from src.services.profile import cv_parser, schemas

    captured: list[str] = []

    async def fake_extract(prompt: str, schema_cls, system: str = "", max_retries: int = 2):
        captured.append(prompt)
        return schemas.CVSchema.model_validate({})

    with patch.object(cv_parser, "extract_text", return_value="RAW"), \
         patch.object(cv_parser, "extract_sections_from_pdf", return_value=None), \
         patch("src.services.profile.llm_provider.llm_extract_validated",
               side_effect=fake_extract):
        fake_pdf = tmp_path / "cv.pdf"
        fake_pdf.write_bytes(b"pdf")
        await cv_parser.parse_cv_async(str(fake_pdf))

    assert "PRE-SEGMENTED SECTIONS" not in captured[0]


def test_17b_build_section_hint_truncates_long_bodies(tmp_path):
    """A ≥1200-char body gets clipped in the hint so prompts stay compact."""
    long_body = "x" * 2000
    with patch.object(cv_parser, "extract_sections_from_pdf",
                      return_value={"experience": long_body}):
        fake_pdf = tmp_path / "cv.pdf"
        fake_pdf.write_bytes(b"pdf")
        hint = cv_parser._build_section_hint(str(fake_pdf))
    assert "…" in hint
    # Full 2000-char body would blow past 1200 + header overhead
    assert len(hint) < 1800


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
    from src.services.profile import skill_normalizer
    doc = skill_normalizer.__doc__ or ""
    assert "ESCO" in doc
    assert "European Union" in doc
    assert "CC BY 4.0" in doc


def test_esco_attribution_in_build_script():
    """Same requirement on the build-time artefact generator — the
    script MUST carry the attribution so anyone running it (or
    auditing the release pipeline) sees it."""
    from pathlib import Path
    # build_esco_index.py was consolidated from repo-root scripts/ into
    # backend/scripts/ — parents[1] is backend/, not parents[2] (repo root).
    script = (Path(__file__).resolve().parents[1]
              / "scripts" / "build_esco_index.py")
    content = script.read_text(encoding="utf-8")
    assert "ESCO" in content
    assert "CC BY 4.0" in content
    assert "European Union" in content


# ── 1.8b extended: restore_profile_version atomic rollback ────────


@pytest.fixture
def versioned_storage_for_restore(tmp_path: Path, monkeypatch):
    """Reuse the test_profile_versions bootstrap pattern for rollback tests."""
    import asyncio

    import aiosqlite

    from migrations import runner
    from src.core import settings as core_settings
    from src.services.profile import storage

    async def _bootstrap():
        db = tmp_path / "t.db"
        async with aiosqlite.connect(str(db)) as con:
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
        async with aiosqlite.connect(str(db)) as con:
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
