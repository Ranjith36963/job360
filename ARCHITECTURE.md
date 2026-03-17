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
|   +-- main.py              # Orchestrator: run_search(), _build_sources(), SOURCE_REGISTRY
|   +-- cli.py               # Click CLI: run, dashboard, status, sources, view, setup-profile
|   +-- cli_view.py          # Rich terminal table viewer
|   +-- dashboard.py         # Streamlit web UI with profile setup sidebar
|   +-- models.py            # Job dataclass with normalized_key()
|   +-- config/
|   |   +-- settings.py      # Env vars, paths, rate limits, thresholds
|   |   +-- keywords.py      # Default AI/ML keywords (fallback when no profile)
|   |   +-- companies.py     # ATS company slugs (200+ companies across 10 ATS platforms)
|   +-- profile/             # <-- NEW (Phase 1)
|   |   +-- models.py        # CVData, UserPreferences, UserProfile, SearchConfig
|   |   +-- cv_parser.py     # PDF/DOCX text extraction + section parsing
|   |   +-- preferences.py   # Form validation, CV+preferences merge
|   |   +-- storage.py       # JSON persistence (data/user_profile.json)
|   |   +-- keyword_generator.py  # UserProfile -> SearchConfig conversion
|   +-- sources/
|   |   +-- base.py          # BaseJobSource ABC with retry, rate limiting, keyword properties
|   |   +-- reed.py          # ... 47 source implementations
|   |   +-- adzuna.py
|   |   +-- ... (48 total)
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
|       +-- time_buckets.py  # Time bucketing, skill extraction, score colors
+-- tests/
|   +-- conftest.py          # Shared fixtures (sample_ai_job, etc.)
|   +-- test_*.py            # 17 test files, 310 tests
+-- data/                    # Runtime data (gitignored)
|   +-- jobs.db              # SQLite database
|   +-- user_profile.json    # User profile (Phase 1)
|   +-- exports/             # CSV exports per run
|   +-- reports/             # Markdown reports per run
|   +-- logs/                # Log files
+-- .env                     # API keys (gitignored)
+-- .env.example             # Template for .env
+-- requirements.txt         # Production dependencies (12 packages)
+-- requirements-dev.txt     # Test dependencies (includes prod via -r)
+-- setup.sh                 # Setup script (venv, deps, validation)
```

---

## Data Flow: Pipeline Run

### 1. Profile Loading (`main.py:run_search`)

```python
profile = load_profile()                    # data/user_profile.json
if profile and profile.is_complete:
    search_config = generate_search_config(profile)  # UserProfile -> SearchConfig
    scorer = JobScorer(search_config)                 # Dynamic scorer
else:
    search_config = None                              # Use defaults
    scorer = None                                     # Use score_job()
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

### 4. Fetch (async, concurrent)

```python
results = await asyncio.gather(*[fetch_source(s) for s in sources])
# Each source filters by self.relevance_keywords in fetch_jobs()
# Each source has 120s timeout
```

### 5. Scoring

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

### 6. Deduplication

```python
unique = deduplicate(all_jobs)
# Groups by (normalized_company, normalized_title)
# Keeps highest match_score, then most complete data
```

### 7. Output

```python
# Filter by MIN_MATCH_SCORE (30)
# Store new jobs in SQLite
# Export CSV to data/exports/
# Generate markdown report to data/reports/
# Send notifications (email, Slack, Discord) if configured
# Print time-bucketed summary to console
```

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
| Negative title | -30 | Title contains excluded keywords |
| Foreign location | -15 | Location matches foreign indicators (US cities, countries, state codes) |

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
    normalized_company TEXT,
    normalized_title TEXT,
    first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(normalized_company, normalized_title)
);

CREATE TABLE run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
    total_found INTEGER,
    new_jobs INTEGER,
    per_source TEXT  -- JSON string
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

### Dev (requirements-dev.txt)
Includes all production deps plus: pytest, pytest-asyncio, aioresponses
