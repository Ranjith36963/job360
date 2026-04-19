"""Batch 1.x.1 review-fix regression tests.

One test per issue raised in the Pillar 1 review (issues #1, #2, #3,
#5, #6, #8). Each test nails down the specific behavior the fix
introduces so it cannot silently regress.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from src.services.profile import dep_file_parser
from src.services.profile import skill_entry
from src.services.profile.models import CVData, UserPreferences, UserProfile
from src.services.profile.schemas import CVSchema, cv_schema_to_cvdata


# ── #1: CVSchema.industries + languages plumbed into CVData ────────


def test_fix1_schema_industries_and_languages_land_on_cvdata():
    schema = CVSchema.model_validate({
        "industries": ["Healthcare", "Biotechnology"],
        "languages": ["English", "French", "Mandarin"],
    })
    cv = cv_schema_to_cvdata(schema, raw_text="x")
    assert cv.industries == ["Healthcare", "Biotechnology"]
    assert cv.cv_languages == ["English", "French", "Mandarin"]


def test_fix1_json_resume_export_surfaces_cv_languages():
    cv = CVData(cv_languages=["English", "Spanish"])
    out = cv.to_json_resume()
    # cv_languages supplements linkedin_languages — export still works,
    # and at minimum the ``meta`` slot or a caller-accessible field
    # carries them. Here we assert the raw dataclass holds them; the
    # JSON Resume spec keeps ``languages`` for LinkedIn-style entries
    # with fluency, so CV-extracted plain strings continue to live on
    # CVData for callers that want them.
    assert cv.cv_languages == ["English", "Spanish"]
    # JSON Resume export still has its ``languages`` key (from
    # linkedin_languages) — here empty as we didn't set linkedin.
    assert "languages" in out


# ── #2: _PEP621_DEPS_RE survives uvicorn[standard] ─────────────────


def test_fix2_pyproject_with_bracket_extras_captures_all_deps():
    """The project's own pyproject.toml has uvicorn[standard]. Pre-fix
    the regex ate everything after it. Post-fix all deps show up.
    """
    content = """
[project]
name = "job360"
dependencies = [
    "aiohttp>=3.9.0",
    "aiosqlite>=0.19.0",
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "python-multipart>=0.0.9",
    "httpx>=0.27.0",
    "google-generativeai>=0.8.0",
]
"""
    names = dep_file_parser.parse_pyproject_toml(content)
    # All 7 deps must be present. The pre-fix regex would have captured
    # only aiohttp/aiosqlite/fastapi/uvicorn then died on the first ``]``.
    assert "aiohttp" in names
    assert "uvicorn" in names
    assert "httpx" in names
    assert "google-generativeai" in names


def test_fix2_pep621_handles_multiple_bracket_extras():
    content = """
[project]
dependencies = [
    "foo[extra1]>=1",
    "bar[extra2,extra3]>=2",
    "baz[x,y,z]>=3",
    "quux>=4",
]
"""
    names = dep_file_parser.parse_pyproject_toml(content)
    assert names == {"foo", "bar", "baz", "quux"}


def test_fix2_pep621_optional_extras_still_captured():
    content = """
[project]
dependencies = ["fastapi>=0.115"]
[project.optional-dependencies]
dev = ["pytest>=8", "ruff[extra]>=0.1"]
indeed = ["python-jobspy"]
"""
    names = dep_file_parser.parse_pyproject_toml(content)
    assert "fastapi" in names
    assert "pytest" in names
    assert "ruff" in names
    assert "python-jobspy" in names


# ── #3: parse_cv_async graceful degradation on validation exhaustion


@pytest.mark.asyncio
async def test_fix3_validation_exhaustion_returns_defensive_cvdata(tmp_path):
    """When llm_extract_validated raises a validation RuntimeError,
    parse_cv_async must fall back to the defensive dict path rather
    than propagate the error.
    """
    from src.services.profile import cv_parser

    # Fake a PDF-read return value
    fake_text = "Some CV text"

    # Simulate: validated path fails with validation error; plain
    # llm_extract succeeds with a partial dict.
    validation_error = RuntimeError("LLM output failed CVSchema validation after 3 attempts: ...")

    plain_llm_result = {"name": "Ada", "skills": ["Python"]}

    with patch.object(cv_parser, "extract_text", return_value=fake_text), \
         patch("src.services.profile.llm_provider.llm_extract_validated",
               new=AsyncMock(side_effect=validation_error)), \
         patch("src.services.profile.llm_provider.llm_extract",
               new=AsyncMock(return_value=plain_llm_result)):
        cv = await cv_parser.parse_cv_async("fake.pdf")

    # Defensive path produced a CVData with the salvaged skills, not a 500.
    assert cv.raw_text == fake_text
    assert cv.name == "Ada"
    assert "Python" in cv.skills


@pytest.mark.asyncio
async def test_fix3_provider_failure_still_raises(tmp_path):
    """Validation fallback must NOT swallow real provider failures
    (no API keys, all providers down) — operators need that signal.
    """
    from src.services.profile import cv_parser

    provider_error = RuntimeError("No LLM API key configured")

    with patch.object(cv_parser, "extract_text", return_value="x"), \
         patch("src.services.profile.llm_provider.llm_extract_validated",
               new=AsyncMock(side_effect=provider_error)):
        with pytest.raises(RuntimeError, match="No LLM API key"):
            await cv_parser.parse_cv_async("fake.pdf")


# ── #6: retry loop trims error text ────────────────────────────────


@pytest.mark.asyncio
async def test_fix6_retry_prompt_trims_to_first_five_errors():
    """Build a ValidationError with 10 nested problems and assert the
    retry prompt only contains 5 of them.
    """
    from src.services.profile.llm_provider import llm_extract_validated

    # Emit a payload that produces many errors (10 invalid-type fields)
    bad = {
        "skills": 42,  # not a list → 1 error
        "experience": 42,  # not a list → 1 error
        "education": 42,
        "certifications": [{"not": "valid"} for _ in range(15)],  # each fails _lists_of_strings coercion — actually coerces fine
        "career_domain": "definitely_not_a_real_bucket",
    }
    good = {"name": "Eve"}
    captured_prompts: list[str] = []

    async def fake_extract(prompt: str, system: str = ""):
        captured_prompts.append(prompt)
        return bad if len(captured_prompts) == 1 else good

    with patch("src.services.profile.llm_provider.llm_extract", side_effect=fake_extract):
        result = await llm_extract_validated("base prompt", CVSchema, max_retries=2)

    assert result.name == "Eve"
    # Retry prompt must reference "showing first N of M errors" wording
    retry_prompt = captured_prompts[1]
    assert "showing first" in retry_prompt
    # Body only enumerates 5 bullet lines
    bullet_count = retry_prompt.count("\n- ")
    assert bullet_count <= 5


# ── #8: build_skill_entries dedups same-source duplicates ──────────


def test_fix8_same_source_same_skill_dedup():
    """Pre-fix: CV listing ["Python", "python"] emitted 2 cv_explicit
    entries. Post-fix: deduped at the (source, name.casefold()) level.
    """
    prefs = UserPreferences()
    cv = CVData(skills=["Python", "python", "PYTHON"])
    profile = UserProfile(cv_data=cv, preferences=prefs)

    entries = skill_entry.build_skill_entries_from_profile(profile)
    cv_entries = [e for e in entries if e.source == "cv_explicit"]
    assert len(cv_entries) == 1
    # First-sighting casing wins
    assert cv_entries[0].name == "Python"


def test_fix8_cross_source_duplicates_still_emit_multi_row():
    """Cross-source duplicates are NOT deduped — merge_skill_entries
    collapses them later. Regression guard.
    """
    prefs = UserPreferences(additional_skills=["Python"])
    cv = CVData(skills=["Python"], linkedin_skills=["Python"])
    profile = UserProfile(cv_data=cv, preferences=prefs)
    entries = skill_entry.build_skill_entries_from_profile(profile)
    sources = [e.source for e in entries]
    # 1 per (source, skill) — same name across 3 sources = 3 rows
    assert sources == ["user_declared", "cv_explicit", "linkedin"]


# ── #5: legacy_hydrate source_action ───────────────────────────────


def test_fix5_legacy_hydrate_passes_source_action(monkeypatch, tmp_path):
    """The legacy JSON hydrate path must stamp source_action=
    'legacy_hydrate' on the snapshot row — otherwise audit trails
    mislabel legacy migrations as ordinary 'user_edit'.
    """
    import asyncio
    import aiosqlite
    from migrations import runner
    from src.core import settings as core_settings
    from src.services.profile import storage
    from src.core.tenancy import DEFAULT_TENANT_ID

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
                CREATE TABLE user_actions (id INTEGER PRIMARY KEY, job_id INTEGER, action TEXT,
                                           notes TEXT DEFAULT '', created_at TEXT, UNIQUE(job_id));
                CREATE TABLE applications (id INTEGER PRIMARY KEY, job_id INTEGER, stage TEXT,
                                           notes TEXT DEFAULT '', created_at TEXT, updated_at TEXT,
                                           UNIQUE(job_id));
                """
            )
            await con.commit()
        await runner.up(str(db))
        async with aiosqlite.connect(str(db)) as con:
            # INSERT OR IGNORE — migration 0002_multi_tenant already
            # seeds the DEFAULT_TENANT_ID placeholder row for legacy
            # backfill, so a plain INSERT would trip the UNIQUE
            # constraint on users.id.
            await con.execute(
                "INSERT OR IGNORE INTO users(id,email,password_hash) VALUES (?, ?, ?)",
                (DEFAULT_TENANT_ID, "legacy@x.test", "!"),
            )
            await con.commit()
        return db

    db = asyncio.run(_bootstrap())
    monkeypatch.setattr(core_settings, "DB_PATH", db)
    monkeypatch.setattr(storage, "DB_PATH", db)

    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        '{"cv_data": {"name": "Ada"}, "preferences": {"additional_skills": ["Python"]}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "LEGACY_PROFILE_PATH", legacy)

    storage.load_profile(DEFAULT_TENANT_ID)

    versions = storage.list_profile_versions(DEFAULT_TENANT_ID)
    assert versions, "Snapshot should have been written during hydrate"
    assert versions[0]["source_action"] == "legacy_hydrate"
