# RULES.md — Canonical Invariant Rules

This is the single source of truth for Job360's core rules. All code changes must comply.

---

## Core Rules

### Rule 1: All Keywords Dynamic and Personalized — Nothing Hard-Coded

All keywords must come from the job seeker's information — their CV, preferences, LinkedIn, GitHub. Nothing should be hard-coded in the codebase. No static keyword lists, no static keyword imports.

- `src/config/keywords.py` contains **only** domain-agnostic data: `LOCATIONS`, `VISA_KEYWORDS`, `KNOWN_SKILLS` (multi-domain skill database for CV parsing), `KNOWN_TITLE_PATTERNS`, `KNOWN_LOCATIONS`
- Sources access keywords via `self.relevance_keywords`, `self.job_titles`, `self.search_queries` (properties on `BaseJobSource`)
- When `search_config=None`, these properties return empty lists — no fallback defaults
- No source imports keyword lists directly from `keywords.py` for job filtering
- No source has its own default keyword list

**Violation examples:** A source file with `KEYWORDS = ["python", "javascript"]`, a default `relevance_keywords` list in any source class, `SearchConfig.from_defaults()`, importing `KEYWORDS`/`SEARCH_TERMS`/`JOB_TITLES` from `keywords.py` in a source.

### Rule 2: CV Mandatory — No CV = No Search

The orchestrator (`src/main.py`) returns early with zero results if no user profile exists. CV is the mandatory input. Preferences, LinkedIn, and GitHub are primary inputs that enrich the profile.

- `load_profile()` returns `None` → `run_search()` returns `{"total_found": 0, "new_jobs": 0, "sources_queried": 0}`
- Profile stored at `data/user_profile.json`
- All tests mock the profile via `_patch_profile()` or provide explicit `SearchConfig`

**Violation examples:** A code path that runs sources without a profile, a fallback profile, a "demo mode" that skips profile loading.

### Rule 3: Single Scoring Path

Only `JobScorer(config)` scores jobs — via `.score()` (legacy 4-component) or `.score_detailed()` (8-dimensional). No module-level `score_job()` function, no static scoring methods, no alternative scoring paths.

- `JobScorer` lives in `src/filters/skill_matcher.py`
- Constructor requires a `SearchConfig` — cannot instantiate without one
- Legacy: Title (0-40), Skill (0-40), Location (0-10), Recency (0-10)
- Detailed: Role (25), Skill (25), Seniority (10), Experience (10), Credentials (5), Location (10), Recency (10), Semantic (5)
- Penalties: Negative title keywords (-30), Foreign location (-15)

**Violation examples:** A standalone `score_job()` function, a `JobScorer.score_with_defaults()` method, scoring logic in a source file.
