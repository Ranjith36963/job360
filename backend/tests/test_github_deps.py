"""Batch 1.2 (Pillar 1) — GitHub dependency-file parsing + temporal weighting tests.

Covers:
  * dep_file_parser — one test per manifest format + malformed-input fallback
  * dependency_map — ecosystem-scoped lookup, case-insensitivity, total count guard
  * github_enricher — temporal weighting, frameworks_inferred plumbing, enrich_cv merge
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.profile import dep_file_parser, dependency_map, github_enricher
from src.services.profile.models import CVData


# ── dep_file_parser — per-format ────────────────────────────────────


def test_parse_package_json_all_sections():
    content = """
    {
      "name": "demo",
      "dependencies": {"react": "^18.2.0", "next": "14.0.0"},
      "devDependencies": {"typescript": "^5.0.0", "vitest": "^1.0.0"},
      "peerDependencies": {"react-dom": "^18.2.0"}
    }
    """
    assert dep_file_parser.parse_package_json(content) == {
        "react", "next", "typescript", "vitest", "react-dom"
    }


def test_parse_package_json_malformed_returns_empty():
    assert dep_file_parser.parse_package_json("{ not json") == set()
    assert dep_file_parser.parse_package_json("null") == set()
    assert dep_file_parser.parse_package_json('"just a string"') == set()


def test_parse_requirements_txt_strips_versions_and_extras():
    content = """
    # A comment line
    django==4.2.1
    flask>=2.0,<3.0
    uvicorn[standard]>=0.30
    fastapi ; python_version >= "3.9"

    -r nested.txt
    -e git+https://github.com/x/y.git
    pandas
    """
    names = dep_file_parser.parse_requirements_txt(content)
    assert "django" in names
    assert "flask" in names
    assert "uvicorn" in names
    assert "fastapi" in names
    assert "pandas" in names
    # nested requires / editable installs skipped
    assert "nested.txt" not in names
    assert "git+https://github.com/x/y.git" not in names


def test_parse_pyproject_toml_pep621():
    content = """
[project]
name = "demo"
dependencies = [
    "fastapi>=0.115.0",
    "pydantic>=2.0",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff"]
"""
    names = dep_file_parser.parse_pyproject_toml(content)
    assert "fastapi" in names
    assert "pydantic" in names
    assert "httpx" in names
    assert "pytest" in names
    assert "ruff" in names


def test_parse_pyproject_toml_poetry():
    content = """
[tool.poetry.dependencies]
python = "^3.11"
django = "^4.2"
celery = {version = "^5.3", extras = ["redis"]}

[tool.poetry.group.dev.dependencies]
pytest = "^8.0"
"""
    names = dep_file_parser.parse_pyproject_toml(content)
    assert "django" in names
    assert "celery" in names
    assert "pytest" in names
    assert "python" not in names  # we exclude the python floor


def test_parse_cargo_toml():
    content = """
[package]
name = "demo"

[dependencies]
tokio = { version = "1", features = ["full"] }
serde = "1.0"
axum = "0.7"

[dev-dependencies]
mockall = "0.12"
"""
    names = dep_file_parser.parse_cargo_toml(content)
    assert {"tokio", "serde", "axum", "mockall"} <= names


def test_parse_gemfile():
    content = """
source 'https://rubygems.org'
gem 'rails', '~> 7.0'
gem "devise"
gem 'sidekiq', '>= 7.0'
# gem 'commented-out'
"""
    names = dep_file_parser.parse_gemfile(content)
    assert {"rails", "devise", "sidekiq"} == names


def test_parse_go_mod():
    content = """
module example.com/demo

go 1.21

require (
    github.com/gin-gonic/gin v1.9.1
    github.com/go-redis/redis v8.11.5
    // a comment line inside require
    gorm.io/gorm v1.25.5
)

require github.com/spf13/cobra v1.7.0
"""
    names = dep_file_parser.parse_go_mod(content)
    assert "github.com/gin-gonic/gin" in names
    assert "github.com/go-redis/redis" in names
    assert "gorm.io/gorm" in names
    assert "github.com/spf13/cobra" in names


def test_parse_composer_json():
    content = """
    {
      "require": {
        "php": ">=8.1",
        "laravel/framework": "^10.0",
        "guzzlehttp/guzzle": "^7.0"
      },
      "require-dev": {
        "phpunit/phpunit": "^10.0"
      }
    }
    """
    names = dep_file_parser.parse_composer_json(content)
    assert {"laravel/framework", "guzzlehttp/guzzle", "phpunit/phpunit"} == names
    assert "php" not in names


def test_parse_manifest_dispatcher_all_filenames():
    for filename, expected_ecosystem in dep_file_parser.MANIFEST_FILES:
        ecosystem, _ = dep_file_parser.parse_manifest(filename, "")
        assert ecosystem == expected_ecosystem


def test_parse_manifest_unknown_filename():
    ecosystem, deps = dep_file_parser.parse_manifest("README.md", "## Hello")
    assert ecosystem == "unknown"
    assert deps == set()


# ── dependency_map — lookup + guard ─────────────────────────────────


def test_dependency_map_lookup_hit():
    assert dependency_map.lookup_skill("pypi", "fastapi") == "FastAPI"
    assert dependency_map.lookup_skill("npm", "react") == "React"
    assert dependency_map.lookup_skill("cargo", "tokio") == "Tokio"


def test_dependency_map_lookup_case_insensitive():
    assert dependency_map.lookup_skill("npm", "REACT") == "React"
    assert dependency_map.lookup_skill("pypi", "Django") == "Django"


def test_dependency_map_lookup_miss_returns_none():
    assert dependency_map.lookup_skill("pypi", "not-a-real-pkg-9999") is None
    assert dependency_map.lookup_skill("unknown_ecosystem", "react") is None


def test_dependency_map_total_mappings_floor():
    # Guard against accidental deletions. Plan §4.2 targets ≥200 entries.
    assert dependency_map.total_mappings() >= 200


# ── github_enricher — temporal weighting ────────────────────────────


def test_is_recent_true_within_window():
    recent = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    assert github_enricher._is_recent(recent) is True


def test_is_recent_false_outside_window():
    old = (datetime.now(timezone.utc) - timedelta(days=500)).isoformat().replace("+00:00", "Z")
    assert github_enricher._is_recent(old) is False


def test_is_recent_none_or_unparseable():
    assert github_enricher._is_recent(None) is False
    assert github_enricher._is_recent("") is False
    assert github_enricher._is_recent("not-a-date") is False


def test_is_recent_accepts_now_injection():
    frozen = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert github_enricher._is_recent("2024-09-01T00:00:00Z", now=frozen) is True
    assert github_enricher._is_recent("2023-01-01T00:00:00Z", now=frozen) is False


# ── github_enricher — fetch + temporal weighting integration ────────


def _make_async_session():
    """Return an AsyncMock that emulates ``aiohttp.ClientSession`` — ``close()`` is awaitable."""
    return AsyncMock()


def _passthrough_cm(resp_status: int, resp_json: dict | list | None):
    """Build a MagicMock behaving like aiohttp's async context manager on ``session.get``."""
    response = MagicMock()
    response.status = resp_status
    response.json = AsyncMock(return_value=resp_json)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


@pytest.mark.asyncio
async def test_fetch_github_profile_weights_recent_repos_above_old():
    """Recent repo's language must outrank an older repo with higher raw bytes."""
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    old_iso = (datetime.now(timezone.utc) - timedelta(days=500)).isoformat().replace("+00:00", "Z")

    repos = [
        {"name": "new-repo", "language": "Rust", "description": "", "stargazers_count": 0,
         "topics": [], "pushed_at": now_iso, "fork": False},
        {"name": "old-repo", "language": "Python", "description": "", "stargazers_count": 0,
         "topics": [], "pushed_at": old_iso, "fork": False},
    ]

    async def fake_get_json(session, url):
        if url.endswith("/repos?per_page=30&sort=pushed"):
            return repos
        if "new-repo/languages" in url:
            return {"Rust": 10_000}
        if "old-repo/languages" in url:
            return {"Python": 25_000}  # more bytes; should lose to Rust after ×3
        return None

    fake_session = _make_async_session()
    with patch("src.services.profile.github_enricher._get_json", side_effect=fake_get_json), \
         patch("src.services.profile.github_enricher._fetch_repo_frameworks",
               new=AsyncMock(return_value=[])), \
         patch("src.services.profile.github_enricher.aiohttp.ClientSession", return_value=fake_session):
        result = await github_enricher.fetch_github_profile("alice")

    skills = result["skills_inferred"]
    assert skills[0] == "Rust", f"Expected Rust first but got {skills}"
    assert "Python" in skills


@pytest.mark.asyncio
async def test_fetch_github_profile_aggregates_frameworks():
    """Frameworks from multiple repos should be deduped + present in ``frameworks_inferred``."""
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    repos = [
        {"name": "api", "language": "Python", "description": "", "stargazers_count": 0,
         "topics": [], "pushed_at": now_iso, "fork": False},
        {"name": "web", "language": "TypeScript", "description": "", "stargazers_count": 0,
         "topics": [], "pushed_at": now_iso, "fork": False},
    ]

    async def fake_get_json(session, url):
        if url.endswith("/repos?per_page=30&sort=pushed"):
            return repos
        if "/languages" in url:
            return {"Python": 1} if "api" in url else {"TypeScript": 1}
        return None

    async def fake_frameworks(session, username, repo_name):
        if repo_name == "api":
            return ["FastAPI", "Pydantic"]
        return ["React", "Next.js", "FastAPI"]  # FastAPI dupe across repos

    fake_session = _make_async_session()
    with patch("src.services.profile.github_enricher._get_json", side_effect=fake_get_json), \
         patch("src.services.profile.github_enricher._fetch_repo_frameworks",
               side_effect=fake_frameworks), \
         patch("src.services.profile.github_enricher.aiohttp.ClientSession", return_value=fake_session):
        result = await github_enricher.fetch_github_profile("bob")

    frameworks = result["frameworks_inferred"]
    assert frameworks.count("FastAPI") == 1
    assert set(frameworks) == {"FastAPI", "Pydantic", "React", "Next.js"}


@pytest.mark.asyncio
async def test_fetch_repo_frameworks_parses_real_manifest_content():
    """Smoke test: given a real requirements.txt payload the helper yields mapped skills."""
    content = "fastapi>=0.115\npydantic>=2.0\nuvicorn"
    encoded = base64.b64encode(content.encode()).decode()
    contents_payload = {"content": encoded, "encoding": "base64"}

    def fake_get(url, **kwargs):
        # Only requirements.txt returns 200 + content; other 6 manifests 404.
        status = 200 if url.endswith("/requirements.txt") else 404
        json_payload = contents_payload if status == 200 else {}
        return _passthrough_cm(status, json_payload)

    session = MagicMock()
    session.get = MagicMock(side_effect=fake_get)

    skills = await github_enricher._fetch_repo_frameworks(session, "alice", "repo")

    assert "FastAPI" in skills
    assert "Pydantic" in skills
    assert "Uvicorn" in skills


# ── enrich_cv_from_github — new framework field ─────────────────────


def test_enrich_cv_from_github_writes_frameworks_without_duplicating():
    cv = CVData(skills=["python", "FastAPI"])  # user already declared FastAPI
    github_data = {
        "skills_inferred": ["Rust", "Python"],       # Python dupe of user's python
        "frameworks_inferred": ["FastAPI", "React"],  # FastAPI dupe of user's
        "languages": {"Python": 1},
        "topics": ["rust"],
    }
    cv = github_enricher.enrich_cv_from_github(cv, github_data)

    assert cv.github_frameworks == ["React"]           # FastAPI deduped
    assert "Python" not in cv.github_skills_inferred   # python/case dupe
    assert "Rust" in cv.github_skills_inferred


def test_enrich_cv_empty_github_data_leaves_cv_clean():
    cv = CVData(skills=["Java"])
    cv = github_enricher.enrich_cv_from_github(cv, {})
    assert cv.github_frameworks == []
    assert cv.github_skills_inferred == []
