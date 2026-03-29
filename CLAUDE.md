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
python -m src.cli setup-profile --cv cv.pdf --linkedin export.zip --github username

# Other
python -m src.cli dashboard                        # Streamlit UI
python -m src.cli status                           # Last run stats
python -m src.cli sources                          # List all 48 sources
python -m src.cli view --hours 24 --min-score 50   # Browse jobs
python -m src.cli pipeline --reminders             # Application tracking

# Validation & QA Benchmark
python -m src.cli validate                         # Validate last 7d, 3 per source
python -m src.cli validate --per-source 5          # Deeper check (5 per source)
python -m src.cli validate --source greenhouse     # Single source deep-check
python -m src.cli validate --min-score 40          # Only validate decent matches

# Tests (all HTTP mocked via aioresponses)
python -m pytest tests/ -v                         # All 745+ tests (3 skip on Windows)
python -m pytest tests/test_scorer.py -v           # Single file
python -m pytest tests/test_scorer.py::test_name -v  # Single test

# Validation
python scripts/validate_rules.py                   # Checks 3 core rules (pure stdlib)
python scripts/validate_tooling.py                 # Structural lint (pure stdlib)
```

## Architecture

### Full Pipeline (in execution order)

```
CLI (Click) → Orchestrator (src/main.py)
  1. load_profile() → UserProfile (returns early if no CV)
  2. generate_search_config(profile) → SearchConfig
  3. _build_sources(session, search_config) → 48 source instances
  4. asyncio.gather → all sources fetch concurrently (rate-limited, circuit-breaker, 60s per-source timeout)
  5. Foreign filter: is_foreign_only() hard-removes non-UK jobs
  6. Embeddings: all-MiniLM-L6-v2 encodes job text → 384-dim vectors
  7. Legacy score: scorer.score(job) → Title+Skill+Location+Recency (0-100)
  8. JD parse: parse_jd(description, user_skills) → structured skills/experience/qualifications (profile-aware)
  9. Detailed 8D score: scorer.score_detailed(job, parsed_jd, cv_data) → OVERWRITES legacy
 10. Feedback: liked/rejected history adjusts score ±10
 11. Rerank: cross-encoder (ms-marco-MiniLM-L-6-v2) re-scores top-50
 12. Dedup: two-pass — normalized key, then description similarity ≥ 0.80 (with text normalization)
 13. Per-source quality metrics: fetched/above_threshold/stored per source
 14. Store: SQLite + FTS5 sync → Notifications + Reports + CSV
```

### Key Modules

| Module | Purpose |
|--------|---------|
| `src/main.py` | Orchestrator: `run_search()`, `SOURCE_REGISTRY` dict, `_build_sources()` |
| `src/cli.py` | Click CLI with run/view/dashboard/status/sources/pipeline/setup-profile |
| `src/models.py` | `Job` dataclass with `normalized_key()` for dedup |
| `src/config/settings.py` | Env vars, paths, `RATE_LIMITS`, `MIN_MATCH_SCORE=30` |
| `src/config/keywords.py` | Domain-agnostic only: `LOCATIONS`, `VISA_KEYWORDS`, `KNOWN_SKILLS`, `KNOWN_TITLE_PATTERNS` |
| `src/config/companies.py` | ATS company slugs (200+ companies across 10 platforms) |
| `src/profile/models.py` | `CVData`, `UserPreferences`, `UserProfile`, `SearchConfig` dataclasses |
| `src/profile/keyword_generator.py` | `generate_search_config(UserProfile)` → `SearchConfig` |
| `src/profile/skill_graph.py` | Skill inference graph: 446 relationships, 1126 edges |
| `src/profile/domain_detector.py` | Detects 10 professional domains from profile |
| `src/sources/base.py` | `BaseJobSource` ABC — `_get_json()`, `_get_text()`, `safe_fetch()` (circuit breaker), `_gather_queries()` (parallel batch execution), synonym-aware `_relevance_match()` |
| `src/filters/skill_matcher.py` | `JobScorer` class: `score()` (legacy) + `score_detailed()` (8D), `is_foreign_only()`, `_excluded_penalty()` |
| `src/filters/description_matcher.py` | 374 synonym groups for fuzzy skill matching (fetch + scoring) |
| `src/filters/deduplicator.py` | Two-pass: normalized key + description similarity (SequenceMatcher 0.80, text-normalized) |
| `src/filters/embeddings.py` | Sentence-transformer bi-encoder (all-MiniLM-L6-v2, 384-dim), profile embedding includes about_me |
| `src/filters/jd_parser.py` | Structured JD parsing + `detect_job_type()`, profile-aware via `user_skills` parameter |
| `src/filters/reranker.py` | Cross-encoder reranking (ms-marco-MiniLM-L-6-v2, top-50) |
| `src/filters/feedback.py` | Liked/rejected signals → ±10 score adjustment |
| `src/storage/database.py` | Async SQLite (aiosqlite), schema v6, 6 tables |
| `src/storage/csv_export.py` | CSV export with 24 columns including 8D scores + skill match lists |
| `src/storage/user_actions.py` | Liked/Applied/Not Interested per job |
| `src/pipeline/tracker.py` | Application stages: applied → interview → offer/rejected |
| `src/diagnostics.py` | `PipelineDiagnostics` — collects timing, score distribution, funnel, dedup, LLM, feedback, reranker stats |
| `src/validation/sampler.py` | Stratified job sampling for QA validation (by source + score range) |
| `src/validation/checker.py` | URL/title/date/description validation against live web pages |
| `src/validation/report.py` | Validation markdown + JSON report generation with per-source confidence |
| `src/llm/client.py` | Multi-provider LLM pool: Groq, Cerebras, Gemini, DeepSeek, OpenRouter, SambaNova |
| `src/llm/jd_enricher.py` | LLM-enriched JD parsing for top-50 candidates (non-destructive merge) |
| `src/llm/cache.py` | Disk-based LLM response cache (SHA-256 keyed, with hit/miss stats) |
| `src/notifications/` | Email (Gmail SMTP), Slack (Block Kit), Discord (Embeds) |

### Scoring

**Legacy `score()`:** Title 0-40 + Skill 0-40 + Location 0-10 + Recency 0-10. Penalties: negative titles (-30), foreign location (-15).

**Detailed `score_detailed()`** (8 dimensions, overwrites legacy in pipeline):

| Dimension | Max | What |
|-----------|-----|------|
| Role | 20 | Title match — word-overlap scoring with core domain word weighting |
| Skill | 25 | Skill overlap with synonym matching |
| Seniority | 10 | Experience level alignment (prefers user-stated over CV-inferred) |
| Experience | 10 | Years requirement vs CV |
| Credentials | 5 | Degree/certification match |
| Location | 10 | Geographic match + work arrangement bonus (±2 for remote/onsite pref) |
| Recency | 10 | Posting freshness |
| Semantic | 10 | Embedding cosine similarity + industry mention bonus (+2) |

**Penalties:** negative title keywords (-30), negative description keywords (-15), excluded skills (-5/match, cap -15).

### Profile → SearchConfig Flow

```
UserProfile
  ├─ cv_data (raw_text, skills, job_titles, education, certifications)
  ├─ preferences (target_titles, additional_skills, locations, ...)
  └─ [optional] LinkedIn + GitHub enrichment
        │
        ▼ keyword_generator.generate_search_config()
  SearchConfig
  ├─ job_titles: prefs.titles + cv.titles (deduped)
  ├─ primary/secondary/tertiary_skills: all skills split into 3 tiers
  ├─ relevance_keywords: lowercased words from titles + skills + industries + domains
  ├─ negative_title_keywords: from prefs.negative_keywords
  ├─ locations: UK defaults + prefs.preferred_locations
  ├─ core_domain_words / supporting_role_words: from title analysis
  ├─ search_queries: 3 types (title×location, skill-combo, title+skill hybrid), capped at 15
  ├─ excluded_skills: from prefs.excluded_skills
  ├─ work_arrangement: from prefs ("remote"/"hybrid"/"onsite")
  ├─ target_experience_level: from prefs (overrides CV-inferred)
  ├─ about_me: from prefs (used in profile embedding)
  ├─ industries: from prefs (relevance keywords + scoring bonus)
  └─ detected_domains: auto-detected from profile via domain_detector
```

Profile completeness: `is_complete` requires `cv_data.raw_text` OR `target_job_titles` OR `additional_skills`.

### Sources: 48 Total

All extend `BaseJobSource`, use `self.relevance_keywords`/`self.job_titles`/`self.search_queries` from SearchConfig. When `search_config=None`, these return `[]`.

- **7 keyed APIs** (Reed, Adzuna, JSearch, Jooble, GoogleJobs, Careerjet, Findwork) — accept `api_key`, return `[]` if missing
- **10 free APIs** (Arbeitnow, RemoteOK, Jobicy, Himalayas, Remotive, DevITJobs, LandingJobs, AIJobs, TheMuse, NoFluffJobs)
- **10 ATS boards** (Greenhouse, Lever, Workable, Ashby, SmartRecruiters, Pinpoint, Recruitee, Workday, Personio, SuccessFactors)
- **8 RSS/XML** (jobs.ac.uk, NHS, WorkAnywhere, WeWorkRemotely, RealWorkFromAnywhere, BioSpace, UniJobs, FindAJob)
- **7 HTML scrapers** (LinkedIn, JobTensor, Climatebase, 80KHours, BCSJobs, AIJobsGlobal, AIJobsAI)
- **4 other** (HackerNews, YCCompanies, JobSpy, Nomis)
- **1 market intel** (Nomis/ONS vacancy statistics)

### Database (schema v6)

6 tables: `jobs` (with `job_type`, `match_data`, `embedding` columns), `jobs_fts` (FTS5 virtual table), `run_log`, `user_actions`, `applications`, `schema_version`. Dedup via `UNIQUE(normalized_company, normalized_title)`.

## Workflow — Read, Write, Verify

Every code change must follow this order:

1. **Read & Explore** — Before editing ANY file, read it fully. Understand what the code does, how it connects to the rest of the system. Never edit blind. When working with any library or framework (Streamlit, aiohttp, Click, aiosqlite, sentence-transformers, etc.), use **Context7 MCP** to fetch the latest documentation so you're coding with up-to-date APIs, not outdated knowledge.
2. **Write** — Make the change (implementation, bug fix, refactor, whatever).
3. **Test & Verify** — After completing a logical change, run the relevant tests. Confirm they pass. Then update any affected MD files (CLAUDE.md, STATUS.md, ARCHITECTURE.md, etc.) if facts changed (test count, source count, scoring rules, architecture).

This is non-negotiable. No shortcuts.

## Core Rules (see RULES.md for detail)

1. **All keywords dynamic and personalized** — nothing hard-coded, no static imports; everything from the job seeker's profile via SearchConfig
2. **CV mandatory** — no CV = no search. Preferences, LinkedIn, GitHub are primary inputs.
3. **Single scoring path** — only `JobScorer(config).score()` and `JobScorer(config).score_detailed()`

## Important Patterns

- **Adding a source:** See `SOURCES.md` — 9-step checklist + 5 templates (free/keyed/ATS/RSS/scraper). Touches 5-7 files including `main.py` (import + registry + `_build_sources`), `settings.py` (rate limits), `test_sources.py`, `test_main.py` (`_mock_free_sources`), `test_cli.py` (registry count)
- **Dynamic keywords:** `self.relevance_keywords`, `self.job_titles`, `self.search_queries` — empty when no config
- **Keyed source:** Accept `api_key` + `search_config=None` in `__init__`, return `[]` if no key
- **No CV = no search:** `main.py` returns early if no profile loaded
- **BaseJobSource helpers:** `_get_json()` (2 retries, exp backoff), `_get_text()`, `_post_json()`, `safe_fetch()` (circuit breaker — skips after 3 consecutive failures), `_gather_queries()` (parallel batch execution for slow sources)
- **Speed tuning:** `REQUEST_TIMEOUT=15s`, `MAX_RETRIES=2`, `RETRY_BACKOFF=[1,3]`, per-source timeout=60s. Slow sources (AIJobsGlobal, JobSpy) use `_gather_queries(batch_size=3)` for concurrent fetching.
- **Testing patterns:** See `TESTING.md` — `_TEST_CONFIG`, `_patch_profile()`, `aioresponses` mocking, `_run()` async helper, `_mock_free_sources()` for integration tests
- **Shared fixtures** (`tests/conftest.py`): `sample_ai_job`, `sample_unrelated_job`, `sample_duplicate_jobs`, `sample_visa_job`

## Environment

- Python 3.9+, deps in `requirements.txt` (prod) / `requirements-dev.txt` (test)
- `.env` for API keys (see `.env.example`); 41 of 48 sources work without keys
- Data: `data/` (gitignored) — `jobs.db`, `user_profile.json`, `exports/`, `reports/`, `logs/`
- CI: `.github/workflows/tests.yml` — pytest on push/PR (Python 3.9/3.11/3.13)
- 3 tests skip on Windows (bash-only tests for `setup.sh` and `cron_run.sh`)

## Tools

### MCP Servers

- **Context7** — Fetches latest library/framework documentation. **When to use**: During explore/plan stages whenever you're about to write code that uses a library (Streamlit, aiohttp, Click, aiosqlite, sentence-transformers, etc.). Always check Context7 before assuming API syntax — outdated knowledge causes first-attempt failures.
- **SQLite** — Direct SQL queries on `data/jobs.db`. **When to use**: When inspecting actual job data, debugging scoring results, checking run history, verifying DB state, or answering questions about stored jobs. Tables: `jobs`, `run_log`, `user_actions`, `applications`, `schema_version`, `jobs_fts`.

## Dual Terminal Testing Workflow

**This workflow runs every session.** When the user is manually testing Job360, Claude Code sets up log watchers automatically — the user never has to configure this.

### Setup (Claude Code does this at session start when testing)

```bash
# Terminal 1 (user): run the dashboard
python -m src.cli dashboard

# Terminal 2 (Claude Code): tail both logs in background
tail -f data/logs/job360.log    # pipeline: fetching, scoring, dedup, DB
tail -f data/logs/dashboard.log  # Streamlit: profile save, search trigger, UI errors
```

Both watchers run via `run_in_background` so Claude Code can pull output on demand without blocking.

### Log Files

| File | What it captures |
|------|-----------------|
| `data/logs/job360.log` | Source fetching, HTTP errors, timeouts, scoring, dedup, DB writes, run stats |
| `data/logs/dashboard.log` | Profile save, search trigger, Streamlit errors, UI crashes |

### Complete Feedback Loop — What Each Output Provides

| Output | What Claude Learns | Diagnoses |
|--------|-------------------|-----------|
| `data/logs/job360.log` | Phase timing, source errors, warnings, per-source counts | Speed bottlenecks, broken sources, rate limits |
| `data/logs/dashboard.log` | Profile saves, search triggers, UI crashes | Dashboard bugs, profile parsing issues |
| `data/exports/*.csv` | Score per dimension (role/skill/seniority/etc), URLs, salary, visa | Scoring quality, broken URLs, missing data, dedup effectiveness |
| `data/reports/*.md` | Per-source funnel (fetched→filtered→scored→stored) | Source ROI, wasted fetches, dedup loss |
| `PIPELINE_HEALTH` log line | JSON summary: total_fetched, new_stored, sources_active | Quick health check without parsing full logs |

After each run, Claude analyzes all 4 outputs to identify: broken sources, score compression, data gaps, timing regressions, and dedup issues — then fixes them proactively.

### How to report failures

The user does NOT need to paste logs or tracebacks. Just say:

- **"it's stuck at 37/48"** — Claude pulls job360.log, finds which source is hanging
- **"dashboard crashed"** — Claude pulls dashboard.log, finds the traceback
- **"error after saving profile"** — Claude pulls both logs, correlates the timeline
- **"search finished but scores look wrong"** — Claude queries the DB directly via SQLite MCP

Claude Code will:
1. Pull the live logs instantly
2. Identify exactly where and why it broke (source, line, HTTP status, timing)
3. Explain the failure before fixing — never blind-fix
4. Use `/debug` or `/implement` skills to fix once root cause is understood

### Known slow sources (diagnosed and optimized)

- `aijobs_global` — WordPress AJAX endpoint, now parallelized (batch_size=3, capped at 5 queries)
- `indeed` (JobSpy) — blocking web scraper, now parallelized (batch_size=3, capped at 4 queries)
- `linkedin` — anti-ban delays, reduced from 3s to 1.5s between queries

## QA Validation & Benchmark System

### Autonomous QA Loop
The system supports autonomous quality improvement via the validate command:
```
Run search → Validate against live URLs → Read report → Fix issues → Repeat → Confidence ↑
```

### Benchmark Tracking
- `data/reports/BENCHMARK.md` — Living document with per-source confidence scores (updated each iteration)
- `data/reports/validation_*.md` — Per-run human-readable validation reports
- `data/reports/validation_*.json` — Machine-readable benchmark snapshots for tracking over time

### What Gets Validated (per sampled job)
1. **URL alive** — HTTP status code (200=alive, 403=blocked, 404=dead)
2. **Title match** — Stored title vs scraped `<title>`/`<h1>`/`og:title`/JSON-LD name
3. **Date accuracy** — Stored date_found vs actual posting date (time bucket comparison)
4. **Description match** — Stored description vs scraped page content (similarity ratio)

### Per-Source Confidence
Each source gets a weighted confidence score: `URL×0.30 + Title×0.25 + Date×0.25 + Description×0.20`

Target: **90%+ confidence** per active source = production-ready.

### Known Source Limitations
- **Workday**: URLs are session-based and expire within 1-4 hours. This is a Workday API design limitation, not a bug.
- **Some sites block bots**: climatebase, weworkremotely return 403. Our stored data may still be correct.
- **Date extraction**: Many sites don't expose posting dates in parseable meta tags. N/A results are validator limitations, not pipeline bugs.

## Related Documentation

| File | Unique Purpose |
|------|---------------|
| `RULES.md` | Invariant rules — "What must NEVER change?" |
| `TESTING.md` | Test patterns — "How do I write/run tests?" |
| `SOURCES.md` | Source patterns — "How do I add/modify sources?" |
| `STATUS.md` | Progress tracking — "What's done, what's next?" |
| `ARCHITECTURE.md` | Deep reference — "How does the system work internally?" |
| `CHANGELOG.md` | Version history — "What changed and when?" |
