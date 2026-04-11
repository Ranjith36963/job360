# Streamlit Removal Impact Report — Job360

**Generated:** 2026-04-10
**Scope:** Complete removal of all Streamlit-related code, tests, dependencies, and documentation
**Method:** Read-only static analysis via `grep`, targeted `Read`, and cross-reference verification (same methodology as `DEADCODE.md`)
**Status:** Report only — no files modified.

---

## TL;DR

Streamlit removal is **safe and surgically clean**. The blast radius is tiny because the dashboard is a **pure downstream consumer** — it imports from the rest of the codebase, but **only one thing in the entire codebase imports from it**: `backend/tests/test_dashboard.py` importing `_safe_url`. That test file goes away with the dashboard.

**Totals:**
- **2 files deleted entirely** (844 lines): `backend/src/dashboard.py` (727) + `backend/tests/test_dashboard.py` (117)
- **3 files surgically edited** (~20 lines touched): `backend/src/cli.py`, `backend/src/main.py`, `backend/tests/test_cli.py`
- **3 dependencies removed**: `streamlit`, `pandas`, `plotly` → `backend/pyproject.toml` shrinks 18 → 15
- **0 impact on:** FastAPI backend, Next.js frontend, 48 job sources, scoring, storage, cron, or 397 of 407 tests
- **2 secondary dead functions surfaced** (judgment call): `parse_cv_from_bytes` and `parse_linkedin_zip_from_bytes`

---

## 1. Why This Is Safe — Dependency Topology

```
                     ┌─────────────────────────┐
                     │   backend/src/dashboard.py      │  ← The ONLY Streamlit importer
                     │   (727 lines)           │
                     └──┬──────────────────────┘
                        │ imports FROM (8 deps)
                        ▼
    src.config.settings, src.profile.storage, src.profile.models,
    src.profile.cv_parser, src.profile.preferences, src.utils.time_buckets,
    src.notifications.report_generator, src.models
                        ▲
                        │ (all of these are also used by CLI / API —
                        │  removing dashboard doesn't orphan them)

     WHO IMPORTS src.dashboard?
     ────────────────────────────
     backend/tests/test_dashboard.py       ← only consumer (imports _safe_url)
     (nothing else in the repo)
```

**Concretely verified:**

| Target | Consumers outside `dashboard.py` |
| --- | --- |
| `from src.dashboard import _safe_url` | `backend/tests/test_dashboard.py:85` only |
| `import streamlit` | `backend/src/dashboard.py:35` only |
| `import pandas` | `backend/src/dashboard.py:36` only |
| `import plotly.express` | `backend/src/dashboard.py:37` only |
| `from src.api.* import dashboard` | **zero matches** — FastAPI is fully independent |
| `from src.dashboard import *` (anywhere in `backend/src/`) | **zero matches** |

The FastAPI backend (`backend/src/api/`) has **zero references to "dashboard"** anywhere in its code. The cron script (`cron_setup.sh`) schedules `python -m src.main` and never mentions the dashboard. Next.js (`frontend/`) is pure TypeScript/React and has no Python dependency at all.

---

## 2. Files to DELETE Entirely

### 2.1 `backend/src/dashboard.py` — 727 lines

The entire Streamlit UI. Contains:
- `_safe_url()` — URL sanitization helper (only used inside this file + its test)
- Streamlit page config, sidebar, table rendering, chart rendering
- `_run_async()` — async-loop bridge for Streamlit's sync context
- All imports of `streamlit`, `pandas`, `plotly`
- Database reads, filtering UI, profile setup UI, CV upload handling
- Calls `parse_cv_from_bytes` and `parse_linkedin_zip_from_bytes` (see Section 7 for secondary impact)

**Previously flagged as partially dead in `DEADCODE.md`:**
- `backend/src/dashboard.py:39` — unused `EXPORTS_DIR` import
- `backend/src/dashboard.py:432` — unused `asyncio` import
- Those findings become moot — the whole file goes.

### 2.2 `backend/tests/test_dashboard.py` — 117 lines

Contains 6 tests, all exercising `_safe_url`:
- `test_safe_url_blocks_javascript` (line 88)
- `test_safe_url_blocks_data_uri` (line 93)
- `test_safe_url_allows_https` (line 97)
- `test_safe_url_allows_http` (line 103)
- `test_safe_url_handles_empty` (line 109)
- `test_safe_url_handles_relative` (line 114)

The file has a module-level Streamlit mock (lines 5-79) that injects a fake `streamlit` module into `sys.modules` so the `backend/src/dashboard.py` import statement doesn't explode during pytest. All this scaffolding disappears cleanly.

**Previously flagged as partially dead in `DEADCODE.md`:**
- `backend/tests/test_dashboard.py:3` — unused `PropertyMock` import (moot — file deleted).

**Test count delta:** `407 → 401` (6 tests removed).

---

## 3. Files to SURGICALLY EDIT

### 3.1 `backend/src/cli.py` (218 lines → ~208 lines)

Remove the `dashboard` CLI command + the `--dashboard` flag from the `run` command:

| Line | Current | Action |
| --- | --- | --- |
| 26 | `@click.option("--dashboard", is_flag=True, help="Launch Streamlit dashboard after the run.")` | **DELETE** |
| 27 | `def run(source, dry_run, log_level, db_path, no_email, dashboard):` | **EDIT** — remove `dashboard` parameter |
| 36 | `launch_dashboard=dashboard,` | **DELETE** (inside `run_search` call) |
| 43 | `click.echo("  python -m src.cli dashboard  # then use Profile sidebar")` | **EDIT** — update to reference frontend URL (e.g. `http://localhost:3000/profile`) or the API |
| 50-55 | Entire `@cli.command() def dashboard():` block that subprocess-runs `streamlit run backend/src/dashboard.py` | **DELETE** (6 lines) |
| 150 | `click.echo("No CV provided. You can add one later via the dashboard.")` | **EDIT** — change to "via the frontend" or "via the /api/profile endpoint" |

### 3.2 `backend/src/main.py` (476 lines → ~468 lines)

Remove the `launch_dashboard` parameter and the subprocess block:

| Line | Current | Action |
| --- | --- | --- |
| 229 | `launch_dashboard: bool = False,` (parameter in `run_search` signature) | **DELETE** |
| 247 | `logger.error("  python -m src.cli dashboard  # then use Profile sidebar")` | **EDIT** — update guidance |
| 426 | `# Launch dashboard if requested` | **DELETE** |
| 427 | `if launch_dashboard:` | **DELETE** |
| 428 | `logger.info("Launching Streamlit dashboard...")` | **DELETE** |
| 429 | `subprocess.Popen([sys.executable, "-m", "streamlit", "run", "backend/src/dashboard.py"])` | **DELETE** |
| 474 | `parser.add_argument("--dashboard", action="store_true", help="Launch dashboard after run")` | **DELETE** |
| 476 | `asyncio.run(run_search(no_notify=args.no_email, launch_dashboard=args.dashboard))` | **EDIT** — remove `launch_dashboard=args.dashboard` |

Also: `import subprocess` / `import sys` at the top of `main.py` may become unused once this block is removed. Verify with `ruff check --select F401 backend/src/main.py` after editing.

### 3.3 `backend/tests/test_cli.py` (119 lines → ~115 lines)

Three test assertions reference the dashboard CLI surface:

| Line | Current | Action |
| --- | --- | --- |
| 15 | `assert "dashboard" in result.output` (in the `test_cli_help` or equivalent test checking that CLI help lists commands) | **DELETE** |
| 65 | `"""run --help should show --no-email and --dashboard flags."""` (test docstring) | **EDIT** — remove `--dashboard` |
| 69 | `assert "--dashboard" in result.output` | **DELETE** |

Also: the `SOURCE_REGISTRY` count assertion (`len(SOURCE_REGISTRY) == 48` at `backend/tests/test_cli.py:47`) is **unaffected** — source registry isn't touched.

---

## 4. Cosmetic String / Docstring Updates (No Behavior Change)

These don't affect correctness but will look wrong after removal. Worth updating in the same commit.

### 4.1 Docstrings that mention Streamlit

| File:Line | Current docstring | Suggested edit |
| --- | --- | --- |
| `backend/src/profile/cv_parser.py:161` | `"""Synchronous wrapper for parse_cv_async (used by CLI and Streamlit)."""` | Remove "and Streamlit" |
| `backend/src/profile/cv_parser.py:298` | `"""Parse CV from in-memory bytes (for Streamlit file_uploader)."""` | Change to "for HTTP file upload" or delete function entirely (see Section 7) |
| `backend/src/profile/linkedin_parser.py:130` | `"""Parse from in-memory bytes (for Streamlit file_uploader)."""` | Change to "for HTTP file upload" |
| `backend/src/utils/time_buckets.py:1` | `"""Shared time-bucketing utilities for dashboard, CLI, and email views."""` | Change to "for CLI and email views" |

### 4.2 User-facing strings that mention "dashboard"

| File:Line | Current string | Suggested edit |
| --- | --- | --- |
| `backend/src/cli.py:150` | `"No CV provided. You can add one later via the dashboard."` | "… via the frontend (`http://localhost:3000/profile`) or the `/api/profile` endpoint." |
| `backend/src/notifications/slack_notify.py:62` | `"_...and {X} more jobs. Check email or dashboard for full list._"` | Change "dashboard" → "frontend" |
| `backend/src/notifications/discord_notify.py:39` | Same text as Slack | Same edit |
| `backend/src/main.py:247` | Log error mentioning `python -m src.cli dashboard` | Update guidance string |

---

## 5. Dependency Removal — `backend/pyproject.toml`

Current file (lines 1-19):

```
aiohttp>=3.9.0
aiosqlite>=0.19.0
python-dotenv>=1.0.0
jinja2>=3.1.0
click>=8.1.0
streamlit>=1.30.0       ← DELETE
pandas>=2.0.0           ← DELETE
plotly>=5.18.0          ← DELETE
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

**After removal:** 18 packages → **15 packages**

### Why each is safe to remove

- **`streamlit>=1.30.0`** — imported only at `backend/src/dashboard.py:35`. Zero other references.
- **`pandas>=2.0.0`** — imported only at `backend/src/dashboard.py:36`. `grep ^import pandas` and `^from pandas` across the repo returned **zero matches** outside dashboard. Critical: the FastAPI routes do NOT use pandas for response construction, and no test imports pandas. Safe to drop.
- **`plotly>=5.18.0`** — imported only at `backend/src/dashboard.py:37` (`import plotly.express as px`). Zero other references.

### Verification command (for when you do the removal)

```bash
ruff check --select F401 backend/src/ backend/tests/        # should show no pandas/plotly/streamlit imports
grep -rn "import pandas\|import plotly\|import streamlit" backend/src/ backend/tests/  # should return nothing
```

---

## 6. Setup Script Update — `setup.sh`

| Line | Current | Action |
| --- | --- | --- |
| 71 | `echo "    4. Dashboard: python -m src.cli dashboard"` | **DELETE** this line (post-install instructions tell users to launch the dashboard) |

`cron_setup.sh` is **not affected** — it only schedules `python -m src.main` and has zero dashboard mentions.

---

## 7. Secondary Dead Code — JUDGMENT CALL

Removing the dashboard orphans **2 functions** that currently exist only to service its file-upload UI:

### 7.1 `backend/src/profile/cv_parser.py:297 — parse_cv_from_bytes`

- **Production consumers:** `backend/src/dashboard.py:409` only
- **Test consumers:** **zero**
- **Verdict:** **fully dead after dashboard removal.** Safe to delete the function body (lines 297-313).
- Leave `parse_cv` (line 160, the sync wrapper) — it's used by `backend/src/cli.py:138`.
- Leave `parse_cv_async` (the real implementation) — it's used by `backend/src/api/routes/profile.py:12`.

### 7.2 `backend/src/profile/linkedin_parser.py:129 — parse_linkedin_zip_from_bytes`

- **Production consumers:** `backend/src/dashboard.py:421` only
- **Test consumers:** **`backend/tests/test_linkedin_github.py`** — 8 test references (lines 16, 171, 186, 198, 618, 626, 645, 794, 804)
- **Verdict:** **design decision needed.**
  - **Option A:** Delete the function AND those 8 tests — cleanest, but loses the bytes-based test coverage
  - **Option B:** Keep the function as a standalone utility — the tests keep passing, and it's a legitimate "parse from bytes" API even without a Streamlit file_uploader
  - The file-path variant `parse_linkedin_zip` (line 120) stays regardless — it's used by `backend/src/api/routes/profile.py:14` and `backend/src/cli.py:154`
- **Recommendation:** Option B. The tests justify keeping it, and the function is generic enough to serve a future frontend or API upload path.

### 7.3 Why these matter

If you don't clean these up along with the dashboard removal, `vulture` on the next `DEADCODE.md` run will flag `parse_cv_from_bytes` as dead. Batching this cleanup into the same commit keeps the codebase internally consistent.

---

## 8. Documentation Sync Required

Four main `.md` files reference the Streamlit dashboard. **All references need updating**, not just deleting — because the frontend replaces the dashboard, many sentences just need "Streamlit dashboard" → "Next.js frontend" substitution.

### 8.1 `CLAUDE.md` — 11+ references

| Line(s) | Content | Action |
| --- | --- | --- |
| 7 | "...delivers results via CLI, email, Slack, Discord, CSV, and a Streamlit dashboard" | Replace "Streamlit dashboard" → "Next.js frontend" |
| 18-20 | Dependency table rows for streamlit / pandas / plotly | **DELETE** all 3 rows |
| 49 | `python -m src.cli run --dashboard   # Launch dashboard after` | **DELETE** line |
| 58 | `python -m src.cli dashboard    # Launch Streamlit UI` | **DELETE** line |
| 70 | `python -m pytest backend/tests/test_dashboard.py -v             # Dashboard helpers (6)` | **DELETE** line |
| 80 | `│   ├── cli.py               # Click CLI: run, api, dashboard, status, sources, view, setup-profile` | Remove "dashboard," from comment |
| 87 | `│   ├── dashboard.py         # Streamlit web dashboard with profile setup sidebar` | **DELETE** line |
| 143 | "Click CLI with `run`, `dashboard`, `status`, `sources`, `view`, `setup-profile` commands" | Remove "`dashboard`, " |
| 204 | `test_dashboard.py — 6 tests: URL sanitization (_safe_url) for XSS prevention` | **DELETE** line |
| misc | Test count "407" → **401** once dashboard tests removed | Update |

### 8.2 `README.md` — 15+ references

| Line(s) | Content | Action |
| --- | --- | --- |
| 3 | "...CLI, email, Slack, Discord, CSV, Rich terminal table, and a Streamlit dashboard" | Replace with "Next.js frontend" |
| 9 | Mermaid diagram: `CLI (Click)\njob360 run / view / dashboard / status / sources / setup-profile` | Remove "dashboard /" |
| 90 | Mermaid diagram: `DB --> Dashboard[Streamlit Dashboard]` | **DELETE** edge + node |
| 133 | "`run` — full pipeline with ... `--dashboard` options" | Remove "`--dashboard`" |
| 136 | "`dashboard` — launch Streamlit web UI" | **DELETE** line |
| 140 | Section header "### Dashboard (Streamlit)" plus whole section below | **DELETE** entire section |
| 213-214 | `# 7. Launch dashboard` / `python -m src.cli dashboard` | **DELETE** both lines |
| 236-237 | `# Launch dashboard after pipeline completes` / `python -m src.cli run --dashboard` | **DELETE** both lines |
| 260-261 | `# Launch Streamlit dashboard` / `python -m src.cli dashboard` | **DELETE** both lines |
| 365 | `│   ├── cli.py                   # Click CLI (run, view, dashboard, status, sources, setup-profile)` | Remove "dashboard, " |
| 368 | `│   ├── dashboard.py             # Streamlit web dashboard (filters, charts, KPIs, profile setup)` | **DELETE** line |
| 438 | Access point table row: `| Dashboard | http://localhost:8501 | Interactive Streamlit UI |` | **DELETE** row |

### 8.3 `STATUS.md` — 6+ references

| Line | Content | Action |
| --- | --- | --- |
| 29 | `Dashboard Profile UI | backend/src/dashboard.py | Done -- sidebar expander with CV upload + form` | **DELETE** row |
| 83 | `DB error logging | backend/src/cli_view.py, backend/src/dashboard.py | ...` | Change to just `backend/src/cli_view.py` |
| 115 | `- CLI commands: run, view, dashboard, status, sources, setup-profile` | Remove "dashboard, " |
| 116 | `- Streamlit dashboard with filters, charts, profile setup` | **DELETE** line |
| 176 | `- backend/src/dashboard.py — Streamlit UI is not unit-tested (would need Streamlit testing framework)` | **DELETE** line |
| 179 | `- Profile dashboard sidebar — interactive Streamlit profile form is not tested` | **DELETE** line |

### 8.4 `ARCHITECTURE.md` — 6+ references

| Line | Content | Action |
| --- | --- | --- |
| 11 | Diagram: `CLI / Dashboard --+       |   (async fetch)  |` | Change to `CLI / Frontend` |
| 21 | Diagram: `Email     Slack    Discord     CSV     Dashboard` | Remove "Dashboard" |
| 32 | `│   +-- cli.py               # Click CLI: run, dashboard, status, sources, view, setup-profile` | Remove "dashboard, " |
| 34 | `│   +-- dashboard.py         # Streamlit web UI with profile setup sidebar` | **DELETE** line |
| 601-603 | Dependency table rows for streamlit / pandas / plotly | **DELETE** all 3 rows |

### 8.5 `DEADCODE.md` — auto-invalidated entries

The dead-code report I committed in `2c7c7b5` contains line references that become meaningless when `dashboard.py` is deleted:

| `DEADCODE.md` Line | Reference | Fate |
| --- | --- | --- |
| 91 | `backend/src/dashboard.py:39` — `EXPORTS_DIR` (unused import) | Moot — file deleted |
| 92 | `backend/src/dashboard.py:432` — `asyncio` (unused import) | Moot — file deleted |
| 106 | `backend/tests/test_dashboard.py:3` — `PropertyMock` (unused import) | Moot — file deleted |
| 264 | `backend/src/dashboard.py:230` — `row_factory` (false positive) | Moot — file deleted |
| 364 | CLI commands list includes "`dashboard`" | Update to remove dashboard |

Consider this either (a) a reason to regenerate `DEADCODE.md` after the Streamlit removal, or (b) acceptable historical debt — the commit message documents what it represented at the time.

### 8.6 Plan documents (`docs/superpowers/plans/*.md`)

Found 2 references:
- `docs/superpowers/plans/2026-04-09-llm-cv-parser.md` — mentions dashboard
- `docs/superpowers/plans/2026-04-07-fastapi-backend.md` — mentions dashboard

**Recommendation:** leave these. Plan docs are historical artifacts that describe what was true at the time they were written. Touching them rewrites history.

---

## 9. What Does NOT Break — Explicit Independence Proof

This section exists so you can sleep at night. For each subsystem, I verified (via grep) that it has zero coupling to the dashboard.

### 9.1 FastAPI backend (`backend/src/api/`) — ✅ Fully independent

- `grep -rn "dashboard" backend/src/api/` → **0 matches**
- `grep -rn "streamlit" backend/src/api/` → **0 matches**
- API routes in `backend/src/api/routes/profile.py` use `parse_cv_async`, `parse_linkedin_zip`, `enrich_cv_from_linkedin` — **not** the `_from_bytes` variants
- The API handles file uploads via `backend/src/api/dependencies.py::save_upload_to_temp` which saves to a tempfile path — then passes the path to the file-path-based parsers. No dashboard code path involved.

### 9.2 Next.js frontend (`frontend/`) — ✅ Fully independent

- Pure TypeScript/React. Zero Python dependency.
- Communicates with the FastAPI backend over HTTP.
- Deleting the Streamlit dashboard has zero impact on the frontend's build, routing, or runtime.

### 9.3 All 48 job sources (`backend/src/sources/`) — ✅ Untouched

- `grep -rn "dashboard\|streamlit" backend/src/sources/` → **0 matches**
- Sources extend `BaseJobSource`, which knows nothing about rendering.

### 9.4 Scoring (`backend/src/filters/`) — ✅ Untouched

- `JobScorer` and `score_job` are pure functions over `Job` objects and `SearchConfig`.
- Zero rendering coupling.

### 9.5 Storage (`backend/src/storage/`) — ✅ Untouched

- `database.py` and `csv_export.py` know nothing about Streamlit.
- SQL queries are consumed identically by CLI, API, and dashboard — only the first two remain.

### 9.6 Notifications (`backend/src/notifications/`) — ✅ Untouched (2 cosmetic string updates)

- No imports of streamlit/dashboard.
- Only the literal text `"Check email or dashboard for full list."` in Slack and Discord message templates references the dashboard — cosmetic only (see Section 4.2).

### 9.7 Cron scheduling (`cron_setup.sh`) — ✅ Fully independent

- Entire file is 51 lines and schedules `python -m src.main` twice daily (4AM/4PM London).
- Zero dashboard or streamlit references.

### 9.8 Pipeline orchestrator (`backend/src/main.py::run_search`) — ✅ Mostly untouched

- The pipeline itself (fetching, scoring, deduplication, storage, notifications) is independent.
- Only the **final** step — an optional `launch_dashboard` branch — touches Streamlit. Ripping out that branch (Section 3.2) leaves the pipeline fully functional.

### 9.9 Test suite — 401/407 tests unaffected

- 6 tests in `backend/tests/test_dashboard.py` — deleted with the file
- ~3 assertions in `backend/tests/test_cli.py` — updated (file stays)
- All other 397 tests: **zero changes**
- `backend/tests/test_linkedin_github.py` 8 tests depend on `parse_linkedin_zip_from_bytes` — see Section 7.2 judgment call (recommended: keep)

---

## 10. Recommended Execution Order

When you're ready to execute the removal, this order minimizes the chance of a broken-intermediate-state commit:

1. **Doc-only commit** — update `CLAUDE.md`, `README.md`, `STATUS.md`, `ARCHITECTURE.md` (Sections 8.1-8.4). Safe to ship first because docs don't affect runtime.
2. **Cosmetic strings commit** — update the 4 user-facing strings in Section 4.2 and the 4 docstrings in Section 4.1.
3. **Core removal commit** — delete `backend/src/dashboard.py`, delete `backend/tests/test_dashboard.py`, edit `backend/src/cli.py`, edit `backend/src/main.py`, edit `backend/tests/test_cli.py`, edit `setup.sh`, edit `backend/pyproject.toml`. This is the big one.
4. **Secondary dead code commit** — delete `parse_cv_from_bytes` from `backend/src/profile/cv_parser.py` (Section 7.1). Keep `parse_linkedin_zip_from_bytes` (Section 7.2).
5. **Verification commit (if needed)** — run `ruff check --fix --select F401 backend/src/ backend/tests/` to catch any newly-orphaned imports (e.g. `subprocess`, `sys` at the top of `main.py`). Commit the fix if ruff touches anything.

After each commit, run:

```bash
python -m pytest backend/tests/ -v
python -m src.cli --help                # verify CLI still works
python -m src.cli run --dry-run         # verify pipeline still works
python -m src.cli api                   # verify API still starts
```

---

## 11. Scoreboard — Net Impact

| Metric | Before | After | Delta |
| --- | --- | --- | --- |
| Python files in `backend/src/` | 48 (in `backend/src/sources/`) + X elsewhere | same − 1 | `-1 file` |
| Test files (`backend/tests/test_*.py`) | 21 | 20 | `-1 file` |
| Test count (pytest collected) | 407 | 401 | `-6 tests` |
| `backend/pyproject.toml` packages | 18 | 15 | `-3 packages` |
| Lines deleted | — | — | `~870 lines` |
| Lines edited (non-delete) | — | — | `~40 lines` |
| Orphan functions cleaned up | — | — | 1 (`parse_cv_from_bytes`); 1 judgment call (`parse_linkedin_zip_from_bytes`) |
| Docs with stale references | 4 | 0 | `~35+ line-level edits` |
| CLI commands | 7 | 6 (removes `dashboard`) | `-1 command` |
| CLI flags on `run` | 6 | 5 (removes `--dashboard`) | `-1 flag` |
| FastAPI routes affected | 0 | 0 | **none** |
| Frontend code affected | 0 | 0 | **none** |
| Source plugins affected | 0 / 48 | 0 / 48 | **none** |
| Cron affected | no | no | **none** |

---

## Appendix A — Verification Commands Used

These are the exact read-only commands that produced this report. Re-running them should reproduce identical findings (modulo any uncommitted changes).

```bash
# 1. Find every file that mentions streamlit or dashboard
grep -rn "streamlit" backend/src/ backend/tests/ --include="*.py"
grep -rn "dashboard" backend/src/ backend/tests/ --include="*.py"

# 2. Find every actual Python import of streamlit
grep -rn "^import streamlit\|^from streamlit" backend/src/ backend/tests/ --include="*.py"

# 3. Find who imports FROM src.dashboard
grep -rn "from src\.dashboard\|from \.dashboard\|import dashboard" backend/src/ backend/tests/ --include="*.py"

# 4. Find every file that imports pandas or plotly
grep -rn "^import pandas\|^from pandas\|^import plotly\|^from plotly" backend/src/ backend/tests/ --include="*.py"

# 5. Find every use of _safe_url (the only symbol backend/tests/test_dashboard.py imports)
grep -rn "_safe_url" backend/src/ backend/tests/ --include="*.py"

# 6. Find parse_cv_from_bytes and parse_linkedin_zip_from_bytes consumers
grep -rn "parse_cv_from_bytes\|parse_linkedin_zip_from_bytes" backend/src/ backend/tests/ --include="*.py"

# 7. Check FastAPI backend independence
grep -rn "dashboard\|streamlit" backend/src/api/

# 8. Check shell scripts
grep -rn "dashboard\|streamlit" *.sh

# 9. Check docs
grep -rni "dashboard\|streamlit" *.md
```

## Appendix B — Cross-Check Verification Ledger

I ran each question-to-answer pair multiple times against different grep queries to guard against false negatives. This table summarizes the corroboration:

| Claim | Verification method 1 | Verification method 2 | Status |
| --- | --- | --- | --- |
| Only `backend/src/dashboard.py` imports streamlit | `grep "^import streamlit" **/*.py` → 1 match | `grep "streamlit" backend/src/ -l` (file-list) → only dashboard.py | ✅ Corroborated |
| `pandas` is dashboard-only | `grep "^import pandas" **/*.py` → 1 match | `grep "pd\." backend/src/` outside dashboard → 0 matches | ✅ Corroborated |
| `plotly` is dashboard-only | `grep "^import plotly" **/*.py` → 1 match | `grep "px\." backend/src/` outside dashboard → 0 matches | ✅ Corroborated |
| `_safe_url` is dashboard-only | Grep for `_safe_url` across `**/*.py` → 2 files (src + test) | Grep in `backend/src/api/` for `_safe_url` → 0 matches | ✅ Corroborated |
| FastAPI has no dashboard coupling | `grep "dashboard" backend/src/api/` → 0 matches | `grep "streamlit" backend/src/api/` → 0 matches | ✅ Corroborated |
| Cron does not reference dashboard | Full `Read` of `cron_setup.sh` (51 lines) | `grep "dashboard\|streamlit" cron_setup.sh` → 0 matches | ✅ Corroborated |
| `parse_cv_from_bytes` is dashboard-only | Grep for function name → `backend/src/dashboard.py:409` + definition | Checked FastAPI routes for its usage → not imported | ✅ Corroborated |
| `parse_linkedin_zip_from_bytes` is dashboard + tests | Grep → `backend/src/dashboard.py:421` + 8 test matches | Checked FastAPI (`backend/src/api/routes/profile.py`) → uses `parse_linkedin_zip` (file-path variant) instead | ✅ Corroborated |
| 3 imports in `cv_parser.py` / `linkedin_parser.py` docstrings mention Streamlit (not code) | Grep for `streamlit` in backend/src/profile/ → 3 matches | Read each line to confirm they're inside `"""..."""` | ✅ Corroborated |
| `time_buckets.py` mentions dashboard only in its docstring | Grep for `dashboard` in time_buckets.py → 1 match at line 1 | Read line 1 to confirm it's a docstring | ✅ Corroborated |

---

## Final Verdict

**Streamlit removal is recommended and safe.** The blast radius is tightly contained, the FastAPI backend and Next.js frontend are fully independent, no production code path outside `dashboard.py` depends on `streamlit`, `pandas`, or `plotly`, and the only cross-module import is a test file that disappears with the dashboard.

**No runtime risk. No frontend risk. No backend risk. No cron risk. No data loss risk.**

The 870-line reduction is almost pure subtraction — the equivalent of removing an unused branch from a tree whose trunk and other limbs are untouched.
