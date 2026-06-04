# Job360

Automated UK job search system supporting **any professional domain**. Aggregates jobs from **49 source instances** (50 keys in `SOURCE_REGISTRY`; `indeed`/`glassdoor` share `JobSpySource`), scores them 0–100 against your profile (CV, LinkedIn, GitHub, and manual preferences), deduplicates via a four-layer cascade, and delivers results via CLI, email/Slack/Discord/Telegram/webhook (per-user via Apprise), CSV, Rich terminal table, and a Next.js dashboard backed by FastAPI.

> **A profile is required.** Default keyword lists (`JOB_TITLES`, `PRIMARY_SKILLS`, …) were emptied on 2026-04-09 (commit `3ba1342`). Without a profile, the system has nothing to score against — `setup-profile` is now a mandatory first step, not optional.

> **For current architecture detail**: see [`docs/pillars/`](./docs/pillars/) — three code-verified pillar manuals (User, Search & Match Engine, Job Providers) plus a glossary and runbook. The sections below in this README cover quick-start and CLI; the pillar docs are authoritative for system internals.

### API docs (auto-generated)

Once the backend is running (`cd backend && python main.py`), interactive API docs are served at **http://localhost:8000/docs** (Swagger UI) and **http://localhost:8000/redoc** (ReDoc). Both are generated from the FastAPI route decorators + Pydantic models — no separate maintenance.

## Architecture

```mermaid
flowchart TD
    CLI["CLI (Click)\njob360 run / view / api / status / sources / setup-profile"]
    Cron["Cron 4AM/4PM\nEurope/London"]

    subgraph Sources["50 Job Sources"]
        direction LR
        subgraph Keyed["Keyed APIs (7)"]
            A1[Reed.co.uk]
            A2[Adzuna]
            A3[JSearch]
            A4[Jooble]
            A5[Google Jobs\nSerpApi]
            A6[Careerjet]
            A7[Findwork]
        end
        subgraph Free["Free APIs (10)"]
            B1[Arbeitnow]
            B2[RemoteOK]
            B3[Jobicy]
            B4[Himalayas]
            B5[Remotive]
            B6[DevITjobs]
            B7[Landing.jobs]
            B8[AIJobs.net]
            B9[HN Jobs]
            B10[YC Companies]
        end
        subgraph ATS["ATS Boards (10) — ~104 companies"]
            C1[Greenhouse\n25 companies]
            C2[Lever\n12 companies]
            C3[Workable\n8 companies]
            C4[Ashby\n9 companies]
            C5[SmartRecruiters\n6 companies]
            C6[Pinpoint\n8 companies]
            C7[Recruitee\n8 companies]
            C8[Workday\n15 companies]
            C9[Personio\n10 companies]
            C10[SuccessFactors\n3 companies]
        end
        subgraph RSS["RSS/XML Feeds (8)"]
            D1[jobs.ac.uk]
            D2[NHS Jobs]
            D3[WorkAnywhere]
            D4[WeWorkRemotely]
            D5[RealWorkFromAnywhere]
            D6[BioSpace]
            D7[University Jobs\n6 UK unis]
            D8[UK GOV FindAJob]
        end
        subgraph Scrapers["HTML Scrapers (7)"]
            E1[LinkedIn\nguest API]
            E2[JobTensor]
            E3[Climatebase]
            E4[80000Hours]
            E5[BCS Jobs]
            E6[AIJobs Global]
            E7[AIJobs AI]
        end
        subgraph Other["Other (5 + 1 stats)"]
            F1[Indeed / Glassdoor\npython-jobspy]
            F2[HackerNews\nAlgolia API]
            F3[TheMuse]
            F4[NoFluffJobs]
            F5[Nomis\nUK GOV stats]
        end
    end

    CLI -->|"--source / --dry-run / --no-email"| Orchestrator["Orchestrator\nsrc/main.py"]
    Cron -->|triggers| Orchestrator
    Sources -->|async fetch\nrate-limited + retries| Orchestrator
    Orchestrator --> Scorer["Scorer\nTitle 40 + Skills 40\nLocation 10 + Recency 10\n− Negative penalty 30\n− Foreign location 15"]
    Scorer --> Dedup[Deduplicator\nnormalized company+title]
    Dedup --> DB[(SQLite\nSeen Jobs + Run Log)]

    DB --> Channels{NotificationChannel ABC}
    Channels --> Email[Email\nHTML + CSV]
    Channels --> Slack[Slack\nBlock Kit]
    Channels --> Discord[Discord\nEmbeds]

    DB --> CSV[CSV Export]
    DB --> Report[Markdown Report]
    DB --> RichTable[Rich Terminal Table\ncli view]
    DB --> NextJS[Next.js Frontend\nvia FastAPI]
```

## Features

### Job Sources (49 classes / 50 registry keys / 49 instances)

> The reconciliation: 49 *class files* on disk → 50 *registry keys* (`indeed`+`glassdoor` both map to `JobSpySource`) → 49 *live instances* per run. Test assertions pin all three (`test_cli.py` requires `len(SOURCE_REGISTRY) == 50`).

- **7 keyed APIs**: Reed, Adzuna, JSearch, Jooble, Google Jobs (SerpApi), Careerjet, Findwork — skip gracefully if no API key set
- **11 free APIs**: Arbeitnow, RemoteOK, Jobicy, Himalayas, Remotive, DevITjobs, Landing.jobs, AIJobs.net, HN Jobs, Teaching Vacancies *(Batch 3)*, Gov Apprenticeships *(Batch 3)* — no auth required
- **12 ATS boards** over ~266 company slugs: Greenhouse, Lever, Workable, Ashby, SmartRecruiters, Pinpoint, Recruitee, Workday, Personio, SuccessFactors, Rippling *(Batch 3)*, Comeet *(Batch 3)*
- **8 RSS/XML feeds**: jobs.ac.uk, NHS Jobs (keyword search), NHS Jobs XML *(Batch 3, full vacancy feed with conditional fetch pilot)*, WorkAnywhere, WeWorkRemotely, RealWorkFromAnywhere, BioSpace, University Jobs
- **7 HTML scrapers**: LinkedIn (guest API), JobTensor, Climatebase, 80000Hours, BCS Jobs, AIJobs Global, AIJobs AI
- **4 other**: Indeed/Glassdoor (via optional `python-jobspy`), HackerNews (Algolia), TheMuse, NoFluffJobs

**Dropped in Batch 3**: `yc_companies` (covered by HN Jobs + Ashby), `findajob` (duplicate of Adzuna), `nomis` (UK ONS *statistics*, not vacancy listings).

**Domain routing**: each source has a `.DOMAINS` set (`tech`, `healthcare`, `academia`, `education`, `climate`, or `general`). `classify_user_domain(profile)` filters sources to the user's domain set so a teacher doesn't get healthcare jobs and vice versa. See `docs/pillars/03-job-providers.md` §4.7.

### Profile System (any domain)
- **CV parsing**: Upload PDF or DOCX, extracts skills, job titles, education, certifications
- **LinkedIn enrichment**: Import a LinkedIn profile PDF (profile → More → Save to PDF) — positions, skills, education
- **GitHub enrichment**: Fetch public repos, infer skills from languages and topics
- **Interactive preferences**: Target titles, skills, locations, salary range, work arrangement
- **Dynamic keywords**: Profile generates personalised search queries, relevance keywords, and scoring criteria
- **Profile required**: As of 2026-04-09 the keyword fallback lists are empty — `setup-profile` must run before the engine can produce meaningful scores

### Scoring (0-100)
- **Title match** (0-40 pts) — exact match = 40, partial = 20, keyword overlap = 5 each
- **Skill match** (0-40 pts) — primary skills 3pts, secondary 2pts, tertiary 1pt (capped at 40)
- **Location** (0-10 pts) — target UK location = 10, remote = 8
- **Recency** (0-10 pts) — 0-1 days = 10, 1-3 days = 8, 3-5 days = 6, 5-7 days = 4, 7+ days = 0
- **Negative keyword penalty** (-30 pts) — titles matching irrelevant roles (sales, nursing, etc.) are penalised
- **Foreign location penalty** (-15 pts) — jobs with non-UK locations (US states, EU countries, etc.) are penalised
- **Experience level detection** — parses Senior, Lead, Junior, Principal, etc. from title

### Data Quality
- **HTML entity decoding** — cleans `&amp;`, `&lt;`, etc. from job descriptions
- **Company name cleaning** — strips suffixes like "Ltd", "Inc", "Limited", and region tags like "UK", "EMEA" for consistent dedup
- **Salary outlier filtering** — ignores unrealistic salary values (<10k or >500k)

### Notifications (extensible)
- **Email** — HTML digest with top jobs, scores, apply links, and CSV attachment via Gmail SMTP
- **Slack** — rich Block Kit message with top 10 jobs via webhook
- **Discord** — embed message with top 10 jobs via webhook
- **NotificationChannel ABC** — add a new channel (e.g. Telegram) by implementing one class

### CLI (Click)
- `run` — full pipeline with `--source`, `--dry-run`, `--log-level`, `--db-path`, `--no-email` options
- `view` — Rich terminal table with `--hours`, `--min-score`, `--source`, `--visa-only`, `--db-path` filters
- `setup-profile` — interactive profile wizard with `--cv`, `--linkedin`, `--github` options
- `api` — start the FastAPI backend server (consumed by the Next.js frontend)
- `status` — show last run stats from database
- `sources` — list all 48 available sources

### Frontend (Next.js + FastAPI)
- Next.js 16 + React 19 + Tailwind 4 + shadcn at `frontend/`
- Talks to FastAPI (`backend/src/api/`) over HTTP — 25 routes (health, jobs, actions, profile, search, pipeline)
- Job list with filters, score radar, time buckets
- Profile setup: CV upload, LinkedIn profile PDF import, GitHub username, preferences form
- Application pipeline Kanban board

### Infrastructure
- **Deduplication** — same job from different sources merged by normalised company+title
- **Persistent tracking** — SQLite database prevents duplicate notifications across runs
- **Visa flagging** — automatically flags jobs mentioning visa/sponsorship keywords
- **Async rate limiting** — per-source concurrency + delay (configurable in settings.py)
- **Retry logic** — 3 attempts with exponential backoff (1s, 2s, 4s) + 30s timeout per request, 120s timeout per source
- **Cron scheduling** — `cron_setup.sh` sets up 4AM/4PM UK time (Europe/London)
- **Logging** — rotating file handler (5MB max, 3 backups) + console output
- **Dry-run mode** — fetch and score without writing to DB or sending notifications
- **Auto-purge** — jobs older than 30 days are automatically deleted on each run
- **Split requirements** — prod deps in `backend/pyproject.toml`, dev/test in `requirements-dev.txt`
- **Hardened setup** — Python 3.9+ version check, idempotent installs, .env validation

### Testing (1000+ test functions — run `pytest --collect-only -q | tail -1` for live count)

| Test file | Count | What it covers |
|-----------|-------|----------------|
| `test_sources.py` | 71 | All 50 sources with mocked HTTP |
| `test_profile.py` | 55 | CV parser, preferences, keyword generator, JobScorer |
| `test_linkedin_github.py` | 58 | LinkedIn PDF parsing (section-split + LLM), GitHub API enrichment |
| `test_scorer.py` | 53 | Scoring algorithm, penalties, recency tiers, edge cases |
| `test_time_buckets.py` | 33 | Time bucket grouping logic |
| `test_models.py` | 21 | Job dataclass, normalisation, company cleaning |
| `test_notifications.py` | 19 | Email, Slack, Discord sending |
| `test_deduplicator.py` | 13 | Cross-source dedup logic |
| `test_main.py` | 12 | Orchestrator integration |
| `test_cli.py` | 11 | CLI commands + options + SOURCE_REGISTRY assertions |
| `test_database.py` | 9 | SQLite operations, migrations, source history |
| `test_api.py` | 9 | FastAPI endpoints (health, jobs, actions, profile, search, pipeline) |
| `test_llm_provider.py` | 8 | Multi-provider LLM client for CV parsing |
| `test_notification_base.py` | 7 | ABC, format_salary, channel discovery |
| `test_setup.py` | 6 | setup.sh validation |
| `test_reports.py` | 6 | Markdown + HTML report generation |
| `test_rate_limiter.py` | 5 | Async rate limiter (acquire/release, concurrency, delay) |
| `test_cron.py` | 5 | cron_setup.sh validation |
| `test_cli_view.py` | 5 | Rich terminal table viewer |
| `test_csv_export.py` | 4 | CSV export format |

## Quick Start

```bash
# 1. Clone and setup
git clone https://github.com/Ranjith36963/job360.git
cd job360
bash setup.sh

# 2. Configure API keys (optional — free sources work without any keys)
nano .env

# 3. Run job search
source venv/bin/activate
python -m src.cli run

# 4. Single source / dry run
python -m src.cli run --source arbeitnow
python -m src.cli run --dry-run --log-level DEBUG

# 5. Set up a personalised profile
python -m src.cli setup-profile --cv path/to/cv.pdf
python -m src.cli setup-profile --cv cv.pdf --linkedin linkedin-profile.pdf --github yourusername

# 6. View results in terminal
python -m src.cli view --hours 24 --min-score 50

# 7. Start the API + frontend
python -m src.cli api            # FastAPI on :8000
# (in another terminal) cd frontend && npm run dev   # Next.js on :3000

# 8. Schedule (optional)
bash cron_setup.sh
```

## CLI Usage

```bash
# Full pipeline — fetch from all 50 sources, score, deduplicate, notify
python -m src.cli run

# Single source only
python -m src.cli run --source arbeitnow
python -m src.cli run --source reed

# Dry run — fetch and score, skip DB writes and notifications
python -m src.cli run --dry-run

# Skip email notifications
python -m src.cli run --no-email

# Debug logging
python -m src.cli run --log-level DEBUG

# Custom database path
python -m src.cli run --db-path /tmp/test.db

# Combine options
python -m src.cli run --source greenhouse --dry-run --log-level DEBUG

# Set up user profile (personalise for any domain)
python -m src.cli setup-profile --cv path/to/cv.pdf
python -m src.cli setup-profile --cv cv.pdf --linkedin linkedin-profile.pdf
python -m src.cli setup-profile --cv cv.pdf --github yourusername
python -m src.cli setup-profile --linkedin linkedin-profile.pdf --github user

# View jobs in Rich terminal table
python -m src.cli view
python -m src.cli view --hours 24 --min-score 50
python -m src.cli view --source reed --visa-only
python -m src.cli view --db-path /tmp/test.db

# Start the FastAPI backend (consumed by the Next.js frontend)
python -m src.cli api

# Show last run stats
python -m src.cli status

# List all available sources
python -m src.cli sources
```

## API Key Setup

| Source | Signup | ENV Variable |
|--------|--------|-------------|
| Reed.co.uk | [reed.co.uk/developers](https://www.reed.co.uk/developers/jobseeker) | `REED_API_KEY` |
| Adzuna | [developer.adzuna.com](https://developer.adzuna.com/) | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` |
| JSearch | [rapidapi.com/jsearch](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch) | `JSEARCH_API_KEY` |
| Jooble | [jooble.org/api](https://jooble.org/api/about) | `JOOBLE_API_KEY` |
| Google Jobs | [serpapi.com](https://serpapi.com/) | `SERPAPI_KEY` |
| Careerjet | [careerjet.com/partners](https://www.careerjet.com/partners/) | `CAREERJET_AFFID` |
| Findwork | [findwork.dev](https://findwork.dev/) | `FINDWORK_API_KEY` |
| GitHub | [github.com/settings/tokens](https://github.com/settings/tokens) | `GITHUB_TOKEN` (optional, for profile enrichment) |
| Gmail | [Google App Passwords](https://myaccount.google.com/apppasswords) | `SMTP_EMAIL`, `SMTP_PASSWORD`, `NOTIFY_EMAIL` |
| Slack | [Slack Webhooks](https://api.slack.com/messaging/webhooks) | `SLACK_WEBHOOK_URL` |
| Discord | [Discord Webhooks](https://discord.com/developers/docs/resources/webhook) | `DISCORD_WEBHOOK_URL` |

**Free sources (no key needed)**: Arbeitnow, RemoteOK, Jobicy, Himalayas, Remotive, DevITjobs, Landing.jobs, AIJobs.net, HN Jobs, YC Companies, LinkedIn, Greenhouse, Lever, Workable, Ashby, SmartRecruiters, Pinpoint, Recruitee, Workday, Personio, SuccessFactors, jobs.ac.uk, NHS Jobs, WorkAnywhere, WeWorkRemotely, RealWorkFromAnywhere, BioSpace, University Jobs, FindAJob, JobTensor, Climatebase, 80000Hours, BCS Jobs, AIJobs Global, AIJobs AI, Indeed/Glassdoor (if python-jobspy installed), HackerNews, TheMuse, NoFluffJobs, Nomis — 41 sources work without any API keys.

## Scoring Algorithm

| Component | Points | How it works |
|-----------|--------|-------------|
| **Title match** | 0-40 | Exact match to target titles = 40pts. Partial match = 20pts. Keyword overlap = 5pts each |
| **Skill match** | 0-40 | Primary skills = 3pts each. Secondary = 2pts each. Tertiary = 1pt each. Capped at 40 |
| **Location** | 0-10 | Target UK location = 10pts. Remote = 8pts |
| **Recency** | 0-10 | Posted 0-1 days ago = 10pts, 1-3 days = 8pts, 3-5 days = 6pts, 5-7 days = 4pts, 7+ days = 0pts |
| **Negative keyword** | -30 | Titles matching irrelevant roles (sales engineer, recruiter, nurse, etc.) get a 30-point penalty |
| **Foreign location** | -15 | Non-UK locations (US states, EU countries, etc.) get a 15-point penalty |

**Total: 0-100** — minimum score threshold is 30 (configurable in `settings.py`)

The scorer uses dynamic keywords from the user's profile (`SearchConfig`). Hard-coded default keyword lists in `core/keywords.py` are empty since 2026-04-09 — a profile is mandatory for the engine to produce meaningful scores. The 4-component formula above runs alongside the **Batch-2.9 multi-dimensional scoring** (seniority + salary + visa + workplace, each weighted via env vars `SENIORITY_WEIGHT`/`SALARY_WEIGHT`/`VISA_WEIGHT`/`WORKPLACE_WEIGHT`) when `ENRICHMENT_ENABLED=true` and the user has filled in preferences. Final 9-field `ScoreBreakdown` documented in `docs/pillars/02-search-and-match-engine.md`.

## Notification Channels

The notification system uses an abstract base class (`NotificationChannel` in `backend/src/notifications/base.py`) with auto-discovery:

```
NotificationChannel (ABC)
├── EmailChannel      — configured if SMTP_EMAIL + SMTP_PASSWORD + NOTIFY_EMAIL set
├── SlackChannel      — configured if SLACK_WEBHOOK_URL set
└── DiscordChannel    — configured if DISCORD_WEBHOOK_URL set
```

`get_configured_channels()` returns only channels whose env vars are set. The orchestrator loops over them:

```python
for channel in get_configured_channels():
    await channel.send(new_jobs, stats)
```

**Adding a new channel** (e.g. Telegram): create `backend/src/notifications/telegram_notify.py`, implement `NotificationChannel`, and register it in `get_all_channels()`.

## Adding a New Job Source

1. Create `backend/src/sources/yoursource.py`, extend `BaseJobSource`
2. Implement `async fetch_jobs() -> list[Job]`
3. Use `self.relevance_keywords` and `self.job_titles` for filtering (not direct imports)
4. If custom `__init__`, accept `search_config=None` and pass to `super().__init__(session, search_config=search_config)`
5. Register in `SOURCE_REGISTRY` dict in `backend/src/main.py`
6. Add to `_build_sources()` list in `backend/src/main.py` (passing `search_config=sc`)
7. Add rate limit entry in `RATE_LIMITS` dict in `backend/src/config/settings.py`
8. Add mocked tests in `backend/tests/test_sources.py`
9. Update the `len(SOURCE_REGISTRY) == N` assertion and expected source set in `backend/tests/test_cli.py`
10. If keyed: add env var to `backend/src/config/settings.py` and `.env.example`

## Configuration

### Default Keywords (`backend/src/config/keywords.py`)
- **25 job titles**: AI Engineer, ML Engineer, Machine Learning Engineer, GenAI Engineer, Generative AI Engineer, LLM Engineer, NLP Engineer, Data Scientist, MLOps Engineer, AI/ML Engineer, Deep Learning Engineer, Computer Vision Engineer, RAG Engineer, AI Solutions Engineer, AI Research Engineer, Applied ML Engineer, Python AI Developer, AI Researcher, ML Scientist, Machine Learning Scientist, AI Platform Engineer, AI Infrastructure Engineer, Conversational AI Engineer, Applied Scientist, Research Scientist
- **15 primary skills** (3pts each): Python, PyTorch, TensorFlow, LangChain, RAG, LLM, Generative AI, Hugging Face, Transformers, OpenAI, NLP, Deep Learning, Neural Networks, Computer Vision, Prompt Engineering
- **17 secondary skills** (2pts each): Scikit-learn, Keras, AWS, SageMaker, Bedrock, Docker, Kubernetes, FastAPI, ChromaDB, FAISS, OpenSearch, Redis, pgvector, Gemini, Agentic AI, LLM fine-tuning, Fine-tuning
- **11 tertiary skills** (1pt each): CI/CD, MLflow, Git, Linux, n8n, Data Pipelines, ETL, Feature Engineering, S3, CloudWatch, Machine Learning
- **24 UK locations** + Remote/Hybrid
- **60 negative title keywords** across 12 categories (sales, IT ops, healthcare, legal, finance, etc.)
- **LLM-only CV parsing** via `backend/src/profile/llm_provider.py` (multi-provider: Gemini, Groq, Cerebras with free-tier fallback). The earlier 391-entry `KNOWN_SKILLS` regex set was removed in commit 3ba1342.

### ATS Companies (`backend/src/config/companies.py`)
- **Greenhouse** (25): DeepMind, Monzo, Deliveroo, Darktrace, Stability AI, Anthropic, Graphcore, Wayve, PolyAI, Synthesia, Wise, Snyk, Stripe, Cloudflare, Databricks, Dataiku, Ocado Technology, Tractable, Paddle, Harness, Isomorphic Labs, Speechmatics, Onfido, Oxford Nanopore, Bloomberg
- **Lever** (12): Mistral, Healx, Palantir, Spotify, ZOE, Tractable, Helsing, SecondMind, MosaicML, Faculty, Dyson, Five AI
- **Workable** (8): BenevolentAI, Exscientia, Oxa, Cervest, Hugging Face, Labelbox, Runway, Adept
- **Ashby** (9): Anthropic, Cohere, OpenAI, Improbable, Synthesia, Multiverse, ElevenLabs, Perplexity, Anyscale
- **SmartRecruiters** (6): Wise, Revolut, Checkout.com, AstraZeneca, Samsung R&D UK, Booking
- **Pinpoint** (8): MoneySuperMarket, Bulb, Starling Bank, Octopus Energy, Faculty, Arm, Sky, Tesco Technology
- **Recruitee** (8): Peak AI, Satalia, Speech Graphics, Signal AI, Eigen Technologies, Causaly, Kheiron Medical, PolyAI
- **Workday** (15): AstraZeneca, NVIDIA, Shell, Roche, Novartis, Cisco, Dell, Intel, Unilever, HSBC, Barclays, Lloyds Banking Group, Rolls-Royce, GSK, Jaguar Land Rover
- **Personio** (10): Celonis, Trade Republic, Sennder, Contentful, Personio, Forto, Taxfix, Wonderkind, Airfocus, Heydata
- **SuccessFactors** (3): BAE Systems, QinetiQ, Thales UK

## Project Structure

```
job360/
├── backend/src/
│   ├── main.py                  # Central orchestrator (run_search, SOURCE_REGISTRY)
│   ├── cli.py                   # Click CLI (run, view, api, status, sources, setup-profile)
│   ├── cli_view.py              # Rich terminal table viewer (time-bucketed)
│   ├── models.py                # Job dataclass with company normalisation
│   ├── api/                     # FastAPI backend consumed by the Next.js frontend
│   ├── config/
│   │   ├── settings.py          # Env vars, rate limits, timeouts, thresholds
│   │   ├── keywords.py          # Default AI/ML keywords (KNOWN_SKILLS and KNOWN_TITLE_PATTERNS removed in commit 3ba1342)
│   │   └── companies.py         # ATS company slugs (~104 companies across 10 platforms)
│   ├── profile/
│   │   ├── models.py            # CVData, UserPreferences, UserProfile, SearchConfig
│   │   ├── cv_parser.py         # PDF/DOCX text extraction; LLM-only skill/title extraction
│   │   ├── llm_provider.py      # Multi-provider LLM client (Gemini/Groq/Cerebras) for CV parsing
│   │   ├── preferences.py       # Form validation, CV+preferences merge
│   │   ├── storage.py           # JSON persistence (backend/data/user_profile.json)
│   │   ├── keyword_generator.py # UserProfile -> SearchConfig conversion
│   │   ├── linkedin_parser.py   # LinkedIn profile PDF parser (pdfplumber + LLM)
│   │   └── github_enricher.py   # GitHub public API enricher
│   ├── sources/
│   │   ├── base.py              # Abstract base with retry logic + rate limiting
│   │   └── *.py                 # 47 source files (48 registry entries)
│   ├── filters/
│   │   ├── skill_matcher.py     # Scoring engine (0-100, 4 components + 2 penalties)
│   │   └── deduplicator.py      # Cross-source dedup by normalised key
│   ├── notifications/
│   │   ├── base.py              # NotificationChannel ABC + auto-discovery
│   │   ├── email_notify.py      # Gmail SMTP (HTML + CSV attachment)
│   │   ├── slack_notify.py      # Slack Block Kit via webhook
│   │   ├── discord_notify.py    # Discord embeds via webhook
│   │   └── report_generator.py  # Markdown + HTML report generation
│   ├── storage/
│   │   ├── database.py          # Async SQLite (jobs + run_log tables, auto-purge)
│   │   └── csv_export.py        # CSV export per run
│   └── utils/
│       ├── logger.py            # Rotating file + console logging
│       ├── rate_limiter.py      # Async semaphore + delay rate limiter
│       └── time_buckets.py      # Time bucket grouping for CLI view
├── backend/tests/                       # 600 passing (post-Step-0 pre-flight) across 20 files
│   ├── conftest.py              # Shared fixtures (sample jobs)
│   └── test_*.py                # 20 test modules
├── backend/data/                        # Exports, reports, logs (gitignored)
├── backend/pyproject.toml             # Production dependencies (17 packages)
├── requirements-dev.txt         # Dev/test dependencies (pytest, aioresponses, fpdf2)
├── .env.example                 # Template for API keys and webhooks
├── setup.sh                     # Setup script (Python 3.9+ check, venv, deps)
└── cron_setup.sh                # Cron scheduling (4AM/4PM Europe/London)
```

## Testing

```bash
# Run the full test suite
python -m pytest backend/tests/ -v

# Run specific test file
python -m pytest backend/tests/test_scorer.py -v

# Run with output
python -m pytest backend/tests/ -v -s
```

The full test suite pass. Every source is tested with mocked HTTP responses (aioresponses). No network access required. 3 tests skip on Windows (bash-only tests for setup.sh and cron_run.sh).

## Output

Each run produces:

| Output | Location | Description |
|--------|----------|-------------|
| CSV | `backend/data/exports/jobs_YYYYMMDD_HHMMSS.csv` | Full job data with scores |
| Markdown | `backend/data/reports/report_YYYYMMDD_HHMMSS.md` | Ranked job tables |
| Rich table | Terminal (`python -m src.cli view`) | Time-bucketed terminal table with filters |
| Email | Inbox | HTML digest with top jobs + CSV attachment |
| Slack | Channel | Block Kit message with top 10 jobs |
| Discord | Channel | Embed message with top 10 jobs |
| Frontend | `http://localhost:3000` | Next.js UI (requires FastAPI on `:8000`) |
| Console | Terminal | Time-bucketed summary of new jobs found |
| Logs | `backend/data/logs/job360.log` | Rotating log file (5MB, 3 backups) |
