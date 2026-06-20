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

from src.services.profile import dep_file_parser, github_enricher
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


# NOTE (CLAUDE.md rule #28): dependency_map (hardcoded dep-name→skill) was retired.
# _fetch_repo_frameworks now returns raw dependency names; the LLM pass canonicalises.


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


# ── github_enricher — username normalisation (URL / @handle / bare) ─


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://github.com/torvalds", "torvalds"),
        ("http://github.com/torvalds", "torvalds"),
        ("github.com/torvalds", "torvalds"),
        ("www.github.com/torvalds", "torvalds"),
        ("github.com/torvalds/linux", "torvalds"),
        ("https://github.com/torvalds/?tab=repositories", "torvalds"),
        ("https://github.com/torvalds/", "torvalds"),
        ("@torvalds", "torvalds"),
        ("torvalds", "torvalds"),
        ("torvalds/linux", "torvalds"),
        ("  torvalds  ", "torvalds"),
        ("https://github.com/foo-bar", "foo-bar"),
        ("", ""),
    ],
)
def test_normalize_github_username(raw, expected):
    assert github_enricher.normalize_github_username(raw) == expected


def test_normalize_github_username_non_string():
    assert github_enricher.normalize_github_username(None) == ""


@pytest.mark.asyncio
async def test_fetch_github_profile_accepts_full_url():
    """A pasted profile URL is normalised to the username before the API call."""
    captured: dict = {}

    async def fake_get_json(session, url):
        captured["url"] = url
        return []  # empty repo list → early return with empty payload

    with patch.object(github_enricher, "_get_json", side_effect=fake_get_json):
        result = await github_enricher.fetch_github_profile(
            "https://github.com/torvalds", session=_make_async_session()
        )

    assert "users/torvalds/repos" in captured["url"]
    assert result["repositories"] == []


# ── github_enricher — fetch + temporal weighting integration ────────


def _make_async_session():
    """Return an AsyncMock that emulates ``aiohttp.ClientSession`` — ``close()`` is awaitable."""
    return AsyncMock()


# ── github_enricher — username normalisation (URL / @handle / bare) ─


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://github.com/torvalds", "torvalds"),
        ("http://github.com/torvalds", "torvalds"),
        ("github.com/torvalds", "torvalds"),
        ("www.github.com/torvalds", "torvalds"),
        ("github.com/torvalds/linux", "torvalds"),
        ("https://github.com/torvalds/?tab=repositories", "torvalds"),
        ("https://github.com/torvalds/", "torvalds"),
        ("@torvalds", "torvalds"),
        ("torvalds", "torvalds"),
        ("torvalds/linux", "torvalds"),
        ("  torvalds  ", "torvalds"),
        ("https://github.com/foo-bar", "foo-bar"),
        ("", ""),
    ],
)
def test_normalize_github_username(raw, expected):
    assert github_enricher.normalize_github_username(raw) == expected


def test_normalize_github_username_non_string():
    assert github_enricher.normalize_github_username(None) == ""


# NOTE (CLAUDE.md rule #28): tests for the hardcoded dev-tooling denylist and the
# description-term scanner were removed along with those functions — GitHub skill
# semantics now come from raw API signals + the LLM pass, not hardcoded keyword lists.


@pytest.mark.asyncio
async def test_unauthenticated_caps_request_footprint(monkeypatch):
    """With no GITHUB_TOKEN, the language + dep-file probes are capped so a
    single fetch fits the 60 req/hr unauthenticated budget (≈48 calls)."""
    monkeypatch.setattr(github_enricher, "GITHUB_TOKEN", "")
    repos = [
        {"name": f"r{i}", "language": "Python", "description": "d",
         "stargazers_count": 0, "topics": [], "pushed_at": None, "fork": False}
        for i in range(30)
    ]
    lang_calls, dep_calls = [], []

    async def fake_get_json(session, url):
        if url.endswith("repos?per_page=30&sort=pushed"):
            return repos
        if "/languages" in url:
            lang_calls.append(url)
            return {"Python": 1}
        return None

    async def fake_fw(session, user, repo):
        dep_calls.append(repo)
        return []

    with patch.object(github_enricher, "_get_json", side_effect=fake_get_json), \
         patch.object(github_enricher, "_fetch_repo_frameworks", side_effect=fake_fw), \
         patch.object(github_enricher.aiohttp, "ClientSession", return_value=_make_async_session()):
        await github_enricher.fetch_github_profile("alice")

    assert len(lang_calls) <= 12, f"language probes not capped: {len(lang_calls)}"
    assert len(dep_calls) <= 5, f"dep probes not capped: {len(dep_calls)}"
    assert 1 + len(lang_calls) + len(dep_calls) * 7 < 60


@pytest.mark.asyncio
async def test_authenticated_keeps_full_coverage(monkeypatch):
    """With a token (5000/hr) the fuller probe is preserved (up to 20 langs)."""
    monkeypatch.setattr(github_enricher, "GITHUB_TOKEN", "tok123")
    repos = [
        {"name": f"r{i}", "language": "Python", "description": "d",
         "stargazers_count": 0, "topics": [], "pushed_at": None, "fork": False}
        for i in range(30)
    ]
    lang_calls = []

    async def fake_get_json(session, url):
        if url.endswith("repos?per_page=30&sort=pushed"):
            return repos
        if "/languages" in url:
            lang_calls.append(url)
            return {"Python": 1}
        return None

    with patch.object(github_enricher, "_get_json", side_effect=fake_get_json), \
         patch.object(github_enricher, "_fetch_repo_frameworks", new=AsyncMock(return_value=[])), \
         patch.object(github_enricher.aiohttp, "ClientSession", return_value=_make_async_session()):
        await github_enricher.fetch_github_profile("alice")

    assert len(lang_calls) == 20


@pytest.mark.asyncio
async def test_fetch_github_profile_accepts_full_url():
    """A pasted profile URL is normalised to the username before the API call."""
    captured: dict = {}

    async def fake_get_json(session, url):
        captured["url"] = url
        return []  # empty repo list → early return with empty payload

    with patch.object(github_enricher, "_get_json", side_effect=fake_get_json):
        result = await github_enricher.fetch_github_profile(
            "https://github.com/torvalds", session=_make_async_session()
        )

    assert "users/torvalds/repos" in captured["url"]
    assert result["repositories"] == []


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


# ── GitHub LLM pass (Pass 2) — infer skills from repo prose ─────────


@pytest.mark.asyncio
async def test_llm_infer_github_skills_parses_skills():
    """The LLM reads repo name/description/topics and returns extra skills
    the hard-coded lookup table can't know (e.g. 'LangChain', 'RAG')."""
    seen = {}

    async def fake_llm(prompt, system=""):
        seen["prompt"] = prompt
        return {"skills": ["LangChain", "RAG", "Vector Search"]}

    repos_brief = [
        {"name": "rag-bot", "description": "A retrieval bot built with langchain", "topics": ["llm"]},
    ]
    with patch("src.services.profile.llm_provider.llm_extract", new=fake_llm):
        skills = await github_enricher.llm_infer_github_skills(repos_brief)

    assert "rag-bot" in seen["prompt"]  # repo data reached the prompt
    assert "LangChain" in skills and "RAG" in skills


@pytest.mark.asyncio
async def test_llm_infer_github_skills_empty_input_skips_llm():
    """No repos → return [] without ever calling the LLM (cost guard)."""
    called = False

    async def fake_llm(prompt, system=""):
        nonlocal called
        called = True
        return {"skills": ["should-not-happen"]}

    with patch("src.services.profile.llm_provider.llm_extract", new=fake_llm):
        skills = await github_enricher.llm_infer_github_skills([])

    assert skills == []
    assert called is False


@pytest.mark.asyncio
async def test_llm_infer_github_skills_llm_failure_returns_empty():
    """Provider error must not crash the pass — returns [] (never raises)."""
    async def boom(prompt, system=""):
        raise RuntimeError("no LLM key configured")

    repos_brief = [{"name": "r", "description": "d", "topics": []}]
    with patch("src.services.profile.llm_provider.llm_extract", new=boom):
        skills = await github_enricher.llm_infer_github_skills(repos_brief)

    assert skills == []


@pytest.mark.asyncio
async def test_fetch_github_profile_includes_repos_brief():
    """fetch_github_profile exposes repos_brief (name/description/topics) so the
    LLM pass can re-run offline on a later profile change."""
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    repos = [
        {"name": "rag-bot", "language": "Python", "description": "RAG with langchain",
         "stargazers_count": 3, "topics": ["llm", "rag"], "pushed_at": now_iso, "fork": False},
    ]

    async def fake_get_json(session, url):
        if url.endswith("/repos?per_page=30&sort=pushed"):
            return repos
        if "/languages" in url:
            return {"Python": 1}
        return None

    fake_session = _make_async_session()
    with patch("src.services.profile.github_enricher._get_json", side_effect=fake_get_json), \
         patch("src.services.profile.github_enricher._fetch_repo_frameworks",
               new=AsyncMock(return_value=[])), \
         patch("src.services.profile.github_enricher.aiohttp.ClientSession", return_value=fake_session):
        result = await github_enricher.fetch_github_profile("alice")

    brief = result["repos_brief"]
    assert brief and brief[0]["name"] == "rag-bot"
    assert brief[0]["description"] == "RAG with langchain"
    assert "rag" in brief[0]["topics"]


def test_enrich_cv_from_github_stores_repos_brief():
    cv = CVData()
    github_data = {
        "languages": {}, "topics": [], "skills_inferred": [], "frameworks_inferred": [],
        "repos_brief": [{"name": "x", "description": "y", "topics": ["z"]}],
    }
    out = github_enricher.enrich_cv_from_github(cv, github_data)
    assert out.github_repos_brief == [{"name": "x", "description": "y", "topics": ["z"]}]


@pytest.mark.asyncio
async def test_fetch_repo_frameworks_parses_real_manifest_content():
    """Smoke test: given a real requirements.txt payload the helper yields the RAW
    declared dependency names (rule #28 — no dep->skill mapping)."""
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

    assert "fastapi" in skills
    assert "pydantic" in skills
    assert "uvicorn" in skills


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
