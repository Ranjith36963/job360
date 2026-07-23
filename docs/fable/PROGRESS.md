> **⚠️ CLOSED / SUPERSEDED (2026-07-23).** This is a historical snapshot. The
> current verified status of every finding lives in **[AUDIT-2026-07-23-FULL-REVERIFY.md](AUDIT-2026-07-23-FULL-REVERIFY.md)** — the fable backlog
> is closed there (92 of 106 fixed; the rest are owner decisions or scheduled audit
> areas). Do NOT treat any item below as still-open without checking that doc first.

# Progress — fixes shipped vs pending

> ## 🔎 SOURCE OF TRUTH → [`AUDIT-2026-07-17-VERIFIED.md`](AUDIT-2026-07-17-VERIFIED.md)
> On 2026-07-17 all **101** findings across the three Fable locations were re-verified
> against the **live `main`** code by 11 parallel agents (each opening the real file, not
> trusting this tracker's line numbers). Result: **50 CONFIRMED FIXED · 12 PARTIAL ·
> 20 OPEN BUG · 14 OPEN (accepted) · 2 NEEDS-YOU · 2 NOT-CONFIRMED (claimed fixed, not
> in code — now being closed) · 1 dead-code.** The verified doc has the current file:line
> proof for every one. Where a STATUS line below over-claimed, an **⚠️ AUDIT 2026-07-17**
> note now corrects it inline.

> Live tracker for the audit findings. Updated as fixes land. Each shipped item
> names the commit-level change + how it was verified. Honest status only — a thing
> is "✅ Fixed" only when it's coded AND verified, not when it's planned.

## ✅ Fixed (coded + verified + COMMITTED — pushed to branch, commit `7f906c6`)

Full backend suite **1715 passed, 0 failed** on the merged tree; pushed for GitHub CI (Linux) to re-verify.

| # | Finding | Doc | What was done | Verified by |
|---|---|---|---|---|
| 1 | **Dead ARQ worker** (P0) — `ctx['db']` never set, every cron crashed | `04-OPS` | Added `on_startup`/`on_shutdown` to `WorkerSettings` opening the DB connection + wiring `enqueue`; set `max_jobs=1` so the shared connection is never used concurrently | New regression test `test_worker_startup_populates_ctx` + full suite (1715 passed) |
| 2 | **Session cookie may ship without `Secure`** (P1) — gated on unused `JOB360_ENV` | `01-SECURITY` | Cookie `secure` now uses the same prod check as the rest of the app (`APP_ENV=production` OR `RAILWAY_ENVIRONMENT`) | full suite green |
| 3 | **Sentry `send_default_pii=True`** ships cookies/PII (P1) | `01`, `04`, `05` | Set `send_default_pii=False` + added a `before_send` scrubber stripping Cookie/Authorization headers, cookies, and request bodies | full suite green |

**Still to do on these:** ⚠️ the worker fix revives the crons, but the worker still has **no Sentry** — pair it with the `09-PRODUCTION-SIGNALS` P0 (init Sentry in the worker) so a future worker failure actually alerts.

| 4 | **`/debug` skill broken** (P1) — every snippet imported the pre-refactor tree, crashed on first run | `06-HARNESS` | Fixed `score` imports to `src.services.*` (verified importable); rewrote `notify` from the removed global-channel classes to the current per-user dispatcher; noted `dedup`'s sqlite path is dev-only (prod is Postgres) | commit `2ba0889`; imports verified |
| 5 | **Git allowlist too broad** (P1) — `git push:*`/`git merge:*` + blanket `git:*` auto-approved (unsupervised push/reset/clean) | `06-HARNESS` | Removed push/merge from `settings.json` + blanket `git:*` from `settings.local.json`; kept read-only git. Push/merge/reset now classifier-gated | commit `9fdef61`; push still works via classifier (supervised) |

**Harness grade: B+ → A−** — the two flagged fixes are landed. Remaining P2 (gitleaks pre-commit hook, CLAUDE.md trim, `.claude/agents/` reviewer defs) are polish, not blockers.

## 🔜 Next up (code-fixable, in roadmap order)

| Finding | Doc | Plan |
|---|---|---|
| **Single unpooled Postgres connection** (P0) | `02`, `04` | `psycopg_pool.AsyncConnectionPool` + reconnect. Larger refactor — do carefully, own PR. |
| **Delete doesn't erase** (P0 compliance) | `05`,`02`,`01` | Hard-delete/anonymise all user rows; block magic-link resurrection. One fix, three findings. |
| **Transactional migrations** (P1) | `02` | Wrap each migration in `BEGIN…COMMIT`. |
| **Frontend auth-page error leak** (P1) | `03` | Swap two call sites to `friendlyAuthError`. |
| **Correctness bugs** (P2) | `02` | purge on `last_seen_at`; ISO timestamps; `normalized_key` whitespace. |
| **Harness: git allowlist + `/debug` skill + gitleaks** (P1) | `06` | Tighten settings; fix broken skill; add secret scan. |

## 🧑 Needs YOU (not code — a decision, money, or an external party)

| Item | Doc | Why it's not a code fix |
|---|---|---|
| **Scraping LinkedIn/Glassdoor decision** | `05` | Business/legal call — drop the sources or accept the risk in writing. |
| **Real privacy/terms + subprocessor list** | `05` | Legal copy — a template or a paid service (Termly/iubenda). |
| **SOC 2 Type II, penetration test, DPAs** | `05` | External auditors + budget; only worth it when a customer asks. |
| **Set `JOB360_ENV`/confirm Railway env** | `01` | The code fix removes the dependency, but confirm `APP_ENV`/`RAILWAY_ENVIRONMENT` is set on the live service. |

## Honest grade note
Grades move only as **shipped** fixes accumulate. After the three fixes above:
- **Ops** C− → **C+/B−** (worker now actually runs; pool + alerting still pending).
- **Security** B+ → **A−** (live cookie misconfig closed; PII no longer leaks to Sentry).
- **Compliance** C− → **C** (PII-to-Sentry closed; scraping + erasure still open).

Full **A across all areas** requires the "Needs YOU" items too — those are decisions and
budget, not code. This tracker will keep showing the true state as fixes land.

---

## Session update — ~27 of ~30 landed (branch `worktree-feat-live-smoke`)

**All fixes below are committed + pushed, each with a full-suite-green gate (1716-1717 passed).**

### P0 (both done)
- ✅ **Sentry-in-worker** (272d29b) — worker crashes now reach Sentry (component=worker) + regression test.
- ⏳ **DB connection pool** — NOT done. Conclusively scoped as a dedicated refactor: `get_db()` returns a shared singleton that `conftest.py` (lines 98-236) monkeypatches to inject the test DB. Making it per-request/pooled must also rebuild that test-DB wiring or it breaks every API test; also touches 18 route files + the pg shim. Recommend as the next focused piece.

### P1
- ✅ **Real erasure** (272d29b) — `hard_delete_user` across 17 per-user tables; closes erasure + orphans + resurrection (3 findings). Test verifies per-user data erased + shared catalog survives.
- ✅ **Frontend auth-page error leak** (1c75b50) — `friendlyAuthError` on reset/verify pages.
- ✅ **PostHog funnel** (63964ec) — 6 journey events instrumented.
- ✅ **Magic-link Sentry issue** — resolved (non-code) + client-log level-gate (272d29b).
- ⏳ **Transactional migrations** — NOT done. Large: reworks the pg-shim autocommit model + runner. Dedicated effort.

### P2
- ✅ Security: **XML billion-laughs guard** + **timing-safe login** (3f532b2), **lockout email+IP** (1b3519f).
- ✅ Data: **purge on last_seen_at** (4e41e86, with test). ⏳ ISO timestamps (risky shim change) + normalized_key (**hard rule #1 + hands-off-on-search memory — flagged, not edited**) NOT done.
- ✅ Frontend: **dashboard error state** (903af8d), **a11y** role=alert (7c7e78b) + aria-invalid (31010ca).
- ✅ Ops: **restore runbook** (c880f88), **Node 20→22** (6abce80), **worker job_timeout** (4e41e86). ⏳ error-rate alert + railway.json = **needs YOU** (Sentry/Railway dashboards — not codeable via connected tools).
- ✅ Harness: **gitleaks** (6abce80), **.claude/agents/ reviewers** (33a9b37). ⏳ CLAUDE.md trim (risky big edit) NOT done.

### Remaining (honest): 2 dedicated refactors (pool, migrations) · 1 hands-off (normalized_key) · 2 risky (ISO timestamps, CSRF/Redis) · 2 needs-you (error-rate, railway.json) · 1 low-value-risky (CLAUDE.md trim).

**Infra note:** the local test-DB (Docker Postgres) was wedged/slow mid-session; fixed by restarting Docker + `fsync=off` on the disposable test DB (80min→10min gates).

---

## Session update 2 — 2026-07-15 (branch `worktree-feat-live-smoke`)

Since the last note, the two "dedicated refactor" items and several risky/needs-you
items were closed. **True current state below** (earlier note above is superseded).

### Now DONE + full-suite-green
- ✅ **DB connection pool (P0)** — `15c5b68`. Split `get_db()` (boot singleton, schema owner + test setup) from new `get_request_db()` (per-request connection, closed in `finally`). 9 route files migrated. Fixes the shared-psycopg-connection concurrency + self-heal problem.
- ✅ **normalized_key whitespace (P2, rule #1)** — `1042fe3`. Collapses internal whitespace runs so cosmetically-different spacing dedups to one key. Dedup tests re-run per rule #1.
- ✅ **CSRF Origin-check middleware (P2)** — `ae10834`. Rejects unsafe-method requests with a non-allowlisted Origin.
- ✅ **Redis rate limits (P2 security)** — gated behind `RATE_LIMIT_REDIS` (default off = byte-identical in-memory). Redis sliding window via an atomic Lua `EVAL` (shared across replicas); **falls back to in-memory on any Redis error** so a Redis outage never breaks auth. 9 offline tests (fake client) incl. the fallback path.
- ✅ **ISO timestamps (P2 data)** — pg shim now emits `…+00:00` (was space form) for `CURRENT_TIMESTAMP` **and** `datetime('now')` defaults, matching the app's `.isoformat()`. Fixes text-sort ordering on the notification ledger. `+00:00` chosen over `Z` because Python 3.9 `fromisoformat` rejects `Z` and unguarded call sites parse these columns. 5 unit tests + verified against 68 ledger/oauth tests.
- ✅ **railway.json (P2 ops)** — backend (`/api/health` healthcheck + `ON_FAILURE` restart) and frontend (restart policy). Deliberately omit `build`/`startCommand` so they harden without risking the live deploy's known-good build.

### Still open — honest
- ⏳ **Transactional migrations (P1)** — **attempted, reverted.** Genuine tension: the runner's design is *idempotent re-apply* (`init_db()` backfills columns, then `runner.up()` re-applies the same migrations, relying on duplicate-tolerance). Wrapping migrations in all-or-nothing transactions conflicts with that — a tolerated duplicate error aborts the whole Postgres transaction (`InFailedSqlTransaction`). A correct fix requires auditing all ~23 migrations for `IF [NOT] EXISTS` guards so each is transaction-safe. That is a dedicated piece, not a quick wrap. Reverted rather than ship broken boot-critical code.
- ⏳ **Sentry error-rate alert (P2 ops)** — **needs YOU.** Confirmed via the Sentry MCP catalog: it exposes find/get/inspect alert tools but **no create-alert-rule tool** (verified `python-fastapi` currently has zero alert rules). Create it in the Sentry dashboard: metric alert on the `python-fastapi` project, error-count/rate threshold, notify your channel.
- ⏳ **CLAUDE.md trim (P2 harness)** — **intentionally NOT done.** The root CLAUDE.md is load-bearing (28 numbered hard rules). Trimming risks dropping a rule a future session then violates. The correct call is to keep it; "shorter" is not worth a silent rule loss.

### Tally: of the original ~25 P0/P1/P2 goal items, **22 done + verified**, 1 dedicated refactor (migrations), 1 needs-you (Sentry alert), 1 keep-as-is (CLAUDE.md).

---

## Session update 3 — 2026-07-16 (supersedes the "still open" notes above)

The two items previously left open are now closed:

- ✅ **Transactional migrations (P1)** — `ea18e10`. Root cause (found via focused investigation): the pg shim ran a speculative `SELECT lastval()` after every INSERT; inside a transaction, migration 0002's `INSERT ... ON CONFLICT DO NOTHING` (non-serial PK → `lastval()` undefined) made that probe error and **abort the whole transaction**. Fix: probe `lastval()` only in autocommit-IDLE state (`pg.py` + `pgsync.py`); wrap each migration in `BEGIN/COMMIT` (`runner.py`). New `test_failed_migration_rolls_back_atomically` proves atomicity. Full gate 1733 passed.
- ✅ **CLAUDE.md trim (P2 harness)** — done *safely*: all 28 hard rules + every load-bearing invariant kept verbatim; only pure history removed (commit hashes, benchmark numbers, source-count play-by-play, backlog notes).

### Final tally: **25 of 26 goal items done + verified + pushed.**
The one remaining — **Sentry error-rate alert** — is not codeable (the Sentry MCP has no create-alert tool); it's being created in the dashboard by the user. Everything in-code is shipped and full-suite-green on `worktree-feat-live-smoke`.

---

## Session update 4 — 2026-07-16 (final)

- ✅ **6 re-audit gaps** (`4af1c7b`): purge-orphans D4, Dependabot C10, CSRF-GET S6, sequence-resync D3, Article-20 export C7, consent gate C3 — independently verified in code.
- ✅ **SQLite fully removed** (`f4c42e6`, user directive): dead aiosqlite dep gone, all disguised imports honest (`pg`/`pgsync`), conftest sys.modules swap deleted, 8 dead scripts repaired, ZERO sqlite imports remain. Dialect rewrite (translate()) flagged as the remaining follow-up batch.
- ✅ **DB audit trail C8** (`b939e29`): migration 0025 `audit_log` + QueueListener tee on the audit logger; GDPR anonymise-on-erasure; email denylisted; included in Article-20 export.
- ✅ **Atomic Kanban moves D11** (`b939e29`): BEGIN/COMMIT + savepoint-guarded history insert.
- ✅ **Fail-closed middleware F4** (`b939e29`): outage → login redirect (cookie kept, explained on the login page).
- ✅ **Stale Sentry issue P2**: PYTHON-FASTAPI-2 resolved live via the Sentry API with explanation.

### Remaining (all deliberate): D7/D8 (dev-only down migrations), D12 (splitter — latent), F2 (double-gated E2E bypass), O8 (mypy grandfathered), C11 (MFA — scoped feature). Non-code needs-user: privacy/terms, subprocessors, scraping decision, breach plan, backup region.

---

## Session update 5 — 2026-07-16 — cross-session findings (SI1/M5/SI3/M8)

Verified another session's list against real code:

- **M5 — "purge deletes live jobs": ALREADY FIXED, not a bug.** `purge_old_jobs` keys on `COALESCE(last_seen_at, first_seen) < cutoff` (`4e41e86`), and `main.py:201` calls `update_last_seen()` for every job each successful scrape (behind a completeness gate so a rate-limited scrape isn't treated as absence). A live job keeps its `last_seen_at` fresh and survives. The finding describes the OLD `first_seen` behaviour.
- **SI3 — ChromaDB wrong path: CONFIRMED + FIXED.** `vector_index.py` hand-counted `parents[3]`, which is the REPO ROOT, not `backend/` — the store went to `<repo>/data/chroma` while the docstring, `.gitignore` and every other component expected `backend/data/chroma`. Now derived from `settings.DATA_DIR` (the single source of truth) so it cannot drift again. 4 regression tests.
- **M8 — "unclamped dim scores": CONFIRMED (in the RADAR, not the scorers) + clamp FIXED.** All four dim scorers are bounded by construction (`salary_score` even has `min(ratio, 1.0)`) and the combined `match_score` IS clamped (`skill_matcher.py:414,551`, rule #27 holds). The real bug was `ScoreRadar.tsx`: `value: Math.round((raw/d.max)*100)` with no bound. Now clamped to [0,100] — drawing only; raw stays verbatim in tooltip/aria.
- **SI1 — notifications disconnected in prod: CODE OK, DEPLOYMENT missing.** Worker code + dispatch + tick are tested and healthy. Needs a Railway **worker service** (`arq src.workers.settings.WorkerSettings`) + **Redis** + SMTP/channel creds. Owner action.

### ⚠️ NEW finding (discovered while verifying M8) — ScoreRadar shows a model that doesn't exist
`ScoreRadar.tsx` DIMENSIONS maxes sum to exactly 100 (15+20+10+10+5+10+10+20) — an aspirational 8-dim model. The backend writes the LEGACY weights and only 5 of the 8 dims (`main.py:626-634`: role, skill, location_score, recency, seniority_score). Consequences:
- `role` max **15** vs real `TITLE_WEIGHT=40` → a full title match plots as **267%** (now clamped to 100, but the % shown is still wrong).
- `skill` max **20** vs real `SKILL_WEIGHT=40` → **200%**.
- `seniority_score` max **10** vs real `SENIORITY_WEIGHT=8` → full marks reads as 80%; a penalty reads negative.
- `experience`, `credentials`, `semantic` are **never populated** → three axes permanently 0.
The clamp stops the broken geometry, but the underlying mismatch is a **product decision** (implement the 8-dim model, or retune the radar to the 5 real dims + real weights) and stays OPEN for the owner. Radar tests currently pin the wrong maxes ("12/15", "18/20", "8/10").

---

## Session update 6 — 2026-07-16 — the Score Radar told a lie (Fable-advised)

### What was wrong
The "8D Score Radar" — the app's hero element — displayed a scoring model that **never existed**. Three layers each believed a different model:

| Layer | Dimensions |
|---|---|
| **Engine computes** (`skill_matcher.py` → `ScoreBreakdown`) | title 40, skill 40, location 10, recency 10, seniority ±8, **salary 10, visa 6, workplace 6** |
| **DB stores** (migration 0011) | role, skill, seniority, location, recency, **experience, credentials, semantic, penalty** |
| **Radar drew** (`ScoreRadar.tsx`) | role(15), skill(20), seniority(10), **experience(10), credentials(5)**, location(10), recency(10), **semantic(20)** |

Only **5 of 8 overlapped**. Consequences that shipped to users:
- `role` max **15** vs real `TITLE_WEIGHT=40` → a perfect title match plotted at **267%**, spiking outside the grid; `skill` 20 vs 40 → 200%.
- `experience` / `credentials` / `semantic` / `penalty` were **never computed by anything** → 4 permanently-zero axes (the exact trap rule #21 warns about).
- `salary` / `visa` / `workplace` **were computed every request and silently discarded** — real signal the user never saw.

### Root cause (git-verified)
The **chart came first and the database was bent to fit it.** `207a71a` (2026-04-08) shipped the frontend with 8 *invented* axes whose maxes sum to a tidy 100. `5ef2e49` (2026-04-25, Step 1.5) then added migration 0011 with the 9 columns *the UI expected* — `main.py:623` admits it: *"Engine doesn't currently produce experience/credentials/semantic/penalty — those columns persist as 0 until later batches."* Those batches never came. Authority ran **UI → API → DB**, with the engine evolving independently.

### The decision (Fable's recommendation, adopted): point the chart at the engine
**Do NOT build the missing dims to justify the chart.** The lucky fact: the real engine has *exactly 8* dimensions, so the 8-axis hero survives — only the labels/maxes/source change.
- Rejected "implement experience/credentials": would mean **inventing scoring rules to fill a drawing**, disturbing the clamped 130→100 economy (rule #27) and the 53+55 scorer/profile tests (rule #9).
- Rejected "implement semantic": it needs `SEMANTIC_ENABLED`, default OFF (rule #18) — you'd build a dim that is structurally 0 for most users. Same bug, new paint.
- **No new DB columns, deliberately.** salary/visa/workplace depend on the **caller's preferences**; a column on the shared `jobs` catalog would bake user A's salary target into a row user B reads (rules #10/#17). The detail route already recomputes the full breakdown per-user at read time — the three dims were literally sitting in a local variable and being dropped. Surfacing them = copying three fields.

### The honest half: `dims_active`
With enrichment off (the default), the four enrichment dims are hard 0. An 8-axis radar with 4 dead axes looks broken — and plotting 0% is a **lie**. So the API sends an explicit `dims_active` boolean and the UI renders those axes **dormant** ("not measured") instead.
**Never infer dormancy from zeros**: 0 is ambiguous — it can be a real, *earned* 0 (`visa_score=0` because the caller needs sponsorship and the job offers none). Tests pin both directions.

### Shipped (PR 1 — safe half, no scoring maths touched)
- `api/models.py`: `JobResponse` gains `salary_score`, `visa_score`, `workplace_score`, `dims_active`; dead fields kept (removal = PR 2).
- `api/routes/jobs.py`: surfaces the three discarded dims + sets `dims_active` from the enrichment-lookup hit.
- `ScoreRadar.tsx`: 8 REAL dims at REAL weights (40/40/10/10/8/10/6/6); dormant rendering; draw-clamp retained (now guarding the −8 seniority case only); average excludes dormant axes.
- `JobDetailClient.tsx`: header "8D Score Breakdown" → **"Score Drivers"** — the axes do *not* break down the score and must never be summed to it.
- Tests rewritten (10) — they previously **pinned the fiction** ("12/15", "18/20").

### ⚠️ Traps for whoever touches this next
1. **Dims never sum to `match_score`.** Raw max 130 clamped to 100; the −30/−15 title/location penalties live on **no axis**. Any `total = sum(axes)` will visibly contradict the score badge. Total comes from `match_score`, always.
2. **Zero is ambiguous, always.** Only `dims_active` separates "not measured" from "scored zero". Anyone "simplifying" by inferring from zeros re-introduces the lie subtly.
3. **Negative seniority can't be drawn** (a radar's centre is 0). The draw-clamp is NOT obsolete after the max fix — it is now *only* guarding the −8 case. Removing it re-inverts the polygon.
4. **The 4 enrichment weights are env-tunable** (`settings.py:148-151`) but title/skill/location/recency are hardcoded — retuning `SALARY_WEIGHT` in prod silently drifts from the frontend's hardcoded maxes. Cheap insurance: emit the maxes with `dims_active`.

### Still OPEN — PR 2 (needs owner sign-off)
Remove `experience`/`credentials`/`semantic`/`penalty` from `JobResponse` + the `Job` dataclass + `database.py` column lists; optionally migration 0026 dropping the 4 dead columns (all-zero on live data, so low-risk — but it IS live-data DDL, so it's the owner's call and can be deferred indefinitely).

---

## Session update 7 — 2026-07-16 — PII in logs (audit M9)

**Reported: 2 leaking log lines. Actual: 6.** `email_sender.py` logged the raw recipient on **five** separate lines (78, 83, 87, 93, 122, 124 — send-ok, send-failed, resend-ok, resend-error, resend-failed, no-credentials), not just the one flagged; `password_reset.py:108` logged the raw address of an *unknown* email on the reset path.

Why it matters: logs rotate, ship and get grepped — they outlive the request by far. An address is personal data, and the reset-path line leaked addresses of people who aren't even users.

**Fix:** `utils/logger.mask_email()` — `alice@example.com` → `a***@example.com`. Applied to all 6 sites.
- **Keeps the domain on purpose**: that's the delivery-debugging value (bounces, DNS, spam filtering) with far less identifying power than the local-part.
- **Keeps the first char on purpose**: enough to correlate two lines as the same user in a support ticket, without identifying them.
- **Degrades safely**: `None`/garbage → `<none>`/`***`, never echoed — a mis-passed value can't leak by accident.
- 11 tests, including **call-site** tests (rule #21): a masking helper nobody calls fixes nothing, so the tests assert the *logger output* contains `v***@example.com` and NOT the raw address.

---

## Session update 8 — 2026-07-16 — the finding docs now tell the truth themselves

**The drift:** `docs/fable/01`–`09` still described every finding as OPEN. Zero FIXED markers. All the truth lived only in this tracker — so anyone opening `01-SECURITY.md` read "your rate limits are broken, login leaks timing, XML DoS is live", all fixed weeks earlier. That drift is not cosmetic: **it already cost real work** — another session re-reported M5 and M8 as open bugs because the docs said so.

**Fixed:** every finding in `01`/`02`/`03`/`04`/`05`/`06`/`09` now carries its own **STATUS** line with the commit (35 findings marked), and `00-EXECUTIVE-SUMMARY.md` opens with a status banner — its "4 blockers" are all fixed, and the grades are explicitly flagged stale. Rule applied: only claim **FIXED** for what was verified in code; partials say **PARTIAL**; accepted risks say **OPEN (accepted)**. An over-claiming doc is worse than a stale one.

**Recorded honestly — a miss:** the plaintext-emails-in-logs leak (M9) was **not found by this audit**; an external one caught it. It's now in `05-COMPLIANCE` labelled as a miss, with *why* it was missed (this audit checked what data reaches **third parties** — Sentry, subprocessors — but never grepped what we write to **our own logs**) and a grep recipe for the next audit. The external report said 2 leaking lines; there were **6**.

### Open decision — `penalty` is NOT a dead column (contradicts Fable's PR-2 advice)
Fable called `penalty` the "4th dead column" — accurate on the symptom (always 0), wrong on the cause. `experience`/`credentials`/`semantic` are **fiction**: nothing computes them. But the engine **does** compute penalties (`_negative_penalty` −30 for a negative title, `_foreign_location_penalty` −15) — it just folds them into the total and never exposes them, because `ScoreBreakdown` has no `penalty` field. There is even a built **"Penalty Applied" UI card** (`JobDetailClient.tsx:616`) that has **never once rendered**.

So `penalty` is the same case as salary/visa/workplace: **real signal, computed, discarded** — and Fable's own principle ("surface what's computed, don't invent what isn't") says **wire it, don't delete it**. Wiring means adding one field to `ScoreBreakdown`; it changes **no maths** (the penalty is already inside `match_score`), only what's reported — so it stays in the safe category.

**PR 2 therefore becomes:** delete the 3 fictions (`experience`, `credentials`, `semantic`); **wire** `penalty`. The `DROP COLUMN` migration stays deferred (live-data DDL, owner's call, harmless to defer forever).

### ⛔ PR 2 — CANCELLED by owner decision (2026-07-16)

**Do not do this.** Not deferred — **declined**. Owner's call, and a sound one.

Scope that is now closed:
- Do NOT remove `experience` / `credentials` / `semantic` / `penalty` from `JobResponse`, the `Job` dataclass, or `database.py`'s column lists.
- Do NOT write migration 0026 dropping those columns.
- Do NOT wire `penalty` into `ScoreBreakdown` (the "Penalty Applied" card in `JobDetailClient.tsx:616` stays dormant by design).

**Why this is right:** the columns are all-zero `INTEGER DEFAULT 0` — they cost nothing and harm nothing. The dead penalty card simply never renders: invisible, not broken. Against that, `DROP COLUMN` on live Railway data is the one change a `git revert` cannot undo. PR 1 already delivered the value that mattered (the radar shows the real engine; salary/visa/workplace are surfaced instead of discarded). The remainder is cosmetic tidiness bought with irreversible risk — a bad trade.

**To a future session:** the dead fields and the dormant penalty card are **known and intentionally left**. Do not "clean them up". If you think otherwise, ask the owner — this was decided with the full picture, not by omission.

---

## Session update 9 — 2026-07-17 — the commit gate itself was broken (Fable-audited)

### The incident that exposed it
`agent-gate.sh` is silent for ~13min. I read silence as death and **relaunched 8 times** — ending with 8 concurrent full pytest suites on one Postgres, ~1,180 leaked `t_*`/`mem_*` schemas, and a wedged test DB. **I caused the outage I was diagnosing.** My "is it alive?" check (`ps aux | grep agent-gate`) was guaranteed to return 0 **even for a perfectly healthy run**: MSYS/Git-Bash `ps` shows only the executable name, never script args. I trusted an instrument that could only ever say one thing. On Windows use `tasklist` / `Get-CimInstance Win32_Process`.

### Fable's verdict: my 4 flaws were real — but only the ERGONOMIC layer
> *"The gate's actual correctness holes — stamp-after-run and hook TOCTOU — predate the incident and would let a bad commit through even on a quiet, single-run day."*

I had found what hurt *me*, not what was *broken*. The two it caught that I missed:

- **M1 (the real hole) — the stamp bound the WRONG tree.** `tree_fingerprint` ran *after* the 13-min suite, so any edit made **during** a run got blessed by a stamp whose tests never covered it — and the hook accepted it. This defeats the gate's single purpose. (I had been editing during runs all session.)
- **M2 — the cleanup sabotages other worktrees.** `pytest_sessionfinish` drops **every** `t_*`/`mem_*` schema in the shared DB — including schemas belonging to the other ~13 worktrees' *in-flight* suites. Under any concurrency this rips schemas out from under live runs, producing "random" native crashes independent of my kills.

Refuted (my worry, wrong): `set -euo pipefail` + subshells propagate failure correctly — a crashed pytest means no stamp. Fail-closed works.

### Shipped
- **M1 fix**: fingerprint at start, verify at end; tree moved → **no stamp**, loud failure. *Verified by test*: a simulated mid-run edit changes the fingerprint and the guard fires.
- **Advisory lock, per DATABASE SERVER** (not per worktree — ~14 worktrees share ONE Postgres, so the contention is on the DB, not the repo). `pg_try_advisory_lock` is **kill-safe**: it dies with the connection, so a hard-killed gate can never leave a stale lockfile. Launch #2 now aborts with *"another gate is running"* — exactly the message that would have stopped launches #2–#8.
- **Heartbeat + log**: `PYTHONUNBUFFERED=1`, output tee'd to `backend/data/logs/gate-<ts>.log`, and an "alive — Nm elapsed" line every 30s. **Silence is now distinguishable from death** — this kills the entire incident class.
- **Honest drift message**: the check regenerates the files first, so the worktree is correct *by construction* — the only possible failure is "you didn't stage them". It used to say "run gen:types" (the thing you just did); it now names the two files to stage.
- **The contract documented** in the script header: stage → gate → commit, and do not edit in between. Nobody had written it down.

### Live proof of M3 (found while documenting it)
Writing this very section was **blocked by the hook** — because the prose contained the literal phrase the hook greps for. `.claude/hooks/commit-gate.sh:9` does `case "$CMD" in *"git commit"*)`, a naive substring match against the whole command string. So *describing* the flaw trips the guard, while a compound command that mutates a file, stages it, and commits (all in one line) passes the check against the pre-mutation tree and commits unverified content. The guard blocks the innocent and waves through the actual bypass.

### Still open (owner sign-off — Fable's #4/#5/#7)
- **Wire `scripts/gate-fresh-db.sh`** — you already built it: a throwaway DB per gate. One `DROP DATABASE` replaces ~1,000 schema drops; a killed run leaks one easily-listed database instead of invisible schemas; cross-worktree interference vanishes. Changes the canonical command → your call.
- **Scope the sweep to own-run schemas** (`conftest.py`), so a finishing suite stops nuking other worktrees' live schemas (M2). Keep the global sweep as an opt-in janitor.
- **Real git `pre-commit` hook** to close the TOCTOU properly (fires inside git, at commit time, on the actual tree). Keep the PreToolUse hook for the friendly early error. Affects human commits too → your call.

---

## Session update 10 — 2026-07-17 — PR #48 inherits the PR-44 clobber (surfaced by `scrapingdesccion.md` §11b)

### What
This branch's history contains `3f532b2` — the stale-base commit that deleted 4,348 lines when PR #44 merged. Verified against this worktree: `.github/workflows/security.yml` (bandit/gitleaks/pip-audit CI), `frontend/src/lib/security-headers.ts` (M15), `test_xxe_hardening.py`, `test_security_hardening.py`, `test_pg_translate.py` and `docs/FABLE_FINDINGS.md` are all **absent here**, and the restore `b465fca` is in **neither** this branch nor `main`.

Uncomfortable detail: this session **cites `3f532b2` as the fix** for timing-safe login in `docs/fable/01`. That claim is true — and the same commit silently reverted newer work. Both facts hold at once.

### Verified, not assumed
- **This PR deletes nothing.** The merge base with the restore branch **is `3f532b2` itself**, which already lacks those files — so relative to the base this branch removes nothing, and `git merge-tree` shows it never touches `security.yml`. Once the restore lands, the files survive a #48 merge.
- **Not a live security hole.** `grep defusedxml` → 0 on main, but `_sanitize_xml()` (`sources/base.py`) strips `<!DOCTYPE>`/`<!ENTITY>` before parsing and billion-laughs needs an entity declaration. Confirmed present on this branch (4 hits). The other doc's §11b reaches the same conclusion independently.
- **Re-verified my own claims** that the clobber could have invalidated: gitleaks is real here (pre-commit config — the *CI* gitleaks is the clobbered one, a different thing); the XML guard is real.

### Action taken
PR #48 retitled **`[MERGE #47 FIRST]`** + a comment documenting the dependency, the evidence, and the 12 files that will conflict (`runner.py`, `pg.py`, `pgsync.py`, `api/main.py`, `routes/{auth,jobs,tailor}.py`, `email_sender.py`, `workers/tasks.py`, `tests/{test_api,test_account_mgmt,test_api_idor}.py`). PR #47 (`fix/restore-clobbered-by-pr44`, already titled "MERGE FIRST") restores everything and **keeps** 3f532b2's three genuinely-new pieces re-applied cleanly.

### What was NOT done, deliberately
**I did not merge #47 into this branch or resolve its 12 conflicts.** Saved memory: *"my cross-worktree merge caused a clobber another session had to restore"* — **that clobber is this clobber.** Resolving another session's work across worktrees, under an "urgent" label, is exactly the move that created the problem. Doing it again faster is not a fix. The restore's owner has it under control; the merge order is the fix.

### Merge order
1. **PR #47** — restores security CI, M15, 3 test files, FABLE_FINDINGS.md
2. **PR #48** — this work (resolve the 12 conflicts against the restored base)

Merging #48 alone is safe for *its own* content but leaves main without security CI.

---

## Session update 11 — 2026-07-17 — I made the suite 20x slower, and the gate caught me

### The regression (mine)
`_resync_identity_sequences` (my D3 fix) was set to run **unconditionally** on every `runner.up()`. My own comment justified it:

> *"Runs unconditionally, NOT just when we applied something: a DB whose sequences are already wrong must self-heal too. setval-to-MAX is idempotent **and cheap**."*

**"Cheap" was an assumption I never measured.** It costs **~2,821 ms** per call: 18 tables × (`pg_get_serial_sequence` + a `setval` whose `MAX(id)` subquery scans the table), on top of an `information_schema.columns` scan (215ms / 22.9k rows — itself inflated by leaked per-test schemas). `up()` is called by nearly every test, almost always with **nothing pending** — so nearly every call paid 2.8s for zero work.

Result: the backend suite went from **~14 min to ~5 hours** (measured: 8% after 26 minutes). I noticed only because the new heartbeat let me *see* it crawling instead of assuming it was dead — the previous silence would have hidden this as "another flaky gate".

### The fix
Restore the `if applied_now:` guard I had removed. **Correctness is unaffected:** a sequence can only fall behind *because a migration copied explicit ids*, so resyncing exactly when a migration is applied covers every case that creates the problem. Healing a DB broken by some earlier boot is a **one-off repair**, not a tax on every test run.

Verified: `test_identity_sequence_resynced_after_id_copy` (the entire reason the resync exists) still passes — 13/13 in 11.7s; `test_account_mgmt` 18/18 in 57s (was crawling).

### Why this one stings
The D3 fix was correct and well-tested. The *deployment decision around it* — "run it always, it's cheap" — was unmeasured, and it silently degraded the whole suite for every session sharing this machine. Same shape as the day's other failures: **a confident claim from an instrument I never actually consulted.** Here the instrument was a stopwatch, and I simply never started it.

**Rule:** if a helper runs on a hot path (every boot, every test), *time it* before choosing "always" over "only when needed". "Idempotent" says nothing about cost.
