# Job360 Architecture

## System Overview

Job360 is a UK-focused job search aggregator that fetches jobs from 48 sources, scores them against a user profile, deduplicates, and delivers results through multiple channels.

```
User Input                    Pipeline                         Output
-----------                   --------                         ------
                          +-> Sources (48) --+
CLI / Dashboard --+       |   (async fetch)  |
                  |       |                  v
Profile (CV+Prefs)+--> main.py ---------> Scorer ---------> Deduplicator
                  |   (orchestrator)    (0-100 score)     (normalized_key)
.env (API keys) --+                                            |
                                                               v
                                                          SQLite DB
                                                               |
                                          +--------------------+--------------------+
                                          |          |         |         |          |
                                        Email     Slack    Discord     CSV     Dashboard
```

---

## Directory Structure

```
job360/
+-- src/
|   +-- __version__.py       # Version constant (imported by cli.py)
|   +-- exceptions.py        # Custom exception hierarchy (Job360Error → SourceError, ScoringError, ProfileError, DatabaseError)
|   +-- main.py              # Orchestrator: run_search(), _build_sources(), SOURCE_REGISTRY
|   +-- cli.py               # Click CLI: run, dashboard, status, sources, view, setup-profile, pipeline
|   +-- cli_view.py          # Rich terminal table viewer
|   +-- dashboard.py         # Streamlit web UI with profile setup sidebar
|   +-- models.py            # Job dataclass with normalized_key()
|   +-- config/
|   |   +-- settings.py      # Env vars, paths, rate limits, thresholds
|   |   +-- keywords.py      # Domain-agnostic data: LOCATIONS, VISA_KEYWORDS, KNOWN_SKILLS, KNOWN_TITLE_PATTERNS, KNOWN_LOCATIONS
|   |   +-- companies.py     # ATS company slugs (200+ companies across 10 ATS platforms)
|   +-- profile/             # User profile system
|   |   +-- models.py        # CVData, UserPreferences, UserProfile, SearchConfig
|   |   +-- cv_parser.py     # PDF/DOCX text extraction + section parsing
|   |   +-- cv_structured_parser.py # Structured work/education/project extraction
|   |   +-- cv_summarizer.py # Optional LLM CV extraction (Phase 3)
|   |   +-- preferences.py   # Form validation, CV+preferences merge
|   |   +-- storage.py       # JSON persistence (data/user_profile.json)
|   |   +-- keyword_generator.py  # UserProfile -> SearchConfig conversion
|   |   +-- linkedin_parser.py    # LinkedIn ZIP parser (Phase 2)
|   |   +-- github_enricher.py    # GitHub API enrichment (Phase 2)
|   |   +-- skill_graph.py        # Skill inference graph (446 relationships)
|   |   +-- domain_detector.py    # Professional domain detection (10 domains)
|   +-- sources/
|   |   +-- base.py          # BaseJobSource ABC with retry, rate limiting, circuit breaker, keyword properties
|   |   +-- reed.py          # ... 47 source implementations
|   |   +-- adzuna.py
|   |   +-- ... (48 total)
|   +-- filters/
|   |   +-- skill_matcher.py      # JobScorer class (score + score_detailed + visa detection), experience level detection
|   |   +-- deduplicator.py       # Two-pass dedup: normalized_key + description similarity (0.85)
|   |   +-- description_matcher.py # 345 synonym groups for skill matching (ESCO-inspired)
|   |   +-- embeddings.py         # Sentence-transformer embeddings (all-MiniLM-L6-v2, 384-dim)
|   |   +-- jd_parser.py          # Structured JD parsing (skills, experience, qualifications, job type)
|   |   +-- hybrid_retriever.py   # FTS5 + vector search with RRF fusion
|   |   +-- reranker.py           # Cross-encoder reranking (ms-marco-MiniLM-L-6-v2, top-50)
|   |   +-- feedback.py           # Feedback loop: liked/rejected signals adjust scores ±5
|   +-- storage/
|   |   +-- database.py      # Async SQLite (aiosqlite), 6 tables, schema versioning + migrations
|   |   +-- user_actions.py  # Liked/Applied/Not Interested per job (Phase 3)
|   |   +-- csv_export.py    # CSV export per run
|   +-- pipeline/            # Application tracking (Phase 3)
|   |   +-- tracker.py       # ApplicationTracker, PipelineStage enum
|   |   +-- reminders.py     # Outreach reminders (7-day intervals)
|   +-- notifications/
|   |   +-- base.py          # NotificationChannel ABC, get_configured_channels()
|   |   +-- email_notify.py  # Gmail SMTP
|   |   +-- slack_notify.py  # Slack Block Kit webhook
|   |   +-- discord_notify.py # Discord embed webhook
|   |   +-- report_generator.py  # Markdown + HTML report templates
|   +-- utils/
|       +-- logger.py        # Logging setup (file + console) with PII sanitization
|       +-- rate_limiter.py  # Per-source rate limiting
|       +-- time_buckets.py  # Time bucketing, skill extraction, score colors
+-- tests/
|   +-- conftest.py          # Shared fixtures (sample_ai_job, etc.)
|   +-- test_*.py            # 29 test files, 658 tests
+-- data/                    # Runtime data (gitignored)
|   +-- jobs.db              # SQLite database
|   +-- user_profile.json    # User profile (Phase 1)
|   +-- exports/             # CSV exports per run
|   +-- reports/             # Markdown reports per run
|   +-- logs/                # Log files
+-- .env                     # API keys (gitignored)
+-- .env.example             # Template for .env with tunable params
+-- .github/workflows/tests.yml  # CI — pytest on push/PR (Python 3.9/3.11/3.13)
+-- pyproject.toml           # Project metadata + pytest config
+-- CHANGELOG.md             # Version history (Phases 1-3 + v2.0.0)
+-- requirements.txt         # Production dependencies
+-- requirements-dev.txt     # Test dependencies (includes prod via -r)
+-- setup.sh                 # Setup script (venv, deps, validation)
```

---

## Data Flow: Pipeline Run

### 1. Profile Loading (`main.py:run_search`)

```python
profile = load_profile()                    # data/user_profile.json
if not profile or not profile.is_complete:
    logger.error("No user profile found. Upload a CV to start searching.")
    return {"total_found": 0, "new_jobs": 0, ...}  # EARLY RETURN — no search
search_config = generate_search_config(profile)  # UserProfile -> SearchConfig
scorer = JobScorer(search_config)                 # Dynamic scorer
```

### 2. Source Instantiation (`main.py:_build_sources`)

All 48 sources get `search_config` passed through:
```python
ReedSource(session, api_key=REED_API_KEY, search_config=sc)
ArbeitnowSource(session, search_config=sc)
GreenhouseSource(session, search_config=sc)
# ... etc for all 48
```

### 3. Keyword Resolution (`base.py` properties)

Each source accesses keywords via properties — no fallback defaults:
```python
class BaseJobSource:
    @property
    def relevance_keywords(self):
        if self._search_config is not None:
            return self._search_config.relevance_keywords
        return []  # No fallback — empty when no config

    @property
    def job_titles(self):
        if self._search_config is not None:
            return self._search_config.job_titles
        return []

    @property
    def search_queries(self):
        if self._search_config is not None and self._search_config.search_queries:
            return self._search_config.search_queries
        return []
```

### 4. Fetch (async, concurrent)

```python
results = await asyncio.gather(*[fetch_source(s) for s in sources])
# Each source filters by self.relevance_keywords in fetch_jobs()
# Each source has 120s timeout
```

### 5. Foreign Job Filter

```python
# Hard filter BEFORE scoring — remove non-UK jobs
all_jobs = [j for j in all_jobs if not is_foreign_only(j.location)]
```

### 6. Embeddings (optional, requires sentence-transformers)

```python
# Compute 384-dim embeddings for all jobs using all-MiniLM-L6-v2
for job in all_jobs:
    job.embedding = encode(f"{job.title} {job.description[:500]}")
```

### 7. Multi-Stage Scoring

```python
# Stage 1: Legacy score (fast, 0-100)
for job in all_jobs:
    job.match_score = scorer.score(job)
    job.visa_flag = scorer.check_visa_flag(job)
    job.experience_level = detect_experience_level(job.title)

# Stage 2: JD parsing + detailed 8-dimensional score
for job in all_jobs:
    parsed_jd = parse_jd(job.description)
    job.job_type = detect_job_type(job.description)
    breakdown = scorer.score_detailed(job, parsed_jd, cv_data)
    job.match_score = breakdown.total  # Overwrites legacy score
    job.match_data = json.dumps(breakdown.to_dict())  # Matched/missing skills

# Stage 3: Feedback adjustment (±5 based on liked/rejected history)
signals = await load_feedback_signals(conn)
for job in all_jobs:
    adjustment = compute_feedback_adjustment(job_text, signals, preference_vector)
    job.match_score = max(0, min(100, job.match_score + adjustment))

# Stage 4: Cross-encoder reranking (top-50 only, +5 max boost)
all_jobs = rerank(profile_text, all_jobs, top_n=50)
```

### 8. Deduplication

```python
unique = deduplicate(all_jobs)
# Pass 1: Group by normalized (company, title), keep best per group
# Pass 2: Same company + description similarity >= 0.85 (SequenceMatcher)
```

### 9. Output

```python
# Filter by MIN_MATCH_SCORE (30)
# Store new jobs in SQLite + sync to FTS5 index
# Export CSV to data/exports/
# Generate markdown report to data/reports/
# Send notifications (email, Slack, Discord) if configured
# Print time-bucketed summary to console (5 buckets: 24h, 48h, 3d, 5d, 7d)
```

---

## Scoring Algorithm Detail

### Legacy Score: `JobScorer(config).score()` — 0-100 points

| Component | Max Points | How |
|-----------|-----------|-----|
| Title match | 40 | Exact match = 40, substring = 20, partial keyword overlap = 5*core + 3*support (capped at 20) |
| Skill match | 40 | primary skills = 3 pts each, secondary = 2, tertiary = 1 (capped at 40) |
| Location | 10 | UK city = 10, remote = 8, unknown = 0 |
| Recency | 10 | <=1 day = 10, <=3d = 8, <=5d = 6, <=7d = 4, older = 0 |
| **Penalties** | | |
| Negative title | -30 | Title contains excluded keywords |
| Foreign location | -15 | Location matches foreign indicators (US cities, countries, state codes) |

**Score = title + skill + location + recency - penalties** (clamped to 0-100)

### Detailed Score: `JobScorer(config).score_detailed()` — 8 Dimensions

| Dimension | Max Points | How |
|-----------|-----------|-----|
| Role (DIM_ROLE) | 25 | Job title match (exact, partial, domain word overlap) |
| Skill (DIM_SKILL) | 25 | Skill overlap with synonym matching via description_matcher |
| Seniority (DIM_SENIORITY) | 10 | Experience level alignment (intern→executive) |
| Experience (DIM_EXPERIENCE) | 10 | Years requirement match against CV |
| Credentials (DIM_CREDENTIALS) | 5 | Degree/certification match |
| Location (DIM_LOCATION) | 10 | Geographic match |
| Recency (DIM_RECENCY) | 10 | Posting freshness |
| Semantic (DIM_SEMANTIC) | 5 | Embedding cosine similarity (requires sentence-transformers) |

**Detailed score = sum of 8 dimensions - penalty** (clamped to 0-100). Returns `ScoreBreakdown` with matched/missing skills, transferable skills, and per-dimension scores.

Both scoring methods are dynamic via `JobScorer(config)`. There is no static scoring path — the system returns early without a profile. All keyword lists come from the user's `SearchConfig`, generated from their profile.

---

## Source Architecture

### BaseJobSource (`src/sources/base.py`)

```
BaseJobSource (ABC)
  |-- __init__(session, search_config=None)
  |-- Properties: relevance_keywords, job_titles, search_queries
  |-- _get_json(url, params, headers) -> dict | None   # 3 retries, exp backoff
  |-- _post_json(url, body, headers) -> dict | None
  |-- _get_text(url, params, headers) -> str | None
  |-- _headers(extra) -> dict                           # User-Agent default
  |-- fetch_jobs() -> list[Job]                         # ABSTRACT
  |-- safe_fetch() -> list[Job]                         # Circuit breaker wrapper (skip after 3 failures)
```

### Source Categories and Patterns

**Keyed APIs** (need API key in .env, skip with info log when empty):
```
ReedSource(session, api_key, search_config)
AdzunaSource(session, app_id, app_key, search_config)
JSearchSource(session, api_key, search_config)
JoobleSource(session, api_key, search_config)
GoogleJobsSource(session, api_key, search_config)
CareerjetSource(session, affid, search_config)
FindworkSource(session, api_key, search_config)
```

**Free JSON APIs** (no auth, filter by relevance_keywords):
```
ArbeitnowSource, RemoteOKSource, JobicySource, HimalayasSource,
RemotiveSource, DevITJobsSource, LandingJobsSource, AIJobsSource,
HNJobsSource, YCCompaniesSource
```

**ATS Boards** (iterate company slugs from companies.py):
```
GreenhouseSource(session, companies, search_config)
LeverSource(session, companies, search_config)
WorkableSource, AshbySource, SmartRecruitersSource, PinpointSource,
RecruiteeSource, WorkdaySource, PersonioSource, SuccessFactorsSource
```

**RSS/XML Feeds** (parse with xml.etree.ElementTree):
```
JobsAcUkSource, NHSJobsSource, WorkAnywhereSource, WeWorkRemotelySource,
RealWorkFromAnywhereSource, BioSpaceSource, UniJobsSource, FindAJobSource
```

**HTML Scrapers** (parse with regex):
```
LinkedInSource, JobTensorSource, ClimatebaseSource, EightyKHoursSource,
BCSJobsSource, AIJobsGlobalSource, AIJobsAISource
```

**Special**:
- `JobSpySource` -- uses python-jobspy for Indeed/Glassdoor
- `HackerNewsSource` -- Algolia "Who is Hiring" threads
- `TheMuseSource` -- TheMuse public API
- `NoFluffJobsSource` -- NoFluffJobs public API
- `NomisSource` -- UK GOV vacancy statistics (market intelligence, not listings)

---

## Profile System (Phase 1)

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
  |     +-- linkedin_positions: list[dict]      # Phase 2 — LinkedIn
  |     +-- linkedin_skills: list[str]          # Phase 2 — LinkedIn
  |     +-- linkedin_industry: str              # Phase 2 — LinkedIn
  |     +-- github_languages: dict[str, int]    # Phase 2 — GitHub
  |     +-- github_topics: list[str]            # Phase 2 — GitHub
  |     +-- github_skills_inferred: list[str]   # Phase 2 — GitHub
  +-- preferences: UserPreferences
        +-- target_job_titles: list[str]
        +-- additional_skills: list[str]
        +-- excluded_skills: list[str]
        +-- preferred_locations: list[str]
        +-- industries: list[str]
        +-- salary_min/max: float | None
        +-- work_arrangement: str
        +-- experience_level: str
        +-- negative_keywords: list[str]
        +-- about_me: str
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

### CV Parser Pipeline

```
PDF/DOCX -> extract_text() -> raw text
  |
  +-> _find_sections() -> {skills, experience, education, certifications, summary}
  |
  +-> _extract_skills_from_text(skills_section) -> ["Python", "SQL", ...]
  +-> _extract_titles_from_experience(exp_section) -> ["Engineer at Google", ...]
  +-> fallback: _extract_tech_names(full_text) -> capitalized tool names
```

---

## LinkedIn & GitHub Enrichment (Phase 2)

### LinkedIn ZIP Parser (`src/profile/linkedin_parser.py`)

Parses LinkedIn data export ZIP files containing:
- `Positions.csv` — job titles, companies, dates → `cv_data.linkedin_positions`
- `Skills.csv` — endorsed skills → `cv_data.linkedin_skills`
- `Education.csv` — degrees, institutions (merged into `cv_data.education`)
- `Certifications.csv` — professional certifications (merged into `cv_data.certifications`)
- `Profile.csv` — industry, headline → `cv_data.linkedin_industry`

### GitHub API Integration (`src/profile/github_enricher.py`)

Fetches public repository data via GitHub API:
- Repository languages (bytes per language) → `cv_data.github_languages`
- Repository topics/tags → `cv_data.github_topics`
- Inferred skills from languages + topics → `cv_data.github_skills_inferred`

Requires `GITHUB_TOKEN` env var for higher rate limits (optional — works without, but limited to 60 requests/hour).

### Enrichment Flow

```
LinkedIn ZIP  --> linkedin_parser.parse_zip() --> CVData fields populated
GitHub username --> github_enricher.enrich() --> CVData fields populated
                                                    |
                                                    v
                                        keyword_generator uses ALL enriched data
                                        (CV + LinkedIn + GitHub) to build SearchConfig
```

All enrichment deduplicates against existing skills/titles in CVData.

---

## Intelligence Layer (Phase 3 — COMPLETE)

All 5 features implemented:

1. **Controlled skill inference** (`src/profile/skill_graph.py`) — 446 skill relationships (1126 edges) with confidence scores. `infer_skills(existing, threshold=0.7)` returns new skills. Inferred skills go to tertiary tier only (1pt each). Integrated into `keyword_generator.py`.
2. **AI-powered CV summarization** (`src/profile/cv_summarizer.py`) — Optional LLM (Anthropic or OpenAI) supplements regex CV parsing. No API key = current regex-only behavior unchanged. Wrapped in try/except — never breaks existing flow.
3. **Skill-to-description matching** (`src/filters/description_matcher.py`) — 345 synonym groups (ML↔Machine Learning, JS↔JavaScript, K8s↔Kubernetes, etc). `text_contains_with_synonyms()` replaces `_text_contains` in `_skill_score()` for all skill tiers.
4. **Job recommendation engine** (`src/storage/user_actions.py`) — 3 actions per job: Liked/Applied/Not Interested. One action per job at a time (UNIQUE constraint). Dashboard action buttons + "My Actions" sidebar filter + Liked count metric.
5. **Interview tracking pipeline** (`src/pipeline/tracker.py`, `src/pipeline/reminders.py`) — Stages: applied → outreach_week1/2/3 → interview → offer/rejected/withdrawn. 7-day outreach reminders. CLI `pipeline` command. Dashboard Pipeline tab with kanban view. "Applied" action auto-creates application entry.

---

## Database Schema

```sql
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    company TEXT,
    location TEXT,
    salary_min REAL,
    salary_max REAL,
    description TEXT,
    apply_url TEXT,
    source TEXT,
    date_found TEXT,
    match_score INTEGER,
    visa_flag INTEGER,
    experience_level TEXT,
    job_type TEXT DEFAULT '',
    match_data TEXT DEFAULT '{}',
    embedding TEXT DEFAULT '',
    normalized_company TEXT,
    normalized_title TEXT,
    first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(normalized_company, normalized_title)
);

-- FTS5 virtual table for full-text search (synced on insert)
CREATE VIRTUAL TABLE jobs_fts USING fts5(title, company, description, content=jobs, content_rowid=id);

CREATE TABLE run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
    total_found INTEGER,
    new_jobs INTEGER,
    sources_queried INTEGER DEFAULT 0,
    per_source TEXT  -- JSON string
);

CREATE TABLE user_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL UNIQUE,
    action TEXT NOT NULL CHECK(action IN ('liked', 'applied', 'not_interested')),
    timestamp TEXT NOT NULL,
    notes TEXT DEFAULT '',
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE TABLE applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'applied',
    date_applied TEXT NOT NULL,
    next_reminder TEXT,
    contact_name TEXT DEFAULT '',
    contact_email TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    last_updated TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE TABLE schema_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL
);
```

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
| `SMTP_EMAIL` + `SMTP_PASSWORD` + `NOTIFY_EMAIL` | No | Email notifications |
| `SLACK_WEBHOOK_URL` | No | Slack notifications |
| `DISCORD_WEBHOOK_URL` | No | Discord notifications |
| `GITHUB_TOKEN` | No | GitHub API (profile enrichment) — higher rate limits |
| `LLM_API_KEY` | No | AI-powered CV summarization (Phase 3) |
| `LLM_PROVIDER` | No | `"anthropic"` or `"openai"` (default: anthropic) |
| `LLM_MODEL` | No | Model name (default: claude-sonnet-4-6) |
| `TARGET_SALARY_MIN` / `TARGET_SALARY_MAX` | No | Salary range sorting (default 40k-120k) |

All API keys are optional -- free sources (41 of 48) work without any keys.

### Rate Limits (`settings.py:RATE_LIMITS`)

Each source has configured `concurrent` (max parallel requests) and `delay` (seconds between requests). Range: 0.5s-5.0s delay, 1-3 concurrent.

---

## Dependencies

### Production (requirements.txt)
| Package | Purpose |
|---------|---------|
| aiohttp | Async HTTP client for source fetching |
| aiosqlite | Async SQLite for job storage |
| python-dotenv | .env file loading |
| jinja2 | HTML report templates |
| click | CLI framework |
| streamlit | Web dashboard |
| pandas | Data manipulation in dashboard |
| plotly | Charts in dashboard |
| python-jobspy | Indeed/Glassdoor scraping |
| rich | Terminal table rendering |
| humanize | Relative time formatting |
| pdfplumber | PDF text extraction (Phase 1) |
| python-docx | DOCX text extraction (Phase 1) |
| numpy | Numerical operations for embeddings |
| sentence-transformers | Bi-encoder embeddings + cross-encoder reranking (optional) |

### Dev (requirements-dev.txt)
Includes all production deps plus: pytest, pytest-asyncio, aioresponses
