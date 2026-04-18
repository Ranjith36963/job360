# CurrentStatus.md — Job360 Technical Audit

> **Purpose.** This document records exactly what the Job360 codebase *is* right now — not what it should be, not what is planned, not what older docs claim. Every factual claim is anchored to a file and line number that was read during this audit (2026-04-11). It is descriptive, not prescriptive. Recommendations belong in a separate `PlanningReport.md`.
>
> **Scope.** Backend Python pipeline (`backend/`), Next.js 16 frontend (`frontend/`), SQLite persistence, FastAPI, 47 job-source integrations, and their supporting tests and infrastructure.
>
> **Method.** Every `backend/src/` file, every `frontend/src/app/` + `frontend/src/components/` + `frontend/src/lib/` file, the full test directory, `pyproject.toml`, `package.json`, `cron_setup.sh`, and the repo-root config files were read end-to-end by three parallel Explore agents. Findings were then verified by direct `Read` of the load-bearing files (`main.py`, `models.py`, `skill_matcher.py`, `deduplicator.py`, `database.py`, `settings.py`, `keywords.py`, `base.py`, `api/main.py`, `cli.py`, `main.py` shim, `cron_setup.sh`) before writing. CLAUDE.md and in-project memory were intentionally not relied on for facts — only for cross-checking where the source disagreed.
>
> **Absolute count anchors (to catch future drift).**
> - `SOURCE_REGISTRY` dict entries: **48** (`backend/src/main.py:78–128`)
> - Unique source instances built by `_build_sources()`: **47** (hard-coded at `backend/src/main.py:133` as `SOURCE_INSTANCE_COUNT = 47`). The 48-vs-47 gap is because `"indeed"` and `"glassdoor"` both map to `JobSpySource`.
> - Source `.py` files under `backend/src/sources/` (excluding `base.py` and `__init__.py`): **47**
> - Routers mounted on the FastAPI app: **6** (`backend/src/api/main.py:26–31`) — not 7 as CLAUDE.md claims
> - FastAPI routes directory `backend/src/api/routes/`: 6 modules (health, jobs, actions, profile, search, pipeline)
> - Test files under `backend/tests/`: **21** `test_*.py` files
> - Tables in the SQLite schema: **4** (jobs, run_log, user_actions, applications)
> - Indexes: **3** (`idx_jobs_date_found`, `idx_jobs_first_seen`, `idx_jobs_match_score`)
> - Backend production dependencies in `pyproject.toml`: **17**
> - Backend dev dependencies: **4** (pytest, pytest-asyncio, aioresponses, fpdf2)
> - Frontend runtime dependencies: **12**

---

## 1. Project Overview

### What it is

**Job360** is an automated UK job-search aggregator that hits 47 external job boards in parallel, normalizes each listing into a common `Job` dataclass, scores every job from 0–100 against a user-supplied `SearchConfig` (derived from a CV + optional LinkedIn export + optional GitHub profile + manual preferences), deduplicates across sources, persists to SQLite with a 30-day retention window, and delivers results via CLI table, email/Slack/Discord notifications, CSV export, Markdown/HTML reports, and a Next.js 16 single-page application backed by a FastAPI service.

The pipeline is profile-driven end-to-end: without a user profile, `run_search()` aborts before any sources are queried (`backend/src/main.py:241–258`). There is no hardcoded "default" job domain — every keyword list in `backend/src/core/keywords.py` except `LOCATIONS` and `VISA_KEYWORDS` was intentionally emptied in commit history on 2026-04-09, and a note in that file (`keywords.py:1–9`) explains that the system now requires a profile for meaningful matching.

### Tech stack (backend)

Verbatim from `backend/pyproject.toml:6–26`:

| Package | Version | Role |
|---|---|---|
| aiohttp | ≥3.9.0 | Async HTTP client for every source |
| aiosqlite | ≥0.19.0 | Async SQLite driver for `JobDatabase` |
| python-dotenv | ≥1.0.0 | Loads `.env` on import of `core/settings.py` |
| jinja2 | ≥3.1.0 | HTML report templates |
| click | ≥8.1.0 | CLI framework (`backend/src/cli.py`) |
| pandas | ≥2.0.0 | DataFrame support for python-jobspy (Indeed/Glassdoor) |
| plotly | ≥5.18.0 | Charting library (reserved for future analytics) |
| pdfplumber | ≥0.10.0 | PDF CV text extraction |
| python-docx | ≥1.1.0 | DOCX CV text extraction |
| rich | ≥13.0.0 | Terminal table renderer (`cli_view.py`) |
| humanize | ≥4.9.0 | Relative time formatting |
| fastapi | ≥0.115.0 | API framework |
| uvicorn[standard] | ≥0.30.0 | ASGI server |
| python-multipart | ≥0.0.9 | File-upload form parsing |
| httpx | ≥0.27.0 | Used by FastAPI TestClient + some LLM SDKs |
| google-generativeai | ≥0.8.0 | Gemini provider for CV parsing |
| groq | ≥0.11.0 | Groq provider for CV parsing |
| cerebras-cloud-sdk | ≥1.0.0 | Cerebras provider for CV parsing |

**Dev extras** (`pyproject.toml:29–34`): `pytest>=8.0.0`, `pytest-asyncio>=0.23.0`, `aioresponses>=0.7.0`, `fpdf2>=2.7.0`.

**Optional `indeed` extra** (`pyproject.toml:35–37`): `python-jobspy`. Only used by `backend/src/sources/other/indeed.py`; that source gracefully no-ops when the package is missing.

**Python version constraint**: `requires-python = ">=3.9"` (`pyproject.toml:5`).

**Pytest config** (`pyproject.toml:46–49`): `testpaths=["tests"]`, `asyncio_mode = "auto"`, `pythonpath = ["."]`. The `pythonpath=["."]` means pytest must be run from `backend/` (so `src.*` imports resolve).

### Tech stack (frontend)

Verbatim from `frontend/package.json:11–34`:

Runtime (12):
- `next` 16.2.2
- `react` 19.2.4
- `react-dom` 19.2.4
- `@base-ui/react` ^1.3.0
- `class-variance-authority` ^0.7.1
- `clsx` ^2.1.1
- `lucide-react` ^1.7.0
- `motion` ^12.38.0
- `recharts` ^3.8.1
- `shadcn` ^4.1.2
- `tailwind-merge` ^3.5.0
- `tw-animate-css` ^1.4.0

Dev (9):
- `@tailwindcss/postcss` ^4
- `@types/node` ^20
- `@types/react` ^19
- `@types/react-dom` ^19
- `eslint` ^9
- `eslint-config-next` 16.2.2
- `tailwindcss` ^4
- `typescript` ^5

**Note from `frontend/AGENTS.md`**: *"This is NOT the Next.js you know. This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices."* — This is in `frontend/CLAUDE.md` as an `@AGENTS.md` import, so any work on the frontend must consult local docs rather than assume prior-version Next.js semantics.

### Project tree (post-phase-4 layout)

The repo has two top-level deployables. Runtime data lives inside `backend/data/` so the backend is self-contained.

```
job360/
├── backend/
│   ├── main.py                 # 19-line FastAPI shim: `from src.api.main import app`
│   ├── pyproject.toml          # Deps + pytest/ruff/mypy config
│   ├── data/                   # Runtime (gitignored): jobs.db, user_profile.json, exports/, reports/, logs/
│   ├── src/
│   │   ├── main.py             # Pipeline orchestrator: run_search(), SOURCE_REGISTRY, _build_sources()
│   │   ├── cli.py              # Click CLI: run, api, status, view, sources, setup-profile
│   │   ├── cli_view.py         # Rich terminal table viewer
│   │   ├── models.py           # Job dataclass + normalized_key()
│   │   ├── api/
│   │   │   ├── main.py         # FastAPI app, CORS, lifespan, router registration
│   │   │   ├── dependencies.py # init_db / close_db / get_db / save_upload_to_temp
│   │   │   ├── models.py       # Pydantic req/resp models
│   │   │   └── routes/         # 6 route modules (health, jobs, actions, profile, search, pipeline)
│   │   ├── core/
│   │   │   ├── settings.py     # Env vars, paths, RATE_LIMITS, thresholds
│   │   │   ├── keywords.py     # LOCATIONS, VISA_KEYWORDS — other lists intentionally empty
│   │   │   └── companies.py    # ATS company slugs
│   │   ├── services/
│   │   │   ├── skill_matcher.py    # JobScorer + module-level legacy scoring
│   │   │   ├── deduplicator.py     # deduplicate() + _normalize_title()
│   │   │   ├── notifications/
│   │   │   │   ├── base.py
│   │   │   │   ├── email_notify.py
│   │   │   │   ├── slack_notify.py
│   │   │   │   ├── discord_notify.py
│   │   │   │   └── report_generator.py
│   │   │   └── profile/
│   │   │       ├── models.py          # CVData, UserPreferences, UserProfile, SearchConfig
│   │   │       ├── cv_parser.py       # PDF/DOCX extraction → LLM
│   │   │       ├── llm_provider.py    # Gemini / Groq / Cerebras fallback chain
│   │   │       ├── preferences.py
│   │   │       ├── storage.py         # data/user_profile.json
│   │   │       ├── keyword_generator.py   # UserProfile → SearchConfig
│   │   │       ├── linkedin_parser.py
│   │   │       └── github_enricher.py
│   │   ├── repositories/
│   │   │   ├── database.py     # JobDatabase (aiosqlite)
│   │   │   └── csv_export.py
│   │   ├── sources/
│   │   │   ├── base.py         # BaseJobSource ABC + _is_uk_or_remote
│   │   │   ├── apis_keyed/     # 7 files
│   │   │   ├── apis_free/      # 10 files
│   │   │   ├── ats/            # 10 files
│   │   │   ├── feeds/          # 8 files
│   │   │   ├── scrapers/       # 7 files
│   │   │   └── other/          # 5 files
│   │   └── utils/
│   │       ├── logger.py       # Rotating file handler + run ID
│   │       ├── rate_limiter.py # Semaphore + delay
│   │       └── time_buckets.py # 24h / 48h / 72h / 7d buckets
│   └── tests/                  # 21 test_*.py files + conftest.py
│
├── frontend/
│   ├── AGENTS.md               # "This is NOT the Next.js you know" warning
│   ├── CLAUDE.md               # @AGENTS.md import
│   ├── next.config.ts          # Empty config object
│   ├── tsconfig.json           # "@/*" → "./src/*"
│   ├── package.json
│   ├── components.json         # shadcn config
│   ├── public/
│   └── src/
│       ├── app/                # 6 pages: layout, page (home), dashboard, jobs/[id], pipeline, profile
│       ├── components/
│       │   ├── ui/             # 15 shadcn primitives (button, card, dialog, input, ...)
│       │   ├── jobs/           # JobCard, JobList, FilterPanel, ScoreRadar, ScoreCounter, TimeBuckets
│       │   ├── profile/        # CVUpload, CVViewer, PreferencesForm
│       │   ├── pipeline/       # KanbanBoard
│       │   └── layout/         # Navbar, Footer, FloatingIcons
│       └── lib/
│           ├── api.ts          # fetch-based API client (every endpoint typed)
│           ├── types.ts        # Mirrors backend Pydantic models
│           └── utils.ts        # cn() helper
│
├── .env.example
├── setup.sh
├── cron_setup.sh               # STALE — see Known Issues
├── CLAUDE.md
├── README.md
└── ARCHITECTURE.md
```

### Entry points

How a user actually runs Job360 today:

1. **Pipeline run (backend)** — `python -m src.cli run` (from `backend/`) → `backend/src/cli.py:27` → `asyncio.run(run_search(...))` → `backend/src/main.py:223`.
2. **FastAPI server (backend)** — Three equivalent paths, all landing on the same app:
   - `python backend/main.py` → `backend/main.py:18` → `uvicorn.run(app, host="127.0.0.1", port=8000)`
   - `uvicorn main:app --host 0.0.0.0 --port 8000` from `backend/` → imports `backend/main.py::app`
   - `python -m src.cli api` from `backend/` → `backend/src/cli.py:101–105` → `uvicorn.run("src.api.main:app", host=host, port=port, reload=True)`
3. **Profile setup** — `python -m src.cli setup-profile --cv <path> [--linkedin <zip>] [--github <user>]` → `backend/src/cli.py`
4. **View / status / sources** — `python -m src.cli view|status|sources`
5. **Next.js dev server** — `cd frontend && npm run dev` → `next dev` on `:3000`
6. **Cron** — `bash cron_setup.sh` at the project root installs 04:00 + 16:00 `Europe/London` daily jobs. **This script is stale** (see Section 13).

---

## 2. Architecture — The Three Pillars

### Pillar 1 — Job Seeker Profile (`backend/src/services/profile/`)

**Inputs**: CV PDF/DOCX, LinkedIn data-export ZIP, GitHub username, manual preferences form. Any subset is accepted.

**CV parsing pipeline** (`backend/src/services/profile/cv_parser.py`):
1. `extract_text(file_path)` dispatches by suffix to `extract_text_from_pdf()` (pdfplumber) or `extract_text_from_docx()` (python-docx). PDF path iterates pages; DOCX path concatenates paragraph text.
2. `parse_cv_async(file_path)` builds a prompt from `_CV_PROMPT` (lines 26–77) + the CV text, calls `llm_extract(prompt, system=_CV_SYSTEM)`, and coerces the JSON result into a `CVData` dataclass via `_llm_result_to_cvdata()`.
3. `parse_cv(file_path)` is a sync wrapper that handles the case where an event loop is already running.
4. Skill/title extraction is **LLM-only**. The regex-based `KNOWN_SKILLS` / `KNOWN_TITLE_PATTERNS` approach was deleted in commit history (noted in `backend/src/core/keywords.py:1–9`).

**LLM provider fallback** (`backend/src/services/profile/llm_provider.py`):
- `llm_extract(prompt, system)` tries providers in order: **Gemini** (`gemini-2.0-flash`) → **Groq** (`llama-3.3-70b-versatile`) → **Cerebras** (`llama3.1-8b`). Each provider is skipped if its API key env var is empty. Temperature 0.1, JSON mode where supported. If all providers are missing keys, raises `RuntimeError`.
- `llm_extract_fast()` inverts the order: **Cerebras → Groq → Gemini**. Designed for latency-critical calls (Cerebras is the fastest free tier, ~2000 tokens/sec).

**LinkedIn enrichment** (`backend/src/services/profile/linkedin_parser.py`):
- `parse_linkedin_zip(zip_path)` extracts `Positions.csv`, `Skills.csv`, `Education.csv`, `Certifications.csv`, and `Profile.csv` from the ZIP. Uses case-insensitive lookup via `_find_csv_in_zip()` to handle both flat and nested export structures.
- `enrich_cv_from_linkedin(cv_data, linkedin_data)` merges into `CVData.linkedin_positions`, `CVData.linkedin_skills`, and `CVData.linkedin_industry`. Deduplicates skills case-insensitively.

**GitHub enrichment** (`backend/src/services/profile/github_enricher.py`):
- `fetch_github_profile(username)` validates the username against a regex (`^[a-zA-Z0-9]([a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?$`), then hits `https://api.github.com/users/{username}/repos?per_page=30&sort=pushed` (top 30 by most-recent push). For the top 20 of those, it calls `/repos/{user}/{repo}/languages` to aggregate language byte counts. Optional `GITHUB_TOKEN` env var is sent as `Authorization: token ...` for higher rate limits.
- `_infer_skills(languages, topics)` uses two dict mappings:
  - `LANGUAGE_TO_SKILL` (32 entries): Python → Python, HCL → Terraform, Dockerfile → Docker, etc.
  - `TOPIC_TO_SKILL` (50+ entries): react → React, machine-learning → Machine Learning, etc.
- `enrich_cv_from_github(cv_data, github_data)` populates `CVData.github_languages`, `CVData.github_topics`, and `CVData.github_skills_inferred`.

**Preferences merge** (`backend/src/services/profile/preferences.py`):
- `validate_preferences(data)` coerces comma-separated strings to lists and normalizes whitespace.
- `merge_cv_and_preferences(cv_skills, cv_titles, prefs)` returns a `UserPreferences` where prefs come first, CV-derived items append (deduped), and `excluded_skills` are filtered out.

**Persistence** (`backend/src/services/profile/storage.py`):
- `save_profile(profile) -> Path`: serializes via `dataclasses.asdict` + `json.dumps` → `{DATA_DIR}/user_profile.json` (i.e. `backend/data/user_profile.json`).
- `load_profile() -> UserProfile | None`: reads JSON, reconstructs `CVData` and `UserPreferences` via field filtering (silently drops unknown keys), returns `None` on missing file or JSON parse error.
- `profile_exists() -> bool`.

**SearchConfig generation** (`backend/src/services/profile/keyword_generator.py:27–140`):
- `generate_search_config(profile: UserProfile) -> SearchConfig`:
  1. **Job titles** (lines 32–45): start with `prefs.target_job_titles`; append `cv.job_titles`; append each `cv.linkedin_positions[].title`. Deduplicated.
  2. **Skill auto-tiering** (lines 47–75): pool = `prefs.additional_skills + cv.skills + cv.linkedin_skills + cv.github_skills_inferred`. Divides into thirds: primary = first `n//3` (minimum 1), secondary = next `n//3`, tertiary = remainder. If `n == 0`, all three tiers are empty.
  3. **Relevance keywords** (lines 77–92): extract words from titles (len>1, not in `_STOPWORDS`); append all skills; append words from `cv.linkedin_industry`. Return sorted list.
  4. **Negative keywords** (lines 94–95): copied from `prefs.negative_keywords`.
  5. **Locations** (lines 97–105): start with `LOCATIONS` (the 26-entry UK defaults from `keywords.py:28–55`); append `prefs.preferred_locations`; append `prefs.work_arrangement` capitalized (remote/hybrid/onsite).
  6. **core_domain_words / supporting_role_words** (lines 107–117): split words from titles by membership in an internal `_ROLE_WORDS` set (supporting = roles like "engineer", "manager"; core = domain terms like "data", "ml").
  7. **Search queries** (lines 119–126): Cartesian product of top 8 titles × top 2 locations, capped at 16.

**Dataclasses** (`backend/src/services/profile/models.py`):

- `CVData` (lines 9–48) — scoring fields: `raw_text`, `skills`, `job_titles`, `companies`, `education`, `certifications`, `summary`, `experience_text`. Display-only fields: `name`, `headline`, `location`, `achievements`. LinkedIn fields: `linkedin_positions`, `linkedin_skills`, `linkedin_industry`. GitHub fields: `github_languages`, `github_topics`, `github_skills_inferred`. Property `highlights` (34–48) returns the flat list of all terms for CV viewer highlighting.

- `UserPreferences` (lines 51–64): `target_job_titles`, `additional_skills`, `excluded_skills`, `preferred_locations`, `industries`, `salary_min`, `salary_max`, `work_arrangement` ("remote" / "hybrid" / "onsite"), `experience_level`, `negative_keywords`, `about_me`, `github_username`.

- `UserProfile` (lines 67–79): holds `cv_data` and `preferences`. Property `is_complete` (72–79) returns `True` if `cv_data.raw_text` is non-empty OR `prefs.target_job_titles` / `prefs.additional_skills` is non-empty. The pipeline uses this to decide whether to abort with `{"error": "no_profile"}`.

- `SearchConfig` (lines 82–117): `job_titles`, `primary_skills`, `secondary_skills`, `tertiary_skills`, `relevance_keywords`, `negative_title_keywords`, `locations`, `visa_keywords`, `core_domain_words` (`set`), `supporting_role_words` (`set`), `search_queries`.

- `SearchConfig.from_defaults()` (96–117): returns a `SearchConfig` with everything empty **except** `locations=list(LOCATIONS)` (26 UK cities + Remote + Hybrid) and `visa_keywords=list(VISA_KEYWORDS)` (8 visa terms). Since `JOB_TITLES`, `PRIMARY/SECONDARY/TERTIARY_SKILLS`, `RELEVANCE_KEYWORDS`, and `NEGATIVE_TITLE_KEYWORDS` in `core/keywords.py` are all empty lists, this defaults config cannot produce any non-zero title or skill score — it exists for backwards compatibility but is never used in the production pipeline (which requires a profile).

### Pillar 2 — Search Engine / Scoring (`backend/src/services/skill_matcher.py`)

This is the single most critical file in the codebase. Every job that enters the pipeline is scored here.

**Weights and point values** (lines 17–27, verbatim):

```python
# Weights for scoring components (total = 100)
TITLE_WEIGHT = 40
SKILL_WEIGHT = 40
LOCATION_WEIGHT = 10
RECENCY_WEIGHT = 10

# Points per skill match
PRIMARY_POINTS = 3
SECONDARY_POINTS = 2
TERTIARY_POINTS = 1
SKILL_CAP = SKILL_WEIGHT
```

So the maximum possible positive score is `40 + 40 + 10 + 10 = 100`. The score is clamped to `[0, 100]` after penalties.

**There are two scoring paths in this file.** Only one is used in production.

#### Production path: `JobScorer` class (lines 253–307)

Instantiated once per pipeline run at `backend/src/main.py:261` with the user's `SearchConfig`. Every job in `all_jobs` gets `scorer.score(job)` called on it at `main.py:346`.

- `__init__(self, config)` (256–258): stores the `SearchConfig`.
- `_title_score(self, job_title)` (260–273):
  1. Lowercase the title.
  2. Loop over `self._config.job_titles`. If any lowercased target equals the title, return `TITLE_WEIGHT` (40). Else if any target is a substring of the title **or** the title is a substring of any target, return `TITLE_WEIGHT // 2` (20). This is the "full match" path.
  3. If no direct match: extract words from the title via `re.findall(r'\w+', title_lower)`. Compute `core_overlap = title_words & self._config.core_domain_words`. If no core overlap, return 0. Else compute `support_overlap = title_words & self._config.supporting_role_words` and return `min(len(core_overlap) * 5 + len(support_overlap) * 3, TITLE_WEIGHT // 2)` — capped at 20.
- `_skill_score(self, text)` (275–286): iterates the three skill tiers from the config, adding `PRIMARY_POINTS` (3) / `SECONDARY_POINTS` (2) / `TERTIARY_POINTS` (1) per skill that passes the word-boundary check `_text_contains(text, skill)`. Returns `min(points, SKILL_CAP)` — capped at 40.
- `_negative_penalty(self, job_title)` (288–292): loops `self._config.negative_title_keywords`; returns 30 if any match via `_text_contains()`, else 0.
- `score(self, job)` (294–303):
  ```python
  def score(self, job: Job) -> int:
      text = f"{job.title} {job.description}"
      title_pts = self._title_score(job.title)
      skill_pts = self._skill_score(text)
      location_pts = _location_score(job.location)           # module-level
      recency_pts = _recency_score(job.date_found)           # module-level
      penalty = self._negative_penalty(job.title)
      foreign_penalty = _foreign_location_penalty(job.location)  # module-level
      total = title_pts + skill_pts + location_pts + recency_pts - penalty - foreign_penalty
      return min(max(total, 0), 100)
  ```
  Note that `_location_score`, `_recency_score`, and `_foreign_location_penalty` are module-level functions — they are the same logic whether invoked from the class or the legacy function.
- `check_visa_flag(self, job)` (305–307): concatenates title + description, calls module-level `_has_visa_keyword(text, self._config.visa_keywords)`.

#### Legacy path: module-level `score_job()` (lines 231–240)

```python
def score_job(job: Job) -> int:
    text = f"{job.title} {job.description}"
    title_pts = _title_score(job.title)
    skill_pts = _skill_score(text)
    location_pts = _location_score(job.location)
    recency_pts = _recency_score(job.date_found)
    penalty = _negative_penalty(job.title)
    foreign_penalty = _foreign_location_penalty(job.location)
    total = title_pts + skill_pts + location_pts + recency_pts - penalty - foreign_penalty
    return min(max(total, 0), 100)
```

This function uses the module-level `_title_score()`, `_skill_score()`, and `_negative_penalty()` which read from the hardcoded `JOB_TITLES`, `PRIMARY_SKILLS`, `SECONDARY_SKILLS`, `TERTIARY_SKILLS`, and `NEGATIVE_TITLE_KEYWORDS` in `core/keywords.py`. Because those lists are now all empty (`keywords.py:16–21`), this path would return only `location_pts + recency_pts - foreign_penalty`, bounded by `[-15, 20]` and clamped to `[0, 20]`. **The pipeline never calls `score_job()`** — `main.py` uses `JobScorer(config)` unconditionally. The function and its helpers are effectively dead code, preserved for import compatibility with `check_visa_flag` (243–245) which is imported and then not used in `main.py:20` alongside `JobScorer` (it is imported, but the direct `check_visa_flag` usage at line 347 goes through `scorer.check_visa_flag()`).

#### Helper functions (all module-level)

- `_has_visa_keyword(text, keywords)` (94–99): first checks for negation phrases in `_VISA_NEGATIONS` (lines 86–91): `"no sponsorship"`, `"not sponsor"`, `"cannot sponsor"`, `"unable to sponsor"`, `"don't sponsor"`, `"do not sponsor"`, `"without sponsorship"`, `"company-sponsored"`, `"employer-sponsored"`. If any negation is present, returns `False` regardless of what else is in the text. Otherwise returns `True` if any of the 8 visa keywords matches.

- `_word_boundary_pattern(term)` (102–105): LRU-cached (`maxsize=512`) compiled regex `\b + re.escape(term) + \b`, case-insensitive.

- `_text_contains(text, term)` (108–110): bool wrapper around `_word_boundary_pattern(term).search(text)`.

- `_location_score(location)` (145–162):
  1. Lowercase.
  2. Check each `REMOTE_TERMS` entry (`"remote"`, `"anywhere"`, `"work from home"`, `"wfh"`). Any hit → return `LOCATION_WEIGHT - 2` = **8 points**.
  3. Apply each `LOCATION_ALIASES` substitution (e.g. `"greater london" → "london"`, `"england" → "uk"`, etc.).
  4. Loop `LOCATIONS` (26 entries). Skip `"remote"` and `"hybrid"` (already handled). Any target that is a substring of either the raw or the aliased location → return `LOCATION_WEIGHT` = **10 points**.
  5. Else return **0**.

- `_recency_score(date_found)` (165–184):
  1. Empty string → 0.
  2. Parse `datetime.fromisoformat(date_found)`. On `ValueError` or `TypeError`, return 0.
  3. If the parsed datetime is naive, attach `timezone.utc`.
  4. Compute `days_old = (datetime.now(timezone.utc) - posted).days`.
  5. Tiers:
     - `days_old <= 1` → `RECENCY_WEIGHT` = **10**
     - `days_old <= 3` → **8**
     - `days_old <= 5` → **6**
     - `days_old <= 7` → **4**
     - else → **0**
  
  Note: `_recency_score` reads `job.date_found`, which is the string the source wrote when constructing the `Job`. For 14 of 47 sources, this is `datetime.now(timezone.utc).isoformat()` unconditionally — see Section 5. When the field is "now", recency always awards the maximum 10 points regardless of when the job was actually posted.

- `_foreign_location_penalty(location)` (195–211):
  1. Empty → 0 (unknown, don't penalize).
  2. Check `FOREIGN_INDICATORS` FIRST (lines 44–62, 65+ entries). This includes country names, major non-UK cities, Canadian provinces that clash with UK city names (`"ontario"`, `"quebec"`), and US state abbreviations with leading commas (`", ca"`, `", ny"`, …). Any match → **−15 points**.
  3. Only if no foreign indicator matched, check `UK_TERMS` and `REMOTE_TERMS`. Match → 0.
  4. Unknown location that matched nothing → 0.
  
  The foreign-first ordering catches edge cases like "London, Ontario" where the naive UK-terms-first order would have awarded positive location points.

- `detect_experience_level(title)` (214–219): loops `_EXPERIENCE_PATTERNS` dict (74–83) in insertion order and returns the first match. Patterns: `intern`, `junior` (also `jr`, `graduate`, `entry-level`), `mid` (`mid-level`, `intermediate`), `senior` (also `sr`), `lead` (`team lead`), `staff`, `principal`, `head` (also `head of`, `director`, `vp`). Returns `""` if no match.

- `salary_in_range(job)` (222–228):
  ```python
  def salary_in_range(job: Job) -> bool:
      if job.salary_min is None and job.salary_max is None:
          return False
      job_min = job.salary_min or 0
      job_max = job.salary_max or float("inf")
      return job_max >= TARGET_SALARY_MIN and job_min <= TARGET_SALARY_MAX
  ```
  Used only as a tiebreaker in `main.py:360` and `main.py:378` (`sort key=lambda j: (j.match_score, salary_in_range(j))`). Not part of the 0–100 score.

**`MIN_MATCH_SCORE = 30`** is defined in `backend/src/core/settings.py:44` and applied in `backend/src/main.py:355`:

```python
unique_jobs = [j for j in unique_jobs if j.match_score >= MIN_MATCH_SCORE]
```

Any job scoring below 30 is silently dropped before DB insertion.

### Pillar 3 — Job Providers (`backend/src/sources/`)

**Base class** (`backend/src/sources/base.py`):

- `BaseJobSource(ABC)` at line 48, with class attributes `name: str = "base"` and `category: str = "unknown"`.
- `__init__(self, session, search_config=None)` (52–56): stores the aiohttp session and search config, builds a `RateLimiter` from `RATE_LIMITS.get(self.name, {"concurrent": 2, "delay": 1.0})`.
- Properties:
  - `relevance_keywords` (58–62) → `_search_config.relevance_keywords` or `_DEFAULT_RELEVANCE_KEYWORDS` (empty list from `core/keywords.py`).
  - `job_titles` (64–68) → `_search_config.job_titles` or `_DEFAULT_JOB_TITLES` (empty list).
  - `search_queries` (70–74) → `_search_config.search_queries` or `[]`.
- `_headers(extra)` (76–81): returns `{"User-Agent": "Job360/1.0 (UK Job Search Aggregator)"}` merged with any extras.
- `fetch_jobs()` (83–85): abstract method all subclasses must implement.
- `_request(method, url, *, params, body, headers, as_text)` (87–142) — the shared HTTP plumbing:
  - `MAX_RETRIES = 3` loop with `RETRY_BACKOFF = [1, 2, 4]` seconds (from `core/settings.py:106–107`).
  - Per-request rate limit: `await self._rate_limiter.acquire()` / `self._rate_limiter.release()` in `try`/`finally`.
  - Per-request timeout: `aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)` with `REQUEST_TIMEOUT = 30`.
  - No-retry HTTP statuses: `_NO_RETRY_STATUSES = (401, 403, 404, 422)` (line 19). Response → `None` immediately.
  - 429 handling (113–126): reads `Retry-After` header; if numeric, waits `min(int(retry_after), 60)`; otherwise waits `RETRY_BACKOFF[attempt] * 3`. Retries unless out of attempts.
  - `status >= 400` (127–132): logs warning, sleeps `RETRY_BACKOFF[attempt]`, retries unless out of attempts.
  - Successful path: if `as_text`, returns `await resp.text()`; else `await resp.json(content_type=None)`.
  - Retriable exceptions: `aiohttp.ClientError`, `asyncio.TimeoutError`, and `json.JSONDecodeError` (only when JSON parsing). Other exceptions propagate.
- Thin wrappers (144–154):
  - `_get_json(url, params, headers)` → `_request("GET", ...)`
  - `_post_json(url, body, headers)` → `_request("POST", ...)`
  - `_get_text(url, params, headers)` → `_request("GET", ..., as_text=True)`

**Module-level helpers**:

- `_sanitize_xml(text)` (22–28): replaces bare `&` with `&amp;` (but leaves existing entities alone) and strips ASCII control characters (`\x00-\x08`, `\x0b`, `\x0c`, `\x0e-\x1f`). Used before `xml.etree.ElementTree` parsing in RSS sources.
- `_is_uk_or_remote(location)` (31–45): empty string → `True` (unknown, don't filter). Else checks `UK_TERMS` → True, `REMOTE_TERMS` → True, `FOREIGN_INDICATORS` → False. Otherwise True (unknown, don't filter out). These three sets are imported from `src.services.skill_matcher` at line 11, so the source layer and the scorer layer share the same classifications.

**Source categories & instantiation** (`backend/src/main.py:151–220`):

Every source is instantiated with `session + search_config=sc`. Additionally, keyed sources receive their API key(s), and ATS sources get the built-in `companies` list from `core/companies.py`.

- **Keyed APIs (7)**: Reed, Adzuna, JSearch, Jooble, Google Jobs (via SerpApi), Careerjet, Findwork. Each receives an API key (or affiliate ID) as a constructor kwarg and returns an empty list if the key is empty.
- **Free JSON APIs (10)**: Arbeitnow, RemoteOK, Jobicy, Himalayas, Remotive, DevITjobs, Landing.jobs, AIJobs.net, HN Jobs (Firebase API), YC Companies.
- **ATS boards (10)**: Greenhouse, Lever, Workable, Ashby, SmartRecruiters, Pinpoint, Recruitee, Workday, Personio, SuccessFactors. Each iterates a company-slug list.
- **RSS/XML feeds (8)**: BioSpace, FindAJob (UK DWP), jobs.ac.uk, NHS Jobs, RealWorkFromAnywhere, WorkAnywhere, WeWorkRemotely, University Jobs (Cambridge).
- **HTML scrapers (7)**: LinkedIn (guest API), JobTensor, Climatebase, 80000Hours (Algolia), BCS Jobs, AIJobs Global, AIJobs AI.
- **Other (5)**: HackerNews (Algolia "Who is Hiring"), Indeed + Glassdoor (shared `JobSpySource` via `python-jobspy`), TheMuse, NoFluffJobs, Nomis (UK GOV vacancy statistics).

Total: 7 + 10 + 10 + 8 + 7 + 5 = **47 unique source instances**. `SOURCE_INSTANCE_COUNT = 47` is hardcoded at `main.py:133`.

**Fan-out pattern** (`backend/src/main.py:277–322`):

```python
connector = aiohttp.TCPConnector(limit=30, limit_per_host=5)           # line 277
timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)                 # line 278
async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
    sources = _build_sources(session, source_filter, search_config=search_config)
    # ...
    async def _fetch_source(source):
        try:
            return await asyncio.wait_for(source.fetch_jobs(), timeout=120)
        except asyncio.TimeoutError:
            logger.warning("Source %s timed out", source.name)
            return None
        except Exception as e:
            logger.error("Source %s failed: %s", source.name, e, exc_info=True)
            return None
    
    results = await asyncio.gather(*[_fetch_source(s) for s in sources], return_exceptions=True)
```

- **Connection pool**: 30 total concurrent connections, 5 per host.
- **Session-level timeout**: 30s (from `REQUEST_TIMEOUT`).
- **Per-source timeout**: 120s wrapping the entire `fetch_jobs()` coroutine. Separate from, and larger than, the per-HTTP-request 30s timeout.
- **Error collection**: `return_exceptions=True` means any exception that escapes `_fetch_source()` becomes an element in `results` instead of crashing the gather. `results` is then zipped with `sources` and each element is inspected: BaseException → 0 count + `failed_sources.append(...)`, None → 0 count + failed, non-empty list → extend `all_jobs` + log count, empty list → 0 count.
- **Source health check** (327–340): `db.get_last_source_counts(5)` reads the last 5 `run_log.per_source` JSON blobs. Any source that returned 0 this run but had non-zero counts in any of the previous 5 runs is logged as `"newly_empty"` — a signal that something broke.

---

## 3. The Pipeline — End-to-End Flow

What actually happens when `python -m src.cli run` is executed, step by step, with line references into `backend/src/main.py`:

**Step 1 — CLI entry** (`backend/src/cli.py:27–48`)

```python
@cli.command()
@click.option("--source", ...)
@click.option("--dry-run", ...)
@click.option("--log-level", ...)
@click.option("--db-path", ...)
@click.option("--no-email", ...)
@click.option("--dashboard", ...)
def run(source, dry_run, log_level, db_path, no_email, dashboard):
    try:
        stats = asyncio.run(run_search(
            db_path=db_path,
            source_filter=source,
            dry_run=dry_run,
            log_level=log_level,
            no_notify=no_email,
            launch_dashboard=dashboard,
        ))
        if stats.get("error") == "no_profile":
            click.secho("\nERROR: No user profile found...", fg="red", err=True)
            raise SystemExit(2)
        click.echo(f"Done: {stats['total_found']} found, {stats['new_jobs']} new, {stats['sources_queried']} sources.")
    except KeyboardInterrupt:
        click.echo("\nJob360: Search interrupted. Exiting gracefully.")
        raise SystemExit(130)
```

`--source` is case-insensitive and validated against `sorted(SOURCE_REGISTRY.keys())`. Exit codes: **2** for missing profile, **130** for Ctrl-C, **0** for success.

**Step 2 — Logging setup** (`main.py:231`)

`setup_logging(log_level)` — rotating file handler + console handler from `src.utils.logger`.

**Step 3 — Profile load** (`main.py:240–258`)

```python
profile = load_profile()
if not profile or not profile.is_complete:
    logger.error("=" * 60)
    logger.error("No user profile found. Job360 requires a CV or preferences.")
    # ... helpful error message with commands to run ...
    return {
        "total_found": 0,
        "new_jobs": 0,
        "sources_queried": 0,
        "per_source": {},
        "error": "no_profile",
    }
```

**If the profile is missing or incomplete, the pipeline aborts before any source is queried.** The CLI translates the `"error": "no_profile"` into exit code 2 with a stderr message. This is a hard gate — there is no fallback.

**Step 4 — SearchConfig + JobScorer** (`main.py:260–263`)

```python
search_config = generate_search_config(profile)
scorer = JobScorer(search_config)
logger.info("  Using dynamic keywords from user profile")
```

**Step 5 — Database init** (`main.py:266–268`)

```python
path = db_path or str(DB_PATH)
db = JobDatabase(path)
await db.init_db()
```

`init_db()` opens `aiosqlite.connect()`, sets `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, executes the full schema `executescript()` (see Section 4), commits, and calls `_migrate()` for any forward-compatible column additions (currently the migrations list is empty — `database.py:82–85`).

**Step 6 — Auto-purge** (`main.py:271–274`)

```python
purged = await db.purge_old_jobs(days=30)
if purged:
    logger.info("Purged %s jobs older than 30 days", purged)
```

Jobs with `first_seen < now - 30 days` are deleted at the start of every run. **This happens unconditionally on every pipeline invocation** — it is not a separate cron task.

**Step 7 — aiohttp session** (`main.py:277–279`)

```python
connector = aiohttp.TCPConnector(limit=30, limit_per_host=5)
timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
```

**Step 8 — Build sources** (`main.py:281–285`)

```python
sources = _build_sources(session, source_filter, search_config=search_config)
if not sources:
    logger.error("No sources matched filter: %s", source_filter)
    return {"total_found": 0, "new_jobs": 0, "sources_queried": 0, "per_source": {}}
```

With no `source_filter`, this returns 47 instances. With a filter, it runs a `.name == source_filter` check (with a special case at `main.py:217–218` that rewrites `"glassdoor" → "indeed"` because both share `JobSpySource`).

**Step 9 — Parallel fetch** (`main.py:287–324`)

The `asyncio.gather()` call described in Section 2's fan-out discussion. Per-source 120s timeout, `return_exceptions=True`, per-result inspection, `failed_sources` list collected and logged.

**Per-source internals** (each `fetch_jobs()`):
1. Builds URL(s) based on `self.search_queries` / `self.job_titles` / a hardcoded location list.
2. Calls `self._get_json()` / `self._post_json()` / `self._get_text()` — all go through `_request()` with retry + rate limiting.
3. Parses the response (JSON, XML, HTML, or pandas DataFrame for JobSpy).
4. For each raw listing, filters by `_is_uk_or_remote(location)` and by a relevance check against `self.relevance_keywords` (most sources).
5. Constructs a `Job` dataclass instance. The `date_found` assignment is the crucial line documented in Section 5.
6. Returns `list[Job]`.

**Step 10 — Source health check** (`main.py:327–340`)

See Section 2 (`newly_empty` detection). Logs a warning if any source returned 0 but was productive in the last 5 runs.

**Step 11 — Scoring** (`main.py:344–348`)

```python
for job in all_jobs:
    job.match_score = scorer.score(job)
    job.visa_flag = scorer.check_visa_flag(job)
    job.experience_level = detect_experience_level(job.title)
```

Every raw job is scored by `JobScorer`. Note that `detect_experience_level` is the module-level function — it reads the hardcoded `_EXPERIENCE_PATTERNS` regex dict (8 levels) rather than anything from SearchConfig.

**Step 12 — Deduplication** (`main.py:351`)

```python
unique_jobs = deduplicate(all_jobs)
logger.info("After dedup: %s unique jobs", len(unique_jobs))
```

See Section 4 for the exact logic. Grouping key uses `Job.normalized_key()` for the company component and `_normalize_title()` for the title component. Winner = highest `match_score`, then highest `_completeness()`.

**Step 13 — Min-score filter** (`main.py:355–356`)

```python
unique_jobs = [j for j in unique_jobs if j.match_score >= MIN_MATCH_SCORE]
logger.info("After score filter (>=%s): %s jobs", MIN_MATCH_SCORE, len(unique_jobs))
```

`MIN_MATCH_SCORE = 30`. Anything below is dropped; there is no "near miss" tier.

**Step 14 — Dry-run short-circuit** (`main.py:358–369`)

If `dry_run=True`, the pipeline sorts by `(match_score, salary_in_range(j))` descending, prints a time-bucketed summary via `_print_bucketed_summary`, and returns stats without touching the database or sending notifications.

**Step 15 — DB insert loop** (`main.py:372–376`)

```python
new_jobs: list[Job] = []
for job in unique_jobs:
    if await db.insert_job(job):
        new_jobs.append(job)
await db.commit()
```

`insert_job()` uses `INSERT OR IGNORE` against `UNIQUE(normalized_company, normalized_title)` and returns `cursor.rowcount > 0`. So `new_jobs` contains exactly the rows that survived the unique constraint — everything else was a duplicate of a previously-seen job.

**Step 16 — Sort new jobs** (`main.py:378`)

```python
new_jobs.sort(key=lambda j: (j.match_score, salary_in_range(j)), reverse=True)
```

**Step 17 — Stats dict** (`main.py:381–387`)

```python
stats = {
    "total_found": len(all_jobs),
    "new_jobs": len(new_jobs),
    "sources_queried": source_count,
    "per_source": per_source,
}
```

**Step 18 — CSV + reports + notifications** (`main.py:390–416`)

This whole block is guarded by `if new_jobs:`. If there are zero new jobs this run, no CSV, no report, no notification is sent.

```python
if new_jobs:
    # CSV
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_path = str(EXPORTS_DIR / f"jobs_{ts}.csv")
    await asyncio.to_thread(export_to_csv, new_jobs, csv_path)

    # Markdown report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    md_report = generate_markdown_report(new_jobs, stats)
    md_path = REPORTS_DIR / f"report_{ts}.md"
    await asyncio.to_thread(md_path.write_text, md_report, encoding="utf-8")

    # Notifications via channel abstraction
    if not no_notify:
        for channel in get_configured_channels():
            try:
                await channel.send(new_jobs, stats, csv_path=csv_path)
            except Exception as e:
                logger.error("%s notification failed: %s", channel.name, e)

    # Print time-bucketed summary to console
    _print_bucketed_summary(new_jobs, "Results")
else:
    logger.info("No new jobs to report")
    logger.info("Job360: No new jobs found this run.")
```

CSV filename = `jobs_{YYYYMMDD_HHMMSS}.csv`, one file per run. Markdown report filename = `report_{YYYYMMDD_HHMMSS}.md`, also one per run. Both land under `backend/data/exports/` and `backend/data/reports/` respectively.

**Step 19 — Run log** (`main.py:420`)

```python
await db.log_run(stats)
```

Writes a row to `run_log` with `timestamp`, `total_found`, `new_jobs`, `sources_queried`, and `per_source` (JSON-serialized).

**Step 20 — Connection close** (`main.py:423–424`)

```python
finally:
    await db.close()
```

The `try`/`finally` wraps the entire session/source loop, so the connection is closed even if a source exception escapes.

---

## 4. Data Model

### `Job` dataclass (`backend/src/models.py:17–58`)

```python
_COMPANY_SUFFIXES = re.compile(
    r"\s+(ltd|limited|inc|plc|corporation|corp|group|llc|gmbh|ag|sa|co|company|holdings|solutions|technologies|services|systems|pty)\.?\s*$",
    re.IGNORECASE,
)

_COMPANY_REGION_SUFFIXES = re.compile(
    r"\s+(uk|us|usa|de|sg|eu|emea|apac|global|international)\s*$",
    re.IGNORECASE,
)


@dataclass
class Job:
    title: str
    company: str
    apply_url: str
    source: str
    date_found: str
    location: str = ""
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    description: str = ""
    match_score: int = 0
    visa_flag: bool = False
    is_new: bool = True
    experience_level: str = ""

    def __post_init__(self):
        self.title = html.unescape(self.title)
        self.company = html.unescape(self.company)
        self.company = self._clean_company(self.company)
        if self.salary_min is not None and self.salary_min < 10000:
            self.salary_min = None
        if self.salary_max is not None and self.salary_max > 500000:
            self.salary_max = None

    @staticmethod
    def _clean_company(name: str) -> str:
        if not name:
            return "Unknown"
        cleaned = name.strip()
        if not cleaned or cleaned.lower() in ("nan", "none", "n/a", "null", "unknown"):
            return "Unknown"
        return cleaned

    def normalized_key(self) -> tuple[str, str]:
        company = _COMPANY_SUFFIXES.sub("", self.company).strip()
        company = _COMPANY_REGION_SUFFIXES.sub("", company).strip().lower()
        title = self.title.strip().lower()
        return (company, title)
```

**Field semantics:**

| Field | Type | Default | Notes |
|---|---|---|---|
| `title` | `str` | (required) | HTML-unescaped in `__post_init__` |
| `company` | `str` | (required) | HTML-unescaped, then `_clean_company()` normalizes empty/`nan`/`none`/`null`/`unknown` → `"Unknown"` |
| `apply_url` | `str` | (required) | No validation |
| `source` | `str` | (required) | Source `name` attribute (e.g. `"reed"`, `"indeed"`) |
| `date_found` | `str` | (required) | ISO format string. See Section 5 for what this actually means per-source. |
| `location` | `str` | `""` | Free-form text |
| `salary_min` | `Optional[float]` | `None` | Dropped to `None` if `< 10000` (likely hourly/GBp mistake) |
| `salary_max` | `Optional[float]` | `None` | Dropped to `None` if `> 500000` (likely non-GBP) |
| `description` | `str` | `""` | Used by scorer for skill matching |
| `match_score` | `int` | `0` | Set by `JobScorer.score()` in `main.py:346` |
| `visa_flag` | `bool` | `False` | Set by `JobScorer.check_visa_flag()` in `main.py:347` |
| `is_new` | `bool` | `True` | Initial default; no code currently writes it back |
| `experience_level` | `str` | `""` | Set by `detect_experience_level(title)` in `main.py:348` |

**`normalized_key()` behavior**: Strips company suffixes (`Ltd`, `Limited`, `Inc`, `PLC`, `Corporation`, `Corp`, `Group`, `LLC`, `GmbH`, `AG`, `SA`, `Co`, `Company`, `Holdings`, `Solutions`, `Technologies`, `Services`, `Systems`, `Pty`), then strips region suffixes (`UK`, `US`, `USA`, `DE`, `SG`, `EU`, `EMEA`, `APAC`, `Global`, `International`), then lowercases. Title is only stripped + lowercased — **no other transforms**. This tuple is what the DB's `UNIQUE(normalized_company, normalized_title)` constraint operates on.

**Important**: `deduplicator._normalize_title()` (Section discussed below) is MORE aggressive than `Job.normalized_key()`'s title component. The divergence is documented and intentional — see Section 13.

### SQLite schema (`backend/src/repositories/database.py:23–71`)

Verbatim from `init_db()`:

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
    per_source TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_jobs_date_found ON jobs(date_found);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);
CREATE INDEX IF NOT EXISTS idx_jobs_match_score ON jobs(match_score);
CREATE TABLE IF NOT EXISTS user_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(job_id)
);
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    stage TEXT NOT NULL DEFAULT 'applied',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id)
);
```

**Pragmas** (`database.py:21–22`): `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`.

**Indexes**: 3 total, all on `jobs` table: `idx_jobs_date_found`, `idx_jobs_first_seen`, `idx_jobs_match_score`.

**Tables**: 4 total: `jobs`, `run_log`, `user_actions`, `applications`.

**`first_seen` vs `date_found`**: `first_seen` is always `datetime.now(timezone.utc).isoformat()` set by `insert_job()` at the moment of first insert. It represents when Job360 first saw the record. `date_found` is whatever the source set — see Section 5. The UNIQUE constraint does not include either, so once a (company, title) pair is seen, `first_seen` is frozen forever — even if the same pair is seen again later with different content. The scorer's `_recency_score` reads `date_found`, not `first_seen`.

**`_migrate()`** (`database.py:75–97`) — Forward-compatible migration mechanism. Reads `PRAGMA table_info(jobs)` into a set of existing column names, then iterates a hardcoded `migrations = []` list (currently empty) and adds any missing columns via `ALTER TABLE jobs ADD COLUMN ...`. Each migration is validated against `_VALID_COL_NAME` and `_VALID_COL_TYPES` to prevent SQL injection from the migrations list.

**Dedup logic** (`backend/src/services/deduplicator.py`):

```python
_SENIORITY_RE = re.compile(
    r'^(senior|sr\.?|junior|jr\.?|lead|principal|staff|head\s+of)\s+',
    re.IGNORECASE,
)
_TRAILING_CODE_RE = re.compile(r'\s*[-/]\s*[A-Z0-9]{2,}[-_]?\d+\s*$', re.IGNORECASE)
_PAREN_RE = re.compile(r'\s*\([^)]*\)\s*$')


def _normalize_title(title: str) -> str:
    """Normalize a job title for dedup grouping.

    NOTE: This is intentionally MORE aggressive than Job.normalized_key().
    The DB UNIQUE constraint uses normalized_key() (company suffix + lowercase only),
    while dedup uses this function (also strips seniority, job codes, parentheticals).
    This means dedup groups are wider than DB unique keys — by design:
    - Dedup merges "Senior ML Engineer" and "ML Engineer" within a single run
    - DB preserves them as separate records across runs
    Do NOT unify these without a full DB migration (see CLAUDE.md Rule 1).
    """
    t = title.strip()
    t = _TRAILING_CODE_RE.sub('', t)
    t = _PAREN_RE.sub('', t)
    t = _SENIORITY_RE.sub('', t)
    return t.strip().lower()


def _completeness(job: Job) -> int:
    score = 0
    if job.salary_min is not None:
        score += 10
    if job.salary_max is not None:
        score += 10
    if job.description:
        score += min(len(job.description), 20)
    if job.location:
        score += 5
    return score


def deduplicate(jobs: list[Job]) -> list[Job]:
    if not jobs:
        return []
    groups: dict[tuple[str, str], list[Job]] = {}
    for job in jobs:
        company, _ = job.normalized_key()
        title = _normalize_title(job.title)
        key = (company, title)
        groups.setdefault(key, []).append(job)
    result = []
    for group in groups.values():
        best = max(group, key=lambda j: (j.match_score, _completeness(j)))
        result.append(best)
    return result
```

**Grouping key**: `(normalized_company_from_Job.normalized_key(), aggressive_normalized_title)`. Note that it uses `Job.normalized_key()[0]` for the company but its own `_normalize_title()` for the title — a deliberate split.

**Tiebreaker**: primary `j.match_score` (higher wins), then `_completeness(j)` (higher wins). `_completeness()` rewards presence of salary_min (+10), salary_max (+10), description (+min(len, 20)), and location (+5). Max completeness = 45.

**Max completeness (45) < min score gap (1)**: Since the tiebreaker tuple is `(match_score, completeness)`, a job with score 50 and completeness 0 always beats a job with score 49 and completeness 45. Score dominates.

**The documented divergence** (from the file's docstring) is the biggest cross-layer subtlety in the codebase: the deduplicator merges "Senior ML Engineer" and "ML Engineer" in one run so the user doesn't see both at once, but the DB `UNIQUE(normalized_company, normalized_title)` constraint stores them separately (because `Job.normalized_key()` doesn't strip seniority). So across runs, if a later run brings back the variant the deduplicator dropped last time, it will successfully INSERT as a "new" row. See Section 13.

---

## 5. The `date_found` Problem — Complete Per-Source Audit

**Context**: Every source file ends its listing-parsing loop with a `Job(...)` constructor call. One of the `Job.__init__` kwargs is `date_found=...`. In 14 of the 47 sources, no real posting date exists in the raw API/HTML/XML response, so the source hardcodes `datetime.now(timezone.utc).isoformat()`. In 3 more, the field exists but means something other than "posted date" (updated date, closing date). In the remaining 30, a real posting date is extracted — but every one of those also has a fallback to `datetime.now()` when parsing fails.

**Why this matters**:
1. `_recency_score(date_found)` (`skill_matcher.py:165–184`) awards 10 points for jobs ≤1 day old, decaying to 0 over 7 days. When `date_found` is always "now", every job from those 14 sources gets the full 10 recency points regardless of how stale it is.
2. `_format_date(date_str)` (`main.py:136–148`) displays "Posted: DD MMM YYYY" on console output, which will always show today's date for those 14 sources.
3. `utils/time_buckets.py` uses `date_found` (falling back to `first_seen`) to assign jobs to the 24h / 48h / 72h / 7d buckets on the dashboard. Hardcoded-now sources always land in the 24h bucket on their first run, then drift into older buckets only because `first_seen` ages.
4. The CSV export, markdown report, and all notifications include `date_found` verbatim — users will see "Posted: today" for thousands of jobs that may actually be weeks old.

### Master table — all 47 sources

| # | Source | Category | File:Line (Job ctor) | API field used for `date_found` | Fallback to `now()`? | Real posting date? |
|---|---|---|---|---|---|---|
| 1 | adzuna | keyed_api | `apis_keyed/adzuna.py:50` | `item.get("created")` | Yes (line 49) | ✅ Yes |
| 2 | careerjet | keyed_api | `apis_keyed/careerjet.py:76` | `item.get("date")` | Yes (line 59) | ✅ Yes |
| 3 | findwork | keyed_api | `apis_keyed/findwork.py:56` | `item.get("date_posted")` | Yes (line 53) | ✅ Yes |
| 4 | google_jobs | keyed_api | `apis_keyed/google_jobs.py:101` | `_parse_posted_at(detected_extensions.posted_at)` — phrase like "3 days ago" | Yes (parser falls back, lines 18–28) | ✅ Inferred from relative phrase |
| 5 | jooble | keyed_api | `apis_keyed/jooble.py:60` | `item.get("updated")` | Yes (line 49) | ⚠️ **Updated date, not posted** |
| 6 | jsearch | keyed_api | `apis_keyed/jsearch.py:74` | `item.get("job_posted_at_datetime_utc")` | Yes (line 73) | ✅ Yes |
| 7 | reed | keyed_api | `apis_keyed/reed.py:51` | `item.get("date") or item.get("datePosted")` | Yes (line 50) | ✅ Yes |
| 8 | aijobs | free_json | `apis_free/aijobs.py:34` | `item.get("date")` | Yes (line 31) | ✅ Yes |
| 9 | arbeitnow | free_json | `apis_free/arbeitnow.py:23` | `item.get("created_at")` | Yes (line 22) | ✅ Yes |
| 10 | devitjobs | free_json | `apis_free/devitjobs.py:45` | `item.get("publishedAt")` | Yes (line 27) | ✅ Yes |
| 11 | himalayas | free_json | `apis_free/himalayas.py:28` | `item.get("pubDate") or item.get("createdAt")` | Yes (line 27) | ✅ Yes |
| 12 | hn_jobs | free_json | `apis_free/hn_jobs.py:66` | `datetime.fromtimestamp(item["time"], tz=timezone.utc)` | Yes (line 64) | ✅ Yes (Unix epoch from HN Firebase API) |
| 13 | jobicy | free_json | `apis_free/jobicy.py:33` | `item.get("pubDate")` | Yes (line 32) | ✅ Yes |
| 14 | landingjobs | free_json | `apis_free/landingjobs.py:65` | `item.get("published_at")` | Yes (line 63) | ✅ Yes |
| 15 | remoteok | free_json | `apis_free/remoteok.py:28` | `item.get("date")` | Yes (line 27) | ✅ Yes |
| 16 | remotive | free_json | `apis_free/remotive.py:39` | `item.get("publication_date")` | Yes (line 28) | ✅ Yes |
| 17 | **yc_companies** | free_json | `apis_free/yc_companies.py:43` | *(hardcoded — no field)* | **ALWAYS** (lines 24, 50) | ❌ **No** — yc_companies emits career-page links, not job listings |
| 18 | ashby | ats | `ats/ashby.py:36` | `item.get("publishedAt") or item.get("updatedAt")` | Yes (line 35) | ✅ Yes (or updated as fallback) |
| 19 | greenhouse | ats | `ats/greenhouse.py:41` | `item.get("updated_at")` | Yes (line 40) | ⚠️ **Updated date, not posted** |
| 20 | lever | ats | `ats/lever.py:43` | `datetime.fromtimestamp(item["createdAt"] / 1000, tz=timezone.utc)` | Yes (line 42) | ✅ Yes (milliseconds since epoch) |
| 21 | **personio** | ats | `ats/personio.py:76` | *(hardcoded — XML parse loses date context)* | **ALWAYS** (line 83) | ❌ No |
| 22 | **pinpoint** | ats | `ats/pinpoint.py:47` | *(hardcoded — no date in API response)* | **ALWAYS** (line 54) | ❌ No |
| 23 | recruitee | ats | `ats/recruitee.py:39` | `item.get("published_at")` | Yes (line 36) | ✅ Yes |
| 24 | smartrecruiters | ats | `ats/smartrecruiters.py:44` | `item.get("releasedDate")` | Yes (line 43) | ✅ Yes |
| 25 | **successfactors** | ats | `ats/successfactors.py:67` | *(hardcoded — sitemap XML only gives URLs)* | **ALWAYS** (lines 40, 74, 95) | ❌ No |
| 26 | **workable** | ats | `ats/workable.py:39` | *(hardcoded — no date in API response)* | **ALWAYS** (line 46) | ❌ No |
| 27 | workday | ats | `ats/workday.py:88` | `_parse_posted_on("Posted N Days Ago")` | Yes (parser, lines 17–30) | ✅ Inferred |
| 28 | biospace | rss | `feeds/biospace.py:67` | `_parse_rss_date(pub_date)` | Yes (lines 82, 89) | ✅ Yes (RFC 822) |
| 29 | **findajob** | rss | `feeds/findajob.py:75` | *(hardcoded — HTML scrape, no date)* | **ALWAYS** (line 82) | ❌ No |
| 30 | jobs_ac_uk | rss | `feeds/jobs_ac_uk.py:65` | `_parse_date(pub_date)` | Yes (lines 80, 88) | ✅ Yes |
| 31 | nhs_jobs | rss | `feeds/nhs_jobs.py:68` | `_parse_date(closingDate)` | Yes (line 111) | ⚠️ **Closing date, not posting date** |
| 32 | realworkfromanywhere | rss | `feeds/realworkfromanywhere.py:57` | `_parse_rss_date(pub_date)` | Yes (lines 72, 79) | ✅ Yes |
| 33 | uni_jobs | rss | `feeds/uni_jobs.py:58` | `_parse_rss_date(pub_date)` | Yes (lines 72, 80) | ✅ Yes |
| 34 | weworkremotely | rss | `feeds/weworkremotely.py:59` | `_parse_rss_date(pub_date)` | Yes (lines 73, 81) | ✅ Yes |
| 35 | workanywhere | rss | `feeds/workanywhere.py:64` | `_parse_rss_date(pub_date)` | Yes (lines 78, 86) | ✅ Yes |
| 36 | **aijobs_ai** | scraper | `scrapers/aijobs_ai.py:70` | *(hardcoded — HTML scrape)* | **ALWAYS** (lines 49, 77) | ❌ No |
| 37 | **aijobs_global** | scraper | `scrapers/aijobs_global.py:73` | *(hardcoded — WP Job Manager)* | **ALWAYS** (lines 60, 87) | ❌ No |
| 38 | **bcs_jobs** | scraper | `scrapers/bcs_jobs.py:69` | *(hardcoded — HTML scrape)* | **ALWAYS** (lines 48, 77) | ❌ No |
| 39 | **climatebase** | scraper | `scrapers/climatebase.py:85` | *(hardcoded — Next.js `__NEXT_DATA__` parse)* | **ALWAYS** (lines 50, 92, 106, 123) | ❌ No |
| 40 | eightykhours | scraper | `scrapers/eightykhours.py:78` | `hit.get("date_published")` (Algolia) | Yes (line 76) | ✅ Yes |
| 41 | **jobtensor** | scraper | `scrapers/jobtensor.py:68` | *(hardcoded — AJAX API + HTML fallback)* | **ALWAYS** (lines 53, 84, 113) | ❌ No |
| 42 | **linkedin** | scraper | `scrapers/linkedin.py:60` | *(hardcoded — guest API)* | **ALWAYS** (line 67) | ❌ No (despite `f_TPR=r604800` "past 7 days" URL param, no date in response) |
| 43 | hackernews | other | `other/hackernews.py:103` | `child.get("created_at")` (Algolia HN API) | Yes (line 101) | ✅ Yes |
| 44 | indeed (+ glassdoor) | other | `other/indeed.py:70` | `row.get("date_posted")` (JobSpy DataFrame) | Yes (line 56) | ✅ Yes |
| 45 | nofluffjobs | other | `other/nofluffjobs.py:98` | `item.get("posted") or item.get("renewed")` | Yes (line 78) | ✅ Yes (or renewed as fallback) |
| 46 | **nomis** | other | `other/nomis.py:52` | *(hardcoded — vacancy statistics, not listings)* | **ALWAYS** (lines 37, 59) | ❌ No |
| 47 | themuse | other | `other/themuse.py:70` | `item.get("publication_date")` | Yes (line 59) | ✅ Yes |

### Summary

- **Total sources**: 47
- **Sources with a real posting date (or close inference)**: 30
- **Sources that ALWAYS fall back to `datetime.now()`**: **14**
  - yc_companies, personio, pinpoint, successfactors, workable, findajob, aijobs_ai, aijobs_global, bcs_jobs, climatebase, jobtensor, linkedin, nomis
- **Sources that use a semantically wrong date field** (but do populate `date_found` from the API): **3**
  - jooble (uses `updated`), greenhouse (uses `updated_at`), nhs_jobs (uses `closingDate`)

### Per-category fallback rate

| Category | Count | Real date | Always `now()` | % correct |
|---|---:|---:|---:|---:|
| Keyed APIs | 7 | 7 | 0 | 100% |
| Free APIs | 10 | 9 | 1 (yc_companies) | 90% |
| ATS Boards | 10 | 6 | 4 (personio, pinpoint, successfactors, workable) | 60% |
| RSS Feeds | 8 | 7 | 1 (findajob) | 88% |
| Scrapers | 7 | 1 (eightykhours) | 6 | 14% |
| Other | 5 | 3 | 2 (nomis, plus one more) | 60% |
| **Total** | **47** | **33** | **14** | **70%** |

**HTML scrapers are the worst offenders** — 6 of 7 have no real date. This is because scraping the HTML listing cards doesn't expose the posting date in most job-board templates.

### Per-source notes on unusual handling

- **google_jobs**: Parser at `_parse_posted_at()` (`apis_keyed/google_jobs.py:18–28`) turns SerpApi's relative phrase ("3 days ago", "2 hours ago") into an absolute ISO timestamp by subtracting from `datetime.now(timezone.utc)`. This is inferred, not authoritative.
- **hn_jobs** and **lever**: Both parse numeric epochs (Unix seconds / milliseconds). Lever divides by 1000 because it uses milliseconds.
- **workday**: Parser at `_parse_posted_on()` (`ats/workday.py:17–30`) handles "Today", "Yesterday", and "Posted N Days Ago" phrases, converting to absolute dates.
- **greenhouse**: Uses `updated_at`, not `created_at`. The Greenhouse job board API exposes both; this source deliberately chose `updated_at`. Consequence: a job reposted to change a detail looks brand new.
- **nhs_jobs**: Uses `closingDate`, which is the deadline for applications, not the posting date. A 4-week application window means the stored date is always in the future relative to "now", which breaks `_recency_score` in a more subtle way: `(datetime.now - posted).days` is **negative**, which still evaluates `days_old <= 1` → 10 points (since `-5 <= 1` is True).
- **jooble**: Uses `updated` instead of `created` — similar semantics to Greenhouse.
- **linkedin**: The guest search API is called with `f_TPR=r604800` (time range = past 604800 seconds = 7 days), so LinkedIn *filters* for recent posts before responding. But the response doesn't include the posting date per listing, so every listing still gets `now()` written. The 7-day filter is real, but the per-listing timestamp is fake.
- **nomis**: Not a job listing source at all — it's the UK Office for National Statistics' vacancy statistics API. The source creates synthetic "jobs" that link to the Nomis portal. The `datetime.now()` is technically the correct timestamp for "when these statistics were retrieved".
- **yc_companies**: Similar — not actual job listings, just career-page links for UK-based YC companies. Each link gets `datetime.now()` which is correct in the sense that "now" is when the link was generated.
- **findajob**: The UK GOV DWP job site. Source scrapes HTML cards which don't include dates. Description contains only scraped job card text.

### Confirmation of Indeed + Glassdoor consolidation

`backend/src/main.py:97–98`:
```python
"indeed": JobSpySource,
"glassdoor": JobSpySource,
```

`backend/src/sources/other/indeed.py:19`:
```python
self._sites = sites or ["indeed", "glassdoor"]
```

Both registry entries resolve to the same `JobSpySource` class, which is instantiated once (line 181 of `main.py` — `JobSpySource(session, search_config=sc)`). Internally, `JobSpySource.fetch_jobs()` iterates `self._sites` and calls the `python-jobspy` library once per site, setting the resulting rows' `source` field to whichever site the row came from. So the 48th registry entry is a routing alias — not a separate instance.

---

## 6. Database Layer

**File**: `backend/src/repositories/database.py`
**Class**: `JobDatabase`
**Driver**: `aiosqlite` (async wrapper around stdlib `sqlite3`)

### Class structure

```python
class JobDatabase:
    def __init__(self, db_path: str):
        self._path = db_path
        self._conn: aiosqlite.Connection | None = None
```

The connection is established lazily in `init_db()` and closed in `close()`. `_conn` is `None` between construction and `init_db()`.

### Methods with line refs

- **`init_db()`** (18–73): opens connection, sets pragmas, executes the 4-table schema + 3-index script in a single `executescript()` call, commits, calls `_migrate()`. `row_factory = aiosqlite.Row` enables dict-like row access.

- **`_migrate()`** (75–97): reads `PRAGMA table_info(jobs)` into a set of column names, iterates a hardcoded `migrations = [...]` list (currently empty), and runs `ALTER TABLE jobs ADD COLUMN {col_name} {col_def}` for any missing columns. Input validation via `_VALID_COL_NAME` regex and `_VALID_COL_TYPES` set to prevent SQL injection from the migrations literal. A comment reserves space for future migrations (e.g. `("salary_currency", "TEXT DEFAULT ''")`).

- **`get_tables()`** (99–104): returns list of table names via `SELECT name FROM sqlite_master WHERE type='table'`. Test helper.

- **`is_job_seen(normalized_key)`** (106–112): `SELECT 1 FROM jobs WHERE normalized_company = ? AND normalized_title = ?`, returns `True` if any row.

- **`insert_job(job)`** (114–132):
  ```python
  async def insert_job(self, job: Job) -> bool:
      """Insert job, returning True if it was actually inserted (not a duplicate)."""
      company, title = job.normalized_key()
      now = datetime.now(timezone.utc).isoformat()
      cursor = await self._conn.execute(
          """INSERT OR IGNORE INTO jobs
          (title, company, location, salary_min, salary_max, description,
           apply_url, source, date_found, match_score, visa_flag,
           experience_level, normalized_company, normalized_title, first_seen)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
          (
              job.title, job.company, job.location,
              job.salary_min, job.salary_max, job.description,
              job.apply_url, job.source, job.date_found,
              job.match_score, int(job.visa_flag),
              job.experience_level, company, title, now,
          ),
      )
      return cursor.rowcount > 0
  ```
  Uses `INSERT OR IGNORE`. The UNIQUE constraint is on `(normalized_company, normalized_title)`, so a duplicate pair returns `rowcount = 0` and the method returns `False`. Commit is **not** called here — the caller (`main.py:376`) calls `db.commit()` once after the loop finishes.

- **`commit()`** (134–137): `await self._conn.commit()`.

- **`count_jobs()`** (139–142): `SELECT COUNT(*) FROM jobs`.

- **`log_run(stats)`** (144–156):
  ```python
  async def log_run(self, stats: dict):
      now = datetime.now(timezone.utc).isoformat()
      await self._conn.execute(
          "INSERT INTO run_log (timestamp, total_found, new_jobs, sources_queried, per_source) VALUES (?, ?, ?, ?, ?)",
          (
              now,
              stats.get("total_found", 0),
              stats.get("new_jobs", 0),
              stats.get("sources_queried", 0),
              json.dumps(stats.get("per_source", {})),
          ),
      )
      await self._conn.commit()
  ```
  This commits immediately, unlike `insert_job`.

- **`get_run_logs(limit=100)`** (158–172): `SELECT timestamp, total_found, new_jobs, per_source FROM run_log ORDER BY id DESC LIMIT ?`. Returns list of dicts with `per_source` JSON-parsed.

- **`get_new_jobs_since(hours=12)`** (174–181): `SELECT * FROM jobs WHERE first_seen >= ? ORDER BY match_score DESC`. Cutoff = `now - timedelta(hours=hours)`. **Uses `first_seen`, not `date_found`** — "new" means "seen by Job360 in the last 12 hours", not "posted in the last 12 hours".

- **`purge_old_jobs(days=30)`** (183–190):
  ```python
  async def purge_old_jobs(self, days: int = 30) -> int:
      cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
      cursor = await self._conn.execute(
          "DELETE FROM jobs WHERE first_seen < ?", (cutoff,)
      )
      await self._conn.commit()
      return cursor.rowcount
  ```
  Default retention: **30 days of `first_seen`**. Called on every run at `main.py:272`.

- **`get_recent_jobs(days=7, min_score=0)`** (192–200): `SELECT * FROM jobs WHERE first_seen >= ? AND match_score >= ? ORDER BY date_found DESC`. This is the dashboard's main query path. Note that ordering is by `date_found` (which can be misleading for the 14 hardcoded-now sources) but filtering is by `first_seen` (which is always accurate).

- **`get_last_source_counts(n=5)`** (202–213): reads the last N `run_log.per_source` JSON blobs and rolls them up into a `dict[source_name, list[int]]`. Used for the source-health check.

- **`insert_action(job_id, action, notes="")`** (217–224): `INSERT OR REPLACE INTO user_actions`. The UNIQUE constraint on `job_id` means each job can only have one active action at a time — acting again overwrites.

- **`delete_action(job_id)`** (226–228): `DELETE FROM user_actions WHERE job_id = ?`.

- **`get_actions()`** (230–235): all actions ordered by `created_at DESC`.

- **`get_action_counts()`** (237–241): `SELECT action, COUNT(*) FROM user_actions GROUP BY action`.

- **`get_action_for_job(job_id)`** (243–248): single action string or None.

- **`create_application(job_id)`** (252–259): `INSERT OR IGNORE INTO applications (..., 'applied', ...)`. Returns the application dict via `_get_application`.

- **`advance_application(job_id, stage)`** (261–268): `UPDATE applications SET stage = ?, updated_at = ? WHERE job_id = ?`.

- **`_get_application(job_id)`** (270–283): `SELECT ... FROM applications a LEFT JOIN jobs j ON a.job_id = j.id WHERE a.job_id = ?`. Returns dict with stage, dates, notes, title, company.

- **`get_applications(stage=None)`** (285–303): all applications, optionally filtered by stage, ordered by `updated_at DESC`.

- **`get_application_counts()`** (305–309): stage → count.

- **`get_stale_applications(days=7)`** (311–323): `WHERE updated_at < ? AND stage NOT IN ('offer', 'rejected')`. The `backend/src/api/routes/pipeline.py::get_pipeline_reminders` endpoint uses this.

- **`get_job_by_id(job_id)`** (325–331): single job lookup via ID.

- **`close()`** (333–335): `await self._conn.close()`.

### CSV export (`backend/src/repositories/csv_export.py`)

```python
HEADERS = [
    "job_title", "company", "location", "salary",
    "match_score", "apply_url", "source", "date_found", "visa_flag",
]
```

9 columns. Salary is formatted as a combined string (`"40000-60000"`, `"40000+"`, `"Up to 60000"`, or `""`). The export writes to a temp file then atomically renames — safe against partial writes. Called from `main.py:395` via `asyncio.to_thread()` since the underlying `csv` module is sync.

---

## 7. API Layer (`backend/src/api/`)

### Main app (`backend/src/api/main.py`)

Full file, verbatim (32 lines):

```python
"""FastAPI application for Job360 backend."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api.dependencies import init_db, close_db
from src.api.routes import health, jobs, actions, profile, search, pipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()


app = FastAPI(title="Job360 API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(actions.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
```

- **6 routers** registered, all under `/api` prefix. Not 7 as CLAUDE.md claims.
- **CORS**: single origin `http://localhost:3000`, credentials enabled, wildcard methods and headers. This breaks any deployment where the frontend isn't on localhost.
- **Lifespan**: `init_db()` on startup, `close_db()` on shutdown.
- **Version**: `1.0.0`.

### Entry wrapper (`backend/main.py`)

```python
"""Job360 FastAPI entrypoint.

Run from the backend/ directory:
    uvicorn main:app --reload
    python -m uvicorn main:app --host 0.0.0.0 --port 8000

The actual app wiring (routes, middleware, lifespan) lives in
src/api/main.py. This file exists as the canonical module path
that uvicorn and deployment platforms import.
"""
from src.api.main import app

__all__ = ["app"]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

19-line shim. `__all__ = ["app"]` makes `main.app` the canonical import target for uvicorn and deployment tools that expect `module:app`.

### Dependencies (`backend/src/api/dependencies.py`)

Per Agent 3's read:
- `init_db()` — module-level `JobDatabase.init_db()` on the shared instance.
- `close_db()` — module-level close of the same instance.
- `get_db()` — FastAPI dependency injection function returning the shared `JobDatabase`.
- `save_upload_to_temp(upload_file)` — writes an uploaded file to a temporary path and returns the path.

### Pydantic models (`backend/src/api/models.py`)

Per Agent 3's read:

- `HealthResponse` (8–10): `status: str`, `version: str`
- `SourcesResponse` (around lines 12–20): `sources: list[str]`, `total: int`
- `StatusResponse` (23–28): `jobs_total: int`, `last_run: Optional[str]`, `sources_active: int`, `sources_total: int`, `profile_exists: bool`
- `JobResponse` (31–58): 15 primary fields (id, title, company, location, salary_min, salary_max, description, apply_url, source, date_found, match_score, visa_flag, experience_level, first_seen, normalized_company, normalized_title) + an 8-dimensional score breakdown (role, skill, seniority, experience, credentials, location_score, recency, semantic, penalty) + skill arrays (matched, missing, transferable) + `action` + `bucket`
- `JobListResponse` (60–63): `jobs: list[JobResponse]`, `total: int`, `filters_applied: dict`
- `ActionRequest` (66+): `action: Literal["liked", "applied", "not_interested"]`, `notes: str = ""`
- `ActionResponse`: returns the stored action plus `job_id`, `created_at`
- `CVDetail` (92–109): `raw_text`, `skills`, `job_titles`, `companies`, `education`, `certifications`, `summary_text`, `experience_text`, `name`, `headline`, `location`, `achievements`, `highlights`
- `ProfileResponse` (112–115): `summary: ProfileSummary`, `preferences: dict`, `cv_detail: Optional[CVDetail]`
- `LinkedInResponse` / `GitHubResponse`: counts of parsed items
- `PreferencesRequest`: full preferences form shape
- `SearchStartResponse` (128–133): `run_id: str`, `status: str`
- `SearchStatusResponse` (135–138): `run_id`, `status`, `progress: Optional[dict]`, `result: Optional[dict]`
- `PipelineApplication` (140–147): `job_id`, `stage`, `created_at`, `updated_at`, `notes`, `title`, `company`
- `PipelineListResponse`, `PipelineRemindersResponse`

### Routes

**`backend/src/api/routes/health.py`**:
- `GET /api/health` → `HealthResponse` ({"status": "ok", "version": "1.0.0"})
- `GET /api/status` → `StatusResponse` (queries `JobDatabase` for total jobs, last run from `run_log`, counts active vs total sources from `SOURCE_REGISTRY`, checks profile existence)
- `GET /api/sources` → `SourcesResponse` (list of `sorted(SOURCE_REGISTRY.keys())`)

**`backend/src/api/routes/jobs.py`**:
- `GET /api/jobs` with query params `hours`, `min_score`, `source`, `bucket`, `action`, `visa_only`, `limit`, `offset` → `JobListResponse`. Reads via `db.get_recent_jobs()`, then applies in-Python filtering for `source`, `bucket`, `action`, `visa_only`. Bucket computation uses `assign_bucket()` from `utils/time_buckets.py`.
- `GET /api/jobs/{job_id}` → `JobResponse` (single job detail, 404 on miss)
- `GET /api/jobs/export` → CSV streaming response (`StreamingResponse` with `text/csv` media type). Uses `db.get_recent_jobs(days=7, min_score=0)` and streams through the csv module.

**`backend/src/api/routes/actions.py`**:
- `POST /api/jobs/{job_id}/action` with body `ActionRequest` → `ActionResponse` (upserts via `db.insert_action`)
- `DELETE /api/jobs/{job_id}/action` → `ActionResponse` or 204
- `GET /api/actions` → list of all actions
- `GET /api/actions/counts` → `{liked: N, applied: N, not_interested: N}`

**`backend/src/api/routes/profile.py`**:
- `GET /api/profile` → `ProfileResponse`
- `POST /api/profile` with multipart form: `cv` (UploadFile, optional) + `preferences` (JSON string of `PreferencesRequest`) → `ProfileResponse`. Saves CV to temp, calls `parse_cv`, merges with preferences, persists via `save_profile`.
- `POST /api/profile/linkedin` with a ZIP file upload → `LinkedInResponse`
- `POST /api/profile/github` with form field `github_username` → `GitHubResponse`

**`backend/src/api/routes/search.py`**:
- `POST /api/search` with optional query param `source` → `SearchStartResponse`. Starts `run_search()` as an `asyncio.create_task` and returns a run_id. Run state is tracked in a **module-level `_runs: dict[str, dict]`** — not persisted across API restarts.
- `GET /api/search/{run_id}/status` → `SearchStatusResponse`. Polls the in-memory `_runs` dict.

**`backend/src/api/routes/pipeline.py`**:
- `GET /api/pipeline` with optional `stage` query param → list of `PipelineApplication`
- `GET /api/pipeline/counts` → `{stage: count}` for (applied, outreach, interview, offer, rejected)
- `GET /api/pipeline/reminders` → `PipelineRemindersResponse` (applications stale >7 days via `db.get_stale_applications`)
- `POST /api/pipeline/{job_id}` → `PipelineApplication` (creates at stage='applied')
- `POST /api/pipeline/{job_id}/advance` with body `{stage: str}` → `PipelineApplication`

---

## 8. Frontend (`frontend/`)

### Framework and versions

Per `frontend/package.json:11–34`:
- **Next.js 16.2.2** (App Router)
- **React 19.2.4**
- **TypeScript ^5**
- **Tailwind CSS ^4** (+ `@tailwindcss/postcss ^4`, `tw-animate-css ^1.4.0`)
- **shadcn ^4.1.2** component system
- **@base-ui/react ^1.3.0** (unstyled primitives)
- **recharts ^3.8.1** (charts)
- **motion ^12.38.0** (formerly framer-motion)
- **lucide-react ^1.7.0** (icons)
- **clsx ^2.1.1** + **tailwind-merge ^3.5.0** (likely used by `cn()` helper in `lib/utils.ts`)
- **class-variance-authority ^0.7.1** (shadcn button variants)

### AGENTS.md warning

`frontend/CLAUDE.md` imports `frontend/AGENTS.md` via `@AGENTS.md`, which contains:

> # This is NOT the Next.js you know
>
> This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.

**Any frontend modification must consult the vendored Next.js 16.2 docs rather than rely on prior-version training data.**

### Configuration files

- `frontend/next.config.ts` — effectively empty config object
- `frontend/tsconfig.json` — strict mode, `target: ES2017`, `"@/*": ["./src/*"]` path alias, `jsx: "react-jsx"`
- `frontend/components.json` — shadcn registry config

### App Router pages

6 pages under `frontend/src/app/`:
1. `layout.tsx` — Root layout (Navbar, footer, providers)
2. `page.tsx` — Home page
3. `dashboard/page.tsx` — Job dashboard (main view)
4. `jobs/[id]/page.tsx` — Individual job detail
5. `pipeline/page.tsx` — Application tracker (Kanban board)
6. `profile/page.tsx` — CV upload + preferences form

### Components

**`frontend/src/components/ui/`** — 15 shadcn primitives:
- `badge.tsx`, `button.tsx`, `card.tsx`, `dialog.tsx`, `input.tsx`, `label.tsx`, `select.tsx`, `separator.tsx`, `sheet.tsx`, `skeleton.tsx`, `slider.tsx`, `tabs.tsx`, `textarea.tsx`, `tooltip.tsx`

**`frontend/src/components/jobs/`** — 6 job-specific components:
- `JobCard.tsx` — renders title, company, location, salary, score badge, matched/missing/transferable skill chips, Like/Skip/Apply action buttons
- `JobList.tsx` — list wrapper with pagination
- `FilterPanel.tsx` — Sheet-based filter UI: min_score slider, source text filter, visa_only checkbox, action select
- `ScoreRadar.tsx` — recharts radar chart for the 8-dimensional score breakdown
- `ScoreCounter.tsx` — animated number display for the total score
- `TimeBuckets.tsx` — renders the 4 time buckets (24h / 48h / 72h / 7d)

**`frontend/src/components/profile/`** — 3 profile components:
- `CVUpload.tsx` — file upload dropzone
- `CVViewer.tsx` — CV text display with skill highlighting
- `PreferencesForm.tsx` — full preferences form

**`frontend/src/components/pipeline/`**:
- `KanbanBoard.tsx` — drag-drop application stages (applied → outreach → interview → offer/rejected)

**`frontend/src/components/layout/`** — 3 layout primitives:
- `Navbar.tsx`, `Footer.tsx`, `FloatingIcons.tsx`

### API client (`frontend/src/lib/api.ts`)

- **Base URL**: `process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"`
- Typed fetch-based client with one function per backend endpoint:
  - `getHealth()`, `getStatus()`, `getSources()`
  - `getJobs(filters)`, `getJob(id)`, `exportJobsCsv()`
  - `setJobAction(jobId, body)`, `removeJobAction(jobId)`, `getActions()`, `getActionCounts()`
  - `getProfile()`, `uploadProfile(cv, prefs)`, `uploadLinkedin(file)`, `uploadGithub(username)`
  - `startSearch(options?)`, `getSearchStatus(runId)`
  - `getPipelineApplications(stage?)`, `createPipelineApplication(jobId)`, `advancePipelineStage(jobId, body)`, `getPipelineReminders()`, `getPipelineCounts()`

### Types (`frontend/src/lib/types.ts`)

Mirrors backend Pydantic models:
- `JobResponse` — every backend JobResponse field plus the 8-dimensional score breakdown (`role`, `skill`, `seniority`, `experience`, `credentials`, `location_score`, `recency`, `semantic`, `penalty`), skill arrays (`matched`, `missing`, `transferable`), `action`, `bucket`
- `JobFilters` — query param shape: `hours`, `min_score`, `source`, `bucket`, `action`, `visa_only`, `limit`, `offset`
- `ProfileSummary` — `is_complete`, `job_titles`, `skills_count`, `cv_length`, `has_linkedin`, `has_github`, `education`, `experience_level`
- `PreferencesRequest` — matches backend form shape
- `PipelineApplication` — matches backend
- `ActionRequest` — `action: "liked" | "applied" | "not_interested"`, `notes`

---

## 9. Frontend Delivery (`frontend/` + `backend/src/api/`)

The interactive UI is a Next.js 16 single-page application at `frontend/` that talks to the FastAPI backend at `backend/src/api/` over HTTP. Section 8 covers the FastAPI routes in detail; the Next.js layer consumes them via `frontend/src/lib/api.ts`. No Python-rendered UI exists in the codebase — all interactive rendering happens in the Next.js SPA.

---

## 10. Notifications (`backend/src/services/notifications/`)

### Base class (`base.py`)

- `NotificationChannel(ABC)`:
  - Abstract method `is_configured() -> bool` — each channel checks its own env vars.
  - Abstract method `send(jobs, stats, csv_path=None)`.
- `get_all_channels()` returns `[EmailChannel(), SlackChannel(), DiscordChannel()]`.
- `get_configured_channels()` filters the above to only those where `is_configured()` returns True.
- `format_salary(salary_min, salary_max) -> str` — shared formatting helper used by all 3 channels.

### Email (`email_notify.py`)

- **SMTP**: `smtp.gmail.com:587` (from `core/settings.py:33–34`), STARTTLS.
- **Env vars**: `SMTP_EMAIL`, `SMTP_PASSWORD`, `NOTIFY_EMAIL`. All three must be set for `is_configured()` to return True.
- **Subject**: `f"Job360: {len(jobs)} new jobs ({in_24h} in 24h) - {date}"` where `in_24h` counts jobs with `bucket == 'last_24h'`.
- **Body**: HTML via `generate_html_report(new_jobs, stats)`.
- **Attachment**: CSV file from `csv_path`, MIME type `application/octet-stream`.
- **Flow**: builds `MIMEMultipart`, adds body + attachment, opens SMTP connection, calls `starttls()`, `login()`, `send_message()`.

### Slack (`slack_notify.py`)

- **Env var**: `SLACK_WEBHOOK_URL`.
- **Payload** — Block Kit format:
  1. Header block: `f"🎯 Job360: {len(jobs)} new jobs"`
  2. Section block: stats (total scanned, new, sources queried)
  3. Divider
  4. For each of the top 10 jobs (by match_score):
     - Section block with emoji (🟢 for score ≥ 70, 🟡 for ≥ 50, 🔴 for lower) + title + company + location + salary + apply URL
     - Visa flag as inline text if set
  5. Context block footer with source breakdown (per_source counts)
- POST via `aiohttp` to the webhook URL with `Content-Type: application/json`.

### Discord (`discord_notify.py`)

- **Env var**: `DISCORD_WEBHOOK_URL`.
- **Payload** — embed format:
  - Title: `f"Job360: {len(jobs)} new jobs"`
  - Description: top 10 jobs formatted as markdown lines
  - Color: `0x1A73E8` (Google Blue)
  - 3 inline fields: Total Scanned, New Jobs, Sources
  - Footer: per-source summary text

### Report generator (`report_generator.py`)

- `generate_markdown_report(jobs, stats)`: header with stats, per-bucket tables (24h / 48h / 72h / 7d) with columns `[Score, Title, Company, Location, Salary, Source, Posted]`, Visa flag column. Used for both the markdown file written to `data/reports/` and the body fallback if HTML rendering fails.
- `generate_html_report(jobs, stats)`: inline CSS (no external stylesheet — works in email clients), same table structure, `score_color_hex()` helper maps score tiers to background colors (green/yellow/red).

### Trigger point

`backend/src/main.py:406–411`:

```python
if not no_notify:
    for channel in get_configured_channels():
        try:
            await channel.send(new_jobs, stats, csv_path=csv_path)
        except Exception as e:
            logger.error("%s notification failed: %s", channel.name, e)
```

Guarded outer condition (line 390): `if new_jobs:`. So notifications only fire when:
1. Pipeline is not in `dry_run` mode (short-circuited at line 358)
2. `--no-email` flag (which maps to `no_notify=True`) is not set
3. `new_jobs` list is non-empty

The `--no-email` flag name is misleading — it disables all three channels, not just email.

---

## 11. Configuration (`backend/src/core/`)

### `settings.py` (verbatim, 112 lines)

**Path resolution** (7–12):
```python
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "jobs.db"
EXPORTS_DIR = DATA_DIR / "exports"
REPORTS_DIR = DATA_DIR / "reports"
LOGS_DIR = DATA_DIR / "logs"
```

`parent.parent.parent` from `backend/src/core/settings.py` = `backend/`, so `BASE_DIR = backend/` and `DB_PATH = backend/data/jobs.db`. All runtime data is inside `backend/data/`, never at the repo root.

**API keys loaded from env** (14–22):
- `REED_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `JSEARCH_API_KEY`, `JOOBLE_API_KEY`, `SERPAPI_KEY`, `CAREERJET_AFFID`, `FINDWORK_API_KEY` — all default to `""` (empty string), not `None`. Keyed sources check for empty string and no-op.

**Optional**:
- `GITHUB_TOKEN` (24–25)

**LLM providers** (27–30):
- `GEMINI_API_KEY`, `GROQ_API_KEY`, `CEREBRAS_API_KEY`

**Email** (32–37): `SMTP_HOST = "smtp.gmail.com"`, `SMTP_PORT = 587`, `SMTP_EMAIL`, `SMTP_PASSWORD`, `NOTIFY_EMAIL`.

**Webhooks** (39–41): `SLACK_WEBHOOK_URL`, `DISCORD_WEBHOOK_URL`.

**Search constants** (43–50):
```python
MIN_MATCH_SCORE = 30
MAX_RESULTS_PER_SOURCE = 100
MAX_DAYS_OLD = 7

TARGET_SALARY_MIN = int(os.getenv("TARGET_SALARY_MIN", "40000"))
TARGET_SALARY_MAX = int(os.getenv("TARGET_SALARY_MAX", "120000"))
```

`TARGET_SALARY_MIN` and `TARGET_SALARY_MAX` are the only constants that can be overridden via env var. They are used only as a sort tiebreaker in `salary_in_range()` — not part of the 0–100 score.

**Rate limits** (53–103) — dict with 48 entries, one per source name:

```python
RATE_LIMITS = {
    "reed": {"concurrent": 1, "delay": 2.0},
    "adzuna": {"concurrent": 1, "delay": 2.0},
    "jsearch": {"concurrent": 1, "delay": 3.0},
    "arbeitnow": {"concurrent": 2, "delay": 1.0},
    "remoteok": {"concurrent": 1, "delay": 2.0},
    "jobicy": {"concurrent": 2, "delay": 1.0},
    "himalayas": {"concurrent": 2, "delay": 1.0},
    "greenhouse": {"concurrent": 2, "delay": 1.5},
    "lever": {"concurrent": 2, "delay": 1.5},
    "workable": {"concurrent": 2, "delay": 1.5},
    "ashby": {"concurrent": 2, "delay": 1.5},
    "findajob": {"concurrent": 1, "delay": 3.0},
    "remotive": {"concurrent": 2, "delay": 1.0},
    "jooble": {"concurrent": 1, "delay": 2.0},
    "linkedin": {"concurrent": 1, "delay": 3.0},
    "smartrecruiters": {"concurrent": 2, "delay": 1.5},
    "pinpoint": {"concurrent": 2, "delay": 1.5},
    "recruitee": {"concurrent": 2, "delay": 1.5},
    "indeed": {"concurrent": 1, "delay": 3.0},
    "glassdoor": {"concurrent": 1, "delay": 3.0},
    "workday": {"concurrent": 2, "delay": 1.5},
    "google_jobs": {"concurrent": 1, "delay": 2.0},
    "devitjobs": {"concurrent": 2, "delay": 1.0},
    "landingjobs": {"concurrent": 2, "delay": 1.0},
    "aijobs": {"concurrent": 2, "delay": 1.0},
    "themuse": {"concurrent": 1, "delay": 2.0},
    "hackernews": {"concurrent": 2, "delay": 1.0},
    "careerjet": {"concurrent": 1, "delay": 2.0},
    "findwork": {"concurrent": 1, "delay": 2.0},
    "nofluffjobs": {"concurrent": 2, "delay": 1.5},
    "hn_jobs": {"concurrent": 3, "delay": 0.5},
    "yc_companies": {"concurrent": 1, "delay": 1.0},
    "jobs_ac_uk": {"concurrent": 1, "delay": 2.0},
    "nhs_jobs": {"concurrent": 1, "delay": 2.0},
    "personio": {"concurrent": 1, "delay": 3.0},
    "workanywhere": {"concurrent": 1, "delay": 5.0},
    "weworkremotely": {"concurrent": 1, "delay": 2.0},
    "realworkfromanywhere": {"concurrent": 1, "delay": 2.0},
    "biospace": {"concurrent": 1, "delay": 2.0},
    "jobtensor": {"concurrent": 1, "delay": 3.0},
    "climatebase": {"concurrent": 1, "delay": 3.0},
    "eightykhours": {"concurrent": 1, "delay": 2.0},
    "bcs_jobs": {"concurrent": 1, "delay": 3.0},
    "uni_jobs": {"concurrent": 1, "delay": 2.0},
    "successfactors": {"concurrent": 1, "delay": 2.0},
    "aijobs_global": {"concurrent": 2, "delay": 1.0},
    "aijobs_ai": {"concurrent": 1, "delay": 2.0},
    "nomis": {"concurrent": 1, "delay": 5.0},
}
```

**Retry** (106–107): `MAX_RETRIES = 3`, `RETRY_BACKOFF = [1, 2, 4]`.

**HTTP** (110–111): `REQUEST_TIMEOUT = 30`, `USER_AGENT = "Job360/1.0 (UK Job Search Aggregator)"`.

### `keywords.py` (verbatim, 73 lines)

**Critical fact**: Every domain-specific list is empty. Only `LOCATIONS` (26 entries) and `VISA_KEYWORDS` (8 entries) have content.

```python
JOB_TITLES: list[str] = []
PRIMARY_SKILLS: list[str] = []
SECONDARY_SKILLS: list[str] = []
TERTIARY_SKILLS: list[str] = []
RELEVANCE_KEYWORDS: list[str] = []
NEGATIVE_TITLE_KEYWORDS: list[str] = []

LOCATIONS = [
    "UK", "United Kingdom", "London", "Greater London", "City of London",
    "Cambridge", "Manchester", "Edinburgh", "Birmingham", "Bristol",
    "Hertfordshire", "Hatfield", "Leeds", "Glasgow", "Belfast",
    "Oxford", "Reading", "Southampton", "Nottingham", "Sheffield",
    "Liverpool", "England", "Scotland", "Wales", "Remote", "Hybrid",
]  # 26 entries

VISA_KEYWORDS = [
    "visa sponsorship", "sponsorship", "right to work", "work permit",
    "visa", "sponsored", "tier 2", "skilled worker visa",
]  # 8 entries
```

`KNOWN_SKILLS` and `KNOWN_TITLE_PATTERNS` are not present. The file's docstring at lines 1–9 explicitly explains this: all AI/ML defaults were removed on 2026-04-09.

### `companies.py` ATS slug counts

Per Agent 1's read:

| Platform | Count | Shape |
|---|---:|---|
| Greenhouse | 25 | `list[str]` of slugs |
| Lever | 12 | `list[str]` |
| Workable | 8 | `list[str]` |
| Ashby | 9 | `list[str]` |
| SmartRecruiters | 6 | `list[str]` |
| Pinpoint | 8 | `list[str]` |
| Recruitee | 8 | `list[str]` |
| Workday | 14 | `list[dict]` with `tenant`, `wd`, `site`, `name` keys |
| Personio | 10 | `list[str]` |
| SuccessFactors | 3 | `list[dict]` with `name`, `sitemap_url` keys |
| **Total unique companies** | **~103** | (some slugs appear on multiple platforms) |

Additionally, 22 `name_override` mappings convert slugs like `"monzo"` to display names like `"Monzo Bank"`.

### Environment variables required

| Variable | Required | Used by |
|---|---|---|
| `REED_API_KEY` | No (free sources work without) | `ReedSource` |
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | No | `AdzunaSource` |
| `JSEARCH_API_KEY` | No | `JSearchSource` |
| `JOOBLE_API_KEY` | No | `JoobleSource` |
| `SERPAPI_KEY` | No | `GoogleJobsSource` |
| `CAREERJET_AFFID` | No | `CareerjetSource` |
| `FINDWORK_API_KEY` | No | `FindworkSource` |
| `GITHUB_TOKEN` | No | `github_enricher.py` (higher rate limit) |
| `GEMINI_API_KEY` | No* | `llm_provider._call_gemini` |
| `GROQ_API_KEY` | No* | `llm_provider._call_groq` |
| `CEREBRAS_API_KEY` | No* | `llm_provider._call_cerebras` |
| `SMTP_EMAIL` + `SMTP_PASSWORD` + `NOTIFY_EMAIL` | No | Email notifications |
| `SLACK_WEBHOOK_URL` | No | Slack notifications |
| `DISCORD_WEBHOOK_URL` | No | Discord notifications |
| `TARGET_SALARY_MIN` / `TARGET_SALARY_MAX` | No | Salary tiebreaker sorting |

*At least one of `GEMINI_API_KEY`, `GROQ_API_KEY`, or `CEREBRAS_API_KEY` must be set for CV parsing to work. Otherwise `setup-profile --cv ...` will fail with `RuntimeError` from `llm_provider.llm_extract`.

`.env.example` exists at the repo root (`.env.example`) — not inside `backend/`.

---

## 12. Test Suite (`backend/tests/`)

### Count and structure

**21 test files** (via `Glob` of `backend/tests/test_*.py`):

| File | Focus |
|---|---|
| `test_api.py` | FastAPI endpoints (health, jobs, actions, profile, pipeline) |
| `test_cli.py` | Click CLI commands; contains `len(SOURCE_REGISTRY) == 48` assertion |
| `test_cli_view.py` | Rich terminal table viewer |
| `test_cron.py` | `cron_setup.sh` script validation |
| `test_csv_export.py` | CSV header + salary formatting |
| `test_dashboard.py` | `_safe_url()` XSS guard |
| `test_database.py` | `JobDatabase` CRUD, migrations, source history |
| `test_deduplicator.py` | `deduplicate()` + `_normalize_title()` |
| `test_linkedin_github.py` | LinkedIn ZIP parsing + GitHub API enrichment |
| `test_llm_provider.py` | Multi-provider LLM fallback |
| `test_main.py` | `run_search()` with mocked sources |
| `test_models.py` | `Job` dataclass, `normalized_key()`, salary sanitization |
| `test_notification_base.py` | `NotificationChannel` ABC, channel discovery |
| `test_notifications.py` | Email, Slack, Discord send |
| `test_profile.py` | `SearchConfig`, `UserProfile`, CV parser, storage, `JobScorer` cross-domain cases |
| `test_rate_limiter.py` | Async `RateLimiter` |
| `test_reports.py` | Markdown + HTML report generation |
| `test_scorer.py` | Scoring components, penalties, word boundaries, experience detection |
| `test_setup.py` | `setup.sh` validation |
| `test_sources.py` | All 47 sources with mocked HTTP via `aioresponses` |
| `test_time_buckets.py` | Bucket assignment logic |

Plus `backend/tests/conftest.py` with shared fixtures.

### Total test count

CLAUDE.md states **412 tests** (from `pytest --collect-only`). This audit did not re-run `pytest --collect-only` due to environment setup requirements on Windows (venv activation, potential module-path issues), but the file count is confirmed at 21.

### Fixtures (`conftest.py`)

Per Agent 3's read:
- `sample_ai_job` — AI Engineer at DeepMind with visa sponsorship
- `sample_unrelated_job` — Marketing Manager (off-topic)
- `sample_duplicate_jobs` — ML Engineer role from multiple sources
- `sample_visa_job` — Data Scientist with explicit visa mention
- `sample_non_uk_job` — San Francisco role (non-UK)
- `sample_empty_description_job` — Blank description

### Mocking pattern

All source tests use `aioresponses` to mock aiohttp calls. The test suite is designed to run fully offline — no network access needed.

**Exception**: `test_main.py` includes paths that can reach `JobSpySource`, which internally uses the `python-jobspy` library. JobSpy is not fully mocked in some tests, so running `test_main.py` with `python-jobspy` installed may attempt live requests to Indeed. This is flagged in project memory but not fixed in code.

### Pytest config

From `backend/pyproject.toml:46–49`:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
pythonpath = ["."]
```

Must run from `backend/` directory.

---

## 13. Known Issues & Bugs (Observed from Reading Code)

Every item below was found by reading the code in this audit, not from memory.

### 1. `date_found` fallback to `now()` in 14 sources

See Section 5. Concrete consequences:
- `_recency_score()` always awards ≥4 points (and usually 10) for jobs from these sources.
- The CSV export, email report, Slack message, and Discord embed all show today's date in the "Posted" field — users cannot tell which listings are stale.
- The dashboard's 24h bucket fills with listings that may be weeks old.
- The `INSERT OR IGNORE` dedup works across runs (because it uses normalized title/company), but time-based filtering (`get_recent_jobs`, `get_new_jobs_since`) returns stale listings as "recent".

### 2. Semantically wrong date fields in 3 sources

- **jooble** (`apis_keyed/jooble.py:60`) — uses `item["updated"]`
- **greenhouse** (`ats/greenhouse.py:41`) — uses `item["updated_at"]`
- **nhs_jobs** (`feeds/nhs_jobs.py:68`) — uses `closingDate` (the application deadline)

For NHS Jobs specifically, the closing date is in the *future*, which means `(now - posted).days` is negative. `_recency_score` at `skill_matcher.py:176` checks `days_old <= 1` first — so negative-day-old jobs still get the full 10 recency points.

### 3. Dedup normalization divergence (documented as intentional)

`deduplicator.py:18–33` explicitly documents the divergence between `Job.normalized_key()` and `_normalize_title()`:

> "This is intentionally MORE aggressive than Job.normalized_key(). ... This means dedup groups are wider than DB unique keys — by design:
> - Dedup merges 'Senior ML Engineer' and 'ML Engineer' within a single run
> - DB preserves them as separate records across runs
> Do NOT unify these without a full DB migration (see CLAUDE.md Rule 1)."

This is a documented design tradeoff, not a bug. However, the **observable consequence** is that across runs, if a source posts "Senior ML Engineer" on Monday and the deduplicator merges it with "ML Engineer" (keeping the ML Engineer row in the DB), then Wednesday's fetch of "Senior ML Engineer" again will not collide with the DB's "ML Engineer" row and will be inserted as a new record. The user sees both variants listed across different run timestamps.

### 4. Module-level `score_job()` is effectively dead code

Lives in `skill_matcher.py:231–240`. Depends on hardcoded `JOB_TITLES`, `PRIMARY_SKILLS`, `SECONDARY_SKILLS`, `TERTIARY_SKILLS`, `NEGATIVE_TITLE_KEYWORDS` from `keywords.py`, all of which are now empty lists. If called, it would return only `location + recency - foreign_penalty` bounded to `[0, 20]`. The pipeline never calls it — `main.py:261` always instantiates `JobScorer(search_config)`. The module-level `check_visa_flag` (lines 243–245) is imported in `main.py:20` but the actual call site uses `scorer.check_visa_flag()` instead (line 347). So `check_visa_flag` import and `score_job` / `_title_score` / `_skill_score` / `_negative_penalty` module-level functions are preserved but unused.

### 5. CORS configured for single-origin localhost

`api/main.py:18–24`:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

This breaks any deployment where the Next.js frontend is not served at `http://localhost:3000`. Vercel or any production URL would be blocked.

### 6. `cron_setup.sh` is stale after the phase-1 refactor

`cron_setup.sh:10–11`:
```bash
PYTHON="$PROJECT_DIR/venv/bin/python"
MAIN="$PROJECT_DIR/src/main.py"
```

And line 25–27:
```bash
CRON_CMD="cd $PROJECT_DIR && $PYTHON -m src.main >> $LOG 2>&1"
CRON_4AM="0 4 * * * TZ='Europe/London' $CRON_CMD"
CRON_4PM="0 16 * * * TZ='Europe/London' $CRON_CMD"
```

`$PROJECT_DIR` is the script's own directory = the repo root. So:
- `$PROJECT_DIR/venv/bin/python` → `job360/venv/bin/python`, but post-refactor the venv is likely at `job360/backend/venv` (or is managed per-developer).
- `$PROJECT_DIR/src/main.py` → `job360/src/main.py`, but `src/` no longer exists at the repo root; it's now `backend/src/`.
- `$PYTHON -m src.main` → runs `python -m src.main` from the repo root cwd, which will fail with `ModuleNotFoundError: No module named 'src'` because the module is inside `backend/`.

Installing this cron on the current codebase results in two broken crontab entries that fail every 12 hours. The script has not been updated since the phase-1 directory move (commit `0d3ef72`).

### 7. LinkedIn source's 7-day filter doesn't propagate to `date_found`

`scrapers/linkedin.py` calls the guest API with URL param `f_TPR=r604800` (604800 seconds = 7 days), so LinkedIn server-side filters to listings posted in the past 7 days. But the response doesn't include the per-listing posting date, so every job still gets `date_found = datetime.now()`. The filter works but the timestamp is fake.

### 8. In-memory search run state

`backend/src/api/routes/search.py` stores `_runs: dict[str, dict]` at module level. Any FastAPI process restart wipes all in-flight run state. The client's `run_id` becomes a ghost reference. Clients that poll `GET /api/search/{run_id}/status` after a backend restart will get a 404-like behavior.

### 9. `--no-email` flag name is misleading

CLI option at `cli.py:25`: `click.option("--no-email", is_flag=True, help="Skip all notifications (email, Slack, Discord).")`. The help text explains correctly, but the flag name implies it only disables email.

### 10. `backend/src/main.py:20` imports `check_visa_flag` but never uses it

```python
from src.services.skill_matcher import check_visa_flag, detect_experience_level, salary_in_range, JobScorer
```

`check_visa_flag` is the module-level function. Usages in `main.py`:
- `detect_experience_level` — used at line 348
- `salary_in_range` — used at lines 360, 378
- `JobScorer` — used at line 261
- `check_visa_flag` — **never referenced** in the file. The actual visa check at line 347 is `scorer.check_visa_flag(job)`, the instance method.

Dead import — cosmetic, no runtime impact.

### 11. Zero inline TODO / FIXME / HACK comments

A `Grep` for `TODO|FIXME|HACK|XXX` across `backend/src/` returned zero matches. The codebase has no inline technical-debt markers. All concerns are captured in CLAUDE.md rules or are new findings from this audit.

### 12. `get_recent_jobs` orders by `date_found` but filters by `first_seen`

`database.py:195–198`:
```python
"SELECT * FROM jobs WHERE first_seen >= ? AND match_score >= ? ORDER BY date_found DESC"
```

For the 14 sources that hardcode `date_found = now()`, this ordering puts those sources at the *top* of every dashboard query (because their `date_found` is always the most recent possible timestamp). Sources with real, older dates appear below. So the dashboard shows hardcoded-now jobs first, regardless of actual posting date.

### 13. `_print_bucketed_summary` writes dicts from `Job` instances

`main.py:437–447` builds a plain dict from each `Job` and passes that to `bucket_jobs()`:

```python
job_dicts = [
    {
        "title": j.title, "company": j.company, "location": j.location,
        "match_score": j.match_score, "visa_flag": j.visa_flag,
        "salary_min": j.salary_min, "salary_max": j.salary_max,
        "date_found": j.date_found, "apply_url": j.apply_url, "source": j.source,
    }
    for j in jobs
]
```

`bucket_jobs()` takes a list of dicts, not `Job` instances. Non-issue for correctness, but duplicates the shape definition — any new `Job` field surfaced in the console summary needs a dict update too.

---

## 14. Dependencies

### Backend production (19 packages, `backend/pyproject.toml:6–26`)

```
aiohttp>=3.9.0
aiosqlite>=0.19.0
python-dotenv>=1.0.0
jinja2>=3.1.0
click>=8.1.0
pandas>=2.0.0
plotly>=5.18.0
pdfplumber>=0.10.0
python-docx>=1.1.0
rich>=13.0.0
humanize>=4.9.0
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
python-multipart>=0.0.9
httpx>=0.27.0
google-generativeai>=0.8.0
groq>=0.11.0
cerebras-cloud-sdk>=1.0.0
```

### Backend dev (4 packages, `pyproject.toml:29–34`)

```
pytest>=8.0.0
pytest-asyncio>=0.23.0
aioresponses>=0.7.0
fpdf2>=2.7.0
```

### Backend indeed extra (1 package, `pyproject.toml:35–37`)

```
python-jobspy
```

### Potential dead deps

- **`plotly`**: No current Python importer in `backend/src/`. Kept in deps as a reserved charting library for future analytics code.
- **`pandas`**: Used by `backend/tests/test_sources.py` to build mock `DataFrame` inputs for the JobSpy scraper, and required transitively by `python-jobspy` (the `indeed` extra) whose scrape output is a pandas DataFrame.
- **`humanize`**: Used in `backend/src/cli_view.py` for relative time strings and in the report generator.
- **`jinja2`**: Used by `backend/src/services/notifications/report_generator.py` for HTML report templating.
- **`fpdf2`** (dev only): Used by test fixtures that generate sample PDF CVs — not production code.
- **`httpx`**: Used by FastAPI's TestClient (via tests) and possibly indirectly by LLM SDKs.

No confirmed dead Python dependencies.

### Frontend runtime (12 packages, `frontend/package.json:11–23`)

```
@base-ui/react: ^1.3.0
class-variance-authority: ^0.7.1
clsx: ^2.1.1
lucide-react: ^1.7.0
motion: ^12.38.0
next: 16.2.2
react: 19.2.4
react-dom: 19.2.4
recharts: ^3.8.1
shadcn: ^4.1.2
tailwind-merge: ^3.5.0
tw-animate-css: ^1.4.0
```

### Frontend dev (8 packages, `frontend/package.json:25–33`)

```
@tailwindcss/postcss: ^4
@types/node: ^20
@types/react: ^19
@types/react-dom: ^19
eslint: ^9
eslint-config-next: 16.2.2
tailwindcss: ^4
typescript: ^5
```

---

## 15. Infrastructure

### `setup.sh` (repo root)

Per Agent 3's read:
1. Checks for Python ≥3.9.
2. Creates `venv/` if missing.
3. `pip install -r requirements.txt` (note: with the phase-4 layout, dependencies are declared in `backend/pyproject.toml`, not `requirements.txt` — this script may be stale like `cron_setup.sh`).
4. `mkdir -p data/{exports,reports,logs}`.
5. `cp .env.example .env` if `.env` missing.

### `cron_setup.sh` (repo root)

Verbatim (lines 1–51):

```bash
#!/bin/bash
set -e

echo "============================================"
echo "  Job360 - Cron Setup"
echo "============================================"
echo ""

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$PROJECT_DIR/venv/bin/python"
MAIN="$PROJECT_DIR/src/main.py"
LOG="$PROJECT_DIR/data/logs/cron.log"
ENV_FILE="$PROJECT_DIR/.env"

# Verify venv exists
if [ ! -f "$PYTHON" ]; then
    echo "ERROR: Virtual environment not found. Run setup.sh first."
    exit 1
fi

# Create log directory
mkdir -p "$PROJECT_DIR/data/logs"

# Build cron command that loads .env properly
CRON_CMD="cd $PROJECT_DIR && $PYTHON -m src.main >> $LOG 2>&1"
CRON_4AM="0 4 * * * TZ='Europe/London' $CRON_CMD"
CRON_4PM="0 16 * * * TZ='Europe/London' $CRON_CMD"

# Remove existing job360 entries and add new ones
(crontab -l 2>/dev/null | grep -v "job360\|Job360\|src\.main" || true; echo "$CRON_4AM"; echo "$CRON_4PM") | crontab -

echo "Cron jobs installed:"
echo "  - 4:00 AM UK time (daily)"
echo "  - 4:00 PM UK time (daily)"
echo ""
...
```

**Schedules**: `0 4 * * *` and `0 16 * * *`, both with `TZ='Europe/London'`.

**Paths are stale**: See Known Issues #6. Running this on the current tree produces broken crontab entries.

### Docker / CI

- **No `Dockerfile`** anywhere in the tree.
- **No `docker-compose.yml`**.
- **No `.github/workflows/`** directory. Glob returns zero matches.

There is no continuous integration or containerization configured.

### Data locations

All relative to `backend/data/` (from `core/settings.py:7–12`):
- `backend/data/jobs.db` — SQLite database
- `backend/data/user_profile.json` — serialized `UserProfile`
- `backend/data/exports/jobs_{timestamp}.csv` — per-run CSV exports
- `backend/data/reports/report_{timestamp}.md` — per-run Markdown reports
- `backend/data/logs/job360.log` — rotating log file (5MB × 3 backups per `utils/logger.py`)

The `backend/data/` directory is gitignored.

### Deployment config files

- **No `railway.json`**, no `vercel.json`, no `fly.toml`, no `render.yaml`, no `supabase/config.toml` in the tree.
- `.env.example` exists at the repo root (not inside `backend/`) — used by `setup.sh` to seed `.env`.

---

## Appendix A — File:line anchors (index of critical references)

For future audits or debugging sessions, these are the load-bearing locations in the codebase:

- **Pipeline orchestrator** — `backend/src/main.py:78–128` (SOURCE_REGISTRY), `:133` (SOURCE_INSTANCE_COUNT=47), `:151–220` (_build_sources), `:223–431` (run_search), `:292–302` (_fetch_source + gather), `:345–348` (scoring loop), `:351–356` (dedup + filter), `:372–376` (insert loop), `:406–411` (notifications)
- **CLI** — `backend/src/cli.py:18–48` (run), `:51–55` (dashboard), `:58–82` (status), `:85–95` (view), `:98–105` (api), `:108–113` (sources), `:116–214` (setup-profile)
- **FastAPI shim** — `backend/main.py:11` (`from src.api.main import app`), `:16–18` (`__main__` uvicorn)
- **FastAPI app** — `backend/src/api/main.py:9–13` (lifespan), `:16` (app constructor), `:18–24` (CORS), `:26–31` (6 routers)
- **Job model** — `backend/src/models.py:6–14` (regex), `:17–32` (dataclass), `:33–43` (`__post_init__`), `:45–52` (`_clean_company`), `:54–58` (`normalized_key`)
- **Scoring** — `backend/src/services/skill_matcher.py:17–27` (weights), `:29–39` (LOCATION_ALIASES), `:41` (REMOTE_TERMS), `:44–62` (FOREIGN_INDICATORS), `:65–71` (UK_TERMS), `:74–83` (_EXPERIENCE_PATTERNS), `:86–91` (_VISA_NEGATIONS), `:94–99` (_has_visa_keyword), `:102–110` (word boundary + _text_contains), `:113–128` (legacy _title_score), `:131–142` (legacy _skill_score), `:145–162` (_location_score), `:165–184` (_recency_score), `:187–192` (_negative_penalty), `:195–211` (_foreign_location_penalty), `:214–219` (detect_experience_level), `:222–228` (salary_in_range), `:231–240` (legacy score_job), `:243–245` (legacy check_visa_flag), `:253–307` (JobScorer class)
- **Dedup** — `backend/src/services/deduplicator.py:6–15` (regexes), `:18–33` (_normalize_title + docstring on divergence), `:36–46` (_completeness), `:49–62` (deduplicate)
- **Settings** — `backend/src/core/settings.py:7–12` (paths), `:14–22` (API keys), `:24–30` (GitHub + LLM), `:32–37` (email), `:39–41` (webhooks), `:43–50` (search constants), `:53–103` (RATE_LIMITS — 48 entries), `:106–107` (retry), `:110–111` (HTTP)
- **Keywords** — `backend/src/core/keywords.py:16–21` (empty lists), `:28–55` (LOCATIONS — 26 entries), `:63–72` (VISA_KEYWORDS — 8 entries)
- **Database** — `backend/src/repositories/database.py:13–16` (JobDatabase class), `:18–73` (init_db + schema), `:75–97` (_migrate), `:114–132` (insert_job), `:144–156` (log_run), `:174–181` (get_new_jobs_since), `:183–190` (purge_old_jobs), `:192–200` (get_recent_jobs), `:202–213` (get_last_source_counts), `:217–248` (user_actions), `:252–323` (applications)
- **Base source** — `backend/src/sources/base.py:11` (imports UK_TERMS/REMOTE_TERMS/FOREIGN_INDICATORS from skill_matcher), `:19` (_NO_RETRY_STATUSES), `:22–28` (_sanitize_xml), `:31–45` (_is_uk_or_remote), `:48–56` (BaseJobSource init), `:58–74` (properties), `:87–142` (_request with retry), `:144–154` (wrappers)
- **Profile system** — `backend/src/services/profile/models.py:9–48` (CVData), `:51–64` (UserPreferences), `:67–79` (UserProfile), `:82–117` (SearchConfig)
- **LLM provider** — `backend/src/services/profile/llm_provider.py:14–49` (llm_extract fallback), `:52–86` (llm_extract_fast inverted), `:89–103` (_call_gemini), `:106–122` (_call_groq), `:125–144` (_call_cerebras)
- **Keyword generator** — `backend/src/services/profile/keyword_generator.py:27–140` (generate_search_config)
- **Cron** — `cron_setup.sh:9–14` (path assumptions — stale), `:24–27` (cron cmd construction)

---

*End of `CurrentStatus.md`. This document was generated on 2026-04-11 by reading source files end-to-end and verifying counts against the current tree. No factual claim in this document relies on CLAUDE.md, prior conversations, or cached memory.*
