# Job360 Architecture

> **Current state lives in `docs/pillars/`** — three code-verified pillar docs (User, Search & Match Engine, Job Providers) plus a glossary and runbook are the *authoritative* architecture reference today. This file is preserved for historical continuity and gives a higher-level system overview; for any specific claim about the codebase, cross-check `docs/pillars/` first.

## System Overview

Job360 is a UK-focused multi-domain job search aggregator. It fetches jobs from **46 source instances** (47 keys in `SOURCE_REGISTRY`; `indeed`+`glassdoor` share `JobSpySource`), scores them against a per-user profile, deduplicates via a four-layer cascade, optionally enriches the high-scorers with an LLM-extracted 18-field structured schema, optionally encodes semantic embeddings into ChromaDB, and delivers results through multiple channels (CLI, email, Slack, Discord, Telegram, webhook, CSV, and a Next.js + FastAPI dashboard).

**Critical inflection (2026-04-09, commit `3ba1342`):** `backend/src/core/keywords.py` was emptied — every default `JOB_TITLES`/`PRIMARY_SKILLS`/`SECONDARY_SKILLS`/`TERTIARY_SKILLS`/`RELEVANCE_KEYWORDS`/`NEGATIVE_TITLE_KEYWORDS` list is now `[]`. **The system requires a user profile.** Without one, the legacy module-level `score_job()` path scores against empty lists and yields near-zero results. Only `LOCATIONS` (25) and `VISA_KEYWORDS` (8) remain — both domain-agnostic.

```
User Input                    Pipeline (Pillar 2: 6 stages)          Output
-----------                   -----------------------------          ------
                          +-> Sources (46) -+                    +-> Email (Apprise per-user)
CLI / Frontend   --+      |  (async fetch)  |                    +-> Slack / Discord / Telegram
                   |      v   tiered cadence v                   +-> Webhook
Profile (CV+Prefs) +-> Fetch -> Prefilter -> Score -> Dedup -+   +-> CSV
  + LinkedIn PDF   |          (3 gates)   (9-dim)  (4 layer)|   +-> Markdown report
  + GitHub API     |                                        v   +-> Next.js dashboard (per-user)
.env (API keys) ---+                              Enrich (opt-in, LLM)
                                                  Store -> Postgres catalog (jobs table)
                                                  Embed (opt-in) -> ChromaDB
```

Two opt-in feature flags gate the advanced surfaces (both default OFF; CLAUDE.md rule #18):

- `ENRICHMENT_ENABLED=true` → LLM enrichment + multi-dimensional scoring activates
- `SEMANTIC_ENABLED=true` → embeddings + ChromaDB + hybrid retrieval (RRF fusion of keyword + **BM25** + vector rankings, then **cross-encoder rerank**) + ESCO skill normalisation activate

---

## Directory Structure

> **Post-Phase-4 layout** (commit `a814ae8`, 2026-03-XX): `config/` → `core/`, `filters/` + `notifications/` + `profile/` → `services/{...}`, `storage/` → `repositories/`. 197 import rewrites across 51 files. The old paths in earlier docs no longer exist.

```
job360/
├── backend/
│   ├── main.py                       # FastAPI uvicorn entry (thin; imports src/api/main.py)
│   ├── pyproject.toml                # Deps + dev + indeed extras, ruff/mypy/pytest config
│   ├── data/                         # Runtime (gitignored): jobs.db, user_profile.json, chroma/, exports/, reports/, logs/
│   ├── migrations/                   # 23 forward/reverse SQL migrations (0000 → 0022) + runner.py
│   ├── src/
│   │   ├── main.py                   # Orchestrator: run_search(), SOURCE_REGISTRY (47 keys → 46 instances), _build_sources()
│   │   ├── cli.py                    # Click CLI: run, api, status, sources, view, setup-profile
│   │   ├── cli_view.py               # Rich terminal table viewer
│   │   ├── models.py                 # Job dataclass + normalized_key() — DB UNIQUE + dedup Layer-1
│   │   ├── api/                      # FastAPI: lifespan, CORS, dependencies, middleware, errors, 12 route modules (64 endpoints)
│   │   │   └── routes/               # health, client_log, jobs, actions, profile, search, pipeline, auth, channels, notifications, notification_rules, runs
│   │   ├── core/                     # (post-Phase-4 rename from config/)
│   │   │   ├── settings.py           # Env vars, RATE_LIMITS (47 entries), thresholds, feature flags
│   │   │   ├── keywords.py           # LOCATIONS (25) + VISA_KEYWORDS (8); all other lists [] post-3ba1342
│   │   │   ├── companies.py          # ATS company slugs (~264 across 11 platforms)
│   │   │   ├── skill_synonyms.py     # 493-entry alias dict (k8s↔kubernetes, ...)
│   │   │   ├── fx.py                 # 18-currency → GBP rates
│   │   │   └── tenancy.py            # DEFAULT_TENANT_ID UUID for CLI/legacy rows
│   │   ├── services/                 # (post-Phase-4 merge of filters/ + notifications/ + profile/)
│   │   │   ├── skill_matcher.py      # JobScorer + score_job; 9-field ScoreBreakdown
│   │   │   ├── scoring_dimensions.py # Batch-2.9 multi-dim scorers (seniority/salary/visa/workplace)
│   │   │   ├── prefilter.py          # 3-stage cascade (location → experience → skill)
│   │   │   ├── deduplicator.py       # 4-layer dedup (exact → fuzzy → TF-IDF → embedding repost)
│   │   │   ├── salary.py             # normalize_salary() hourly→annual GBP
│   │   │   ├── domain_classifier.py  # tech / healthcare / academia / education / climate
│   │   │   ├── feed.py               # FeedService (per-user user_feed SSOT)
│   │   │   ├── ghost_detection.py    # stale → confirmed_expired lifecycle
│   │   │   ├── scheduler.py          # TieredScheduler (poll: 60s ATS / 5m reed / 15m workday+RSS / 60m rest; fetch ceiling 240s ATS / 60s others)
│   │   │   ├── circuit_breaker.py    # 5-fail/300s per-source state machine
│   │   │   ├── conditional_cache.py  # 256-entry FIFO for ETag/Last-Modified
│   │   │   ├── llm_matcher.py        # Engine #4: LLM judge (MATCHER_ENABLED; MatchVerdict persisted onto user_feed)
│   │   │   ├── job_enrichment.py     # enrich_batch() (opt-in)
│   │   │   ├── job_enrichment_schema.py  # 18-field Pydantic JobEnrichment + 8 enums
│   │   │   ├── embeddings.py         # encode_job() via sentence-transformers (opt-in, lazy)
│   │   │   ├── vector_index.py       # ChromaDB wrapper (opt-in, lazy)
│   │   │   ├── retrieval.py          # BM25 + RRF fusion + cross-encoder rerank (opt-in)
│   │   │   ├── auth/                 # passwords (argon2id), sessions (HMAC cookies)
│   │   │   ├── channels/             # crypto (Fernet), dispatcher (Apprise lazy — the only send path, see Notification System below)
│   │   │   ├── notifications/        # report_generator only (legacy CLI markdown/CSV summaries) — no more per-channel classes
│   │   │   └── profile/              # cv_parser, llm_provider, linkedin_parser, github_enricher, models, preferences, storage, keyword_generator
│   │   ├── repositories/             # (post-Phase-4 rename from storage/)
│   │   │   ├── database.py           # Async Postgres (psycopg3 via pg.py; `aiosqlite` name kept as a compat alias) + 23-migration forward-compat schema
│   │   │   └── csv_export.py
│   │   ├── sources/                  # (post-Phase-2 split into 6 category subfolders)
│   │   │   ├── base.py               # BaseJobSource ABC: retry, rate limit, conditional fetch, _is_uk_or_remote
│   │   │   ├── apis_keyed/   (8)     # reed, adzuna, jsearch, jooble, google_jobs, careerjet, findwork, gov_apprenticeships
│   │   │   ├── apis_free/    (9)     # arbeitnow, remoteok, jobicy, himalayas, remotive, devitjobs, landingjobs, aijobs, hn_jobs, teaching_vacancies
│   │   │   ├── ats/          (11)    # greenhouse, lever, workable, ashby, smartrecruiters, pinpoint, recruitee, workday, personio, successfactors, rippling
│   │   │   ├── feeds/        (8)     # jobs_ac_uk, nhs_jobs, nhs_jobs_xml, workanywhere, weworkremotely, realworkfromanywhere, biospace, uni_jobs
│   │   │   ├── scrapers/     (5)     # linkedin, climatebase, eightykhours, bcs_jobs, aijobs_ai
│   │   │   └── other/        (4)     # indeed (JobSpySource → indeed+glassdoor), hackernews, themuse, nofluffjobs
│   │   ├── workers/                  # ARQ tasks (lazy arq import; pure-async for tests)
│   │   │   └── tasks.py              # score_and_ingest, send_notification, send_daily_digest, nightly_ghost_sweep, enrich_job_task
│   │   └── utils/
│   │       ├── logger.py             # Structured logging: console + data/logs/job360.log + job360.jsonl (JSON) + audit.log; run_uuid + request_id correlation ids
│   │       ├── rate_limiter.py       # Async semaphore + delay
│   │       ├── telemetry.py          # MatcherTelemetry counters (Engine 4)
│   │       └── time_buckets.py
│   └── tests/                        # 1,636 collected / 1,638 total (2 deselected) across 100+ files (defer to runtime count)
├── frontend/                         # Next.js 16 + React 19 + Tailwind 4 + shadcn
│   ├── src/app/                      # App Router pages (server/client split; params is Promise<...> per Next.js 16)
│   ├── src/components/{ui,jobs,profile,pipeline,layout}/
│   └── src/lib/{api.ts,types.ts,utils.ts}
├── docs/
│   ├── pillars/                      # 3 pillar manuals + glossary + runbook (THE current architecture reference)
│   └── ...                           # IMPLEMENTATION_LOG, plans/, research/, reviews/, step_*_plan.md
├── .env.example
└── CLAUDE.md                         # Canonical AI agent instructions (24 hard rules)
```

---

## Data Flow: Pipeline Run

### 1. Profile Loading (`main.py:run_search`)

```python
profile = load_profile()                    # backend/data/user_profile.json
if profile and profile.is_complete:
    search_config = generate_search_config(profile)  # UserProfile -> SearchConfig
    scorer = JobScorer(search_config)                 # Dynamic scorer
else:
    search_config = None                              # Use defaults
    scorer = None                                     # Use score_job()
```

`UserProfile.is_complete` returns `True` if the profile has either `cv_data.raw_text` or any `target_job_titles` / `additional_skills` in preferences.

### 2. Source Instantiation (`main.py:_build_sources`)

All 46 sources get `search_config` passed through:
```python
ReedSource(session, api_key=REED_API_KEY, search_config=sc)
ArbeitnowSource(session, search_config=sc)
GreenhouseSource(session, search_config=sc)
# ... etc for all 46
```

The `_build_sources()` function groups sources into labeled groups (A through K) and instantiates all of them. When `--source <name>` is passed, it filters to just the matching source. Special case: `--source glassdoor` maps to `JobSpySource` (same as `indeed`).

### 3. SOURCE_REGISTRY

`SOURCE_REGISTRY` is a `dict[str, type]` mapping 47 source names to their classes. It serves two purposes:
1. CLI `--source` validation — Click uses it for `click.Choice(sorted(SOURCE_REGISTRY.keys()))`
2. `sources` command — lists all available source names
3. Test assertion — `test_cli.py` asserts `len(SOURCE_REGISTRY) == 47` and checks the exact set of keys

Note: `"indeed"` and `"glassdoor"` both map to `JobSpySource`, so there are 47 registry entries but 46 unique classes.

### 4. Keyword Resolution (`base.py` properties)

Each source accesses keywords via properties that fall back to defaults:
```python
class BaseJobSource:
    @property
    def relevance_keywords(self):
        if self._search_config is not None:
            return self._search_config.relevance_keywords  # Dynamic
        return _DEFAULT_RELEVANCE_KEYWORDS                  # Hard-coded AI/ML

    @property
    def job_titles(self):
        if self._search_config is not None:
            return self._search_config.job_titles
        return _DEFAULT_JOB_TITLES

    @property
    def search_queries(self):
        if self._search_config is not None and self._search_config.search_queries:
            return self._search_config.search_queries
        return []  # Sources use their own fallback lists
```

Sources that use `self.search_queries` with their own fallback lists: JSearch, LinkedIn, NHS Jobs.

### 5. Fetch (async, concurrent)

```python
results = await asyncio.gather(*[_safe_fetch(s) for s in sources])
# Each source filters by self.relevance_keywords in fetch_jobs()
# Fetch ceiling: 240s for ATS-category sources / 60s for all others
#   resolved by TieredScheduler.resolve_fetch_timeout() inside _safe_fetch()
# Each _get_json/_get_text has 30s timeout (REQUEST_TIMEOUT)
```

`_safe_fetch` calls `TieredScheduler.resolve_fetch_timeout(source)` to pick the ceiling (240 s for ATS, 60 s for others), wraps the call in `asyncio.wait_for`, and catches both `TimeoutError` and general `Exception`, logging warnings/errors but never crashing the pipeline.

### 6. Scoring

```python
for job in all_jobs:
    if scorer:  # Profile exists
        job.match_score = scorer.score(job)       # Dynamic keywords
        job.visa_flag = scorer.check_visa_flag(job)
    else:       # No profile
        job.match_score = score_job(job)           # Hard-coded keywords
        job.visa_flag = check_visa_flag(job)
    job.experience_level = detect_experience_level(job.title)
```

### 7. Deduplication

```python
unique = deduplicate(all_jobs)
# Groups by (normalized_company, normalized_title)
# Keeps highest match_score, then most complete data
```

### 8. Output Pipeline

```python
# Filter by MIN_MATCH_SCORE (30)
unique_jobs = [j for j in unique_jobs if j.match_score >= MIN_MATCH_SCORE]

# Check against DB for new-ness
for job in unique_jobs:
    if not await db.is_job_seen(job.normalized_key()):
        await db.insert_job(job)
        new_jobs.append(job)

# Sort by (match_score, salary_in_range) descending
new_jobs.sort(key=lambda j: (j.match_score, salary_in_range(j)), reverse=True)

# Export CSV to backend/data/exports/
# Generate markdown report to backend/data/reports/
# Send notifications (email, Slack, Discord) if configured and --no-email not set
# Print time-bucketed summary to console
# Log run to run_log table
```

In dry-run mode: scoring and dedup still happen, but no DB writes and no notifications.

---

## Scoring Algorithm Detail

**Total: 0-100 points**

| Component | Max Points | How |
|-----------|-----------|-----|
| Title match | 40 | Exact match = 40, substring = 20, partial keyword overlap = 5*core + 3*support (capped at 20) |
| Skill match | 40 | primary skills = 3 pts each, secondary = 2, tertiary = 1 (capped at 40) |
| Location | 10 | UK city = 10, remote = 8, unknown = 0 |
| Recency | 10 | <=1 day = 10, <=3d = 8, <=5d = 6, <=7d = 4, older = 0 |
| **Penalties** | | |
| Negative title | -30 | Title contains excluded keywords (60 default entries across 12 categories) |
| Foreign location | -15 | Location matches foreign indicators (US cities/states, EU countries, etc.) |

**Score = title + skill + location + recency - penalties** (clamped to 0-100)

### Dynamic vs Static Scoring

| | `score_job()` (static) | `JobScorer(config).score()` (dynamic) |
|---|---|---|
| Title list | `JOB_TITLES` from keywords.py | `config.job_titles` from profile |
| Skill lists | `PRIMARY/SECONDARY/TERTIARY_SKILLS` | `config.primary/secondary/tertiary_skills` |
| Core words | Hard-coded AI/ML set | `config.core_domain_words` from titles |
| Support words | Hard-coded role set | `config.supporting_role_words` from titles |
| Negatives | `NEGATIVE_TITLE_KEYWORDS` | `config.negative_title_keywords` from prefs |
| Location/Recency | Same | Same (always UK-focused) |

Note: as of 2026-04-09 (commit `3ba1342`) all default keyword lists in `keywords.py` are `[]`. The static `score_job()` path therefore produces near-zero scores without a profile — only `LOCATIONS` and `VISA_KEYWORDS` remain.

### Matching Engine Stack (four engines, all default OFF except #1)

| # | Engine | Service | Flag | Default |
|---|--------|---------|------|---------|
| 1 | Keyword | `services/skill_matcher.py` (`JobScorer`, 4-component 0–100) | always on | ON |
| 2 | Dimensions | `services/scoring_dimensions.py` (+30 seniority/salary/visa/workplace, `skill_matcher.py:519-536`; data from the enrichment step `services/job_enrichment.py`) | `ENRICHMENT_ENABLED` | false |
| 3 | Hybrid | `services/embeddings.py` + `vector_index.py` + `retrieval.py` | `SEMANTIC_ENABLED` | false |
| 4 | LLM judge | `services/llm_matcher.py` (`MatchVerdict`) | `MATCHER_ENABLED` | false |

**Engine 4 — LLM judge detail:**
- Service: `backend/src/services/llm_matcher.py`. `MatchVerdict{fit_score: int 0-100, verdict: str, reason: str}`.
- `match_batch()` runs with `asyncio.Semaphore(3)`, skips jobs already holding a verdict, per-job errors swallowed.
- Uses `llm_provider.llm_extract_validated` (Gemini→Groq→Cerebras chain). Test isolation via `llm_extract_validated_fn` kwarg.
- Results stored on `user_feed` (per-user state; rules #10/#17 keep shared catalog tables untouched). Migration 0017 adds `llm_fit_score`, `llm_verdict`, `llm_reason`, `llm_matched_at`.
- `_run_matcher_stage` in `src/main.py` invokes `match_batch` after the per-user feed write.
- Feed read path: `SELECT ... ORDER BY COALESCE(llm_fit_score, score) DESC`.
- API: `GET /api/jobs` exposes the four `llm_*` fields; dashboard renders an AI-verdict badge.
- Measured: 18/18 judged in 89.8 s (concurrency 3); judge spread 20–92 vs keyword 30–43; 10/10 fit accuracy.

**Profile-version re-score (migration 0018, automatic, no new env flags):**

Every row written to `user_feed` now carries a `profile_version INTEGER` column — the ID of the `user_profile_versions` snapshot that produced the score and verdict.

Two operating modes:

- **Mode 1 — profile content changes.** When `POST /api/profile` (save or upload) completes, the API trigger in `src/api/routes/profile.py` compares the last two `user_profile_versions` snapshots. If the content differs, it fires `rescore_user_feed` as a FastAPI `BackgroundTask`. The rescore service (`src/services/rescore.py`) clears the user's LLM verdicts (`clear_user_verdicts` in `llm_matcher.py`) and re-scores every job in that user's 30-day catalog view against the new profile, writing fresh keyword scores and stamping the new version. If `MATCHER_ENABLED` is on, the LLM re-judge also runs for the top candidates.
- **Mode 2 — ordinary search / refresh.** Newly-fetched jobs get scored and stamped with the current profile version. Existing `user_feed` rows are left untouched — their scores and verdicts stay as-is (`skip_existing` lock in `match_batch`).

**Invariant:** a job's score only changes when the PROFILE changes, never just because time passed. The `jobs` and `job_enrichment` shared catalog tables are not touched (rules #10/#17 still hold).

---

## Source Architecture

### BaseJobSource (`backend/src/sources/base.py`)

```
BaseJobSource (ABC)
  |-- __init__(session, search_config=None)
  |-- Properties: relevance_keywords, job_titles, search_queries
  |-- _get_json(url, params, headers) -> dict | None   # 3 retries, exp backoff (1s, 2s, 4s)
  |-- _post_json(url, body, headers) -> dict | None
  |-- _get_text(url, params, headers) -> str | None
  |-- _headers(extra) -> dict                           # User-Agent default
  |-- _is_uk_or_remote(location) -> bool                # Checks UK_TERMS, REMOTE_TERMS, FOREIGN_INDICATORS
  |-- fetch_jobs() -> list[Job]                         # ABSTRACT
```

### Source Categories and Patterns

**Keyed APIs** (8 — need API key in .env, skip with info log when empty):
```
ReedSource(session, api_key, search_config)
AdzunaSource(session, app_id, app_key, search_config)
JSearchSource(session, api_key, search_config)
JoobleSource(session, api_key, search_config)
GoogleJobsSource(session, api_key, search_config)
CareerjetSource(session, affid, search_config)
FindworkSource(session, api_key, search_config)
GovApprenticeships(session, api_key, search_config)  # DfE Display Advert API v2 (restored 2026-06-16)
```

**Free JSON APIs** (9 — no auth, filter by relevance_keywords):
```
ArbeitnowSource, RemoteOKSource, JobicySource, HimalayasSource,
RemotiveSource, DevITJobsSource, LandingJobsSource, AIJobsSource,
HNJobsSource
```

**ATS Boards** (11 — iterate company slugs from companies.py):
```
GreenhouseSource(session, companies, search_config)   # 25 companies
LeverSource(session, companies, search_config)         # 12 companies
WorkableSource(session, companies, search_config)      # 8 companies
AshbySource(session, companies, search_config)         # 9 companies
SmartRecruitersSource(session, companies, search_config) # 6 companies
PinpointSource(session, companies, search_config)      # 8 companies
RecruiteeSource(session, companies, search_config)     # 8 companies
WorkdaySource(session, companies, search_config)       # 15 companies (dict format)
PersonioSource(session, companies, search_config)      # 10 companies
SuccessFactorsSource(session, companies, search_config) # 3 companies (sitemap format)
RipplingSource(session, companies, search_config)       # added Batch 3
```

**RSS/XML Feeds** (9 — parse with xml.etree.ElementTree):
```
JobsAcUkSource, NHSJobsSource, NHSJobsXMLSource, WorkAnywhereSource, WeWorkRemotelySource,
RealWorkFromAnywhereSource, BioSpaceSource, UniJobsSource,
TeachingVacanciesSource  # lives in apis_free/ but category="rss"
```

**HTML Scrapers** (5 — parse with regex):
```
LinkedInSource, ClimatebaseSource, EightyKHoursSource,
BCSJobsSource, AIJobsAISource
```

**Special** (4 classes / 5 registry keys):
- `JobSpySource` — uses python-jobspy for Indeed/Glassdoor (`"indeed"` + `"glassdoor"` keys); optional dependency, skips with warning if not installed
- `HackerNewsSource` — Algolia "Who is Hiring" threads
- `TheMuseSource` — TheMuse public API
- `NoFluffJobsSource` — NoFluffJobs public API

Note: `YCCompaniesSource`, `NomisSource`, `FindAJobSource`, `JobTensorSource`, `AIJobsGlobalSource` were removed in earlier rotations (upstream dead or retired).

---

## Job Normalization and Deduplication

### Job Dataclass (`backend/src/models.py`)

```python
@dataclass
class Job:
    title: str                          # Required
    company: str                        # Required
    apply_url: str                      # Required
    source: str                         # Required
    date_found: str                     # Required (ISO format)
    location: str = ""
    salary_min: Optional[float] = None  # Sanitized: <10k set to None
    salary_max: Optional[float] = None  # Sanitized: >500k set to None
    description: str = ""
    match_score: int = 0                # Set by scorer (0-100)
    visa_flag: bool = False             # Set by check_visa_flag
    is_new: bool = True
    experience_level: str = ""          # Set by detect_experience_level
```

**Post-init processing:**
- HTML entity decoding on title and company (`html.unescape`)
- Company name cleaning: empty/nan/none/null → "Unknown"
- Salary outlier filtering: <10k → None (likely hourly), >500k → None (likely non-GBP)

### normalized_key()

```python
def normalized_key(self) -> tuple[str, str]:
    # 1. Strip company suffixes: Ltd, Limited, Inc, PLC, Corp, GmbH, etc.
    # 2. Strip region suffixes: UK, US, EU, EMEA, APAC, Global, International
    # 3. Lowercase both company and title
    return (normalized_company, normalized_title)
```

This key is used for:
- **Deduplication** — `deduplicator.py` groups jobs by this key, keeps highest-scored
- **Database uniqueness** — `UNIQUE(normalized_company, normalized_title)` constraint
- **Seen-check** — `is_job_seen()` queries by these columns

### Deduplication Logic

1. Group all jobs by `normalized_key()`
2. Within each group, sort by: `match_score` (desc), then data completeness (has salary, has description, has location)
3. Keep only the best job from each group

---

## Profile System

### Data Model

```
UserProfile
  +-- cv_data: CVData
  |     +-- raw_text: str
  |     +-- skills: list[str]
  |     +-- job_titles: list[str]
  |     +-- education: list[str]
  |     +-- certifications: list[str]
  |     +-- summary: str
  |     +-- linkedin_positions: list[dict]      # From LinkedIn profile PDF
  |     +-- linkedin_skills: list[str]           # From LinkedIn profile PDF
  |     +-- linkedin_industry: str               # From LinkedIn profile PDF
  |     +-- github_languages: dict[str, int]     # From GitHub API
  |     +-- github_topics: list[str]             # From GitHub API
  |     +-- github_skills_inferred: list[str]    # From GitHub API
  |     +-- linkedin_raw_text: str               # Two-pass: stored for offline LLM re-run
  |     +-- github_repos_brief: list[dict]       # Two-pass: name/description/topics for LLM re-run
  |     +-- github_llm_skills: list[str]         # Two-pass: LLM read repo prose
  |     +-- about_me_inferred_skills: list[str]  # Two-pass: LLM mined preferences.about_me
  +-- preferences: UserPreferences
        +-- target_job_titles: list[str]
        +-- additional_skills: list[str]
        +-- excluded_skills: list[str]
        +-- preferred_locations: list[str]
        +-- industries: list[str]
        +-- salary_min/max: float | None
        +-- work_arrangement: str    # "remote", "hybrid", "onsite", or ""
        +-- experience_level: str
        +-- negative_keywords: list[str]
        +-- about_me: str
        +-- github_username: str
```

### SearchConfig Generation

```
UserProfile -> keyword_generator.generate_search_config() -> SearchConfig
  |
  +-- job_titles: prefs.titles + cv.titles (deduped)
  +-- primary_skills: first 1/3 of all_skills
  +-- secondary_skills: middle 1/3
  +-- tertiary_skills: last 1/3
  +-- relevance_keywords: words from titles + skills (lowercased, no stopwords)
  +-- negative_title_keywords: prefs.negative_keywords
  +-- locations: UK defaults + prefs.preferred_locations
  +-- core_domain_words: non-role words from titles (e.g., "machine", "learning")
  +-- supporting_role_words: role words from titles (e.g., "engineer", "scientist")
  +-- search_queries: top 8 titles x top 2 locations
```

### LinkedIn Parser Pipeline

```
LinkedIn profile PDF -> parse_linkedin_pdf() -> dict
  |
  +-> pdfplumber text extraction (all pages)
  +-> is_linkedin_pdf() 2-of-3 heuristic (URL / headings / footer)
  +-> _split_sections() by known heading vocabulary
  +-> Deterministic: summary, skills (one per line), headline, industry
  +-> LLM (Gemini -> Groq -> Cerebras) in parallel for:
  |     - Experience -> [{title, company, start, end, description}, ...]
  |     - Education  -> [{school, degree, start, end, notes}, ...]
  |     - Certifications -> [{name, authority, start, end}, ...]
  |
  enrich_cv_from_linkedin(cv_data, linkedin_data) -> CVData
  # Merges LinkedIn data into existing CVData fields (same as old ZIP path)
```

### GitHub Enricher Pipeline

```
GitHub username -> fetch_github_profile(username) -> dict  [async]
  |
  +-> GET /users/{username}/repos -> repo list (up to 30)
  +-> For each repo: languages, topics from API
  +-> LANGUAGE_TO_SKILL mapping -> inferred skills
  |
  enrich_cv_from_github(cv_data, github_data) -> CVData
  # Adds github_languages, github_topics, github_skills_inferred to CVData
```

Uses optional `GITHUB_TOKEN` env var for higher API rate limits (60 req/hr unauthenticated, 5000 req/hr authenticated).

### CV Parser Pipeline

```
PDF/DOCX -> extract_text() -> raw text
  |
  +-> _find_sections() -> {skills, experience, education, certifications, summary}
  |
  +-> LLM extraction via llm_provider.py (Gemini/Groq/Cerebras with free-tier fallback)
  |     Returns: skills[], job_titles[], education[], certifications[], summary
  |     The regex KNOWN_SKILLS / KNOWN_TITLE_PATTERNS approach was removed in commit 804725c
```

### Two-Pass Extraction (`services/profile/two_pass.py`)

Every input gets a **deterministic pass** (plain code) AND an **LLM enhance pass**,
both merged into one `CVData`:

```
run_two_pass_extraction(profile)        # in place, never raises, no network
  CV         : deterministic_cv_fields(raw_text)      + llm_cv_fields_from_text(raw_text)
  LinkedIn   : header/skills split (deterministic)    + parse_linkedin_from_text(linkedin_raw_text)
  GitHub     : LANGUAGE/TOPIC lookup (deterministic)  + llm_infer_github_skills(github_repos_brief)
  Preferences: form parse (deterministic)             + llm_infer_from_about_me(about_me)

reextract_and_rescore(user_id)          # change trigger (background)
  load_profile -> run_two_pass_extraction -> save_profile("two_pass_reextract")
               -> new profile version id -> rescore_user_feed
```

Re-runs use only **stored** inputs (`raw_text`, `linkedin_raw_text`,
`github_repos_brief`, `about_me`) — no re-upload, no GitHub re-fetch. Each pass
no-ops when its input or LLM key is missing. Skill provenance is preserved: the
new sources `about_me_llm` (weight 2.0) and `github_llm` (1.5) feed
`skill_tiering` alongside the existing ones.

---

## Notification System

> **The old `NotificationChannel` ABC + `get_all_channels() -> [EmailChannel, SlackChannel, DiscordChannel]` hierarchy no longer exists** (no `class NotificationChannel` anywhere in the codebase). The real, current path is the Apprise-backed dispatcher below. `src/services/notifications/` today holds only `report_generator.py` (legacy CLI markdown/CSV report generation, unrelated to per-user delivery).

### Apprise Dispatcher (`src/services/channels/dispatcher.py`)

Channels are **per-user rows** in `user_channels` (migration 0005; `channel_type` one of `email`/`slack`/`discord`/`telegram`/`webhook`), each holding a Fernet-encrypted Apprise URL (e.g. `slack://tokenA/tokenB/tokenC`). There is no static "auto-discovery" list — every user configures their own channels via `POST /api/channels` or the OAuth connect flow (`channels.py` — Slack/Discord OAuth, migration 0019 `oauth_states`).

```python
async def dispatch(db, *, user_id, title, body, job_id=None, match_score=None, force=False) -> list[ChannelSendResult]:
    channels = await load_user_channels(db, user_id)      # user_channels WHERE enabled=1
    rule = await _load_notification_rule(db, user_id)     # single notification_rules row (rule #23)
    # Gate 1: rule.enabled=0 -> skip all channels
    # Gate 2: match_score < rule.score_threshold -> skip all channels
    # Gate 3: notify_mode in (daily, every_n_hours) OR quiet-hours active (zoneinfo, user's IANA tz)
    #         -> queue into user_notification_digests, unless force=True
    # Gate 4: otherwise decrypt the channel's Apprise URL and notify() now
```

- `load_user_channels()` reads `user_channels` for the user (optionally filtered to `enabled=1`).
- `format_payload(channel_type, title, body)` shapes the message per channel type before Apprise sends it.
- Quiet hours: `_is_in_quiet_window()` converts UTC `now` to the user's `users.timezone` (stdlib `zoneinfo`, not pytz) and compares against `quiet_hours_start`/`quiet_hours_end`.
- `force=True` (used by `send_bundle` when the `notification_tick` ARQ cron flushes a digest) bypasses the mode/quiet-hours gate — see CLAUDE.md rule #24.
- Delivery itself is one line: `apprise.Apprise(); ap.add(decrypted_url); await ap.async_notify(title, body)` — Apprise handles the actual per-channel-type protocol (SMTP, Slack webhook, Discord webhook, Telegram bot API, generic webhook).
- Every call to `dispatch()` returns one `ChannelSendResult(channel_id, channel_type, ok, error, skipped, queued_digest)` per channel, logged via `_log_dispatch()` and (for the worker path) persisted to `notification_ledger` (migration 0004) for idempotency — `UNIQUE(user_id, job_id, channel)` guarantees the same job is never sent twice on the same channel.
- Credentials are Fernet-encrypted at rest (`services/channels/crypto.py`, key from `CHANNEL_ENCRYPTION_KEY`); Apprise itself is lazy-imported (`_get_apprise_cls()`) so importing `dispatcher.py` doesn't pull in the ~30 MB Apprise dependency tree unless a send actually happens (CLAUDE.md rule #11).

---

## Database Schema

> **Postgres, not SQLite.** The engine is Postgres via psycopg3 (`src/repositories/pg.py`); `database.py:5` does `from src.repositories import pg as aiosqlite` so the rest of the codebase keeps calling the aiosqlite-shaped API, but no SQLite file is ever opened in production. `DATABASE_URL` (default `postgresql://job360:job360dev@localhost:5433/job360`, `settings.py:24-26`) is the single source of truth for the connection. The full schema is built by 23 forward-migrations (0000–0022) plus `JobDatabase._migrate()` in `database.py`, which mirrors the same columns forward so a bare `init_db()` (no external runner) still produces the current shape — see `database.py:91-203`.

### Shared catalog tables (no `user_id` — rules #1/#10/#17)

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT DEFAULT '',
    salary_min REAL,
    salary_max REAL,
    description TEXT DEFAULT '',
    apply_url TEXT NOT NULL,
    source TEXT NOT NULL,
    date_found TEXT NOT NULL,
    match_score INTEGER DEFAULT 0,
    visa_flag INTEGER DEFAULT 0,
    experience_level TEXT DEFAULT '',
    normalized_company TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    -- Pillar 3 Batch 1 — 5-column date model + ghost-detection lifecycle
    posted_at TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    last_updated_at TEXT,
    date_confidence TEXT DEFAULT 'low',
    date_posted_raw TEXT,
    consecutive_misses INTEGER DEFAULT 0,
    staleness_state TEXT DEFAULT 'active',
    -- Step-1.5 S1.1 — 9 per-dimension score columns (migration 0011)
    role INTEGER DEFAULT 0,
    skill INTEGER DEFAULT 0,
    seniority_score INTEGER DEFAULT 0,
    experience INTEGER DEFAULT 0,
    credentials INTEGER DEFAULT 0,
    location_score INTEGER DEFAULT 0,
    recency INTEGER DEFAULT 0,
    semantic INTEGER DEFAULT 0,
    penalty INTEGER DEFAULT 0,
    -- migration 0021 — application deadline
    deadline TEXT,
    deadline_source TEXT,
    UNIQUE(normalized_company, normalized_title)
);

CREATE INDEX IF NOT EXISTS idx_jobs_date_found ON jobs(date_found);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);
CREATE INDEX IF NOT EXISTS idx_jobs_match_score ON jobs(match_score);
CREATE INDEX IF NOT EXISTS idx_jobs_staleness_state ON jobs(staleness_state);
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen_at ON jobs(last_seen_at);

CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    total_found INTEGER DEFAULT 0,
    new_jobs INTEGER DEFAULT 0,
    sources_queried INTEGER DEFAULT 0,
    per_source TEXT DEFAULT '{}',      -- JSON string
    -- migration 0010 — observability columns
    run_uuid TEXT,
    per_source_errors TEXT DEFAULT '{}',
    per_source_duration TEXT DEFAULT '{}',
    total_duration REAL,
    user_id TEXT,
    matcher_stats TEXT DEFAULT '{}'    -- backlog #9 (33768b9) — per-run LLM-judge telemetry
                                       -- (judged/skipped/failed/avg_fit/fit_min/fit_max),
                                       -- snapshotted from MatcherTelemetry.as_dict() in log_run();
                                       -- auto-adds on boot (database.py:133)
);
```

**Auto-purge:** `purge_old_jobs(days=30)` deletes jobs where `first_seen` is older than 30 days. Runs at the start of every pipeline run.

**first_seen:** Set in Python via `datetime.now(timezone.utc).isoformat()` at insert time (not a schema-level DEFAULT).

**No PRAGMAs.** SQLite's `journal_mode=WAL` / `busy_timeout=5000` no longer apply — Postgres handles concurrent readers/writers natively via row/table locking, so there is no "database is locked" failure mode to guard against. `src/repositories/db_retry.py` ("Postgres edition") is kept for callers (rescore loop, LLM judge) that still route writes through `with_write_retry()`, but it now retries only transient `psycopg.OperationalError` connection blips, not lock contention.

### Per-user tables (all scoped by `user_id`, rules #10/#12/#25)

| Table | Migration | Key columns |
|-------|-----------|--------------|
| `users` | 0001 | `id, email UNIQUE, password_hash, created_at, deleted_at`; `+email_verified_at` (0016), `+timezone` (0012, default `'UTC'`) |
| `sessions` | 0001 | `id, user_id, created_at, expires_at, last_seen, user_agent, ip_hash` |
| `user_feed` | 0003 | `id, user_id, job_id, score CHECK 0-100, bucket, status, notified_at, UNIQUE(user_id, job_id)`; `+llm_fit_score/llm_verdict/llm_reason/llm_matched_at` (0017); `+profile_version INTEGER` (0018 — stamps the `user_profile_versions` snapshot that produced the row) |
| `notification_ledger` | 0004 | `id, user_id, job_id, channel, status ('queued'/'sent'/'failed'/'dlq'), sent_at, error_message, retry_count, UNIQUE(user_id, job_id, channel)` — per-channel send idempotency |
| `user_channels` | 0005 | `id, user_id, channel_type, display_name, credential_encrypted BLOB (Fernet), key_version, enabled`; `+connection_status, +target_label` (0019) |
| `user_profiles` | 0006 | `user_id PK, cv_data, preferences, linkedin_data, github_data, created_at, updated_at` — mutable "tip" row |
| `user_profile_versions` | 0007 | `id, user_id, created_at, source_action, cv_data, preferences` — append-only snapshot history |
| `notification_rules` | 0012, collapsed to one row per user by 0020 | `id, user_id UNIQUE, score_threshold, notify_mode CHECK ('instant'/'daily'/'every_n_hours'), interval_hours, daily_send_time, quiet_hours_start, quiet_hours_end, last_sent_at, enabled` — rule #23: ONE row governs all of a user's channels |
| `user_notification_digests` | 0013 | `id, user_id, channel, job_id, queued_at, sent, sent_at` — queue drained by the `notification_tick` ARQ cron |
| `application_stage_history` | 0014 | `id, job_id, user_id, from_stage, to_stage, transitioned_at, notes` |
| `password_resets` | 0015 | `id, user_id, token_hash UNIQUE (SHA256), created_at, expires_at, used_at` — 1-hour expiry, single-use |
| `email_verifications` | 0016 | `id, user_id, token_hash UNIQUE, email, created_at, expires_at, used_at` |
| `oauth_states` | 0019 | `state PK, user_id, channel_type, created_at` — one-time nonce for the Slack/Discord channel-connect OAuth redirect |
| `magic_link_tokens` | 0022 | `id, email, token_hash, expires_at, used_at, created_at` — keyed on email (not `user_id`) since the account may not exist yet; consuming a valid token both authenticates AND verifies the email in one step |

`user_actions` and `applications` predate multi-tenancy (baseline `UNIQUE(job_id)`) but migration 0002 rebuilt both with a `user_id` column and widened the constraint to `UNIQUE(user_id, job_id)`, so two users can independently save/apply to the same shared-catalog job. `applications` also gained `last_advanced_at, interview_dates, notes_history` in migration 0014.

---

## Configuration

### Environment Variables (.env)

Source: `backend/src/core/settings.py`.

| Variable | Required | Used by |
|----------|----------|---------|
| `REED_API_KEY` | No | ReedSource (`settings.py:39`) |
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | No | AdzunaSource (`settings.py:40-41`) |
| `JSEARCH_API_KEY` | No | JSearchSource (`settings.py:42`) |
| `JOOBLE_API_KEY` | No | JoobleSource (`settings.py:43`) |
| `SERPAPI_KEY` | No | GoogleJobsSource (`settings.py:44`) |
| `CAREERJET_AFFID` | No | CareerjetSource (`settings.py:45`) |
| `FINDWORK_API_KEY` | No | FindworkSource (`settings.py:46`) |
| `DFE_APPRENTICESHIPS_API_KEY` | No | GovApprenticeships (DfE Display Advert API v2; `settings.py:47-50`) |
| `GITHUB_TOKEN` | No | GitHub profile enrichment — higher rate limit, 5000/hr vs 60/hr (`settings.py:53`) |
| `GEMINI_API_KEY` / `GROQ_API_KEY` / `CEREBRAS_API_KEY` | No | LLM provider fallback chain for CV/LinkedIn/GitHub parsing + the Engine-4 judge (`settings.py:56-58`) |
| `SMTP_EMAIL` + `SMTP_PASSWORD` + `NOTIFY_EMAIL` | No | Legacy CLI email report (`settings.py:63-65`) |
| `SLACK_WEBHOOK_URL` / `DISCORD_WEBHOOK_URL` | No | Legacy CLI webhook report (`settings.py:68-69`); per-user channels now go through `user_channels` + the Apprise dispatcher instead |
| `SLACK_CLIENT_ID` + `SLACK_CLIENT_SECRET` / `DISCORD_CLIENT_ID` + `DISCORD_CLIENT_SECRET` | No | One-click Slack/Discord channel-connect OAuth (`settings.py:75-87`); blank disables the `/connect/{slack,discord}` endpoints |
| `OAUTH_REDIRECT_BASE` | No | Public base URL used to build the OAuth `redirect_uri` (`settings.py:77-79`) |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_BOT_USERNAME` | No | Telegram channel-connect flow (`settings.py:92-93`) |
| `TARGET_SALARY_MIN` / `TARGET_SALARY_MAX` | No | Salary range tiebreaker sort, default 40k-120k (`settings.py:168-169`) |
| `ENRICHMENT_THRESHOLD` | No (default `60`) | Min `match_score` to send a job to LLM enrichment (`settings.py:103`) |
| `MAX_CONCURRENT_SEARCHES_PER_USER` | No (default `3`) | Per-user concurrent-search cap, HTTP 429 once exceeded (`settings.py:109`) |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCKOUT_WINDOW_SECONDS` | No (default `5` / `900`) | Brute-force login lockout window (`settings.py:115-116`) |
| `MIN_TITLE_GATE` / `MIN_SKILL_GATE` | No (default `0.15` / `0.15`) | Pillar 2.2 gate-pass score thresholds (`settings.py:124-125`) |
| `SALARY_WEIGHT` / `SENIORITY_WEIGHT` / `VISA_WEIGHT` / `WORKPLACE_WEIGHT` | No (default `10`/`8`/`6`/`6`) | Pillar 2.9 multi-dim scoring weights, added on top of the legacy 100 then clamped (`settings.py:132-135`) |
| `SEMANTIC_ENABLED` | No (default `false`) | Embeddings + ChromaDB + ESCO normalisation opt-in (`settings.py:140`) |
| `ENGINE1_ENABLED`..`ENGINE4_ENABLED` | No | Independent per-engine switches; each defaults to its legacy flag (E2↔`ENRICHMENT_ENABLED`, E3↔`SEMANTIC_ENABLED`, E4↔`MATCHER_ENABLED`) (`settings.py:162-165`) |
| `ENRICHMENT_ENABLED` | No (default `false`) | Legacy gate for Engine 2 — LLM enrichment + multi-dim scoring (`src/services/job_enrichment.py:33`) |
| `MATCHER_ENABLED` | No (default `false`) | Legacy gate for Engine 4 — the LLM judge (`src/services/llm_matcher.py:36`) |
| `MATCHER_THRESHOLD` | No (default `30`) | Min keyword `match_score` for a job to be judged (`llm_matcher.py:39`) |
| `MATCHER_MAX_JOBS` | No (default `30`) | Max jobs per user per run sent to the judge (`llm_matcher.py:40`) |
| `SOURCE_FETCH_TIMEOUT` | No (default `60`) | Per-source fetch ceiling in seconds, non-ATS categories (`settings.py:239`) |
| `SOURCE_FETCH_TIMEOUT_ATS` | No (default `240`) | Per-source fetch ceiling for ATS-category sources (`settings.py:245`) |
| `SESSION_SECRET` | Yes in prod | `itsdangerous` HMAC session cookie signing (`settings.py:248`) |
| `CHANNEL_ENCRYPTION_KEY` | Yes in prod | Fernet encryption of `user_channels` credentials (`settings.py:249`) |
| `DATABASE_URL` | Yes in prod | Postgres connection string, psycopg3 (`settings.py:24-26`; default points at local dev Postgres on port 5433) |
| `SENTRY_DSN` | No | Error tracking + performance monitoring; empty = Sentry disabled no-op (`settings.py:253`) |
| `LOG_LEVEL` | No (default `INFO`) | Root logging level read once at import (`settings.py:10`) |

`validate_required_env()` (`settings.py:260-277`) raises at boot if `SESSION_SECRET` / `CHANNEL_ENCRYPTION_KEY` / `DATABASE_URL` are missing, but only when `APP_ENV=production` or `RAILWAY_ENVIRONMENT` is set — a no-op in dev/test.

All source API keys are optional — 39 of 47 sources work without any keys.

### Constants (`settings.py`)

| Constant | Value | Purpose |
|----------|-------|---------|
| `MIN_MATCH_SCORE` | 30 | Minimum score to keep a job |
| `MAX_RESULTS_PER_SOURCE` | 100 | Cap per source |
| `MAX_DAYS_OLD` | 7 | Maximum job age |
| `MAX_RETRIES` | 3 | HTTP retry attempts |
| `RETRY_BACKOFF` | [1, 2, 4] | Seconds between retries |
| `REQUEST_TIMEOUT` | 30 | HTTP timeout in seconds |
| `USER_AGENT` | "Job360/1.0 ..." | Default User-Agent header |

### Rate Limits (`settings.py:RATE_LIMITS`)

Each source has configured `concurrent` (max parallel requests) and `delay` (seconds between requests). Range: 0.5s-5.0s delay, 1-3 concurrent. Examples:

| Source | Concurrent | Delay |
|--------|-----------|-------|
| Reed/Adzuna/Jooble | 1 | 2.0s |
| JSearch/LinkedIn | 1 | 3.0s |
| Arbeitnow/Jobicy | 2 | 1.0s |
| Greenhouse/Lever | 2 | 1.5s |
| HN Jobs | 3 | 0.5s |
| WorkAnywhere | 1 | 5.0s |

---

## Structured Logging

Full-lifecycle logging so "why did X happen" is answerable from `data/logs/` without re-running anything. All pieces write through `src/utils/logger.py`'s `get_logger("job360.<name>")`.

### Output streams (`src/utils/logger.py`)

- `data/logs/job360.log` — human-readable text, rotating (5 MB × 3 backups).
- `data/logs/job360.jsonl` — the same events as structured JSON lines (`JSONFormatter`), rotating (5 MB × 3 backups) — the machine-consumable stream.
- `data/logs/audit.log` — a separate, `propagate=False` logger (`job360.audit`, `setup_audit_logger()`) so audit records never mix into the main streams; rotating (5 MB × 5 backups).
- Console (stdout) mirrors the text stream.

### Correlation ids

Two independent context-local ids get stamped onto every log line automatically via `JSONFormatter`:

- **`run_uuid`** — set once per pipeline run (`set_run_uuid()`), ties every log line emitted during one `run_search()` invocation together. `None` outside a pipeline run (e.g. plain CLI subcommands).
- **`request_id`** — set per HTTP request by `RequestIdMiddleware` (`src/api/middleware.py:41-68`). Honours an incoming `X-Request-Id` header (load-balancer/upstream chains) or generates a fresh 16-hex-char id; echoed back in the `X-Request-Id` response header.

### HTTP middleware (`src/api/middleware.py`)

- `RequestIdMiddleware` — stamps the correlation id described above (outermost middleware).
- `AccessLogMiddleware` — logs one `http_request` line per HTTP request on `job360.access`, unconditionally (even routes that log nothing themselves): method, path, status_code, duration_ms, request_id, user_id (from `request.state`), client IP. The `finally` block guarantees a line even when the route raises (recorded as status 500).
- `SecurityHeadersMiddleware` — adds `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, a `Content-Security-Policy` (locked to `'none'` by default, `cdn.jsdelivr.net` + `'unsafe-inline'` allowed for the Swagger `/docs` UI), and `Strict-Transport-Security` in production only.

### Exception + error logging (`src/api/errors.py`)

`register_exception_logging(app)` attaches three handlers, each reproducing FastAPI's default response so client behaviour is unchanged:

- `log_unhandled_exception` — any unhandled `Exception` logs with full traceback + `request_id` on `job360.error`, then returns a clean 500.
- `log_http_exception` — 4xx/5xx `HTTPException`s log their `detail` (why, not just the status code) on `job360.error`; routine 401s (logged-out `/me` checks) are skipped as high-volume/low-value.
- `log_validation_error` — 422 `RequestValidationError`s log which field(s) failed (first 10 errors) on `job360.error`.

### Client-side error bridge (`src/api/routes/client_log.py`)

`POST /api/client-log` — the browser POSTs UI errors/events here so they land in `data/logs/` instead of vanishing when the tab closes. Per-IP rate-limited (60/min via `services/auth/rate_limit.py`), optional-user (captures pre-login errors too), always returns 204 (logging must never break the client, even when rate-limited or malformed).

### Write-retry (`src/repositories/db_retry.py`)

`with_write_retry(fn)` retries a DB write up to 5 times with exponential backoff, but only on a `psycopg.OperationalError` whose message looks like a lock/busy condition (the SQLite-era heuristic, kept because Postgres callers still route through it — see Database Schema above). Every retry attempt and final failure logs on `job360.db`.

---

## Architectural Decisions

1. **Async-first design:** All source fetching, database operations, and notifications use async/await. Sources run concurrently via `asyncio.gather`, with per-source rate limiting to avoid bans.

2. **Two scoring paths:** `score_job()` (static, module-level) exists for backward compatibility. `JobScorer(config).score()` (dynamic, instance-based) was added in Phase 1. The orchestrator picks based on whether a user profile exists. Both produce the same 0-100 scale.

3. **Graceful degradation:** Every source catches its own exceptions. A failing source logs an error and returns `[]` — it never crashes the pipeline. Keyed sources return `[]` when their API key is empty. python-jobspy is imported with try/except.

4. **Normalization for dedup:** Company names are aggressively normalized (strip suffixes, regions, lowercase) to merge "Anthropic Ltd" and "Anthropic" as the same employer. This is deliberately aggressive — false positives (merging different companies) are considered less harmful than false negatives (duplicate listings).

5. **Profile as optional overlay:** The entire profile system is additive. Removing `backend/data/user_profile.json` restores exact pre-Phase-1 behavior. No existing function signatures were changed — new functionality was added alongside existing code.

6. **python-jobspy as optional dependency:** Not listed in backend/pyproject.toml because it has heavy transitive dependencies. Indeed/Glassdoor source gracefully skips if not installed.

---

## Dependencies

### Production (backend/pyproject.toml)

| Package | Purpose |
|---------|---------|
| aiohttp >=3.9.0,<3.14 | Async HTTP client for source fetching (pinned below 3.14 — breaks aioresponses 0.7.8 test mocking) |
| psycopg[binary] >=3.2 | Async Postgres driver — the real DB engine (Phase 1 migration; `src/repositories/pg.py`) |
| aiosqlite >=0.19.0 | Kept only as the import name `database.py` binds to (`from src.repositories import pg as aiosqlite`) — no SQLite file is opened in production |
| python-dotenv >=1.0.0 | .env file loading |
| jinja2 >=3.1.0 | HTML report templates |
| click >=8.1.0 | CLI framework |
| pandas >=2.0.0 | DataFrame support for python-jobspy (Indeed/Glassdoor) |
| pdfplumber >=0.10.0 | PDF text extraction (CV parsing) |
| python-docx >=1.1.0 | DOCX text extraction (CV parsing) |
| rich >=13.0.0 | Terminal table rendering |
| humanize >=4.9.0 | Relative time formatting |
| fastapi >=0.115.0 | API server for Next.js frontend (`backend/src/api/`) |
| uvicorn[standard] >=0.30.0 | ASGI server for FastAPI |
| python-multipart >=0.0.9 | File upload support for FastAPI |
| httpx >=0.27.0 | Async HTTP client (used by API + LLM providers) |
| google-generativeai >=0.8.0 | Gemini LLM provider for CV parsing |
| groq >=0.11.0 | Groq LLM provider for CV parsing |
| cerebras-cloud-sdk >=1.0.0 | Cerebras LLM provider for CV parsing |
| apprise >=1.7.0 | Multi-channel notification dispatch (Batch 2; lazy-imported — see Notification System above) |
| sentry-sdk >=1.40.0 | Error tracking + performance monitoring (Phase 3) |
| argon2-cffi / itsdangerous / cryptography | Password hashing (argon2id) / signed session cookies / Fernet channel-credential encryption |

### Dev (requirements-dev.txt)

Includes all production deps (via `-r backend/pyproject.toml`) plus:

| Package | Purpose |
|---------|---------|
| pytest >=8.0.0 | Test framework |
| pytest-asyncio >=0.23.0 | Async test support |
| aioresponses >=0.7.0 | Mock aiohttp responses |
| fpdf2 >=2.7.0 | Generate test PDF files for CV parser tests |

### Optional (not in backend/pyproject.toml)

| Package | Purpose |
|---------|---------|
| python-jobspy | Indeed/Glassdoor scraping (backend/src/sources/other/indeed.py) |
