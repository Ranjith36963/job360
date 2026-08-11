"""Fetch GitHub public data to infer skills from repos, languages, topics, and dependency files.

Batch 1.2 (Pillar 1) adds two signals to the original language+topic
inference:

* **Dependency-file parsing** — fetches ``package.json`` /
  ``requirements.txt`` / ``pyproject.toml`` / ``Cargo.toml`` / ``Gemfile``
  / ``go.mod`` / ``composer.json`` via the GitHub Contents API, runs
  each through ``dep_file_parser``, and keeps the dependency names
  VERBATIM — canonicalising them to skills is the LLM pass's job. An
  earlier version routed them through a hand-typed
  ``dependency_map.lookup_skill``; that module was deleted under rule #28
  (no hardcoded skill vocabularies) and this line described it for weeks
  after it was gone. Typically yields 3-5× more signal than
  language-only inference, because frameworks (React, Django, Laravel)
  don't map 1:1 to a language.

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
    # INTERNAL whitespace means this is not a handle — a GitHub username cannot
    # contain a space. The old code split on whitespace and kept the first part,
    # so "john smith" quietly became "john" and we would fetch a STRANGER's
    # account into this user's profile. That is not hypothetical: a live profile
    # was found holding another person's entire GitHub (bio, repos, and the
    # skills derived from them, all scoring the wrong jobs). Guessing is the
    # failure mode here, so ambiguous input is refused and the caller asks.
    # (Leading/trailing spaces are already gone — .strip() above.)
    if re.search(r"\s", s):
        return ""
    # Drop a leading @ that users copy from social handles.
    s = s.lstrip("@").strip()
    # A GitHub PAGES url encodes the username in its SUBDOMAIN:
    # "octocat.github.io" IS octocat. Checked BEFORE the github.com rule, since
    # the string also contains "github.io" not "github.com".
    #
    # This case is not academic: a CV lists the Pages site under "Portfolio", so
    # it is exactly what someone pastes into a box labelled "GitHub Profile".
    # Rejecting it told the owner his own URL was "not a GitHub username" while
    # the username was sitting in it — over-strict, and it read as a bug.
    pages = re.match(
        r"^(?:https?://)?([a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?)\.github\.io(?:/.*)?$",
        s,
        re.IGNORECASE,
    )
    if pages:
        return pages.group(1)
    # If it mentions github.com, take the first path segment after it.
    # Tolerates http(s)://, www., a trailing path, query string, or fragment.
    match = re.search(r"github\.com/+([^/?#\s]+)", s, re.IGNORECASE)
    if match:
        return match.group(1)
    else:
        # Otherwise treat the whole thing as a handle, but still strip any
        # stray path/query so "torvalds/repo" -> "torvalds".
        s = re.split(r"[/?#\s]", s, maxsplit=1)[0]
    # SELF-VALIDATE. The old contract left validation "to the caller", so a
    # caller that forgot (the _apply_preferences fallback) stored "https:" for a
    # real user and silently broke GitHub enrichment. A handle that fails the
    # GitHub rule is not a handle — return "" so no caller can persist poison.
    return s if _GITHUB_USERNAME_RE.match(s) else ""


from src.core.settings import GITHUB_TOKEN
from src.services.profile.dep_file_parser import MANIFEST_FILES, parse_manifest
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
# "Read 100% of GitHub" — README prose is the richest signal, but each README
# is one more request AND more prompt tokens, so cap how many we read and how
# long each excerpt is. Authenticated (5000/hr) reads far more than
# unauthenticated (60/hr, where the whole probe must fit ≈54 < 60).
README_EXCERPT_CHARS = 1200          # per-repo README, truncated for the prompt
PROFILE_README_CHARS = 2500          # the self-authored {u}/{u} portfolio README
README_REPOS_AUTHED = 15
README_REPOS_UNAUTHED = 4


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
            if resp.status == 404:
                # Expected for optional resources (a repo with no README, a
                # user with no {u}/{u} profile repo). Not an error — debug only,
                # so a missing README doesn't spam warnings for every repo.
                logger.debug("GitHub API 404 for %s", url)
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
        # BaseException, not Exception: gather(return_exceptions=True) also
        # returns BaseExceptions such as CancelledError, and those are truthy,
        # so `not content` would NOT catch them either. Narrowing to Exception
        # let a cancelled fetch through to be parsed as if it were file text.
        if isinstance(content, BaseException) or not content:
            continue
        # The guard above drops every ``Exception`` gather returned, so this is a
        # real ``str``. mypy can't subtract ``Exception`` from ``BaseException``,
        # hence the narrow ignore. (A bare ``BaseException`` — e.g. CancelledError
        # — would slip past the guard, but that is pre-existing behaviour and is
        # left untouched here rather than silently changed.)
        _ecosystem, dep_names = parse_manifest(filename, content)
        for dep in dep_names:
            d = (dep or "").strip()
            if d and d.lower() not in seen:
                skills.append(d)
                seen.add(d.lower())
    return skills


async def _fetch_user(session: aiohttp.ClientSession, username: str) -> dict[str, Any]:
    """GET ``/users/{u}`` — the developer's own identity block.

    Returns bio, name, company, blog, location, hireable, twitter — the words
    a developer uses to describe THEMSELVES, which we never read before. One
    cheap request. Returns ``{}`` on any failure (never raises)."""
    data = await _get_json(session, f"{GITHUB_API}/users/{username}")
    return data if isinstance(data, dict) else {}


def _identity_fields(user: dict[str, Any]) -> dict[str, Any]:
    """The /users/{u} identity block kept as TYPED VALUES, not a sentence.

    ``_compose_bio`` folds these same fields into one display string. That is
    fine for a human and for an LLM prompt, and useless for anything that has
    to compare, filter or score — you cannot match on a sentence.

    Two are genuine matching signals, which is why these are shelves and not
    decoration:
      * ``hireable``  — GitHub's own "open to work" flag, stated by the person.
      * ``location``  — a place claim the UK eligibility gate can reason about.
    The rest (account age, followers, public repo count) are cheap tenure and
    activity proxies for later ranking work.

    Costs nothing: this is the SAME response ``_compose_bio`` already reads.
    Every value is read defensively — GitHub omits fields on sparse profiles,
    and a missing field must be absent, never ``None`` leaking into the UI.
    """
    def s(key: str) -> str:
        v = user.get(key)
        return v.strip() if isinstance(v, str) and v.strip() else ""

    def i(key: str) -> int:
        v = user.get(key)
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

    return {
        "name": s("name"),
        "company": s("company"),
        "location": s("location"),
        "blog": s("blog"),
        "twitter": s("twitter_username"),
        # Tri-state on purpose: True / False / None are three different facts.
        # None means "never said", which is NOT "not looking" — the same
        # distinction the visa signal makes (rule #31).
        "hireable": user.get("hireable") if isinstance(user.get("hireable"), bool) else None,
        "account_created_at": s("created_at"),
        "followers": i("followers"),
        "public_repos": i("public_repos"),
    }


def _compose_bio(user: dict[str, Any]) -> str:
    """Fold the /users/{u} identity fields into one short self-description
    string for the LLM pass. Only non-empty fields are included, so a sparse
    profile yields a short line and a rich one yields more."""
    parts: list[str] = []
    for label, key in (
        ("Name", "name"), ("Bio", "bio"), ("Company", "company"),
        ("Location", "location"), ("Website", "blog"),
    ):
        val = user.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(f"{label}: {val.strip()}")
    if user.get("hireable") is True:
        parts.append("Open to work: yes")
    tw = user.get("twitter_username")
    if isinstance(tw, str) and tw.strip():
        parts.append(f"Twitter/X: @{tw.strip()}")
    return " | ".join(parts)


def _decode_readme(payload: Any, limit: int) -> str:
    """Decode a GitHub ``/readme`` payload (base64 JSON) to a truncated string.

    Returns ``""`` on a missing/oversized/malformed payload. Truncation keeps
    the LLM prompt bounded — the first ``limit`` chars of a README carry the
    'what this is / what it's built with' summary; the tail is usually install
    steps and licence boilerplate."""
    if not isinstance(payload, dict):
        return ""
    encoded = payload.get("content")
    if payload.get("encoding") != "base64" or not isinstance(encoded, str) or not encoded:
        return ""
    try:
        text = base64.b64decode(encoded).decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        logger.debug("README base64 decode failed: %s", e)
        return ""
    return text.strip()[:limit]


async def _fetch_readme(
    session: aiohttp.ClientSession, username: str, repo_name: str, limit: int
) -> str:
    """GET ``/repos/{u}/{repo}/readme`` — the richest prose GitHub holds.

    A repo ``description`` is often one line or null; the README is where the
    real project story lives. Base64 JSON → decoded → truncated. Returns ``""``
    on 404 (no README — common) or any failure. Routes through ``_get_json`` so
    tests that patch it stay offline (rule #4)."""
    payload = await _get_json(session, f"{GITHUB_API}/repos/{username}/{repo_name}/readme")
    return _decode_readme(payload, limit)


async def _fetch_pinned(session: aiohttp.ClientSession, username: str) -> list[str]:
    """Return the names of the user's PINNED repos — their own curated
    highlights — via the GraphQL API. REST cannot expose pins; GraphQL needs a
    token, so this is a no-op (returns ``[]``) when unauthenticated. Never
    raises. Used only to REORDER which repos we read first within the request
    budget, so the user's showcased work is covered before the long tail."""
    if not GITHUB_TOKEN:
        return []
    # Concatenation, not f-string/%: the GraphQL body is full of literal { }
    # braces. `username` is already validated by _GITHUB_USERNAME_RE upstream
    # (alphanumeric + hyphen), so there is no injection surface here.
    query = (
        '{ user(login: "' + username + '") { pinnedItems(first: 6, types: REPOSITORY) '
        '{ nodes { ... on Repository { name } } } } }'
    )
    try:
        async with session.post(
            f"{GITHUB_API}/graphql",
            json={"query": query},
            headers=_headers(),
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.debug("GitHub GraphQL pinned fetch %s", resp.status)
                return []
            data = await resp.json()
    except Exception as e:  # noqa: BLE001
        logger.debug("GitHub GraphQL pinned fetch failed: %s", e)
        return []
    if not isinstance(data, dict):
        return []
    try:
        nodes = data["data"]["user"]["pinnedItems"]["nodes"]
    except (KeyError, TypeError):
        return []
    return [n["name"] for n in nodes if isinstance(n, dict) and n.get("name")]


async def fetch_github_profile(
    username: str, session: aiohttp.ClientSession | None = None
) -> dict[str, Any]:
    """Fetch public repos, languages, topics, and framework dependencies.

    Returns a dict with keys:
      ``repositories`` — raw repo list (name, language, description, stars, topics, pushed_at)
      ``languages``    — merged language → bytes map (temporally weighted)
      ``topics``       — sorted list of topic tags across all repos
      ``skills_inferred`` — language + topic → skill list (Batch 1 pre-deps signal)
      ``frameworks_inferred`` — dependency-file → skill list (Batch 1.2)
    """
    empty: dict[str, Any] = {
        "repositories": [],
        "languages": {},
        "topics": [],
        "skills_inferred": [],
        "frameworks_inferred": [],
        "repos_brief": [],
        "bio": "",
        "profile_readme": "",
    }
    # Accept a full profile URL or @handle, not just a bare username.
    username = normalize_github_username(username)
    if not _GITHUB_USERNAME_RE.match(username):
        logger.warning("Invalid GitHub username format: %s", username)
        return empty

    own_session = session is None
    # Same condition as ``own_session`` (which is never reassigned) — spelled
    # out so the type checker can narrow ``session`` to a non-None ClientSession
    # for the rest of the function.
    if session is None:
        session = aiohttp.ClientSession()

    try:
        repos_url = f"{GITHUB_API}/users/{username}/repos?per_page={MAX_REPOS}&sort=pushed"
        repos_data = await _get_json(session, repos_url)
        if not repos_data or not isinstance(repos_data, list):
            return empty

        repositories: list[dict[str, Any]] = []
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
                # Already in this response; kept because each answers a
                # question matching will want later:
                #   created_at — TENURE. pushed_at says "recently touched";
                #     created_at + pushed_at together say "worked on this for
                #     two years", which is a different, stronger claim.
                #   archived   — a dead project. Evidence, but weaker evidence.
                #   forks      — reuse by others, a quieter quality signal
                #     than stars and less gameable.
                #   homepage   — a shipped, reachable product.
                "created_at": repo.get("created_at") or "",
                "archived": bool(repo.get("archived")),
                "forks": repo.get("forks_count", 0) or 0,
                "homepage": (repo.get("homepage") or "").strip(),
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
        readme_n = README_REPOS_AUTHED if authed else README_REPOS_UNAUTHED

        # The user's PINNED repos are their own curated highlights. Pull them to
        # the front so the capped language / dep-file / README probes cover the
        # work the developer chose to showcase before the long tail. No-op when
        # unauthenticated (GraphQL needs a token); stable — pinned first in pin
        # order, then the rest in their existing pushed-desc order.
        pinned = await _fetch_pinned(session, username)
        if pinned:
            pin_rank = {name: i for i, name in enumerate(pinned)}
            repositories.sort(key=lambda r: pin_rank.get(r["name"], len(pin_rank) + 1))

        # The developer's own identity block (bio / name / company / location /
        # blog / hireable). One cheap request; prose fed to the LLM pass.
        user_obj = await _fetch_user(session, username)

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
            # BaseException for the same reason as _fetch_repo_frameworks above:
            # a cancelled task returns a BaseException that Exception misses.
            if isinstance(result, BaseException):
                logger.debug("Dep fetch failed: %s", result)
                continue
            # Same narrowing gap as in ``_fetch_repo_frameworks``: the guard above
            # removes every ``Exception``, leaving a real ``list[str]``, but mypy
            # cannot subtract ``Exception`` from ``BaseException``.
            for skill in result or []:
                if skill.lower() not in seen_framework:
                    frameworks_inferred.append(skill)
                    seen_framework.add(skill.lower())

        skills_inferred = _infer_skills(weighted_languages, all_topics)

        # ── READMEs — the richest per-repo prose. A repo `description` is often
        # one line or null; the README is where "what this is / what it's built
        # with" actually lives. Fetched for the top `readme_n` repos (pinned
        # first after the reorder), truncated, and fed ONLY to the LLM pass
        # (rule #28 safe). Routes through _fetch_readme → _get_json so tests stay
        # offline. A failed/absent README just yields no excerpt for that repo.
        readme_targets = repositories[:readme_n]
        readme_results = await asyncio.gather(
            *[_fetch_readme(session, username, r["name"], README_EXCERPT_CHARS)
              for r in readme_targets],
            return_exceptions=True,
        )
        readme_by_name: dict[str, str] = {}
        for r, res in zip(readme_targets, readme_results):
            if isinstance(res, str) and res:
                readme_by_name[r["name"]] = res

        # Two-pass — compact repo briefs for the LLM pass. Keep a repo if it has
        # ANY prose worth reading: a description, topics, a language, OR a README
        # we just fetched. Each brief carries its README excerpt so the offline
        # re-run reads the same prose without re-fetching.
        repos_brief: list[dict[str, Any]] = []
        for r in repositories:
            excerpt = readme_by_name.get(r["name"], "")
            if not (r.get("description") or r.get("topics") or r.get("language") or excerpt):
                continue
            brief: dict[str, Any] = {
                "name": r["name"],
                "language": r.get("language", "") or "",
                "description": r.get("description", ""),
                "topics": list(r.get("topics", []) or []),
                # RECENCY and WEIGHT were fetched and thrown away. ``pushed_at``
                # is the only recency signal GitHub hands us — the engine has no
                # skill-recency input anywhere, and "used this in the last
                # month" is a different claim from "used it in 2019".
                # ``stars`` is the one public quality signal on a repo. Both are
                # already in the /users/{u}/repos payload, so keeping them costs
                # nothing and closes the last GitHub field with no shelf.
                "stars": int(r.get("stars", 0) or 0),
                "pushed_at": r.get("pushed_at", "") or "",
                "created_at": r.get("created_at", "") or "",
                "archived": bool(r.get("archived")),
                "forks": int(r.get("forks", 0) or 0),
                "homepage": r.get("homepage", "") or "",
            }
            if excerpt:
                brief["readme_excerpt"] = excerpt
            repos_brief.append(brief)
            if len(repos_brief) >= MAX_REPOS:
                break

        # ── Profile README — the {u}/{u} special repo GitHub renders at the top
        # of a profile: the developer's self-authored portfolio. Often absent.
        profile_readme = await _fetch_readme(session, username, username, PROFILE_README_CHARS)

        # ── Identity block — bio / name / company / location the developer wrote
        # about themselves, folded into one short self-description line.
        bio = _compose_bio(user_obj)

        return {
            "repositories": repositories,
            "languages": weighted_languages,
            "topics": sorted(all_topics),
            "skills_inferred": skills_inferred,
            "frameworks_inferred": frameworks_inferred,
            "repos_brief": repos_brief,
            "bio": bio,
            "profile_readme": profile_readme,
            # STRUCTURED identity. Every field below was ALREADY fetched in the
            # same /users/{u} request and then flattened into the `bio` string —
            # readable by a human and by the LLM, but unqueryable by anything
            # else. A string cannot be matched on.
            #
            # Two of these are matching-grade signals, which is why this is not
            # cosmetic: `hireable` is GitHub's own "open to work" flag, and
            # `location` is a place claim that the UK gate reasons about. Costs
            # zero extra requests — it is the same response, kept instead of
            # concatenated.
            "identity": _identity_fields(user_obj),
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


def deterministic_github_fields(repos_brief: list[dict[str, Any]]) -> list[str]:
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
        # Primary language first — the strongest structural GitHub signal (an API
        # field, not a keyword map: rule #28 safe). Then repo topics.
        lang = repo.get("language")
        if isinstance(lang, str) and lang.strip() and lang.strip().lower() not in seen:
            out.append(lang.strip())
            seen.add(lang.strip().lower())
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

_GITHUB_LLM_PROMPT = """Below is a developer's public GitHub presence: an optional self-description
(their profile bio and portfolio README), followed by their repositories —
each with a name, language, description, topic tags, and (when available) an
excerpt of the repo's README.

Infer the concrete technical SKILLS this developer demonstrates: frameworks,
libraries, tools, platforms, and technical domains. Focus on things a
hard-coded language/topic table would MISS — e.g. "LangChain", "RAG",
"Computer Vision", "Fraud Detection", "Cloudflare Workers". The README excerpts
and self-description are the richest source — read them carefully.

Return JSON: {{"skills": ["Skill One", "Skill Two", ...]}}

Rules:
- GROUNDED ONLY: every skill must be supported by words actually present in the
  self-description, a name, description, topics, or a README excerpt. Do NOT
  guess a tech stack from a repo's purpose — e.g. for "cold outreach platform"
  do not assume "Gmail API"/"GPT-4o" unless named.
- Individual items, not categories ("PyTorch", not "ML frameworks").
- Skip bare programming languages (Python/Java/etc.) — those are covered elsewhere.

{self_desc}REPOSITORIES:
---
{repos}
---"""


async def llm_infer_github_skills(
    repos_brief: list[dict[str, Any]],
    *,
    bio: str = "",
    profile_readme: str = "",
) -> list[str]:
    """Pass 2 for GitHub — ask the LLM to read repo prose and name skills the
    hard-coded ``LANGUAGE_TO_SKILL`` / ``TOPIC_TO_SKILL`` tables can't know.

    Reads, in order of richness: the profile bio + portfolio README (the
    developer's self-description), each repo's README excerpt, then the
    name/description/topics. All are prose the LLM canonicalises — no hardcoded
    map (rule #28).

    Returns ``[]`` (never raises) when there is nothing worth reading or the
    provider chain fails — graceful no-op, mirroring the deterministic path.
    The empty-input branch never calls the LLM (cost guard).
    """
    bio = (bio or "").strip()
    profile_readme = (profile_readme or "").strip()
    if not (repos_brief or bio or profile_readme):
        return []

    lines: list[str] = []
    for r in repos_brief:
        name = (r.get("name") or "").strip()
        lang = (r.get("language") or "").strip()
        desc = (r.get("description") or "").strip()
        topics = ", ".join(t for t in (r.get("topics") or []) if t)
        readme = (r.get("readme_excerpt") or "").strip()
        if not (name or desc or topics or lang or readme):
            continue
        lang_tag = f" [language: {lang}]" if lang else ""
        entry = f"- {name}:{lang_tag} {desc} [topics: {topics}]"
        if readme:
            entry += f"\n  README: {readme}"
        lines.append(entry)

    # A self-description block (bio + portfolio README) may exist even with no
    # repo prose — a valid reason to call the LLM on its own.
    self_desc = ""
    if bio or profile_readme:
        sd_parts = []
        if bio:
            sd_parts.append(bio)
        if profile_readme:
            sd_parts.append(f"Profile README:\n{profile_readme}")
        self_desc = "DEVELOPER SELF-DESCRIPTION:\n---\n" + "\n\n".join(sd_parts) + "\n---\n\n"

    if not lines and not self_desc:
        return []

    prompt = _GITHUB_LLM_PROMPT.format(
        self_desc=self_desc, repos="\n".join(lines) or "(none)"
    )
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


def enrich_cv_from_github(cv: CVData, github_data: dict[str, Any]) -> CVData:
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
    # "Read 100% of GitHub" — the self-authored prose signals. Only overwrite on
    # a non-empty fetch so a rate-limited partial re-enrich never wipes them.
    if github_data.get("bio"):
        cv.github_bio = github_data["bio"]
    if github_data.get("profile_readme"):
        cv.github_profile_readme = github_data["profile_readme"]
    # Structured identity — same "only overwrite on a non-empty fetch" rule as
    # the two above, so a rate-limited partial re-enrich never blanks it.
    if github_data.get("identity"):
        cv.github_identity = dict(github_data["identity"])

    return cv
