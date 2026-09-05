"""Slice 5 (#483) — the sourcing era is deleted, not hidden.

Frozen before the build (docs/plans/2026-09-05-delete-sourcing-era/spec.md,
rows R1–R13). Product rule 4: Job360 never sources, scores or ranks a job;
VISION decision 2: "hide now, delete later" — this is later.

Every test here is red on `origin/main` 1fba085 and green when the slice is
done. The token scan is the roadmap's own Done-when (`grep -r SOURCE_REGISTRY`
finds nothing), bounded to the LIVE surfaces: code, workflows, scripts and the
living docs. History (`docs/plans`, `docs/_archive`, the implementation log,
VISION.md) is excluded on purpose — it is allowed to say what used to be.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent

# ── R1: modules that must not exist ─────────────────────────────────────────
_GONE_MODULES = (
    "src.sources",
    "src.main",
    "src.cli_view",
    "src.workers",
    "src.api.routes.search",
    "src.api.routes.jobs",
    "src.api.routes.runs",
    "src.repositories.csv_export",
    "src.services.skill_matcher",
    "src.services.deduplicator",
    "src.services.rescore",
    "src.services.scheduler",
    "src.services.feed",
    "src.services.job_enrichment",
    "src.services.job_enrichment_schema",
    "src.services.shelf_enrichment",
    "src.services.shelf_gate",
    "src.services.prefilter",
    "src.services.retrieval",
    "src.services.embeddings",
    "src.services.vector_index",
    "src.services.pg_vector_index",
    "src.services.llm_matcher",
    "src.services.scoring_dimensions",
    "src.services.uk_gate",
    "src.services.domain_classifier",
    "src.services.coverage",
    "src.services.ghost_detection",
    "src.services.description_backfill",
    "src.services.circuit_breaker",
    "src.services.metrics_exporter",
    "src.services.audit_trail",
    "src.services.conditional_cache",
    "src.services.job_signals",
    "src.services.salary",
    "src.services.visa_signal",
    "src.services.skill_gap",
    "src.services.query_text",
    "src.services.profile.keyword_generator",
)


@pytest.mark.parametrize("name", _GONE_MODULES)
def test_modules_gone(name: str) -> None:
    assert importlib.util.find_spec(name) is None, f"{name} still exists"


# ── R1/R9/R10/R11: the token scan ───────────────────────────────────────────
_FORBIDDEN = re.compile(
    r"SOURCE_REGISTRY|run_search|JobScorer|fill_shelves|rescore_user_feed"
    r"|SEARCH_UI_ENABLED|CATALOG_CRONS_ENABLED|BaseJobSource"
)
_SCAN_ROOTS = (
    ".claude/skills",
    "backend/src",
    "backend/scripts",
    "backend/migrations",
    "backend/pyproject.toml",
    "frontend/src",
    "frontend/tests",
    "frontend/.env.local.example",
    ".github",
    "scripts",
    ".env.example",
    "docker-compose.prod.yml",
    "docker-compose.dev.yml",
)
_SCAN_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".next", "dist", "build"}
# Recorded third-party payloads (review threads captured from GitHub) are
# evidence, not code — rewriting them would falsify the fixture.
_SCAN_SKIP_PREFIXES = ("scripts/fixtures/",)
_SCAN_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".mjs", ".yml", ".yaml", ".toml", ".json",
    ".sql", ".sh", ".example", ".md",
}


def _scan_files() -> list[Path]:
    out: list[Path] = []
    for root in _SCAN_ROOTS:
        p = REPO / root
        if p.is_file():
            out.append(p)
            continue
        if not p.is_dir():
            continue
        for f in p.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(REPO)
            if any(part in _SCAN_SKIP_DIRS for part in rel.parts):
                continue
            if rel.as_posix().startswith(_SCAN_SKIP_PREFIXES):
                continue
            if f.suffix not in _SCAN_SUFFIXES and f.name != ".env.local.example":
                continue
            out.append(f)
    # This file names the tokens on purpose.
    return [f for f in out if f.resolve() != Path(__file__).resolve()]


def _hits(paths: list[Path]) -> list[str]:
    hits: list[str] = []
    for f in paths:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _FORBIDDEN.search(line):
                hits.append(f"{f.relative_to(REPO)}:{i}: {line.strip()[:100]}")
    return hits


def test_no_forbidden_tokens() -> None:
    hits = _hits(_scan_files())
    assert not hits, "sourcing-era tokens still live:\n" + "\n".join(hits[:60])


# ── R13: living docs ────────────────────────────────────────────────────────
_LIVING_DOCS = (
    "ARCHITECTURE.md",
    "STATUS.md",
    "CLAUDE.md",
    "backend/CLAUDE.md",
    "backend/README.md",
    "frontend/README.md",
    "docs/README.md",
    ".claude/skills/hard-rules/SKILL.md",
)


def test_live_docs_clean() -> None:
    paths = [REPO / d for d in _LIVING_DOCS if (REPO / d).exists()]
    pillars = REPO / "docs" / "product" / "pillars"
    if pillars.is_dir():
        paths.extend(sorted(pillars.glob("*.md")))
    hits = _hits(paths)
    assert not hits, "living docs still describe the sourcing era:\n" + "\n".join(hits[:60])


# ── R12: the archive is gone, not frozen ─────────────────────────────────────
# The sourcing era's frozen reference docs (docs/_archive/sourcing-era/) were
# themselves deleted as part of the 2026-09-03 mission cleanup — the era is
# not just dead code, it is dead history too. This flips the earlier version
# of this test, which asserted the archive existed with a FROZEN header.
_FORMERLY_ARCHIVED = (
    "02-search-and-match-engine.md",
    "03-job-providers.md",
    "CATALOG_STATE.md",
    "SHELF_FILL_MEASURED.md",
    "UNIVERSAL_SHELF.md",
    "add-source/SKILL.md",
)


def test_archive_deleted() -> None:
    assert not (REPO / "docs" / "_archive").exists(), (
        "docs/_archive/ must not exist — the sourcing-era archive was deleted "
        "in the mission cleanup, not just the sourcing-era code."
    )
    for name in _FORMERLY_ARCHIVED[:5]:
        assert not (REPO / "docs" / "product" / "pillars" / name).exists(), f"{name} still in pillars/"
    assert not (REPO / ".claude" / "skills" / "add-source").exists()


# ── R2: routes ──────────────────────────────────────────────────────────────
def test_routes_gone() -> None:
    from src.api.main import app
    from tests._routes import route_paths

    # route_paths, not app.routes: FastAPI 0.141 nests included routers, and
    # the raw list has NO /api/* rows -- "nothing sourcing-era is mounted" would
    # hold vacuously and "/api/jobs/bring" would look absent.
    paths = sorted(set(route_paths(app)))
    # The two /api/jobs/* doors that are the PRODUCT, not the sourcing era:
    # bring (the front door) and fetch-url (slice 3's web fallback that
    # pre-fills the bring form — docs/plans/2026-09-04-url-fetch/spec.md).
    kept = {"/api/jobs/bring", "/api/jobs/fetch-url"}
    bad = [
        p for p in paths
        if p.startswith(("/api/search", "/api/runs", "/api/sources"))
        or (p.startswith("/api/jobs") and p not in kept)
    ]
    assert not bad, f"sourcing-era routes still mounted: {bad}"
    assert "/api/jobs/bring" in paths


# ── R5: settings + .env.example ─────────────────────────────────────────────
_GONE_SETTINGS = (
    "REED_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JSEARCH_API_KEY",
    "JOOBLE_API_KEY", "SERPAPI_KEY", "CAREERJET_AFFID", "FINDWORK_API_KEY",
    "DFE_APPRENTICESHIPS_API_KEY", "MIN_MATCH_SCORE", "FEED_CANDIDATE_CAP",
    "MAX_RESULTS_PER_SOURCE", "MAX_DAYS_OLD", "ENRICHMENT_MAX_JOBS",
    "ENRICHMENT_MIN_SCORE", "ENRICHMENT_THRESHOLD", "MAX_CONCURRENT_SEARCHES_PER_USER",
    "MIN_TITLE_GATE", "MIN_SKILL_GATE", "SALARY_WEIGHT", "SENIORITY_WEIGHT",
    "VISA_WEIGHT", "WORKPLACE_WEIGHT", "SEMANTIC_ENABLED", "EMBED_BACKFILL_PER_RUN",
    "DESCRIPTION_BACKFILL_PER_TICK", "LLM_OUTPUT_TOKENS_PER_JOB",
    "SOURCE_FETCH_TIMEOUT", "SOURCE_FETCH_TIMEOUT_ATS", "SEARCH_UI_ENABLED",
    "CATALOG_CRONS_ENABLED",
)


def test_settings_gone() -> None:
    from src.core import settings

    left = [n for n in _GONE_SETTINGS if hasattr(settings, n)]
    left += [n for n in dir(settings) if n.startswith(("SHELF_ENRICHMENT_", "ENGINE", "MATCHER_"))]
    assert not left, f"sourcing-era settings still defined: {left}"


def test_env_example_gone() -> None:
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    left = [n for n in _GONE_SETTINGS if re.search(rf"^\s*#?\s*{n}=", text, re.M)]
    assert not left, f".env.example still documents: {left}"


# ── R7: deps ────────────────────────────────────────────────────────────────
def test_deps_gone() -> None:
    text = (BACKEND / "pyproject.toml").read_text(encoding="utf-8")
    for dep in ("arq", "python-jobspy"):
        assert not re.search(rf'"{dep}[\s>=<\[]', text) and f'"{dep}"' not in text, dep
    assert "indeed" not in text


# ── R3: bring stores, never scores ──────────────────────────────────────────
_AD = {
    "title": "Senior Python Engineer",
    "company": "Acme Robotics Ltd",
    "location": "Berlin, Germany",
    "apply_url": "https://acme.example/jobs/42",
    "description": (
        "We build warehouse robots. You will own the Python services that "
        "schedule fleets. Requirements: 5+ years Python, FastAPI, Postgres. "
        "Hybrid, 3 days in the Berlin office. Salary EUR 90,000 - 110,000."
    ),
}
_SCORE_FIELDS = {
    "match_score", "role", "skill", "location_score", "recency", "seniority_score",
    "salary_score", "visa_score", "workplace_score", "enrichment_applied",
}


@pytest.mark.asyncio
async def test_bring_stores_without_scoring(authenticated_async_context):
    from src.api import dependencies as api_deps

    async with authenticated_async_context() as client:
        resp = await client.post("/api/jobs/bring", json=_AD)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "scored" not in body
        assert body["existing"] is False
        assert body["status"] == "considering"
        assert isinstance(body["application_id"], int)
        job = body["job"]
        leaked = _SCORE_FIELDS & set(job)
        assert not leaked, f"bring still returns a score: {sorted(leaked)}"
        assert job["source"] == "user_brought"
        assert job["description"] == _AD["description"]

        detail = await client.get(f"/api/applications/{body['application_id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["job_id"] == job["id"]

    db = await api_deps.get_db()
    cur = await db._conn.execute("SELECT source FROM jobs WHERE id = ?", (job["id"],))
    assert (await cur.fetchone())[0] == "user_brought"
    # `user_feed` — the scorer's per-user feed row bring used to be checked
    # against — was itself dropped by the mission-sweep migration (0040): the
    # table no longer exists, so "bring writes no feed row" now holds by
    # construction rather than by query.


# ── R4: profile ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_profile_has_no_search_titles(authenticated_async_context):
    from src.api.routes import profile as profile_route

    assert not hasattr(profile_route, "_maybe_trigger_rescore")
    assert not hasattr(profile_route, "_run_rescore_in_process")
    from src.api.models import ProfileResponse

    assert "search_titles" not in ProfileResponse.model_fields
    # A fresh user has no profile row: the route 404s by design
    # (test_mcp_server pins that). The contract is the schema, not the row.
    async with authenticated_async_context() as client:
        resp = await client.get("/api/profile")
        assert resp.status_code in (200, 404), resp.text
        assert "search_titles" not in resp.json()
        schema = (await client.get("/openapi.json")).json()
        assert "search_titles" not in str(schema["components"]["schemas"]["ProfileResponse"])


# ── R6: migration 0039 ──────────────────────────────────────────────────────
_DROPPED = ("run_log", "job_enrichment", "job_embeddings")


@pytest.mark.asyncio
async def test_migration_0039_up_down_up(migrated_db_path):
    from migrations import runner
    from src.repositories import pg as _pg

    db_path = migrated_db_path
    # Resolve the migration by NAME, not by number: three open slices shared
    # the 0038 slot and each rebase renumbers, and any later migration would
    # otherwise turn this test red the day it lands.
    stems = sorted(s for s, _ in runner._discover_pairs())
    drop_stem = next(s for s in stems if s.endswith("_drop_sourcing_tables"))
    later = stems[stems.index(drop_stem) + 1 :]

    async def _has_table(name: str) -> bool:
        async with _pg.connect(db_path) as db:
            cur = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (name,)
            )
            return (await cur.fetchone()) is not None

    for t in _DROPPED:
        assert not await _has_table(t), f"{t} present after the fixture's up()"
    # `user_feed` and `user_actions` used to be on this survivor list — 0039
    # deliberately did not touch them, but they did not survive FOREVER: the
    # mission-sweep migration (0040) drops both, and this fixture applies every
    # migration up to head before this test's own down/up walk even starts.
    for t in ("jobs", "applications", "application_events"):
        assert await _has_table(t), f"{t} must survive 0039"

    # Walk down through anything newer first, then through the drop itself.
    for expected in reversed(later):
        assert await runner.down(db_path) == expected
    assert await runner.down(db_path) == drop_stem
    for t in _DROPPED:
        assert await _has_table(t), f"{t} not recreated by down()"

    assert await runner.up(db_path) == [drop_stem, *later]
    for t in _DROPPED:
        assert not await _has_table(t), f"{t} present after re-up()"

    up_sql = (BACKEND / "migrations" / f"{drop_stem}.up.sql").read_text(encoding="utf-8")
    drops = re.findall(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(\w+)", up_sql, re.I)
    assert sorted(drops) == sorted(_DROPPED), f"0039 drops {drops}"


# R8 (MCP tool set unchanged) is pinned by tests/test_mcp_server.py::EXPECTED_TOOLS
# and tests/test_mcp_gate_parity.py — both turn red if a tool appears or goes.
