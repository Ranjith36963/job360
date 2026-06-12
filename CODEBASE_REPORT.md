# Job360 Discovery Audit — CODEBASE_REPORT

Generated 2026-06-11 (~06:00 UTC+1) by the in-session auditor (Fable orchestrator + 3 Sonnet investigators). Read-only: no code was changed. All command outputs are real and were executed during this audit unless marked otherwise.

---

## 1. Repository map

### Raw git state

```
$ git worktree list
D:/dev/job360                              b8322d8 [fix/per-user-search-and-scoring-gate]
D:/dev/job360/.claude/worktrees/generator  8b005ae [worktree-generator]
D:/dev/job360/.claude/worktrees/reviewer   d5047bf [worktree-reviewer]

$ git branch -a (local)
* fix/per-user-search-and-scoring-gate
  main
  step-3-batch
+ worktree-generator
+ worktree-reviewer
(remotes: origin/main, origin/fix/per-user-search-and-scoring-gate, origin/step-{1,1-5,2,3}-batch,
 origin/c02-notif-rules, origin/c03-account, origin/c07-kanban,
 origin/claude/job-logistics-pillars-docs-H9zcw, plus 11 stale origin/worktree-agent-* branches)

$ git log --oneline -15
b8322d8 chore(maintenance): adopt the owner-provided autonomous-round system prompt
08830a1 chore(maintenance): journal iteration 3 — jobtensor quarantined
b7b2c60 fix(sources): quarantine jobtensor — upstream removed the AJAX API
a8ed8f7 chore(maintenance): journal iteration 2 — jobicy 400 fixed (tag param)
e054ec7 fix(sources): jobicy 400 — drop sub-3-char 'tag' param the API now rejects
e432773 chore(maintenance): journal iteration 1 — judge-ranking sort fix DONE
6974bb6 fix(dashboard): client sort honors the LLM judge ranking (llm_fit_score ?? match_score)
0479369 chore(maintenance): journal live-verify results + new P1: dashboard sort defeats judge ranking
0a0777d chore(maintenance): /maintain skill + seeded backlog/journal + matcher build plan
d801f78 feat(frontend): AI verdict badge on job cards
c2c9035 feat(api): expose per-user LLM verdict on the jobs response
d999017 feat(pipeline): run LLM judge on the per-user shortlist after feed write
76f6ca7 fix(matcher): drop module-level union alias that breaks Python 3.9 import
fc072e8 feat(matcher): LLM judge service -- per-user fit verdicts on user_feed
741825a feat(db): rank user feed by COALESCE(llm_fit_score, score) + surface verdict columns

$ git status --short
 M backend/src/main.py
 M backend/src/services/channels/dispatcher.py
 M backend/tests/test_channels_dispatcher.py
?? loop.md
```

The 3 modified files are an UNCOMMITTED HUMAN/OTHER-SESSION work-in-progress: a friendlier channel-delivery error string in `dispatcher.py` (+ matching test edit) and a `user_id=user_id` kwarg into the `run_log` write in `main.py` (the `run_log.user_id` column already exists via migration 0010 — this WIP wires the value). The autonomous loop's rule 5 treats this as a dirty tree; backend rounds currently work around it only when file ownership is disjoint. **A human should commit or discard these 3 files.**

### Worktrees

| Worktree | Branch | Last commit | Status | Role |
|---|---|---|---|---|
| `D:/dev/job360` (main checkout) | `fix/per-user-search-and-scoring-gate` | b8322d8, 2026-06-11 | ACTIVE | The working repo; also where the maintenance loop runs |
| `.claude/worktrees/generator` | `worktree-generator` | 8b005ae, 2026-05-03 ("fix(observability/review-2): residual nits") | **STALE** (~5 weeks) | Past batch "generator" role (writes batches per `templates/v1/generator-commit.md`) |
| `.claude/worktrees/reviewer` | `worktree-reviewer` | d5047bf, 2026-04-27 ("wip(reviewer): pre-relocation snapshot of audit reports") | **STALE** (~6 weeks) | Past batch "reviewer" role (produces verdicts per `templates/v1/reviewer-verdict.md`) |

Both are FULL repo checkouts (not sparse), with pre-Step-3-era copies of CLAUDE.md. They embody a previous generator/reviewer two-agent workflow that has since been superseded by the in-session subagent flow. The 11 `origin/worktree-agent-*` remote branches are leftovers of the same era.

### Top-level tree (2 levels, annotated)

```
job360/
├── .claude/            Claude Code config: settings, 5 skills, hooks, templates/v1, 2 stale worktrees, step-N-verified.txt stamps
├── .playwright-mcp/    Playwright MCP browser state + page snapshots
├── backend/            Python 3.13 venv FastAPI backend (deliverable #1)
│   ├── data/           RUNTIME (gitignored): jobs.db (WAL), logs/, exports/, reports/, chroma/
│   ├── migrations/     18 fwd+rev SQL pairs (0000–0017) + runner.py
│   ├── ops/            operational scripts/configs
│   ├── scripts/        analysis harnesses (enrichment comparison + accuracy scorer)
│   ├── src/            api/ core/ services/ repositories/ sources/ workers/ utils/
│   └── tests/          1,287 collected tests
├── data/chroma/        SECOND ChromaDB dir at repo root (see Problems #9 — likely stray duplicate of backend/data/chroma)
├── docs/               maintenance/ (loop memory), pillars/, plans/, research/, reviews/, superpowers/, _archive/, IMPLEMENTATION_LOG.md
├── frontend/           Next.js 16 + React 19 + Tailwind 4 (deliverable #2); src/app|components|lib, tests/
├── scripts/            root-level scripts (batch review tooling)
├── test-artifacts/     sample_cv.pdf + verification screenshots
├── User_info/          REAL personal data (gitignored): CV/, Linkedin_pdf/, github_url — test inputs only
├── ARCHITECTURE.md  CLAUDE.md  CONTRIBUTING.md  DEADCODE.md  Makefile  README.md  STATUS.md
└── loop.md  joproviderlayerReport.md  planning_report.md   (owner notes/reports, mostly untracked)
```

---

## 2. Claude Code configuration inventory

### `.claude/settings.json` (project, checked in)

```json
{
  "permissions": {
    "deny": [
      "mcp__plugin_github_github__*", "mcp__claude_ai_Apollo_io__*",
      "mcp__claude_ai_Vibe_Prospecting__*", "mcp__claude_ai_Apify__*",
      "mcp__claude_ai_Excalidraw__*", "mcp__claude_ai_Indeed__*",
      "mcp__claude_ai_Gmail__*", "mcp__claude_ai_Google_Calendar__*",
      "mcp__claude_ai_Google_Drive__*", "mcp__claude_ai_Slack__*",
      "mcp__claude_ai_Supabase__*"
    ]
  }
}
```

No secrets. Denies 11 external MCP namespaces (mail/calendar/drive/Slack/etc.) — a sensible blast-radius limiter for autonomous operation.

### `.claude/settings.local.json` (local, gitignored)

~150 accumulated `Bash(...)` allow patterns (python/pip/pytest/git/npm/npx/gh/sqlite3/ruff/taskkill/...), `enabledMcpjsonServers: [github, redis, sentry]`, `disabledMcpjsonServers: [context7, sqlite]`, `outputStyle: "Explanatory"`, and ONE hook (below). No secrets.

### Commands, agents

- `.claude/commands/` — **does not exist** (this repo uses skills, not commands).
- `.claude/agents/` — **does not exist** (subagents are dispatched inline via the Agent tool with model overrides).

### Skills (`.claude/skills/`)

| Skill | Summary |
|---|---|
| `debug` | CRIIVP debugging methodology (Capture→Reproduce→Isolate→Implement→Verify→Prevent) |
| `implement` | Explore → Plan → Code → Commit feature workflow |
| `maintain` | **The autonomous loop's system prompt** (full text below) |
| `sync` | Code ↔ documentation sync in 4 steps |
| `verify-job360` | Run-the-real-app verification (browser + curl + SQLite + logs), 3 flavors, hard-won gotchas list |

The full text of `maintain/SKILL.md` is the owner-provided "Job360 Autonomous Round — System Prompt" (adopted verbatim at commit b8322d8): one item per round; never push; `/verify-job360` PASS required before any commit; never weaken tests; abort on dirty tree; NEEDS-HUMAN for credentials/config/migrations; Sonnet executor test-first with Fable as reviewer; max 3 fix attempts then revert; `skipped:` aging; evidence-verbatim journaling; commit format `loop: <item> — <what> [verified: tests + /verify-job360]`. Plus a Job360 context block (test account, server commands, known environment quirks).

`verify-job360` defines frontend (Playwright drive + screenshot + console errors), backend (curl routes + SQLite queries + log grep), and E2E (register→CV→search→jobs) procedures, and a 10-item gotchas list (stale DB schema crash, aiosqlite lock-holding, SESSION_SECRET/CHANNEL_ENCRYPTION_KEY required for auth, web-vs-pipeline profile mismatch, editable-install worktree import resolution, Base UI not Radix, dark-only theme, Apply-opens-new-tab Playwright trap, screenshots land at repo root, test_main.py hits live Indeed).

### Hooks

Exactly ONE hook is configured (settings.local.json):

| Event | Command | Behavior |
|---|---|---|
| `Stop` | `bash .claude/hooks/verify-reminder.sh` (10s timeout) | Checks `git status --porcelain -- backend/src frontend/src` for modified .py/.ts/.tsx/.js/.jsx; if any, prints a systemMessage: "⚠️ App code changed but not verified yet. Run /verify-job360…". **Non-blocking by design** (comment notes a blocking hook would loop forever). |

### Templates, stamps, locks

- `templates/v1/generator-commit.md` + `reviewer-verdict.md` — machine-parseable batch-commit and review-verdict formats from the older two-worktree workflow (still referenced by `scripts/review_batch.sh` / `make verify-batch`).
- `step-{1,1-5,2,3}-verified.txt` — one commit SHA each, stamping when each step was verified.
- `scheduled_tasks.lock` — session-scheduler lock (sessionId + pid + timestamp).

### CLAUDE.md / MEMORY.md summary

Root `CLAUDE.md` is the source of truth: 27 numbered hard rules grouped as schema+data integrity (#1,3,10,17), sources (#2,8,13,14,15), heavy lazy imports (#11,16), auth/IDOR (#12,25,26), scoring+flags (#9,18,19,20,27), notifications (#23,24), process (#4,5,6,7,21,22); canonical test command; 50-source registry facts (49 classes; indeed+glassdoor alias JobSpySource); phase history (Phases 1–2.5, Batch 2, Batch 3, Pillar 2, Step 3 + carry-overs V-01..V-05, C-07). `backend/CLAUDE.md` is a thin pointer (+ test-infra gotchas: conftest mocks asyncio.sleep, DB_PATH import-time binding). `frontend/AGENTS.md`: "This is NOT the Next.js you know" — read bundled v16 docs first. No `MEMORY.md` in-repo (assistant memory lives outside the repo, user-private).

**Stale doc figures detected:** CLAUDE.md says "1,154 passing"; backend/CLAUDE.md says "~1,256"; live count is **1,287 collected / 1,284 passing** (see §6).

---

## 3. The current loop

### Trigger

- **In-session cron job** `c96d6940`: cron `11 */2 * * *` (every 2 hours at :11), prompt `/maintain`, recurring, **session-only** — it dies with the Claude Code session and auto-expires after 7 days. There is NO OS-level timer, NO CI trigger, NO headless `claude -p` script. Manual `/maintain` invocations also work and have been used.
- Loop ran 3 productive rounds so far (commits 6974bb6, e054ec7, b7b2c60 + journal commits).

### Lock mechanism

`docs/maintenance/.lock` — a timestamp file. A round exits if the lock exists and is <3h old; writes it otherwise; deletes it at the end. It is advisory (a crashed process leaves it; the 3h staleness window self-heals). The lock is currently absent (last round released it).

### Memory

- `docs/maintenance/BACKLOG.md` — full current text:

(verbatim — current state, 3 DONE / 15 TODO)

```
# Job360 Maintenance Backlog
Statuses: TODO / DOING / DONE (sha) / BLOCKED(reason) / NEEDS-HUMAN / DEAD.
Aging: every round increments `skipped: N` on items it passes over; at `skipped: 3` an item
must be taken or escalated to NEEDS-HUMAN with justification.

P1 — bugs and broken behavior (live evidence)
0. DONE (6974bb6) — dashboard client sort defeats the judge's ranking. [fixed + live-verified]
1. DONE (e054ec7) — jobicy source broken: HTTP 400. [tag param; live-proven 6 jobs]
2. DONE (b7b2c60) — jobtensor source broken: HTTP 400. [upstream dead; quarantined]
3. TODO — comeet ATS slugs dead: HTTP 400 (riskified, lightricks) → prune/replace in core/companies.py
4. TODO — gov_apprenticeships returns non-JSON → diagnose, fix or skip gracefully
5. TODO — aijobs_global returns non-JSON → diagnose, fix or downgrade
6. TODO — JobSpy/Glassdoor 400 "location not parsed" every run → fix location format or stop querying glassdoor
7. TODO — enrichment accuracy: L1 merge prefers weak rules seniority over LLM "unknown" (measured −10pts)

P2 — matcher (funnel→judge) follow-ons
8. TODO — re-judge policy: clear llm_matched_at when profile version changes
9. TODO — matcher telemetry: judged/skipped/failed counts into run_log
10. TODO — Level 6 experiment: ONE combined LLM call (facts+fit), head-to-head vs L1+L4

P3 — Step-3 carry-overs
11. TODO — V-04: CV upload size cap + MIME allowlist
12. TODO — V-01..V-03: RHF + zod form validation (frontend)
13. TODO — C-07: @dnd-kit keyboard a11y on KanbanBoard
14. TODO — V-05: OpenAPI → TS codegen (types.ts drift; llm_* fields were hand-mirrored again)

P4 — docs and hygiene
15. TODO — document the matcher batch (STATUS.md, IMPLEMENTATION_LOG.md, CLAUDE.md env table)
16. TODO — README/ARCHITECTURE sweep: one correct three-engine + judge description
16b. TODO — remove jobtensor for real (5-surface rotation, 50→49)
17. TODO — repo tidy: test-artifacts screenshots, stray experiment outputs
```

- `docs/maintenance/JOURNAL.md` — 4 entries (all of them; verbatim summaries):
  1. **2026-06-10 ~21:50 bootstrap** — skill+backlog created; matcher batch context (a925f42..d801f78); live-verified 18/18 judged in 89.8s; Gemini 429-dead note.
  2. **2026-06-11 ~00:55 iteration 1** — item #0 DONE (6974bb6); dirty-tree note (foreign WIP described); frontend gates 65/√/√; live screenshot fit-92-first.
  3. **2026-06-11 ~02:50 iteration 2** — item #1 DONE (e054ec7); canonical baseline on dirty tree 1281/3; gotcha: CLI exits(2) without data/user_profile.json — probe sources directly.
  4. **2026-06-11 ~04:55 iteration 3** — item #2 DONE (b7b2c60); jobtensor upstream-dead diagnosis; 16b queued; test_sources 84/84; live probe 1 request/0 jobs/no warnings.

No `MISSIONS.md` exists.

### Honest enforcement assessment

| Control | Enforced by machinery | Honor-system (prompt only) |
|---|---|---|
| MCP namespace denies | ✅ settings.json `deny` | |
| Bash/tool allowlist prompts | ✅ permission system | |
| "Run /verify-job360 before commit" | | ⚠️ Stop hook only REMINDS (non-blocking); the gate lives in the maintain prompt |
| One-item-per-round, no-push, dirty-tree abort, 3-strikes-revert, no test-weakening | | ⚠️ 100% prompt discipline |
| Lock file | ⚠️ advisory file, self-healing 3h window | |
| Tests-green-before-commit | | ⚠️ no pre-commit config exists; prompt + practice only |
| Migrations need human | | ⚠️ prompt rule 6 only |

**Bottom line: almost the entire safety model is prompt-level.** It has held for 3 rounds, but a multi-agent design should move the commit gate (tests + verify) into a real PreToolUse/pre-commit hook, and the no-push rule into permissions (deny `Bash(git push*)`).

---

## 4. Application architecture

### Entry points

- `backend/main.py` — thin uvicorn entry → `src/api/main.py`: FastAPI app, `lifespan` runs `init_db()` (migrations auto-apply), CORS from `FRONTEND_ORIGIN`, RequestIdMiddleware, 12 route modules under `/api`.
- `backend/src/cli.py` (Click): `run` (pipeline), `status`, `view`, `api`, `sources`, `setup-profile`.
- Frontend is **Next.js 16** (NOT Streamlit). Pages: `/login /register /forgot-password /reset-password /verify-email /dashboard /jobs/:id /pipeline /profile /notifications /settings{,/channels,/notifications,/account} /admin /`.

### API endpoints (by module)

| Module | Endpoints |
|---|---|
| health | GET /api/health, /api/status, /api/sources |
| auth | POST register/login/logout, GET me, DELETE users/me, PATCH users/me/{password,email}, password-reset request/confirm, verify-email request/confirm, GET me/email-verified |
| jobs | GET /api/jobs (mode=keyword|hybrid), /api/jobs/{id}, /api/jobs/{id}/duplicates, /api/jobs/export |
| actions | POST+DELETE /api/jobs/{id}/action, GET /api/actions, /api/actions/counts |
| search | POST /api/search, GET /api/search/{run_id}/status |
| pipeline | GET pipeline/counts/reminders/timeline, POST {job_id}, POST advance, PATCH notes |
| profile | GET/POST profile, POST linkedin/github, versions list/restore/diff, json-resume |
| channels | GET/POST/DELETE /api/settings/channels(+/{id}/test) |
| notifications | GET /api/notifications, /stats |
| notification_rules | GET/POST/PATCH/DELETE /api/settings/notification-rules |
| runs | GET /api/runs/recent, /api/runs/source-health |

### Background jobs

ARQ (`src/workers/`): `score_and_ingest`, `send_notification`, `enrich_job_task`, `send_daily_digest` (digest drain), `nightly_ghost_sweep` (cron daily 02:00 UTC), ledger state tasks. Requires `REDIS_URL`. `TieredScheduler` (`src/services/scheduler.py`): 60s ATS / 5m reed / 15m workday+RSS / 60m everything else; consults `BreakerRegistry` (5 failures → 300s open); each fetch bounded by `asyncio.wait_for(SOURCE_FETCH_TIMEOUT=60s)`.

### Data layer

18 migration pairs 0000→0017 (full table in investigator output; latest: 0015 password_resets, 0016 email_verification, 0017 user_feed llm verdict columns). Shared catalog: `jobs` (UNIQUE normalized_company+normalized_title, staleness state machine, 9 dim-score columns), `job_enrichment`, `job_embeddings`, `run_log` (has run_uuid, per_source JSON, per_source_errors, durations, optional user_id). Per-user: `users, sessions, user_feed (SSOT; llm_fit_score/llm_verdict/llm_reason/llm_matched_at), user_actions, applications(+stage_history), notification_ledger, user_channels (Fernet), user_profiles(+versions), notification_rules, user_notification_digests, password_resets, email_verifications`.

### Job sources — all 50 registry entries with status (live evidence from run f409bdecd708, 2026-06-11 04:41 UTC; "0*" = legitimately 0 without keys)

| Status | Sources (registry keys) |
|---|---|
| **WORKING (returned jobs this run)** | reed 315, adzuna 1, arbeitnow 64, remoteok 100, himalayas 8, remotive 10, linkedin 25, smartrecruiters 123, pinpoint 3, indeed 4 (JobSpy), google_jobs 17, devitjobs 2494, landingjobs 13, themuse 78, hackernews 146, nofluffjobs 200, hn_jobs 30, weworkremotely 100, realworkfromanywhere 118, eightykhours 1, bcs_jobs 8, uni_jobs 136 — **22 productive** |
| **0 — keyed, no API key configured (expected)** | jsearch, jooble, careerjet, findwork |
| **0 + ERRORED this run (suspect: 60s timeout on multi-slug sweeps or upstream)** | greenhouse, lever, workable, ashby, recruitee, workday, personio, workanywhere — note ashby returned 404 jobs in the 19:44 run and 0+error at 04:41 → intermittent, consistent with timeout/rate-limit, NOT dead |
| **0 — KNOWN BROKEN upstream (backlog P1)** | comeet (dead slugs riskified/lightricks → 400), gov_apprenticeships (non-JSON), aijobs_global (non-JSON), glassdoor (JobSpy 400 "location not parsed" — registry pair of indeed) |
| **0 — FIXED in code but server not restarted yet** | jobicy (fix e054ec7 live-proven 6 jobs via direct probe; the :8000 server still runs pre-fix code in memory) |
| **0 — QUARANTINED (upstream dead)** | jobtensor (b7b2c60) |
| **0 — quiet this run, no error (thin feeds / niche)** | aijobs, jobs_ac_uk, successfactors, aijobs_ai, rippling, nhs_jobs, nhs_jobs_xml, biospace, climatebase, teaching_vacancies (last 5 not in this run's 44-source list — registry vs run delta is keyed-skip + breaker skips) |

Implementations: `src/sources/{apis_free,apis_keyed,ats,feeds,scrapers,other}/` — 49 classes; `indeed`+`glassdoor` alias `JobSpySource` (`other/indeed.py`).

### AI judge / scoring / per-user search

- **Keyword (engine #1)** — `services/skill_matcher.py`: legacy `score_job()` and `JobScorer.score()` (Title 40/Skill 40/Location 10/Recency 10, gates `MIN_TITLE_GATE`/`MIN_SKILL_GATE` 0.15, penalties −30/−15). With `user_preferences`+`enrichment_lookup`: + `scoring_dimensions.py` Salary 10/Seniority 8/Visa 6/Workplace 6, clamp [0,100] (rule #27).
- **Enrichment (engine #2)** — `services/job_enrichment.py` (`ENRICHMENT_ENABLED`, threshold-gated, idempotent per job) via `services/profile/llm_provider.py` chain: `llm_extract` Gemini→Groq→Cerebras; `llm_extract_fast` Cerebras→Groq→Gemini; `llm_extract_validated` adds Pydantic-retry.
- **Semantic (engine #3)** — `services/embeddings.py` (all-MiniLM-L6-v2, 384-dim, chunked) + `vector_index.py` (ChromaDB at backend/data/chroma) + `retrieval.py` (RRF k=60, keyword top-500 + ANN; cross-encoder rerank); surfaced via `GET /api/jobs?mode=hybrid` (`_maybe_apply_hybrid_reorder`, profile-query text).
- **LLM judge (engine #4, newest)** — `services/llm_matcher.py`: `MATCHER_ENABLED` (default false) / `MATCHER_THRESHOLD` 30 / `MATCHER_MAX_JOBS` 30; `MatchVerdict{fit_score 0-100, verdict, reason}`; `match_batch` (semaphore 3, skip-existing, per-job error swallow) persists onto `user_feed`; called from `src/main.py::_run_matcher_stage` after the per-user feed write; read path `get_user_feed_jobs` ranks `COALESCE(llm_fit_score, score) DESC`; API exposes llm_* fields; dashboard badge + client sort fixed (6974bb6). **Measured: 18/18 jobs judged in 89.8s, fit verdicts 10/10 on the labeled accuracy sample; the keyword engine alone scored everything 30–43 while the judge spread 20–92 and correctly rejected every intern role for the senior profile.**
- **Known issues:** judge-once (no re-judge on profile change — backlog #8); no judge telemetry in run_log (#9); the run pipeline loads `load_profile(user_id)` per-user on the HTTP path, but the CLI path requires `data/user_profile.json` (web-vs-CLI profile split — verify-skill gotcha).

### Config & secrets

`.env` at repo root (gitignored), loaded by `core/settings.py` (names only): REED/ADZUNA/JSEARCH/JOOBLE/SERPAPI/CAREERJET/FINDWORK keys; GITHUB_TOKEN; GEMINI/GROQ/CEREBRAS keys; SMTP_*/NOTIFY_EMAIL/SLACK/DISCORD webhooks; ENRICHMENT_THRESHOLD; MIN_*_GATE; *_WEIGHT×4; TARGET_SALARY_*; SEMANTIC_ENABLED; MAX_CONCURRENT_SEARCHES_PER_USER; SOURCE_FETCH_TIMEOUT; LOG_LEVEL. Outside settings.py: ENRICHMENT_ENABLED (job_enrichment.py), MATCHER_* (llm_matcher.py), SESSION_SECRET (api/auth), CHANNEL_ENCRYPTION_KEY (channels), REDIS_URL (workers), FRONTEND_ORIGIN (api/main.py). Prod-required: SESSION_SECRET, CHANNEL_ENCRYPTION_KEY.

---

## 5. Quality infrastructure

### Tests

- Backend: pytest + pytest-asyncio(auto) + aioresponses (+xdist, +randomly installed but disabled by default via addopts). `pythonpath=["."]`. Canonical: `cd backend && python -m pytest -q -p no:randomly --ignore=tests/test_main.py`.
- **Live numbers (this audit):** collected `1287 tests in 6.88s`; full run:

```
1284 passed, 3 skipped, 1 warning in 312.83s (0:05:12)
```

- `test_main.py` excluded: 13 tests skip-marked `_PRE_STEP_1_5_SCAFFOLDING_DEBT` + live Indeed via sync `requests` (un-mockable by aioresponses). Other skips: 3 Windows bash-only (test_cron×2, test_setup×1), 1 semantic-stack conditional.
- **Coverage gaps:** full `run_search()` E2E untested in the canonical run; semantic/Chroma paths only tested when the `[semantic]` extra is installed (no gate enforces it); frontend coverage scoped to `src/components/**`+`src/lib/**` only (App Router pages excluded).
- Frontend: Vitest 3 (jsdom) — 65 unit tests passing; Playwright for E2E; `npm run type-check` + `lint` both clean as of 6974bb6.

### Lint/type/format

- Ruff: `target-version=py39`, line 120, rules E,F,W,I,N,UP,S,B,G (ignores S101,UP007,B008,B904 + per-file ignores). CI-gate by convention.
- mypy: strict, `python_version=3.10` (deliberate: codebase uses `X | Y` in annotations; 3.9 runtime safety via `from __future__ import annotations` — module-level union ALIASES are forbidden, see commit 76f6ca7).
- **No `.pre-commit-config.yaml` exists.** Nothing lint/test runs automatically on commit.
- Hook wiring: the only hook is the non-blocking Stop reminder (§2/§3).

### /verify-job360

Three flavors (browser-drive + screenshot + console; curl + SQLite + logs; full register→CV→search→jobs). Durations: backend ~1–2 min, frontend ~2–3 min, E2E ~3–8 min. Gotchas list is the project's hard-won ops knowledge (10 items, §2).

### Logging

`backend/data/logs/`: `job360.log` 1.66MB (human), `job360.jsonl` 2.39MB (structured), `audit.log` 5.9KB (auth/security). Top recurring patterns (counts over the recent log window):

```
1. Rate limited (429)                                   198   ← upstream throttling (personio, workanywhere, LLM)
2. HTTP 500                                             168   ← upstream server errors
3. Request error: Expecting value: line 1 column 1      138   ← non-JSON bodies (gov_apprenticeships, aijobs_global)
4. HTTP 400                                             120   ← jobicy(pre-fix), jobtensor(pre-fix), comeet slugs, glassdoor
5. HTTP 503                                              42   ← transient upstream
   (also: Connection refused jobtensor.com ×36, timed out ×26, HTTP 502 ×2)
```

Interpretation: all top patterns are upstream-source failures absorbed by retry/breaker — not application bugs. Items 3 and 4's biggest contributors are the exact P1 backlog items (two already fixed in code).

---

## 6. Health snapshot (all run during this audit)

### Test suite (verbatim)

```
$ cd backend && python -m pytest -q -p no:randomly --ignore=tests/test_main.py
1284 passed, 3 skipped, 1 warning in 312.83s (0:05:12)
```

(Run on the dirty tree including the foreign 3-file WIP → the WIP is self-consistent.)

### Live pipeline run (fresh, POST /api/search as the demo user)

```
run f409bdecd708 → run_log @ 2026-06-11T04:41:25Z
total_found: 3994   new: 2   sources_queried: 44   duration: 162.0s
returned>0 (22): reed 315, adzuna 1, arbeitnow 64, remoteok 100, himalayas 8, remotive 10,
  linkedin 25, smartrecruiters 123, pinpoint 3, indeed 4, google_jobs 17, devitjobs 2494,
  landingjobs 13, themuse 78, hackernews 146, nofluffjobs 200, hn_jobs 30, weworkremotely 100,
  realworkfromanywhere 118, eightykhours 1, bcs_jobs 8, uni_jobs 136
returned 0 (22): jsearch, jobicy, greenhouse, lever, workable, ashby, jooble, recruitee, workday,
  aijobs, careerjet, findwork, jobs_ac_uk, personio, workanywhere, jobtensor, successfactors,
  aijobs_global, aijobs_ai, gov_apprenticeships, rippling, comeet
errors (9, one each): greenhouse, lever, workable, ashby, recruitee, workday, personio,
  workanywhere, aijobs_global
```

**Audit finding:** the :8000 server process predates last night's source fixes → `jobicy` shows 0 here although the committed fix works (direct post-fix probe: `fetched: 6 jobs; first: Data Scientist`). Long-running uvicorn does NOT hot-reload; restart required after loop commits. The 8 ATS-family errors are consistent with the 60s `SOURCE_FETCH_TIMEOUT` cancelling ~268-slug sweeps (ashby oscillates 404 jobs ↔ 0+error between runs).

### /verify-job360-style live checks (from the matcher verification, same session)

```
matcher: judging 18 shortlisted jobs for user e34aeb69… 
matcher: judged 18/18 jobs in 89.8s
user_feed judged rows: 18 (fit range 20–92; every intern role rejected "seniority mismatch")
GET /api/jobs (authed): rank 1 = llm_fit 92 (keyword 34) "Software engineer, generative AI (UK)"
Dashboard screenshot: test-artifacts/judge-ranking-fixed.png (fit-92 card first, green badge)
```

### DB sanity (read-only sqlite3)

```
jobs 92 | user_feed 95 | users 5 | user_profiles 2 | job_enrichment 91 | job_embeddings 92
applications 1 | user_actions 1 | run_log 8 | notification_ledger 0
latest job: id 182, 2026-06-10T20:37:15Z, "Solutions Engineer / Network Automation …"
judged rows (llm_matched_at NOT NULL): 18
```

Note `jobs`=92 vs `job_embeddings`=92 (full vector coverage), `job_enrichment`=91 (one unenriched).

---

## 7. Pillars and gaps

Per `docs/` planning files and CLAUDE.md phases, the three pillars are:

- **Pillar 1 — Ingestion** (sources, scheduler, dedup, catalog): *Works:* 22 productive sources, tiered scheduler + breakers + per-source timeout, 4-layer dedup, ghost detection. *Broken:* comeet slugs, gov_apprenticeships, aijobs_global, glassdoor param (4 known-broken); ATS sweeps suspect vs the 60s timeout; jobtensor dead (quarantined). *Missing:* per-source health dashboard is API-only (`/api/runs/source-health`); no automated dead-slug pruning; conditional-fetch adoption is partial (rule #14).
- **Pillar 2 — Matching/judging** (keyword + enrichment + semantic + LLM judge): *Works:* all four engines live end-to-end behind flags, measured (judge 10/10 fit verdicts; enrichment A/B +10 avg; embeddings fully backfilled). *Broken:* nothing measured-broken today. *Missing:* re-judge on profile change (#8), judge telemetry (#9), combined facts+fit single-call (#10), enrichment merge fix (#7), conftest/env flags must stay OFF-default (rule #18 — currently respected).
- **Pillar 3 — Delivery** (auth, feed, dashboard, pipeline, notifications): *Works:* multi-tenant auth + sessions, per-user feed SSOT, dashboard with verdict badges, kanban pipeline, channels with Fernet creds, notification rules + quiet hours + digests. *Broken:* nothing observed live this audit; `notification_ledger` is empty (dispatch path exercised only in tests so far — no real sends yet). *Missing:* Step-3 carry-overs V-01..V-05 + C-07; email channel unconfigured in env.

### Top 10 problems, ranked by impact

1. **The safety model of the autonomous loop is honor-system** — no enforcing hooks, no pre-commit, push prevented only by prompt (§3). Files: `.claude/settings.local.json`, missing `.pre-commit-config.yaml`.
2. **Uncommitted human WIP blocks loop rounds** (rule 5) and muddies attribution — `backend/src/main.py`, `backend/src/services/channels/dispatcher.py`, `backend/tests/test_channels_dispatcher.py`. Needs a 30-second human decision.
3. **Server staleness after loop fixes** — fixes land in git but the long-running uvicorn serves old code (jobicy proved it). Needs a controlled restart step (or `--reload` in dev) wired into the loop's verify stage. Files: loop skill §verify, `backend/main.py` ops.
4. **ATS sweeps vs 60s SOURCE_FETCH_TIMEOUT** — greenhouse/lever/workable/ashby/recruitee/workday intermittently return 0+error; 268 slugs × rate-limit delays can exceed 60s. Needs per-category timeout or slug-batch chunking. Files: `core/settings.py:SOURCE_FETCH_TIMEOUT`, `services/scheduler.py:_safe_fetch`, `sources/ats/*`.
5. **4 known-broken sources** (comeet slugs, gov_apprenticeships, aijobs_global, glassdoor param) burning retries every run — backlog #3–6. Files: `sources/ats/comeet.py`, `core/companies.py`, `sources/apis_free/gov_apprenticeships.py`, `sources/scrapers/aijobs_global.py`, `sources/other/indeed.py`.
6. **Judge-once staleness** — a profile change does not re-trigger verdicts, so the "top-notch matcher" silently judges against an old left side. Backlog #8. Files: `services/llm_matcher.py:has_verdict`, profile upload routes.
7. **Type drift backend↔frontend is manual** — `lib/types.ts` hand-mirrors `api/models.py` (bit us twice already). Backlog #14 (OpenAPI→TS codegen). 
8. **Docs drift** — CLAUDE.md/backend CLAUDE.md test counts stale (1,154/1,256 vs 1,287); matcher batch undocumented in STATUS/IMPLEMENTATION_LOG/env tables; README/ARCHITECTURE don't describe the judge. Backlog #15/#16.
9. **Stray root `data/chroma/`** directory duplicates `backend/data/chroma/` (one of them is dead weight from a cwd mistake) + stale generator/reviewer worktrees + 11 dead remote worktree branches + `backend/None` junk file — repo hygiene. Backlog #17.
10. **test_main.py scaffolding debt** — the only end-to-end pipeline tests are 13 dead skips; the most important integration path has no offline coverage. Files: `backend/tests/test_main.py`.

### NEEDS-HUMAN decisions

- **Commit/discard the 3-file WIP** (problem #2) — only the human knows if it's wanted.
- **Paid APIs**: keys for jsearch/jooble/careerjet/findwork (4 dead-without-key sources), SerpApi quota, paid LLM tier vs local Gemma for matcher scale (free Cerebras/Groq are the current bottleneck; Gemini is quota-dead).
- **Source rotation batch** (16b): approve 50→49 (or batch with other removals) — it ripples through tests + docs.
- **Email/SMTP + real notification channels**: `notification_ledger` is empty; sending real mail needs credentials and consent.
- **Architecture**: adopt OpenAPI→TS codegen (#14)?; move loop trigger from in-session cron to OS scheduler/CI for durability?; SQLite→Postgres timing (multi-agent writes, see §8).
- **Migrations** are loop-forbidden by rule 6 — any schema change (e.g. matcher telemetry columns) needs human sign-off.

---

## 8. Readiness for multi-agent operation

### Shared resources that WILL conflict

| Resource | Conflict | Mitigation |
|---|---|---|
| `backend/data/jobs.db` (single SQLite, WAL) | WAL allows 1 writer + N readers; two pipelines/test-suites writing → `database is locked` (5s busy timeout) | Per-worktree DB files via `--db-path`/`DB_PATH` redirect (test fixtures already do this); long-term Postgres |
| Ports 8000 (uvicorn) / 3000 (next dev) | Fixed defaults; second instance fails or silently reuses the other worktree's server | Per-agent port assignment (`--port`, `PORT`); the CLI already supports `api --port` |
| `backend/data/chroma/` (ChromaDB PersistentClient) | Single-process file lock; concurrent writers corrupt/contend | Per-worktree chroma dir or SEMANTIC_ENABLED=off in worker agents |
| Root `.env` | Shared keys + flags; one agent flipping MATCHER_ENABLED affects all | Read-only convention + per-process env overrides |
| Redis (ARQ) | Shared queue — fine by design, but cron tasks would double-fire if two workers run | One worker total |
| `docs/maintenance/.lock` + BACKLOG/JOURNAL | The loop's memory is single-writer by design | Keep exactly one integrator owning these files |
| Editable install (`pip install -e`) | `import src` can resolve to a WORKTREE copy (documented gotcha) | Each worktree needs its own venv, or PYTHONPATH pinning per agent |
| Playwright MCP browser profile | Single profile dir — "browser already in use" (hit during this audit) | One UI-verifier at a time, or `--isolated` |

**Tests in parallel:** the pytest suite redirects DB_PATH per-test and mocks HTTP — two worktrees CAN run suites simultaneously (separate venvs, ~5 min each, 8 cores/16GB is enough for 2–3 concurrent suites; watch RAM if sentence-transformers loads).

### Mission parallelism map (disjoint file ownership)

**Safe in parallel** (different files, no shared runtime):
- Per-source fixes: #3 comeet, #4 gov_apprenticeships, #5 aijobs_global, #6 glassdoor — each touches one source file + tests (shared `test_sources.py` is append-only → merge-friendly but serialize commits).
- Frontend-only: #12 RHF+zod, #13 a11y — disjoint from backend missions.
- Docs: #15, #16 — disjoint from code.
- Scripts-only: #7 enrichment merge fix.

**Must be serialized / single-owner:**
- Anything touching `src/main.py`, `database.py`, `api/models.py`+`types.ts` (#14 codegen), migrations (human-gated anyway), the 5-surface rotation (16b), scheduler/timeout work (problem #4).
- The integrator role: BACKLOG/JOURNAL writes, the canonical full-suite gate, and live verification (one server pair, one browser).

### Machine constraints (measured)

```
OS: Windows 11 Home 10.0.26200 · CPU cores: 8 · RAM: 15.9 GB · Disk D: 346 GB free of 379 GB
```

8 cores / 16 GB supports: 1 live server pair + 1 full pytest suite + 2–3 Sonnet implementer agents comfortably. It does NOT comfortably support multiple sentence-transformer loads or 2+ browser sessions. Recommended fleet shape on this machine: **1 scout + 2–3 workers (worktree-isolated, no servers) + 1 integrator (owns DB/ports/browser/journal) + the existing 2h heartbeat**.

---

## Executive summary (10 lines)

1. The repo is healthy and verified: **1284/1284 runnable tests pass (3 platform skips)**, frontend 65/65 + type-check + lint clean, on a tree carrying one small uncommitted human WIP that itself passes.
2. A four-engine matching stack (keyword funnel → LLM enrichment → semantic RRF → **LLM judge**) is live end-to-end behind default-off flags; measured tonight: 18/18 jobs judged in 89.8s, fit 10/10 on the labeled sample, dashboard renders and ranks by the judge.
3. An autonomous maintenance loop (in-session cron, every 2h) has completed 3 rounds: dashboard ranking fix, jobicy un-broken (live-proven), jobtensor correctly diagnosed as upstream-dead and quarantined.
4. The loop's safety model is almost entirely prompt-level — the only hook is a non-blocking reminder; no pre-commit, push prevented by convention. This is the #1 thing to harden before scaling to multiple agents.
5. Ingestion reality: 22 of 50 sources productive; 4 keyed sources idle without API keys; 4 known-broken upstreams queued; the big ATS sweeps intermittently die against the 60s per-source timeout (suspected, needs confirmation).
6. One operational trap proved tonight: committed fixes do NOT reach the long-running server until restart — the loop needs a restart/reload step after backend commits.
7. The single SQLite file, fixed ports 8000/3000, one ChromaDB dir, one Playwright profile, and one `.env` are the five concurrency choke points for any multi-agent design; per-worktree DB/ports/venvs solve most of it today.
8. Two stale worktrees (generator/reviewer, Apr–May) + 11 dead remote agent branches + a stray root `data/chroma/` are leftovers worth pruning; `docs/maintenance/{BACKLOG,JOURNAL}.md` is the live, working memory.
9. Documentation lags reality: test counts, the matcher batch, and the judge architecture are undocumented in CLAUDE.md/STATUS/ARCHITECTURE — queued as backlog items.
10. Human decisions pending: commit-or-discard the 3-file WIP, API-key/paid-tier choices, the 50→49 source rotation, real notification credentials, and any schema migration the loop is (rightly) forbidden to touch.
