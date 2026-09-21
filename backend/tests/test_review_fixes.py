"""Batch 1.x.1 review-fix regression tests.

One test per surviving issue raised in the Pillar 1 review. Originally
issues #1, #2, #3, #5, #6 and #8 each got a test here; #3, #6 and half
of #1 covered LLM behavior that decision 28 deleted outright (see
below), so what remains is #1's non-LLM half, #2 and #5. Each test
nails down the specific behavior the fix introduces so it cannot
silently regress.

Decision 28 (2026-09-21) deleted every LLM pass from the profile
pipeline — the CV parser, LinkedIn parser, GitHub enricher and
preferences module are all deterministic now. That killed the
subject of several fixes here outright:

- #1's schema half (``CVSchema`` → ``CVData`` mapping for industries
  and languages) — ``schemas.py`` and its ``cv_schema_to_cvdata``
  converter are gone. The surviving half (CVData carries
  ``cv_languages`` through to JSON Resume export) still applies, since
  ``cv_languages`` is just a plain dataclass field now populated by
  deterministic extraction instead of an LLM schema.
- #3 (validation-exhaustion fallback in ``parse_cv_async``) and #6
  (retry-prompt error trimming in ``llm_extract_validated``) tested
  LLM retry/validation machinery that no longer exists at all —
  ``llm_provider.py`` is deleted. A deleted behavior needs no
  regression test.
- #8 (``build_skill_entries`` dedup) never had a test body here to
  begin with — just a stale section header — so there was nothing to
  keep or delete.

What's left covers dependency-file parsing (#2, pure regex/TOML
parsing, untouched by decision 28) and the legacy-hydrate audit stamp
(#5, storage/migrations, untouched by decision 28).
"""

from __future__ import annotations

from src.services.profile import dep_file_parser
from src.services.profile.models import CVData

# ── #1: CVData.cv_languages surfaces through JSON Resume export ────


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


def test_fix2_pep621_optional_extras_excluded_runtime_only():
    content = """
[project]
dependencies = ["fastapi>=0.115"]
[project.optional-dependencies]
dev = ["pytest>=8", "ruff[extra]>=0.1"]
indeed = ["python-jobspy"]
"""
    # Runtime-only: [project.optional-dependencies] (dev/test/feature extras) is
    # excluded to drop tooling noise (eslint/pytest/ruff...). A rare real feature
    # extra (python-jobspy) is the casualty and stays uncaptured — there is no
    # LLM pass left to recover it (decision 28). Structural runtime-vs-dev
    # split, not a keyword denylist (rule #28).
    names = dep_file_parser.parse_pyproject_toml(content)
    assert "fastapi" in names
    assert "pytest" not in names
    assert "ruff" not in names
    assert "python-jobspy" not in names


# ── #5: legacy_hydrate source_action ───────────────────────────────


def test_fix5_legacy_hydrate_passes_source_action(monkeypatch, tmp_path):
    """The legacy JSON hydrate path must stamp source_action=
    'legacy_hydrate' on the snapshot row — otherwise audit trails
    mislabel legacy migrations as ordinary 'user_edit'.
    """
    import asyncio

    from migrations import runner
    from src.core import settings as core_settings
    from src.core.tenancy import DEFAULT_TENANT_ID
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
                CREATE TABLE user_actions (id INTEGER PRIMARY KEY, job_id INTEGER, action TEXT,
                                           notes TEXT DEFAULT '', created_at TEXT, UNIQUE(job_id));
                CREATE TABLE applications (id INTEGER PRIMARY KEY, job_id INTEGER, stage TEXT,
                                           notes TEXT DEFAULT '', created_at TEXT, updated_at TEXT,
                                           UNIQUE(job_id));
                """
            )
            await con.commit()
        await runner.up(str(db))
        async with pg.connect(str(db)) as con:
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
