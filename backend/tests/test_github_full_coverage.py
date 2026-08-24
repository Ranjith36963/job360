"""'Read 100% of GitHub' (2026-08-05) — the enricher now reads every high-signal
corner of a public profile, not just languages/topics/deps.

WHY THIS EXISTS. Matches from GitHub were only okay because we read the *labels*
(languages, topics) but never the *prose* (READMEs, bio). A repo `description`
is usually null; the README is where "I build human-in-the-loop AI agents with
LangGraph" actually lives. These tests pin the new corners so a refactor can't
silently drop them again:

  * the /users/{u} identity block (bio/name/company/location) → github_bio
  * each repo's README excerpt → repos_brief[i]['readme_excerpt']
  * the {u}/{u} profile README → github_profile_readme
  * pinned repos reorder the probe so curated work is read first (authed only)
  * all of the above flow into the LLM pass's prompt (rule #28: prose → LLM)

Every network call is mocked (rule #4 — the suite runs offline).
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import pytest

from src.services.profile import github_enricher
from src.services.profile.models import CVData


def _b64_readme(text: str) -> dict:
    """A GitHub /readme payload: base64-encoded content."""
    return {"content": base64.b64encode(text.encode()).decode(), "encoding": "base64"}


def _make_async_session():
    """AsyncMock standing in for aiohttp.ClientSession (close() awaitable)."""
    return AsyncMock()


# ── The identity block (/users/{u}) ─────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_reads_user_bio_and_identity(monkeypatch):
    """The developer's own words about themselves — never read before."""
    monkeypatch.setattr(github_enricher, "GITHUB_TOKEN", "")
    repos = [{"name": "proj", "language": "Python", "description": "", "fork": False,
              "stargazers_count": 0, "topics": [], "pushed_at": None}]

    async def fake_get_json(session, url):
        if url.endswith("repos?per_page=30&sort=pushed"):
            return repos
        if url.endswith("/users/octocat"):
            return {"name": "Octo Cat", "bio": "ML engineer building fraud detection",
                    "company": "Acme", "location": "London", "hireable": True,
                    "blog": "octo.dev", "twitter_username": "octo"}
        if "/languages" in url:
            return {"Python": 1}
        return None

    with patch.object(github_enricher, "_get_json", side_effect=fake_get_json), \
         patch.object(github_enricher, "_fetch_repo_frameworks", new=AsyncMock(return_value=[])), \
         patch.object(github_enricher.aiohttp, "ClientSession", return_value=_make_async_session()):
        result = await github_enricher.fetch_github_profile("octocat")

    bio = result["bio"]
    assert "ML engineer building fraud detection" in bio
    assert "Octo Cat" in bio and "Acme" in bio and "London" in bio
    assert "Open to work: yes" in bio  # hireable=True surfaced


# ── Repo READMEs — the richest per-repo prose ───────────────────────


@pytest.mark.asyncio
async def test_fetch_attaches_repo_readme_excerpt(monkeypatch):
    """A repo with a null description but a rich README must still carry that
    README into the brief — that is the whole point of reading READMEs."""
    monkeypatch.setattr(github_enricher, "GITHUB_TOKEN", "")
    repos = [{"name": "agent", "language": "Python", "description": None, "fork": False,
              "stargazers_count": 0, "topics": [], "pushed_at": None}]

    async def fake_get_json(session, url):
        if url.endswith("repos?per_page=30&sort=pushed"):
            return repos
        if url.endswith("/repos/octocat/agent/readme"):
            return _b64_readme("A human-in-the-loop agent built with LangGraph and n8n.")
        if "/languages" in url:
            return {"Python": 1}
        return None

    with patch.object(github_enricher, "_get_json", side_effect=fake_get_json), \
         patch.object(github_enricher, "_fetch_repo_frameworks", new=AsyncMock(return_value=[])), \
         patch.object(github_enricher.aiohttp, "ClientSession", return_value=_make_async_session()):
        result = await github_enricher.fetch_github_profile("octocat")

    brief = next(b for b in result["repos_brief"] if b["name"] == "agent")
    assert "LangGraph" in brief["readme_excerpt"]
    assert "human-in-the-loop" in brief["readme_excerpt"].lower()


@pytest.mark.asyncio
async def test_readme_excerpt_is_truncated(monkeypatch):
    """A giant README is truncated so it can't blow the LLM prompt budget."""
    monkeypatch.setattr(github_enricher, "GITHUB_TOKEN", "")
    monkeypatch.setattr(github_enricher, "README_EXCERPT_CHARS", 50)
    repos = [{"name": "big", "language": "Go", "description": "", "fork": False,
              "stargazers_count": 0, "topics": [], "pushed_at": None}]

    async def fake_get_json(session, url):
        if url.endswith("repos?per_page=30&sort=pushed"):
            return repos
        if url.endswith("/repos/octocat/big/readme"):
            return _b64_readme("X" * 5000)
        if "/languages" in url:
            return {"Go": 1}
        return None

    with patch.object(github_enricher, "_get_json", side_effect=fake_get_json), \
         patch.object(github_enricher, "_fetch_repo_frameworks", new=AsyncMock(return_value=[])), \
         patch.object(github_enricher.aiohttp, "ClientSession", return_value=_make_async_session()):
        result = await github_enricher.fetch_github_profile("octocat")

    brief = next(b for b in result["repos_brief"] if b["name"] == "big")
    assert len(brief["readme_excerpt"]) == 50


# ── The profile README ({u}/{u} special repo) ───────────────────────


@pytest.mark.asyncio
async def test_fetch_reads_profile_readme(monkeypatch):
    """The {u}/{u} special-repo README is the self-authored portfolio page."""
    monkeypatch.setattr(github_enricher, "GITHUB_TOKEN", "")
    repos = [{"name": "octocat", "language": "Markdown", "description": "", "fork": False,
              "stargazers_count": 0, "topics": [], "pushed_at": None}]

    async def fake_get_json(session, url):
        if url.endswith("repos?per_page=30&sort=pushed"):
            return repos
        if url.endswith("/repos/octocat/octocat/readme"):
            return _b64_readme("I'm a data scientist specialising in NLP and RAG systems.")
        if "/languages" in url:
            return {"Markdown": 1}
        return None

    with patch.object(github_enricher, "_get_json", side_effect=fake_get_json), \
         patch.object(github_enricher, "_fetch_repo_frameworks", new=AsyncMock(return_value=[])), \
         patch.object(github_enricher.aiohttp, "ClientSession", return_value=_make_async_session()):
        result = await github_enricher.fetch_github_profile("octocat")

    assert "RAG systems" in result["profile_readme"]


@pytest.mark.asyncio
async def test_missing_readme_is_not_an_error(monkeypatch):
    """Most repos have no README (404 → None). That must degrade to no excerpt,
    never crash, and never fabricate content."""
    monkeypatch.setattr(github_enricher, "GITHUB_TOKEN", "")
    repos = [{"name": "empty", "language": "C", "description": "a thing", "fork": False,
              "stargazers_count": 0, "topics": [], "pushed_at": None}]

    async def fake_get_json(session, url):
        if url.endswith("repos?per_page=30&sort=pushed"):
            return repos
        if "/languages" in url:
            return {"C": 1}
        return None  # every readme + user lookup 404s → None

    with patch.object(github_enricher, "_get_json", side_effect=fake_get_json), \
         patch.object(github_enricher, "_fetch_repo_frameworks", new=AsyncMock(return_value=[])), \
         patch.object(github_enricher.aiohttp, "ClientSession", return_value=_make_async_session()):
        result = await github_enricher.fetch_github_profile("octocat")

    brief = next(b for b in result["repos_brief"] if b["name"] == "empty")
    assert "readme_excerpt" not in brief
    assert result["profile_readme"] == ""
    assert result["bio"] == ""


# ── Pinned repos reorder the probe (authed only) ────────────────────


@pytest.mark.asyncio
async def test_pinned_repos_are_read_first(monkeypatch):
    """A user's PINNED repo is their curated highlight. Within the capped probe
    it must be read before the long tail — so its README lands even when the
    README cap is small.

    Finding 8 (Pillar-1 closeout audit) raised the AUTHENTICATED README cap to
    MAX_REPOS (no more README_REPOS_AUTHED ceiling below it), so the way to
    force a small cap here is now MAX_REPOS itself — which also caps the repo
    list fetch (``per_page``), hence the dynamic URL below instead of a
    hardcoded ``per_page=30``."""
    monkeypatch.setattr(github_enricher, "GITHUB_TOKEN", "tok")
    monkeypatch.setattr(github_enricher, "MAX_REPOS", 1)  # only 1 repo probed at all
    # 'showcase' is pushed OLDEST so it sorts last by default — pinning must
    # override that and pull it to the front.
    repos = [
        {"name": "noise", "language": "Python", "description": "x", "fork": False,
         "stargazers_count": 0, "topics": [], "pushed_at": "2026-01-01T00:00:00Z"},
        {"name": "showcase", "language": "Python", "description": "x", "fork": False,
         "stargazers_count": 0, "topics": [], "pushed_at": "2000-01-01T00:00:00Z"},
    ]

    async def fake_get_json(session, url):
        if url.endswith("repos?per_page=1&sort=pushed"):
            return repos
        if url.endswith("/repos/octocat/showcase/readme"):
            return _b64_readme("Kubernetes operator written in Go.")
        if url.endswith("/repos/octocat/noise/readme"):
            return _b64_readme("should not be read — cap is 1 and this isn't pinned")
        if "/languages" in url:
            return {"Python": 1}
        return None

    async def fake_pinned(session, username):
        return ["showcase"]

    with patch.object(github_enricher, "_get_json", side_effect=fake_get_json), \
         patch.object(github_enricher, "_fetch_pinned", side_effect=fake_pinned), \
         patch.object(github_enricher, "_fetch_repo_frameworks", new=AsyncMock(return_value=[])), \
         patch.object(github_enricher.aiohttp, "ClientSession", return_value=_make_async_session()):
        result = await github_enricher.fetch_github_profile("octocat")

    showcase = next(b for b in result["repos_brief"] if b["name"] == "showcase")
    assert "Kubernetes" in showcase["readme_excerpt"], "pinned repo's README was not read first"
    # With MAX_REPOS=1 the cap now governs the WHOLE probe (repo list, deps,
    # README) not just a separate README sub-cap, so 'noise' is squeezed out
    # of repos_brief entirely — the point being it's squeezed out in favour
    # of the PINNED repo, not by arbitrary list order.
    assert not any(b["name"] == "noise" for b in result["repos_brief"]), (
        "cap did not respect pin order — the non-pinned repo should have "
        "been dropped before the pinned one"
    )


@pytest.mark.asyncio
async def test_pinned_is_noop_when_unauthenticated(monkeypatch):
    """No token → no GraphQL → _fetch_pinned returns [] and never posts."""
    monkeypatch.setattr(github_enricher, "GITHUB_TOKEN", "")
    session = AsyncMock()
    out = await github_enricher._fetch_pinned(session, "octocat")
    assert out == []
    session.post.assert_not_called()


# ── The LLM pass reads the new prose ────────────────────────────────


@pytest.mark.asyncio
async def test_llm_pass_prompt_includes_bio_readme_and_excerpts():
    """The self-description + README excerpts must reach the model. Capture the
    prompt and assert the prose is in it."""
    captured = {}

    async def fake_extract(prompt, system=None):
        captured["prompt"] = prompt
        return {"skills": ["RAG"]}

    briefs = [{"name": "agent", "language": "Python", "description": "",
               "topics": [], "readme_excerpt": "Built with LangGraph for RAG."}]

    with patch("src.services.profile.llm_provider.llm_extract", side_effect=fake_extract):
        out = await github_enricher.llm_infer_github_skills(
            briefs, bio="Bio: NLP engineer", profile_readme="I love Cloudflare Workers.")

    p = captured["prompt"]
    assert "NLP engineer" in p            # bio
    assert "Cloudflare Workers" in p      # profile readme
    assert "LangGraph" in p               # repo readme excerpt
    assert out == ["RAG"]


@pytest.mark.asyncio
async def test_llm_pass_runs_on_self_description_alone():
    """Even with zero repos, a bio/profile README is reason enough to call the
    LLM — a user can have a rich profile README and few public repos."""
    called = {"n": 0}

    async def fake_extract(prompt, system=None):
        called["n"] += 1
        return {"skills": ["Computer Vision"]}

    with patch("src.services.profile.llm_provider.llm_extract", side_effect=fake_extract):
        out = await github_enricher.llm_infer_github_skills(
            [], bio="", profile_readme="Portfolio: I ship computer-vision models.")

    assert called["n"] == 1, "a self-description alone should still call the LLM"
    assert out == ["Computer Vision"]


@pytest.mark.asyncio
async def test_llm_pass_empty_when_nothing_to_read():
    """No repos, no bio, no README → no LLM call at all (cost guard)."""
    called = {"n": 0}

    async def fake_extract(prompt, system=None):
        called["n"] += 1
        return {"skills": []}

    with patch("src.services.profile.llm_provider.llm_extract", side_effect=fake_extract):
        out = await github_enricher.llm_infer_github_skills([], bio="", profile_readme="")

    assert called["n"] == 0
    assert out == []


# ── enrich_cv_from_github stores the new prose ──────────────────────


def test_enrich_stores_bio_and_profile_readme():
    cv = CVData()
    cv = github_enricher.enrich_cv_from_github(cv, {
        "bio": "Name: Jane | Bio: SRE",
        "profile_readme": "I run Kubernetes at scale.",
        "repos_brief": [{"name": "r", "language": "Go"}],
    })
    assert cv.github_bio == "Name: Jane | Bio: SRE"
    assert cv.github_profile_readme == "I run Kubernetes at scale."


def test_enrich_does_not_wipe_prose_on_empty_reenrich():
    """A rate-limited partial re-enrich (empty bio/readme) must not erase the
    prose we already have — same guard as repos_brief."""
    cv = CVData(github_bio="keep me", github_profile_readme="keep me too")
    cv = github_enricher.enrich_cv_from_github(cv, {"bio": "", "profile_readme": ""})
    assert cv.github_bio == "keep me"
    assert cv.github_profile_readme == "keep me too"


# ── Finding 8 (Pillar-1 closeout audit) — authenticated caps ───────────────
#
# README reads were capped at 15 and dep-manifest reads at 10, out of
# MAX_REPOS=30, EVEN WHEN AUTHENTICATED (5000 req/hr — see the rate-limit
# maths in github_enricher.py and the notes on this fix). Anyone with more
# than ~15 public repos silently lost README prose and dependency proof for
# their older work. The unauthenticated path (real 60/hour ceiling) must stay
# exactly as conservative as before — that is the control.


def _make_repos(n: int) -> list[dict]:
    return [
        {"name": f"repo{i}", "language": "Python", "description": "x", "fork": False,
         "stargazers_count": 0, "topics": [], "pushed_at": None}
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_authenticated_enricher_reads_more_repos_than_old_caps(monkeypatch):
    """20 repos: more than the OLD caps (15 README / 10 deps), less than
    MAX_REPOS (30). With a token, every one of them must be read."""
    monkeypatch.setattr(github_enricher, "GITHUB_TOKEN", "tok")
    n = 20
    repos = _make_repos(n)

    async def fake_get_json(session, url):
        if url.endswith("repos?per_page=30&sort=pushed"):
            return repos
        if "/repos/octocat/repo" in url and url.endswith("/readme"):
            return _b64_readme("some readme prose")
        if "/languages" in url:
            return {"Python": 1}
        return None

    dep_calls: list[str] = []

    async def fake_dep_fetch(session, username, repo_name):
        dep_calls.append(repo_name)
        return []

    with patch.object(github_enricher, "_get_json", side_effect=fake_get_json), \
         patch.object(github_enricher, "_fetch_repo_frameworks", side_effect=fake_dep_fetch), \
         patch.object(github_enricher, "_fetch_pinned", new=AsyncMock(return_value=[])), \
         patch.object(github_enricher.aiohttp, "ClientSession", return_value=_make_async_session()):
        result = await github_enricher.fetch_github_profile("octocat")

    readmes_read = sum(1 for b in result["repos_brief"] if "readme_excerpt" in b)
    assert readmes_read == n, (
        f"authenticated README cap should reach all {n} repos (<= MAX_REPOS), "
        f"only {readmes_read} were read"
    )
    assert len(dep_calls) == n, (
        f"authenticated dep-manifest cap should reach all {n} repos, "
        f"only {len(dep_calls)} were probed"
    )


@pytest.mark.asyncio
async def test_unauthenticated_enricher_stays_conservative(monkeypatch):
    """CONTROL for the fix above. Same 20 repos, NO token — the unauthenticated
    path must be byte-for-byte unchanged (README_REPOS_UNAUTHED=4, dep cap=5),
    because that path genuinely can exhaust the real 60/hour limit."""
    monkeypatch.setattr(github_enricher, "GITHUB_TOKEN", "")
    n = 20
    repos = _make_repos(n)

    async def fake_get_json(session, url):
        if url.endswith("repos?per_page=30&sort=pushed"):
            return repos
        if "/repos/octocat/repo" in url and url.endswith("/readme"):
            return _b64_readme("some readme prose")
        if "/languages" in url:
            return {"Python": 1}
        return None

    dep_calls: list[str] = []

    async def fake_dep_fetch(session, username, repo_name):
        dep_calls.append(repo_name)
        return []

    with patch.object(github_enricher, "_get_json", side_effect=fake_get_json), \
         patch.object(github_enricher, "_fetch_repo_frameworks", side_effect=fake_dep_fetch), \
         patch.object(github_enricher.aiohttp, "ClientSession", return_value=_make_async_session()):
        result = await github_enricher.fetch_github_profile("octocat")

    readmes_read = sum(1 for b in result["repos_brief"] if "readme_excerpt" in b)
    assert readmes_read == github_enricher.README_REPOS_UNAUTHED == 4
    assert len(dep_calls) == 5, "unauthenticated dep-manifest cap must stay at 5"
