# Job360 Architecture
<!-- doc: LIVING | last-verified: 2026-09-05 by slice 5 (delete the sourcing era) -->

> **Mission (2026-09-03, [`docs/product/VISION.md`](docs/product/VISION.md)):** Job360 is the memory and context layer for the seeker's own AI agent. The agent finds the job, judges fit, writes the CV, reads Gmail, does outreach; Job360 stores the profile, every artifact version, every typed event and the receipt. **We never source, rank or recommend jobs.**
>
> This file describes the one path the app has: `api/routes/bring.py` (`POST /jobs/bring`, link or text) → `api/routes/receipts.py` (append-only `application_receipts`) → `api/routes/tailor.py` (CV tailor, web fallback only) → `api/mcp_server.py` (the MCP tools at `/api/mcp` — count them with `grep -c "@mcp.tool()"`; bearer `j360_…`, OAuth 2.1). The FastAPI app behind it is the route modules under `backend/src/api/routes/` (counts: [`docs/GENERATED.md`](docs/GENERATED.md)). Profile extraction (`services/profile/`) feeds it, and the application spine (`applications`, `application_events`, `application_artifacts`, `application_receipts`) records everything that happens to a brought job.
>
> **The sourcing-era pipeline was deleted 2026-09-05** (slice 5, #483): job search, keyword-driven scoring, four-layer dedup, LLM enrichment, embeddings, the search dashboard, and the 40 job-source classes that fed them. **The per-user notification-channel system (Apprise dispatcher, Slack/Discord/Telegram connect flows, digest queue) was deleted the same day.** None of that code exists in this repo any more, and nothing archives its history in-tree — git history is the record.
>
> Three Railway services: `backend`, `frontend`, `Postgres`. The `worker` and `Redis` services were deleted 2026-09-02, and `src/workers/` was deleted with the sourcing era — nothing runs in the background (no notifications, no crons).

## Repo facts

The countable facts — migration head, route and endpoint counts, test-file count, workflow count, hard-rule count — are machine-written into [`docs/GENERATED.md`](docs/GENERATED.md) by `scripts/gen_doc_blocks.py`. They are deliberately NOT repeated here: a generated block beside a prose copy is two copies again, and a block that rewrites itself on every merge is what made this file conflict with every open PR that touched its prose.

---

## Directory Structure

> **Post-Phase-4 layout** (commit `a814ae8`, 2026-03-XX): `config/` → `core/`, `filters/` + `notifications/` + `profile/` → `services/{...}`, `storage/` → `repositories/`. The old paths in earlier docs no longer exist.

```
job360/
├── backend/
│   ├── main.py                       # FastAPI uvicorn entry (thin; imports src/api/main.py)
│   ├── pyproject.toml                # Deps + dev extras, ruff/mypy/pytest config
│   ├── data/                         # Runtime (gitignored): exports/, reports/, logs/, chroma/, legacy user_profile.json. NO jobs.db — the store is Postgres; `core.settings.DB_PATH` is a connection selector `repositories.pg.connect` maps to a schema, not a file
│   ├── migrations/                   # forward/reverse SQL migration pairs + runner.py (counts: repo facts above)
│   ├── src/
│   │   ├── cli.py                    # Click CLI: api, setup-profile
│   │   ├── models.py                 # Job dataclass + normalized_key() — DB UNIQUE constraint, still used by a brought ad
│   │   ├── api/                      # FastAPI: lifespan, CORS, dependencies, route modules (counts: repo facts above)
│   │   │   └── routes/               # health, applications, profile, auth, tailor, client_log, tokens, bring, receipts, oauth, well_known (root-mounted)
│   │   ├── core/                     # (post-Phase-4 rename from config/)
│   │   │   ├── settings.py           # Env vars, rate limits, the ESCO flag
│   │   │   ├── observability.py      # Sentry init
│   │   │   └── tenancy.py            # DEFAULT_TENANT_ID UUID for CLI/legacy rows
│   │   ├── services/                 # (post-Phase-4 merge of filters/ + notifications/ + profile/)
│   │   │   ├── auth/                 # passwords (argon2id), sessions (HMAC cookies), magic-link + system email (Resend/SMTP)
│   │   │   ├── applications/         # application-spine services (events, artifacts, authorship, contacts, stats, diff, lessons, visa)
│   │   │   ├── fetch/                # the URL-fetch web fallback (extract, fetcher, ssrf guard.py, outcomes)
│   │   │   ├── tailoring/            # generator, prompts, provenance, integrity, docx, pdf — the tailor web fallback
│   │   │   └── profile/              # cv_parser, llm_provider, linkedin_parser, github_enricher, models, preferences, storage, seniority, skill_normalizer
│   │   ├── repositories/             # (post-Phase-4 rename from storage/)
│   │   │   └── database.py           # Postgres via psycopg3 (`pg.py` aiosqlite-shaped shim) + forward-compat migration schema
│   │   └── utils/
│   │       ├── logger.py             # Rotating file + console logging
│   │       ├── audit_trail.py        # who-did-what rows for account changes
│   │       └── loop_guard.py         # refuses blocking work on the event loop
│   └── tests/                        # file count: `docs/GENERATED.md` (collected-test count: measure it, never quote it)
├── frontend/                         # Next.js 16 + React 19 + Tailwind 4 + shadcn
│   ├── src/app/                      # App Router pages (server/client split; params is Promise<...> per Next.js 16)
│   ├── src/components/{ui,applications,tailor,profile,layout}/
│   └── src/lib/{api.ts,types.ts,utils.ts}
├── docs/
│   └── product/                      # VISION.md (the mission), product_design_rules.md
├── .env.example
└── CLAUDE.md                         # Canonical AI agent instructions
```

---

## Job Model and the `jobs` Table

### Job Dataclass (`backend/src/models.py`)

`jobs` is now "the ad the user brought", nothing more — a shared catalog row keyed
by the same `(normalized_company, normalized_title)` pair a scraper used to
dedup against, because the uniqueness constraint on the table still needs it
(hard rule 1). No code sets a score, a visa flag or an experience level on it
any more; those fields are trimmed from the model wherever nothing remaining
writes them.

**Post-init processing kept:**
- HTML entity decoding on title and company (`html.unescape`)
- Company name cleaning: empty/nan/none/null → "Unknown"

### normalized_key()

```python
def normalized_key(self) -> tuple[str, str]:
    # 1. Strip company suffixes: Ltd, Limited, Inc, PLC, Corp, GmbH, etc.
    # 2. Strip region suffixes: UK, US, EU, EMEA, APAC, Global, International
    # 3. Lowercase both company and title
    return (normalized_company, normalized_title)
```

This key is used for:
- **Database uniqueness** — `UNIQUE(normalized_company, normalized_title)` constraint, so
  two users pasting the same ad share one `jobs` row (`database.get_job_id_by_key`)

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
  +-> LLM extraction via llm_provider.py (OpenAI PRIMARY, then Gemini/Groq/Cerebras free-tier fallback)
  |     Returns: skills[], job_titles[], education[], certifications[], summary
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
```

Re-runs use only **stored** inputs (`raw_text`, `linkedin_raw_text`,
`github_repos_brief`, `about_me`) — no re-upload, no GitHub re-fetch. Each pass
no-ops when its input or LLM key is missing. Skill provenance is preserved: the
new sources `about_me_llm` (weight 2.0) and `github_llm` (1.5) feed
`skill_tiering` alongside the existing ones.

A profile save saves the profile — nothing else happens. There is no re-score
to trigger any more (the code that queued one, and the queue itself, were
deleted with the sourcing era).

### Seniority helpers (`services/profile/seniority.py`)

Profile extraction infers a candidate's seniority band from job titles
(`seniority.detect_seniority`), independent of any job search.

---

## Notification System

Job360 is **pull, not push** (VISION.md decision 11): the
seeker reads `GET /whats-new` and the web home; there is no background delivery, no
per-user notification channels, and no queue. The Apprise dispatcher, the per-user
channel CRUD, the digest queue and `notification_rules` were all deleted 2026-09-05 along
with the sourcing era — do not rebuild them.

---

## Database Schema

> **The schema is code, not prose.** The legacy baseline `init_db()` hands to
> `executescript()` is `repositories.database.JobDatabase.init_db`; every statement goes
> through `repositories.pg.translate` first, which rewrites SQLite spellings
> (`INTEGER PRIMARY KEY AUTOINCREMENT` → a Postgres identity column, `?` placeholders,
> `datetime('now')`, `INSERT OR IGNORE`) and strips FK clauses. The full schema is the
> baseline plus the forward migrations in `backend/migrations/` — read those two, in that
> order. `tests/test_pg_translate.py` pins the rewriting.

---

## API Routes

Every endpoint the app declares. Generated, so it cannot disagree with the
routers — a wrong endpoint reads like a contract and 404s whoever trusts it.

The full table — every method, path and router file, generated from the routers themselves — lives in [`docs/GENERATED.md`](docs/GENERATED.md).

## Configuration

### Environment Variables

`backend/src/core/settings.py` is the only list. Every knob is an `os.getenv`
call there with the comment that says why it exists and what it costs to move —
a table here is a second copy that falls behind silently (it had drifted 22
variables behind by 2026-09-15, including `SITE_BASE_URL`, which `README.md`
tells you to set).

- `.env` lives in the repo root (see `.env.example`).
- Required in production: `core.settings._REQUIRED_PROD_VARS`, enforced at boot
  by `core.settings.validate_required_env`. Everything else is optional.
- Tuning constants that are NOT env-readable live in the same file; changing
  them in `.env` does nothing.
- Data outputs go to `backend/data/` (gitignored): `exports/`, `reports/`, `logs/`, `chroma/`.

---

## Architectural Decisions

1. **Normalization for dedup on the one table that still needs it.** `jobs.normalized_key()` (company/title, suffix-stripped, lowercased) still backs the `UNIQUE(normalized_company, normalized_title)` constraint, because two users pasting the same job ad must land on one shared catalog row (hard rule 1). There is no dedup SERVICE any more — this is the DB constraint alone.

---

## Dependencies

### Production (backend/pyproject.toml)

> **Source of truth is `backend/pyproject.toml` — read it, don't trust this table.**

| Package | Version | Purpose |
|---------|---------|---------|
| aiohttp | >=3.14.1 | Async HTTP client (profile-side: GitHub API, LLM providers) |
| defusedxml | >=0.7.1 | XXE-safe XML parsing |
| psycopg[binary,pool] | >=3.2 | Async PostgreSQL driver — ALL storage. SQLite is fully removed; `pg.py` shims an aiosqlite-shaped API over it, but **aiosqlite is NOT a dependency** |
| python-dotenv | >=1.0.0 | .env file loading |
| jinja2 | >=3.1.0 | HTML report templates |
| click | >=8.1.0 | CLI framework |
| pdfplumber | >=0.10.0 | PDF text extraction (CV parsing) |
| python-docx | >=1.1.0 | DOCX text extraction (CV parsing) |
| rich | >=13.0.0 | Terminal table rendering |
| humanize | >=4.9.0 | Relative time formatting |
| fastapi | >=0.115.0 | API server for the Next.js frontend (`backend/src/api/`) |
| uvicorn[standard] | >=0.30.0 | ASGI server for FastAPI |
| python-multipart | >=0.0.9 | File upload support |
| httpx | >=0.27.0 | Async HTTP client (used by API + LLM providers) |
| openai | >=1.0.0 | **PRIMARY** CV-parsing LLM provider |
| google-generativeai / groq / cerebras-cloud-sdk | >=0.8.0 / >=0.11.0 / >=1.0.0 | Fallback LLM providers for CV parsing |
| argon2-cffi / itsdangerous / email-validator | >=23.1.0 / >=2.2.0 / >=2.1.0 | Password hashing (argon2id) + signed session cookies + pydantic `EmailStr`. `cryptography` (Fernet channel-credential encryption) was dropped with the notification-channel system, 2026-09-05 |
| rapidfuzz | >=3.0 | Tailor integrity check + two-pass profile merge (**lazy-imported**, rule #16) |
| scikit-learn | >=1.4 | **No importer left** — it was the dedup TF-IDF layer (deleted, slice 5). Still declared because the `[semantic]` stack expects it; dropping it is a follow-up |
| sentry-sdk | >=1.40.0 | Error tracking + performance monitoring |
| sentence-transformers / numpy | `[semantic]` extra (~300 MB) | One importer left: `services/profile/skill_normalizer.py` (**lazy-imported**, opt-in). `chromadb` left the extra with the sourcing-era embeddings stack (slice 5) |

`arq` and `python-jobspy` are gone — no worker process, no Indeed/Glassdoor scraper. The `indeed` extra no longer exists.

### Dev (`pip install -e ".[dev]"` from `backend/`)

Read `[project.optional-dependencies].dev` in `backend/pyproject.toml` — each
entry carries the comment explaining why it is pinned or opt-in.

One thing the manifest does NOT tell you: **`pre-commit` is not in the extra**,
even though `CONTRIBUTING.md` makes `pre-commit run --all-files` a merge gate.
Install it separately.
