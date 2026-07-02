# DocSync (Loop 3) — code ↔ documentation sync

**Loop 3 = DocSync.** The two sibling loops: **Loop 1** = autonomous maintenance
(disabled), **Loop 2** = full-lifecycle E2E test (CI). **Loop 3** keeps every MD file
honest against the code.

> **Rule of the loop: code is the proof.** Docs must match code. When they
> disagree, the doc is wrong (unless the code is a genuine bug — then flag it,
> don't edit code). DocSync **never touches a line of code** — MD files only.

## How to run Loop 3

1. **Extract code facts** (the ground truth). Source count, test count, route
   modules + endpoints, migrations + tables, engine flags, DB backend, deps,
   frontend routes, CLI commands. Verify from code, never from another doc.
2. **Fan out the audit** — one reader per living-doc cluster, each verifying its
   doc's claims against code, citing `file:line`, classifying every finding:
   - `CONTRADICTS` — doc says X, code says Y.
   - `BEHIND` — code has something the doc omits/undercounts.
   - `AHEAD` — doc describes something not in the code (documented-but-not-built).
   - `UNDOCUMENTED` — code exists, no doc covers it.
   - `STALE-OPEN` — listed as a TODO but already done in code.
   - `UNVERIFIED` — could not confirm from code (say what to check).
3. **Ledger** the findings (the dated section below).
4. **Fix the living docs** — MD only, cite the code proof, keep each doc's tone.
5. **Flag code-side issues** separately (things where the *code* looks wrong, so a
   doc fix would launder a bug). The owner decides.
6. **Do NOT rewrite historical docs** (plans, research, reviews, `_archive/`,
   `IMPLEMENTATION_LOG.md`, dated reports). They are point-in-time records.

## Which docs are "living" (synced) vs "historical" (left alone)

- **Living (sync):** `CLAUDE.md`, `backend/CLAUDE.md`, `frontend/CLAUDE.md`,
  `ARCHITECTURE.md`, `STATUS.md`, `README.md`, `backend/README.md`,
  `frontend/README.md`, `docs/README.md`, `docs/STACK.md`, `docs/STACK_checklist.md`,
  `docs/pillars/*`, `CONTRIBUTING.md`, `docs/troubleshooting.md`, `docs/DEPLOY.md`,
  `.claude/skills/*` (they cite live code facts), `docs/maintenance/{BACKLOG,MISSIONS,STATUS-DAILY}.md`.
- **Roadmap (update status lines only):** `LAUNCH_PLAN.md`, `docs/MONETIZATION_GAPS.md`,
  `ENGINES_KANBAN.md`, `docs/PRD.md`.
- **Historical (leave):** `docs/plans/*`, `docs/research/*`, `docs/reviews/*`,
  `docs/_archive/*`, `docs/IMPLEMENTATION_LOG.md`, `docs/engine_eval_*`,
  `docs/evaluation_report.md`, `docs/maintenance/JOURNAL.md`.

---

# Ledger — 2026-07-02 run

Repo state at audit: `main`/`phase-1-postgres`. **91 project MD files** (excl. vendor).
Ground truth verified from code:

| Fact | Code truth (proof) |
|------|--------------------|
| DB backend | **Postgres** — `database.py:5` `from src.repositories import pg as aiosqlite`; `pg.py` psycopg3; `DATABASE_URL` default `postgresql://…`; `db_retry.py` "Postgres edition" |
| Sources | 46 unique `BaseJobSource` classes / **47** `SOURCE_REGISTRY` keys / `SOURCE_INSTANCE_COUNT=46` |
| API | **12** route modules (incl. `client_log.py`), **64** endpoints |
| Migrations | 0000 → **0022** (`0022_magic_link_tokens`); 0015 password_resets, 0016 email_verification, 0019 channel_oauth, 0020 notification_rule_single, 0021 add_job_deadline |
| Tests | **1636/1638** collected (2 deselected) |
| Deps | `apprise>=1.7.0`, `psycopg[binary]>=3.2`, `sentry-sdk>=1.40.0` (pyproject) |
| Category enum | code uses `"scraper"` (singular), not `"scrapers"` |
| Infra shipped | Sentry (`api/main.py`), PostHog (`PostHogProviderWrapper.tsx`), Resend (`auth/email_sender.py`), Docker + `docker-compose.prod.yml`, Railway, magic-link auth |
| Frontend routes | 21 pages incl. `/channels /admin/sources /contact /privacy /terms /auth/magic /jobs /sentry-test` |

## Cross-cutting drifts (touch many docs)

- **A — SQLite→Postgres.** All core docs still say "async SQLite (aiosqlite)". WAL /
  `busy_timeout` notes now false (`database.py:24-26`). Files: CLAUDE.md, backend/CLAUDE.md,
  ARCHITECTURE.md, STATUS.md, STACK.md, PRD.md, MONETIZATION_GAPS.md, LAUNCH_PLAN.md.
- **B — Test count.** `~1,409` (and `~1,333`, `1,288`) → **1,636/1,638**. Files: CLAUDE.md ×3,
  backend/CLAUDE.md, STATUS.md ×3, ARCHITECTURE.md, CONTRIBUTING.md, backend/README.md ("1000+/30 files" → 1636/111 files).
- **C — Routes.** "11 route modules / 46 endpoints" → **12 / 64** (added `client_log`). Files: CLAUDE.md, ARCHITECTURE.md, README.md ("25 routes"→60/64).
- **D — Migrations + tables.** "22 pairs (0000→0021)" → **23 (0000→0022)**. Schema docs miss
  tables: `password_resets`, `email_verifications`, `oauth_states`, `magic_link_tokens`, plus
  `application_stage_history`, `notification_rules`, `user_notification_digests`, and jobs-table
  columns (deadline, staleness, 9 score dims). Files: CLAUDE.md, ARCHITECTURE.md, pillars/01.
- **E — Structured logging system undocumented.** `middleware.py` (RequestId+AccessLog+security
  headers), `errors.py` (500/4xx/422 handlers), `db_retry.py` (`with_write_retry`/`open_db`),
  `client_log.py` (`POST /api/client-log`), `utils/logger.py` (jsonl+audit streams, correlation IDs).
  Files: CLAUDE.md, ARCHITECTURE.md.
- **F — Magic-link auth + channel OAuth undocumented.** `auth.py` magic-link request/consume +
  `channels.py` Slack/Discord/Telegram OAuth. Files: STATUS.md, README.md, pillars/01, verify CHECKLIST.
- **G — Shipped infra marked "to build".** Postgres, Sentry, PostHog, Docker, Resend, Railway,
  magic-link — all live but STACK.md/LAUNCH_PLAN/MONETIZATION_GAPS list as todo. (0 AHEAD found — no
  fake "built" claims.)
- **H — Loop status.** STATUS.md headline "autonomous maintenance loop running" → loop is **paused**
  (JOURNAL/MISSIONS/STATUS-DAILY all say paused since 2026-06-13).

## Per-file findings (see git history of this run for full citations)

- **CLAUDE.md** — apprise `>=1.9.9`→`>=1.7.0`; add DATABASE_URL + SENTRY_DSN env rows; add
  psycopg/sentry-sdk to stack; rule #15 `"scrapers"`→`"scraper"`; services tree missing
  `llm_matcher, deadline, rescore, metrics_exporter, job_enrichment_schema`; utils missing `telemetry`;
  rule #9 "53+55"→ test_scorer 79 / test_profile 51; + A,B,C,D,E.
- **ARCHITECTURE.md** — A,B,C,D,E + notification section describes a removed `NotificationChannel`
  class hierarchy (real path = `channels/dispatcher.py`, Apprise); stale source refs `FindAJob`/`Nomis`
  (removed); missing indexes; env table (12 vars → ~35).
- **STATUS.md** — A,B,F,H + Step 4/5 "todo" items already done (Dockerfile, security headers,
  `/livez`+`/readyz`, password-reset flow); Resend not SES.
- **README.md** — auth system entirely undocumented; "25 routes"→60/64 + 12 modules; keyed APIs 7→8
  (missing gov_apprenticeships in diagram); ATS slug counts (Workable 25→18, Greenhouse 80→82, total
  ~264→256); `test_main.py` now in canonical run (12→15 tests).
- **backend/README.md** — "1000+ tests / 30+ files" → 1636 / 111 files.
- **frontend/CLAUDE.md** — route list missing `/ /jobs /channels /admin/sources /contact /privacy
  /terms /auth/magic /sentry-test`; wrongly nests `settings/channels` (channels is top-level); add
  `layout/PageHeader.tsx`, `lib/clientLog.ts`, `lib/toast.ts`.
- **STACK.md** — mark Postgres/Sentry/PostHog/Docker/Resend/Railway DONE (self-contradicts
  STACK_checklist same date). **STACK_checklist.md** — Resend done; magic-link auth missing; test count.
- **docs/README.md** — add `DEPLOY.md` to index; STACK_checklist score "27/21" → "36/12".
- **docs/pillars/01-user** — auth endpoints 7→13; lockout IS implemented (doc says not); magic-link +
  channel OAuth undocumented; `/api/profile/jsonresume`→`/json-resume`; granular `/profile/cv` +
  `/profile/preferences`; migration list stops at 0014.
- **docs/pillars/02** — `tests/test_vector_index.py` does not exist.
- **docs/pillars/03** — "49 subclasses"→46; Workable ~25→18; `COMPANY_NAME_OVERRIDES` ~77→67;
  "~266"→256; `test_sources.py` 81→78.
- **docs/pillars/glossary** — `RATE_LIMITS` 46→47.
- **docs/pillars/runbook** — `GET /api/runs` does not exist (`/runs/recent` + `/runs/source-health`).
- **CONTRIBUTING.md** — "26 hard rules"→28; test count; PR flow omits `scripts/agent-gate.sh` + api-types drift check.
- **.claude/skills/verify-job360** — `E2E_TEST_REPORT.md` doesn't exist; CHECKLIST omits magic-link.
- **docs/maintenance/BACKLOG.md** — 7 STALE-OPEN items already done (enrichment L1 merge, matcher
  telemetry, V-04/V-01..03/C-07/V-05 carry-overs, "document matcher batch", "README/ARCH sweep", repo tidy).
- **docs/maintenance/STATUS-DAILY.md** — 13-day-stale one-off (its stated purpose is daily).

## ⚠️ Code-side flags (NOT doc bugs — do NOT fix by editing docs; owner decides)

These are places where the **code** looks wrong or contradicts a STRICT rule. DocSync
does not touch code, so these are flagged for a human, not silently papered over:

1. **`core/skill_synonyms.py` still live + imported** by `skill_matcher.py` and
   `profile/keyword_generator.py` — a 600+-line static skill alias dict. This
   contradicts **Hard Rule #28** ("ZERO hardcoded skill/keyword lists") and CLAUDE.md's
   own claim that this file is "being removed". Either the code should be removed or
   rule #28 amended. (Not a doc-only fix.)
2. **Scheduler `"scrapers"` tier key unreachable.** `scheduler.py` `TIER_INTERVALS_SECONDS`
   has a `"scrapers"` key, but sources set `category="scraper"` (singular), so the lookup
   falls through to `"default"` (same value → accidentally correct). Latent bug.
3. **Matcher telemetry may not persist.** `utils/telemetry.py::MatcherTelemetry` counters
   increment but no confirmed sink to `run_log`/logs — "morning review without grepping"
   goal may be partially open (BACKLOG item 9). Verify a persistence call site.

## Clean (no drift found)

`frontend/AGENTS.md`, `ENGINES_KANBAN.md`, `docs/troubleshooting.md`,
`.claude/skills/sync/SKILL.md`.
