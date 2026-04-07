"""Fetch GitHub public data to infer skills from repos, languages, and topics."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import aiohttp

_GITHUB_USERNAME_RE = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?$')

from src.config.settings import GITHUB_TOKEN
from src.profile.models import CVData

logger = logging.getLogger("job360.profile.github")

GITHUB_API = "https://api.github.com"
MAX_REPOS = 30

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


async def fetch_github_profile(
    username: str, session: aiohttp.ClientSession | None = None
) -> dict:
    """Fetch public repos, languages, and topics for a GitHub user."""
    if not _GITHUB_USERNAME_RE.match(username):
        logger.warning("Invalid GitHub username format: %s", username)
        return {"repositories": [], "languages": {}, "topics": [], "skills_inferred": []}

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    try:
        # Fetch repos sorted by most recently pushed
        repos_url = f"{GITHUB_API}/users/{username}/repos?per_page={MAX_REPOS}&sort=pushed"
        repos_data = await _get_json(session, repos_url)
        if not repos_data or not isinstance(repos_data, list):
            return {"repositories": [], "languages": {}, "topics": [], "skills_inferred": []}

        repositories = []
        all_topics: set[str] = set()
        aggregated_languages: dict[str, int] = {}

        # Collect repo info and topics
        for repo in repos_data:
            if repo.get("fork"):
                continue
            repo_info = {
                "name": repo.get("name", ""),
                "language": repo.get("language", ""),
                "description": repo.get("description", "") or "",
                "stars": repo.get("stargazers_count", 0),
                "topics": repo.get("topics", []),
            }
            repositories.append(repo_info)
            all_topics.update(repo.get("topics", []))

        # Fetch language breakdown for top repos (limit requests)
        lang_tasks = []
        for repo in repositories[:20]:
            url = f"{GITHUB_API}/repos/{username}/{repo['name']}/languages"
            lang_tasks.append(_get_json(session, url))

        lang_results = await asyncio.gather(*lang_tasks, return_exceptions=True)
        for result in lang_results:
            if isinstance(result, Exception):
                logger.debug("Language fetch failed: %s", result)
            elif isinstance(result, dict):
                for lang, bytes_count in result.items():
                    aggregated_languages[lang] = aggregated_languages.get(lang, 0) + bytes_count

        # Infer skills from languages and topics
        skills_inferred = _infer_skills(aggregated_languages, all_topics)

        return {
            "repositories": repositories,
            "languages": aggregated_languages,
            "topics": sorted(all_topics),
            "skills_inferred": skills_inferred,
        }
    finally:
        if own_session:
            await session.close()


def _infer_skills(languages: dict[str, int], topics: set[str]) -> list[str]:
    """Map languages and topics to skill names, ranked by code bytes."""
    seen: set[str] = set()
    skills: list[str] = []

    # Languages sorted by bytes (most used first)
    for lang, _ in sorted(languages.items(), key=lambda x: x[1], reverse=True):
        skill = LANGUAGE_TO_SKILL.get(lang)
        if skill and skill.lower() not in seen:
            skills.append(skill)
            seen.add(skill.lower())

    # Topics
    for topic in sorted(topics):
        skill = TOPIC_TO_SKILL.get(topic)
        if skill and skill.lower() not in seen:
            skills.append(skill)
            seen.add(skill.lower())

    return skills


def enrich_cv_from_github(cv: CVData, github_data: dict) -> CVData:
    """Merge GitHub-inferred skills into CVData, deduplicating."""
    seen_skills = {s.lower() for s in cv.skills}
    new_github_skills = []
    for s in github_data.get("skills_inferred", []):
        if s.lower() not in seen_skills:
            new_github_skills.append(s)
            seen_skills.add(s.lower())

    cv.github_languages = github_data.get("languages", {})
    cv.github_topics = github_data.get("topics", [])
    cv.github_skills_inferred = new_github_skills

    return cv
