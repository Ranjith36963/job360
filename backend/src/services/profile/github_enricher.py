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

from src.core.settings import GITHUB_TOKEN
from src.services.profile.dep_file_parser import MANIFEST_FILES, parse_manifest
from src.services.profile.models import CVData

_GITHUB_USERNAME_RE = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?$')


def normalize_github_username(raw: str) -> str:
    """Reduce any GitHub input to a bare username.

    Accepts what users actually paste — a full profile URL
    (``https://github.com/torvalds``), an ``@handle``, or the plain
    username — and returns just the username. The caller still runs
    ``_GITHUB_USERNAME_RE`` on the result, so junk input that doesn't
    reduce to a valid handle is rejected downstream (returns "").

    Examples:
      ``https://github.com/torvalds``       -> ``torvalds``
      ``github.com/torvalds/repo``          -> ``torvalds``
      ``www.github.com/torvalds?tab=repos`` -> ``torvalds``
      ``@torvalds``                         -> ``torvalds``
      ``torvalds``                          -> ``torvalds``
    """
    if not isinstance(raw, str):
        return ""
    s = raw.strip()
    if not s:
        return ""
    # Drop a leading @ that users copy from social handles.
    s = s.lstrip("@").strip()
    # If it mentions github.com, take the first path segment after it.
    # Tolerates http(s)://, www., a trailing path, query string, or fragment.
    match = re.search(r"github\.com/+([^/?#\s]+)", s, re.IGNORECASE)
    if match:
        return match.group(1)
    # Otherwise treat the whole thing as a handle, but still strip any
    # stray path/query so "torvalds/repo" -> "torvalds".
    return re.split(r"[/?#\s]", s, maxsplit=1)[0]


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


# NOTE (CLAUDE.md rule #28): the hardcoded LANGUAGE_TO_SKILL and TOPIC_TO_SKILL
# maps were removed. _infer_skills uses the raw GitHub language/topic strings;
# the LLM pass canonicalises their meaning.


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
    """Probe all 7 manifest filenames for a single repo and return the raw
    declared dependency names.

    CLAUDE.md rule #28: NO hardcoded dependency→skill map. We parse the manifest
    *structure* (still data — dep_file_parser) and return the dependency names
    verbatim; the LLM pass canonicalises which of them are recruiter-relevant
    skills. Aggregation + dedup happens at the caller.
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
        _ecosystem, dep_names = parse_manifest(filename, content)
        for dep in dep_names:
            d = (dep or "").strip()
            if d and d.lower() not in seen:
                skills.append(d)
                seen.add(d.lower())
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
        "repos_brief": [],
    }
    # Accept a full profile URL or @handle, not just a bare username.
    username = normalize_github_username(username)
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

        # Request-budget guard: unauthenticated GitHub allows only 60 req/hr,
        # and a full probe (1 + 20 languages + 10×7 dep-files ≈ 91) blows it,
        # so the later calls 403 and the result is empty. With no token we cap
        # the footprint (12 languages + 5×7 dep-files ≈ 48 < 60) so a fetch
        # reliably returns data. With a token (5000/hr) we keep full coverage.
        authed = bool(GITHUB_TOKEN)
        lang_n = 20 if authed else 12
        dep_n = MAX_REPOS_FOR_DEPS if authed else 5

        # Fetch per-repo language breakdown with temporal weight.
        # We keep a per-repo map so the recency multiplier can be applied
        # per-repo *before* aggregation, not after.
        top_for_languages = repositories[:lang_n]
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
            for repo in repositories[:dep_n]
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

        # Two-pass — compact repo briefs (name/description/topics) for the
        # LLM pass. Only repos with prose worth reading (a description or
        # topics) are kept, capped so the prompt stays small.
        repos_brief = [
            {
                "name": r["name"],
                "description": r.get("description", ""),
                "topics": list(r.get("topics", []) or []),
            }
            for r in repositories
            if r.get("description") or r.get("topics")
        ][:MAX_REPOS]

        return {
            "repositories": repositories,
            "languages": weighted_languages,
            "topics": sorted(all_topics),
            "skills_inferred": skills_inferred,
            "frameworks_inferred": frameworks_inferred,
            "repos_brief": repos_brief,
        }
    finally:
        if own_session:
            await session.close()


# NOTE (CLAUDE.md rule #28): the hardcoded description-term vocabulary and the
# dev-tooling denylist that used to live here were removed. Repo descriptions are
# read by the LLM pass (llm_infer_github_skills); the deterministic side only
# surfaces the raw signals the GitHub API itself returns.


def _infer_skills(languages: dict[str, int], topics: set[str]) -> list[str]:
    """Surface the raw GitHub signals as candidate skills — languages ranked by
    (weighted) code bytes, then repo topics. CLAUDE.md rule #28: NO hardcoded
    language/topic→skill map; the API strings are used as-is (topics only get a
    cosmetic hyphen→space cleanup), and the LLM pass canonicalises meaning."""
    seen: set[str] = set()
    skills: list[str] = []

    for lang, _ in sorted(languages.items(), key=lambda x: x[1], reverse=True):
        if lang and lang.lower() not in seen:
            skills.append(lang)
            seen.add(lang.lower())

    for topic in sorted(topics):
        t = topic.replace("-", " ").strip()
        if t and t.lower() not in seen:
            skills.append(t)
            seen.add(t.lower())

    return skills


def deterministic_github_fields(repos_brief: list[dict]) -> list[str]:
    """Pass 1 for GitHub over the STORED repo briefs — STRUCTURE only, NO LLM.

    Surfaces the repo *topics* the GitHub API attached to each repo (cosmetic
    hyphen→space cleanup), deduped. No skill map and no prose mining
    (CLAUDE.md rule #28) — reading repo descriptions for meaning is the LLM
    pass's job (``llm_infer_github_skills``).

    Exists so the two-pass GitHub lane has a real deterministic half that reads
    the SAME stored raw (``cv.github_repos_brief``) the LLM half reads — mirroring
    the CV/LinkedIn/preferences deterministic passes.
    """
    seen: set[str] = set()
    out: list[str] = []
    for repo in repos_brief or []:
        if not isinstance(repo, dict):
            continue
        for topic in repo.get("topics", []) or []:
            if not isinstance(topic, str):
                continue
            t = topic.replace("-", " ").strip()
            if t and t.lower() not in seen:
                out.append(t)
                seen.add(t.lower())
    return out


# ── GitHub LLM pass (Pass 2) — read repo prose for extra skills ─────

_GITHUB_LLM_SYSTEM = (
    "You are an expert at reading a developer's GitHub repositories and naming "
    "the concrete technologies, frameworks, and domains they demonstrate. You "
    "return JSON only and never invent skills the text does not support."
)

_GITHUB_LLM_PROMPT = """Below is a list of a developer's public GitHub repositories — each with
a name, description, and topic tags.

Infer the concrete technical SKILLS this developer demonstrates: frameworks,
libraries, tools, platforms, and technical domains. Focus on things a
hard-coded language/topic table would MISS — e.g. "LangChain", "RAG",
"Computer Vision", "Fraud Detection", "Cloudflare Workers".

Return JSON: {{"skills": ["Skill One", "Skill Two", ...]}}

Rules:
- GROUNDED ONLY: every skill must be supported by words actually in the name,
  description, or topics. Do NOT guess a tech stack from a repo's purpose — e.g.
  for "cold outreach platform" do not assume "Gmail API"/"GPT-4o" unless named.
- Individual items, not categories ("PyTorch", not "ML frameworks").
- Skip bare programming languages (Python/Java/etc.) — those are covered elsewhere.

REPOSITORIES:
---
{repos}
---"""


async def llm_infer_github_skills(repos_brief: list[dict]) -> list[str]:
    """Pass 2 for GitHub — ask the LLM to read repo prose and name skills the
    hard-coded ``LANGUAGE_TO_SKILL`` / ``TOPIC_TO_SKILL`` tables can't know.

    Returns ``[]`` (never raises) when there are no repos worth reading or the
    provider chain fails — graceful no-op, mirroring the deterministic path.
    The empty-input branch never calls the LLM (cost guard).
    """
    if not repos_brief:
        return []

    lines: list[str] = []
    for r in repos_brief:
        name = (r.get("name") or "").strip()
        desc = (r.get("description") or "").strip()
        topics = ", ".join(t for t in (r.get("topics") or []) if t)
        if not (name or desc or topics):
            continue
        lines.append(f"- {name}: {desc} [topics: {topics}]")
    if not lines:
        return []

    prompt = _GITHUB_LLM_PROMPT.format(repos="\n".join(lines))
    try:
        from src.services.profile.llm_provider import llm_extract  # noqa: PLC0415
        result = await llm_extract(prompt, system=_GITHUB_LLM_SYSTEM)
    except Exception as e:  # noqa: BLE001 — never crash the pass
        logger.warning("GitHub LLM skill inference failed: %s", e)
        return []

    raw = result.get("skills") if isinstance(result, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for s in raw:
        if isinstance(s, str) and s.strip() and s.strip().lower() not in seen:
            out.append(s.strip())
            seen.add(s.strip().lower())
    return out


def enrich_cv_from_github(cv: CVData, github_data: dict) -> CVData:
    """Merge GitHub-inferred skills into CVData, deduplicating.

    Batch 1.2 — also writes ``github_frameworks`` from
    ``frameworks_inferred`` with dedup against existing CV skills AND
    the language/topic-derived skills, so the same framework never
    appears twice in a downstream SearchConfig.

    Two-pass — also stores ``github_repos_brief`` so the LLM pass can re-run
    offline on a later profile change.
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
    # Two-pass — keep repo briefs for offline LLM re-runs. Only overwrite when
    # a non-empty value arrives, so a partial re-enrich never wipes them.
    if github_data.get("repos_brief"):
        cv.github_repos_brief = github_data["repos_brief"]

    return cv
