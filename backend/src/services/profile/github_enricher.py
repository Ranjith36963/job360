"""Fetch GitHub public data to infer skills from repos, languages, topics, and dependency files.

Batch 1.2 (Pillar 1) adds two signals to the original language+topic
inference:

* **Dependency-file parsing** — fetches ``package.json`` /
  ``requirements.txt`` / ``pyproject.toml`` / ``Cargo.toml`` / ``Gemfile``
  / ``go.mod`` / ``composer.json`` via the GitHub Contents API, runs
  each through ``dep_file_parser``, and maps dep names to skills via
  ``dependency_map.lookup_skill``. Typically yields 3-5× more skills
  than language-only inference because frameworks (React, Django,
  Laravel) don't map 1:1 to a language.

* **Temporal weighting** — repos pushed within the last 12 months
  contribute 3× their code-bytes to the ranking. This pushes "what
  the user is currently doing" above "what they did in 2019".
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

_GITHUB_USERNAME_RE = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?$')

from src.core.settings import GITHUB_TOKEN
from src.services.profile.dep_file_parser import MANIFEST_FILES, parse_manifest
from src.services.profile.dependency_map import lookup_skill
from src.services.profile.models import CVData

logger = logging.getLogger("job360.profile.github")

GITHUB_API = "https://api.github.com"
MAX_REPOS = 30
# Temporal weighting constants (plan §4.2 — "repos pushed within 12 months → ×3")
RECENT_WINDOW_DAYS = 365
RECENT_REPO_MULTIPLIER = 3
# Dep-file parsing is I/O heavy (7 files × N repos). Cap the repo count
# we probe to stay within GitHub's 60 unauthenticated / 5000 authenticated
# requests-per-hour budget. Authenticated runs comfortably cover this.
MAX_REPOS_FOR_DEPS = 10


# Map GitHub language names to skill names
LANGUAGE_TO_SKILL: dict[str, str] = {
    "Python": "Python",
    "JavaScript": "JavaScript",
    "TypeScript": "TypeScript",
    "Java": "Java",
    "C#": "C#",
    "C++": "C++",
    "C": "C",
    "Go": "Go",
    "Rust": "Rust",
    "Ruby": "Ruby",
    "PHP": "PHP",
    "Swift": "Swift",
    "Kotlin": "Kotlin",
    "Scala": "Scala",
    "R": "R",
    "Dart": "Dart",
    "Lua": "Lua",
    "Shell": "Shell Scripting",
    "PowerShell": "PowerShell",
    "Perl": "Perl",
    "Haskell": "Haskell",
    "Elixir": "Elixir",
    "Clojure": "Clojure",
    "Jupyter Notebook": "Jupyter",
    "HCL": "Terraform",
    "Dockerfile": "Docker",
    "Nix": "Nix",
    "Vue": "Vue.js",
    "SCSS": "CSS/SCSS",
    "CSS": "CSS",
    "HTML": "HTML",
}


# Map GitHub topic tags to skill names
TOPIC_TO_SKILL: dict[str, str] = {
    "react": "React",
    "reactjs": "React",
    "nextjs": "Next.js",
    "angular": "Angular",
    "vue": "Vue.js",
    "svelte": "Svelte",
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "express": "Express.js",
    "nodejs": "Node.js",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "terraform": "Terraform",
    "aws": "AWS",
    "azure": "Azure",
    "gcp": "GCP",
    "machine-learning": "Machine Learning",
    "deep-learning": "Deep Learning",
    "natural-language-processing": "NLP",
    "nlp": "NLP",
    "computer-vision": "Computer Vision",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "data-science": "Data Science",
    "graphql": "GraphQL",
    "rest-api": "REST API",
    "postgresql": "PostgreSQL",
    "mongodb": "MongoDB",
    "redis": "Redis",
    "elasticsearch": "Elasticsearch",
    "ci-cd": "CI/CD",
    "github-actions": "GitHub Actions",
    "pandas": "Pandas",
    "numpy": "NumPy",
    "scikit-learn": "scikit-learn",
    "spark": "Apache Spark",
    "kafka": "Apache Kafka",
    "airflow": "Apache Airflow",
    "sql": "SQL",
    "devops": "DevOps",
    "web-scraping": "Web Scraping",
    "automation": "Automation",
}


def _headers() -> dict[str, str]:
    """Build request headers, with optional auth token."""
    h = {
        "Accept": "application/vnd.github.mercy-preview+json",
        "User-Agent": "Job360/1.0",
    }
    if GITHUB_TOKEN:
        h["Authorization"] = f"token {GITHUB_TOKEN}"
    return h


async def _get_json(session: aiohttp.ClientSession, url: str) -> Any:
    """GET a GitHub API endpoint and return parsed JSON."""
    try:
        async with session.get(url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 403:
                logger.warning("GitHub API rate limited")
                return None
            if resp.status != 200:
                logger.warning("GitHub API %s for %s", resp.status, url)
                return None
            return await resp.json()
    except Exception as e:
        logger.warning("GitHub API error: %s", e)
        return None


def _is_recent(pushed_at: str | None, now: datetime | None = None) -> bool:
    """Return True if ``pushed_at`` is within the last ``RECENT_WINDOW_DAYS``.

    Accepts the ISO-8601 string GitHub returns (``2025-03-14T12:00:00Z``).
    Unparseable / missing timestamps return False — i.e. we do not grant
    the recency bonus on uncertainty. The ``now`` param is a hook for
    deterministic unit tests.
    """
    if not pushed_at:
        return False
    try:
        dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    return (now - dt) <= timedelta(days=RECENT_WINDOW_DAYS)


async def _fetch_dep_file(
    session: aiohttp.ClientSession, username: str, repo_name: str, path: str
) -> str | None:
    """Fetch a single manifest file via the GitHub Contents API.

    Returns the **decoded** file content (base64-decoded to UTF-8) or
    ``None`` on 404 (file absent — the common case) / 403 (rate limit)
    / malformed payload. Silent on absence by design: most repos will
    have only 1-2 of the 7 manifests we probe, and 404s are expected.
    """
    url = f"{GITHUB_API}/repos/{username}/{repo_name}/contents/{path}"
    try:
        async with session.get(url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 404:
                return None
            if resp.status == 403:
                logger.warning("GitHub API rate limited on contents fetch")
                return None
            if resp.status != 200:
                return None
            payload = await resp.json()
    except Exception as e:
        logger.debug("Contents fetch %s/%s/%s failed: %s", username, repo_name, path, e)
        return None

    if not isinstance(payload, dict):
        return None
    # Files >1MB return empty content + download_url instead; we skip those
    # rather than follow the download_url (avoids a second hop for what is
    # almost certainly a vendored lockfile, not a hand-authored manifest).
    encoded = payload.get("content")
    encoding = payload.get("encoding", "base64")
    if encoding != "base64" or not isinstance(encoded, str) or not encoded:
        return None
    try:
        return base64.b64decode(encoded).decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        logger.debug("Contents base64 decode failed for %s/%s/%s: %s", username, repo_name, path, e)
        return None


async def _fetch_repo_frameworks(
    session: aiohttp.ClientSession, username: str, repo_name: str
) -> list[str]:
    """Probe all 7 manifest filenames for a single repo and return mapped skills.

    Runs the 7 contents-API fetches in parallel. Unmapped dependencies
    (i.e. not in ``dependency_map``) are dropped rather than returned
    as bare names — we only care about *recognisable* frameworks for
    skill inference. Aggregation + dedup happens at the caller.
    """
    fetches = [
        _fetch_dep_file(session, username, repo_name, filename)
        for filename, _ in MANIFEST_FILES
    ]
    contents = await asyncio.gather(*fetches, return_exceptions=True)

    skills: list[str] = []
    seen: set[str] = set()
    for (filename, _), content in zip(MANIFEST_FILES, contents):
        if isinstance(content, Exception) or not content:
            continue
        ecosystem, dep_names = parse_manifest(filename, content)
        for dep in dep_names:
            skill = lookup_skill(ecosystem, dep)
            if skill and skill.lower() not in seen:
                skills.append(skill)
                seen.add(skill.lower())
    return skills


async def fetch_github_profile(
    username: str, session: aiohttp.ClientSession | None = None
) -> dict:
    """Fetch public repos, languages, topics, and framework dependencies.

    Returns a dict with keys:
      ``repositories`` — raw repo list (name, language, description, stars, topics, pushed_at)
      ``languages``    — merged language → bytes map (temporally weighted)
      ``topics``       — sorted list of topic tags across all repos
      ``skills_inferred`` — language + topic → skill list (Batch 1 pre-deps signal)
      ``frameworks_inferred`` — dependency-file → skill list (Batch 1.2)
    """
    empty = {
        "repositories": [],
        "languages": {},
        "topics": [],
        "skills_inferred": [],
        "frameworks_inferred": [],
    }
    if not _GITHUB_USERNAME_RE.match(username):
        logger.warning("Invalid GitHub username format: %s", username)
        return empty

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    try:
        repos_url = f"{GITHUB_API}/users/{username}/repos?per_page={MAX_REPOS}&sort=pushed"
        repos_data = await _get_json(session, repos_url)
        if not repos_data or not isinstance(repos_data, list):
            return empty

        repositories: list[dict] = []
        all_topics: set[str] = set()

        for repo in repos_data:
            if repo.get("fork"):
                continue
            repo_info = {
                "name": repo.get("name", ""),
                "language": repo.get("language", ""),
                "description": repo.get("description", "") or "",
                "stars": repo.get("stargazers_count", 0),
                "topics": repo.get("topics", []),
                "pushed_at": repo.get("pushed_at"),
            }
            repositories.append(repo_info)
            all_topics.update(repo.get("topics", []))

        # Fetch per-repo language breakdown for top 20 repos with temporal weight.
        # We keep a per-repo map so the recency multiplier can be applied
        # per-repo *before* aggregation, not after.
        top_for_languages = repositories[:20]
        lang_tasks = [
            _get_json(
                session,
                f"{GITHUB_API}/repos/{username}/{repo['name']}/languages",
            )
            for repo in top_for_languages
        ]
        lang_results = await asyncio.gather(*lang_tasks, return_exceptions=True)

        weighted_languages: dict[str, int] = {}
        for repo, result in zip(top_for_languages, lang_results):
            if isinstance(result, Exception) or not isinstance(result, dict):
                continue
            weight = RECENT_REPO_MULTIPLIER if _is_recent(repo.get("pushed_at")) else 1
            for lang, bytes_count in result.items():
                weighted_languages[lang] = weighted_languages.get(lang, 0) + int(bytes_count) * weight

        # Batch 1.2 — dep-file parsing across the top N repos (network-heavy).
        dep_tasks = [
            _fetch_repo_frameworks(session, username, repo["name"])
            for repo in repositories[:MAX_REPOS_FOR_DEPS]
        ]
        dep_results = await asyncio.gather(*dep_tasks, return_exceptions=True)
        frameworks_inferred: list[str] = []
        seen_framework: set[str] = set()
        for result in dep_results:
            if isinstance(result, Exception):
                logger.debug("Dep fetch failed: %s", result)
                continue
            for skill in result or []:
                if skill.lower() not in seen_framework:
                    frameworks_inferred.append(skill)
                    seen_framework.add(skill.lower())

        skills_inferred = _infer_skills(weighted_languages, all_topics)

        return {
            "repositories": repositories,
            "languages": weighted_languages,
            "topics": sorted(all_topics),
            "skills_inferred": skills_inferred,
            "frameworks_inferred": frameworks_inferred,
        }
    finally:
        if own_session:
            await session.close()


def _infer_skills(languages: dict[str, int], topics: set[str]) -> list[str]:
    """Map languages and topics to skill names, ranked by (weighted) code bytes."""
    seen: set[str] = set()
    skills: list[str] = []

    for lang, _ in sorted(languages.items(), key=lambda x: x[1], reverse=True):
        skill = LANGUAGE_TO_SKILL.get(lang)
        if skill and skill.lower() not in seen:
            skills.append(skill)
            seen.add(skill.lower())

    for topic in sorted(topics):
        skill = TOPIC_TO_SKILL.get(topic)
        if skill and skill.lower() not in seen:
            skills.append(skill)
            seen.add(skill.lower())

    return skills


def enrich_cv_from_github(cv: CVData, github_data: dict) -> CVData:
    """Merge GitHub-inferred skills into CVData, deduplicating.

    Batch 1.2 — also writes ``github_frameworks`` from
    ``frameworks_inferred`` with dedup against existing CV skills AND
    the language/topic-derived skills, so the same framework never
    appears twice in a downstream SearchConfig.
    """
    seen_skills = {s.lower() for s in cv.skills}

    new_github_skills: list[str] = []
    for s in github_data.get("skills_inferred", []):
        if s.lower() not in seen_skills:
            new_github_skills.append(s)
            seen_skills.add(s.lower())

    new_frameworks: list[str] = []
    for s in github_data.get("frameworks_inferred", []):
        if s.lower() not in seen_skills:
            new_frameworks.append(s)
            seen_skills.add(s.lower())

    cv.github_languages = github_data.get("languages", {})
    cv.github_topics = github_data.get("topics", [])
    cv.github_skills_inferred = new_github_skills
    cv.github_frameworks = new_frameworks

    return cv
