# Job360 Architecture

## System Overview

Job360 is a UK-focused job search aggregator that fetches jobs from 48 sources, scores them against a user profile, deduplicates, and delivers results through multiple channels. It supports any professional domain through a dynamic profile system, with AI/ML as the default fallback.

```
User Input                    Pipeline                         Output
-----------                   --------                         ------
                          +-> Sources (48) --+
CLI / Dashboard --+       |   (async fetch)  |
                  |       |                  v
Profile (CV+Prefs)+--> main.py ---------> Scorer ---------> Deduplicator
  + LinkedIn ZIP  |   (orchestrator)    (0-100 score)     (normalized_key)
  + GitHub API    |                                            |
.env (API keys) --+                                            v
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
+-- backend/src/
|   +-- main.py              # Orchestrator: run_search(), _build_sources(), SOURCE_REGISTRY (48)
|   +-- cli.py               # Click CLI: run, dashboard, status, sources, view, setup-profile
|   +-- cli_view.py          # Rich terminal table viewer
|   +-- dashboard.py         # Streamlit web UI with profile setup sidebar
|   +-- models.py            # Job dataclass with normalized_key()
|   +-- config/
|   |   +-- settings.py      # Env vars, paths, RATE_LIMITS (48 entries), thresholds
|   |   +-- keywords.py      # Default AI/ML keywords (KNOWN_SKILLS and KNOWN_TITLE_PATTERNS removed in commit 3ba1342 — replaced by LLM parser)
|   |   +-- companies.py     # ATS company slugs (~104 companies across 10 ATS platforms)
|   +-- profile/
|   |   +-- models.py        # CVData, UserPreferences, UserProfile, SearchConfig
|   |   +-- cv_parser.py     # PDF/DOCX text extraction + section parsing
|   |   +-- preferences.py   # Form validation, CV+preferences merge
|   |   +-- storage.py       # JSON persistence (backend/data/user_profile.json)
|   |   +-- keyword_generator.py  # UserProfile -> SearchConfig conversion
|   |   +-- linkedin_parser.py    # LinkedIn ZIP export parser
|   |   +-- github_enricher.py    # GitHub public API enricher
|   |   +-- llm_provider.py       # Multi-provider LLM client (Gemini/Groq/Cerebras) for CV parsing
|   +-- sources/
|   |   +-- base.py          # BaseJobSource ABC with retry, rate limiting, keyword properties
|   |   +-- ... (47 source files, 48 registry entries)
|   +-- filters/
|   |   +-- skill_matcher.py # Scoring (score_job + JobScorer), visa detection, experience level
|   |   +-- deduplicator.py  # Group by normalized_key, keep highest-scored
|   +-- storage/
|   |   +-- database.py      # Async SQLite (aiosqlite), jobs + run_log tables
|   |   +-- csv_export.py    # CSV export per run
|   +-- notifications/
|   |   +-- base.py          # NotificationChannel ABC, get_configured_channels()
|   |   +-- email_notify.py  # Gmail SMTP
|   |   +-- slack_notify.py  # Slack Block Kit webhook
|   |   +-- discord_notify.py # Discord embed webhook
|   |   +-- report_generator.py  # Markdown + HTML report templates
|   +-- utils/
|       +-- logger.py        # Logging setup (file + console)
|       +-- rate_limiter.py  # Per-source rate limiting
|       +-- time_buckets.py  # Time bucketing, score colors, bucket_summary_counts
+-- backend/tests/
|   +-- conftest.py          # Shared fixtures (sample_ai_job, etc.)
|   +-- test_*.py            # 21 test files, 412 tests
+-- backend/data/                    # Runtime data (gitignored)
|   +-- jobs.db              # SQLite database
|   +-- user_profile.json    # User profile (optional)
|   +-- exports/             # CSV exports per run
|   +-- reports/             # Markdown reports per run
|   +-- logs/                # Log files
+-- .env                     # API keys (gitignored)
+-- .env.example             # Template for .env
+-- backend/pyproject.toml         # Production dependencies (12 packages)
+-- requirements-dev.txt     # Test dependencies (includes prod via -r)
+-- setup.sh                 # Setup script (venv, deps, validation)
+-- cron_setup.sh            # Cron scheduling (4AM/4PM Europe/London)
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

All 48 sources get `search_config` passed through:
```python
ReedSource(session, api_key=REED_API_KEY, search_config=sc)
ArbeitnowSource(session, search_config=sc)
GreenhouseSource(session, search_config=sc)
# ... etc for all 48
```

The `_build_sources()` function groups sources into labeled groups (A through K) and instantiates all of them. When `--source <name>` is passed, it filters to just the matching source. Special case: `--source glassdoor` maps to `JobSpySource` (same as `indeed`).

### 3. SOURCE_REGISTRY

`SOURCE_REGISTRY` is a `dict[str, type]` mapping 48 source names to their classes. It serves two purposes:
1. CLI `--source` validation — Click uses it for `click.Choice(sorted(SOURCE_REGISTRY.keys()))`
2. `sources` command — lists all available source names
3. Test assertion — `test_cli.py` asserts `len(SOURCE_REGISTRY) == 48` and checks the exact set of keys

Note: `"indeed"` and `"glassdoor"` both map to `JobSpySource`, so there are 48 registry entries but 47 unique classes.

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
results = await asyncio.gather(*[_fetch_source(s) for s in sources])
# Each source filters by self.relevance_keywords in fetch_jobs()
# Each source has 120s timeout (asyncio.wait_for)
# Each _get_json/_get_text has 30s timeout (REQUEST_TIMEOUT)
```

`_fetch_source` wraps each call in `asyncio.wait_for(timeout=120)` and catches both `TimeoutError` and general `Exception`, logging warnings/errors but never crashing the pipeline.

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

**Keyed APIs** (7 — need API key in .env, skip with info log when empty):
```
ReedSource(session, api_key, search_config)
AdzunaSource(session, app_id, app_key, search_config)
JSearchSource(session, api_key, search_config)
JoobleSource(session, api_key, search_config)
GoogleJobsSource(session, api_key, search_config)
CareerjetSource(session, affid, search_config)
FindworkSource(session, api_key, search_config)
```

**Free JSON APIs** (10 — no auth, filter by relevance_keywords):
```
ArbeitnowSource, RemoteOKSource, JobicySource, HimalayasSource,
RemotiveSource, DevITJobsSource, LandingJobsSource, AIJobsSource,
HNJobsSource, YCCompaniesSource
```

**ATS Boards** (10 — iterate company slugs from companies.py):
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
```

**RSS/XML Feeds** (8 — parse with xml.etree.ElementTree):
```
JobsAcUkSource, NHSJobsSource, WorkAnywhereSource, WeWorkRemotelySource,
RealWorkFromAnywhereSource, BioSpaceSource, UniJobsSource, FindAJobSource
```

**HTML Scrapers** (7 — parse with regex):
```
LinkedInSource, JobTensorSource, ClimatebaseSource, EightyKHoursSource,
BCSJobsSource, AIJobsGlobalSource, AIJobsAISource
```

**Special** (5):
- `JobSpySource` — uses python-jobspy for Indeed/Glassdoor (optional dependency, skips with warning if not installed)
- `HackerNewsSource` — Algolia "Who is Hiring" threads
- `TheMuseSource` — TheMuse public API
- `NoFluffJobsSource` — NoFluffJobs public API
- `NomisSource` — UK GOV vacancy statistics (market intelligence, not individual listings)

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
  |     +-- linkedin_positions: list[dict]      # From LinkedIn ZIP
  |     +-- linkedin_skills: list[str]           # From LinkedIn ZIP
  |     +-- linkedin_industry: str               # From LinkedIn ZIP
  |     +-- github_languages: dict[str, int]     # From GitHub API
  |     +-- github_topics: list[str]             # From GitHub API
  |     +-- github_skills_inferred: list[str]    # From GitHub API
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
LinkedIn ZIP -> parse_linkedin_zip() -> dict
  |
  +-> positions.csv -> job titles, companies, date ranges
  +-> skills.csv -> endorsed skills list
  +-> education.csv -> degrees, institutions
  |
  enrich_cv_from_linkedin(cv_data, linkedin_data) -> CVData
  # Merges LinkedIn data into existing CVData fields
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

All API keys are optional — free sources (41 of 48) work without any keys.

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
| aiosqlite >=0.19.0 | Async SQLite for job storage |
| python-dotenv >=1.0.0 | .env file loading |
| jinja2 >=3.1.0 | HTML report templates |
| click >=8.1.0 | CLI framework |
| streamlit >=1.30.0 | Web dashboard |
| pandas >=2.0.0 | Data manipulation in dashboard |
| plotly >=5.18.0 | Charts in dashboard |
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
