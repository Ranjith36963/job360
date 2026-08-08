# Job360 Architecture
<!-- doc: LIVING | last-verified: 2026-08-08 by /sync -->

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
                                                  Store -> SQLite catalog (jobs table)
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
│   ├── migrations/                   # 30 forward/reverse SQL migrations (0000 → 0029) + runner.py
│   ├── src/
│   │   ├── main.py                   # Orchestrator: run_search(), SOURCE_REGISTRY (47 keys → 46 instances), _build_sources()
│   │   ├── cli.py                    # Click CLI: run, api, status, sources, view, setup-profile
│   │   ├── cli_view.py               # Rich terminal table viewer
│   │   ├── models.py                 # Job dataclass + normalized_key() — DB UNIQUE + dedup Layer-1
│   │   ├── api/                      # FastAPI: lifespan, CORS, dependencies, 13 route modules
│   │   │   └── routes/               # health, jobs, actions, profile, search, pipeline, auth, channels, notifications, notification_rules, runs
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
│   │   │   ├── channels/             # crypto (Fernet), dispatcher (Apprise lazy)
│   │   │   ├── notifications/        # email / slack / discord / report_generator (legacy CLI summaries)
│   │   │   └── profile/              # cv_parser, llm_provider, linkedin_parser, github_enricher, models, preferences, storage, keyword_generator
│   │   ├── repositories/             # (post-Phase-4 rename from storage/)
│   │   │   ├── database.py           # Postgres via psycopg3 (`pg.py` aiosqlite-shaped shim) + 25-migration forward-compat schema
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
│   │       ├── logger.py             # Rotating file + console logging
│   │       ├── rate_limiter.py       # Async semaphore + delay
│   │       └── time_buckets.py
│   └── tests/                        # 1,288 collected / 1,285 passing across 60+ files (defer to runtime count)
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

Sources that use `self.search_queries` with their own fallback lists: JSearch, LinkedIn, FindAJob, NHS Jobs.

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

### Auto-Discovery

```python
def get_all_channels():
    return [EmailChannel(), SlackChannel(), DiscordChannel()]

def get_configured_channels():
    return [ch for ch in get_all_channels() if ch.is_configured()]
```

Each channel implements:
- `is_configured() -> bool` — checks if required env vars are set
- `send(jobs, stats, csv_path=None)` — sends the notification

### Channel Details

```
NotificationChannel (ABC)
├── EmailChannel      — configured if SMTP_EMAIL + SMTP_PASSWORD + NOTIFY_EMAIL set
│   Uses: Gmail SMTP (smtp.gmail.com:587), HTML template, CSV attachment
├── SlackChannel      — configured if SLACK_WEBHOOK_URL set
│   Uses: Block Kit message format, top 10 jobs, webhook POST
└── DiscordChannel    — configured if DISCORD_WEBHOOK_URL set
    Uses: Embed message format, top 10 jobs, webhook POST
```

---

## Database Schema

> This section shows the baseline schema. The full schema is built by 26 forward-migrations (0000–0025). Key additions beyond the baseline below: `user_feed` gains `llm_fit_score/llm_verdict/llm_reason/llm_matched_at` (migration 0017) and `profile_version INTEGER` (migration 0018 — stamps the profile snapshot that produced each row's score); `users` gains `email_verified_at` (migration 0016); `password_resets` table (migration 0015); `email_verifications` table (migration 0016).

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
    UNIQUE(normalized_company, normalized_title)
);

CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    total_found INTEGER DEFAULT 0,
    new_jobs INTEGER DEFAULT 0,
    sources_queried INTEGER DEFAULT 0,
    per_source TEXT DEFAULT '{}'  -- JSON string
);

CREATE TABLE IF NOT EXISTS user_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    action TEXT NOT NULL,            -- save, dismiss, applied, etc.
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(job_id)
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    stage TEXT NOT NULL DEFAULT 'applied',  -- applied, interview, offer, rejected
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_date_found ON jobs(date_found);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);
CREATE INDEX IF NOT EXISTS idx_jobs_match_score ON jobs(match_score);
```

**Pragmas:** `journal_mode=WAL`, `busy_timeout=5000`

**Auto-purge:** `purge_old_jobs(days=30)` deletes jobs where `first_seen` is older than 30 days. Runs at the start of every pipeline run.

**first_seen:** Set in Python via `datetime.now(timezone.utc).isoformat()` at insert time (not a SQLite DEFAULT).

---

## Configuration

### Environment Variables (.env)

| Variable | Required | Used by |
|----------|----------|---------|
| `REED_API_KEY` | No | ReedSource |
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | No | AdzunaSource |
| `JSEARCH_API_KEY` | No | JSearchSource |
| `JOOBLE_API_KEY` | No | JoobleSource |
| `SERPAPI_KEY` | No | GoogleJobsSource |
| `CAREERJET_AFFID` | No | CareerjetSource |
| `FINDWORK_API_KEY` | No | FindworkSource |
| `GITHUB_TOKEN` | No | GitHub profile enrichment (higher rate limits) |
| `SMTP_EMAIL` + `SMTP_PASSWORD` + `NOTIFY_EMAIL` | No | Email notifications |
| `SLACK_WEBHOOK_URL` | No | Slack notifications |
| `DISCORD_WEBHOOK_URL` | No | Discord notifications |
| `TARGET_SALARY_MIN` / `TARGET_SALARY_MAX` | No | Salary range sorting (default 40k-120k) |

All API keys are optional — 39 of 47 sources work without any keys.

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
| WorkAnywhere/Nomis | 1 | 5.0s |

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
| aiohttp >=3.9.0 | Async HTTP client for source fetching |
| psycopg[binary] >=3.2 | Postgres driver — actual job storage backend since 2026-07-02 |
| aiosqlite >=0.19.0 | Legacy driver-shaped API only; `pg.py` shims it over Postgres (real storage is not SQLite) |
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
