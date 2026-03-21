# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Job360 is an automated UK job search system supporting **any professional domain**. Aggregates jobs from 48 sources, scores them 0-100 against a user profile, deduplicates, and delivers via CLI/email/Slack/Discord/CSV/Streamlit dashboard. A user profile with CV is **mandatory** — no CV = no search. All keywords come from the user's profile via `SearchConfig`. No hard-coded domain defaults.

## Commands

```bash
# Run
python -m src.cli run                              # Full pipeline
python -m src.cli run --source arbeitnow           # Single source
python -m src.cli run --dry-run --log-level DEBUG   # Debug mode
python -m src.cli run --no-email                    # Skip notifications

# Profile
python -m src.cli setup-profile --cv path/to/cv.pdf

# Other
python -m src.cli dashboard                        # Streamlit UI
python -m src.cli status                           # Last run stats
python -m src.cli sources                          # List all sources
python -m src.cli view --hours 24 --min-score 50   # Browse jobs
python -m src.cli pipeline --reminders             # Application tracking

# Tests (all HTTP mocked via aioresponses)
python -m pytest tests/ -v                         # All 658 tests
python -m pytest tests/test_scorer.py -v           # Single file
python -m pytest tests/test_scorer.py::test_name -v  # Single test
```

## Architecture

**Pipeline:** CLI (Click) → Orchestrator (`src/main.py`) → Sources (async) → Foreign filter → Embeddings → Legacy score → JD parse → Detailed 8D score → Feedback adjustment → Cross-encoder rerank → Deduplicator → SQLite DB + FTS5 → Notifications + Reports + CSV

**Key modules:** `src/main.py` (orchestrator, `SOURCE_REGISTRY`, `_build_sources()`), `src/cli.py` (Click CLI), `src/models.py` (Job dataclass), `src/config/settings.py` (env vars, `RATE_LIMITS`), `src/config/keywords.py` (domain-agnostic: LOCATIONS, VISA_KEYWORDS, KNOWN_SKILLS), `src/profile/` (CV parser, structured CV parser, preferences, keyword generator, LinkedIn/GitHub enrichment, skill graph, domain detector), `src/filters/` (scorer, deduplicator, description matcher, embeddings, jd_parser, reranker, feedback, hybrid_retriever), `src/pipeline/` (application tracker, reminders), `src/storage/` (async SQLite, user actions, CSV export), `src/notifications/` (email, Slack, Discord)

**Scoring:** Two scoring methods — `JobScorer(config).score()` (legacy: Title 0-40 + Skill 0-40 + Location 0-10 + Recency 0-10) and `JobScorer(config).score_detailed()` (8 dimensions: Role 25, Skill 25, Seniority 10, Experience 10, Credentials 5, Location 10, Recency 10, Semantic 5). Penalties: negative titles (-30), foreign location (-15). Threshold: `MIN_MATCH_SCORE=30`.

**Sources:** 48 total — 7 keyed APIs, 10 free APIs, 10 ATS boards, 8 RSS/XML, 7 HTML scrapers, 4 other, 1 market intel. All extend `BaseJobSource`, use `self.relevance_keywords`/`self.job_titles`/`self.search_queries` from SearchConfig.

## Workflow — Read, Write, Verify

Every code change must follow this order:

1. **Read & Explore** — Before editing ANY file, read it fully. Understand what the code does, how it connects to the rest of the system. Never edit blind. When working with any library or framework (Streamlit, aiohttp, Click, SQLAlchemy, etc.), use **Context7 MCP** to fetch the latest documentation so you're coding with up-to-date APIs, not outdated knowledge.
2. **Write** — Make the change (implementation, bug fix, refactor, whatever).
3. **Test & Verify** — After completing a logical change, run the relevant tests. Confirm they pass. Then update any affected MD files (CLAUDE.md, STATUS.md, ARCHITECTURE.md, etc.) if facts changed (test count, source count, scoring rules, architecture).

This is non-negotiable. No shortcuts.

## Core Rules (see RULES.md for detail)

1. **All keywords dynamic and personalized** — nothing hard-coded, no static imports; everything from the job seeker's profile via SearchConfig
2. **CV mandatory** — no CV = no search. Preferences, LinkedIn, GitHub are primary inputs.
3. **Single scoring path** — only `JobScorer(config).score()` and `JobScorer(config).score_detailed()`

## Important Patterns

- **Adding a source:** See `SOURCES.md` — 9-step checklist + 5 templates (free/keyed/ATS/RSS/scraper)
- **Dynamic keywords:** `self.relevance_keywords`, `self.job_titles`, `self.search_queries` — empty when no config
- **Testing patterns:** See `TESTING.md` — `_TEST_CONFIG`, `_patch_profile()`, `aioresponses` mocking, fixtures
- **Keyed source:** Accept `api_key` + `search_config=None` in `__init__`, return `[]` if no key
- **No CV = no search:** `main.py` returns early if no profile loaded

## Environment

- Python 3.9+, deps in `requirements.txt` (prod) / `requirements-dev.txt` (test)
- `.env` for API keys (see `.env.example`); free sources work without keys
- Data: `data/` (gitignored) — `jobs.db`, `user_profile.json`, `exports/`, `reports/`, `logs/`
- Validation: `scripts/validate_rules.py` (3 rules) and `scripts/validate_tooling.py` (structural lint) — pure stdlib, no src/ imports
- DB schema: `run_log` has columns `id, timestamp, total_found, new_jobs, sources_queried, per_source(JSON)`

## Tools

### Validation Scripts (run manually when needed)
- `python scripts/validate_rules.py` — checks 3 core rules

### MCP Servers

- **Context7** — Fetches latest library/framework documentation. **When to use**: During explore/plan stages whenever you're about to write code that uses a library (Streamlit, aiohttp, Click, aiosqlite, sentence-transformers, etc.). Always check Context7 before assuming API syntax — outdated knowledge causes first-attempt failures.
- **SQLite** — Direct SQL queries on `data/jobs.db`. **When to use**: When inspecting actual job data, debugging scoring results, checking run history, verifying DB state, or answering questions about stored jobs. Tables: `jobs`, `run_log`, `user_actions`, `applications`, `schema_version`, `jobs_fts`.

## Related Documentation

| File | Unique Purpose |
|------|---------------|
| `RULES.md` | Invariant rules — "What must NEVER change?" |
| `TESTING.md` | Test patterns — "How do I write/run tests?" |
| `SOURCES.md` | Source patterns — "How do I add/modify sources?" |
| `STATUS.md` | Progress tracking — "What's done, what's next?" |
| `ARCHITECTURE.md` | Deep reference — "How does the system work internally?" |
| `CHANGELOG.md` | Version history — "What changed and when?" |
