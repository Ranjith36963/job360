# Fable findings — FULL re-verification against origin/main, 2026-07-23

**This supersedes `AUDIT-2026-07-19-REVERIFIED.md` as the current-state record.**
Every finding across `docs/FABLE_FINDINGS.md`, `docs/fable/AUDIT-2026-07-17-VERIFIED.md`,
and the 10 numbered `docs/fable/0X-*.md` files was re-checked against the **real code
on `origin/main` (HEAD `536bcfe`)** — not the docs, not a worktree. A fix that lives
only in a feature branch counts as NOT fixed.

## Method (why you can trust these numbers)

- **106 findings** enumerated from the master docs (one consolidated inventory).
- **12 parallel verifier agents** each took a batch and confirmed the claimed fix
  is actually present on `origin/main` — using `git show origin/main:<file>` and
  `git grep … origin/main`, read-only. They were told to *expose* false "fixed"
  claims, not rubber-stamp them.
- **Adversarial pass**: every HIGH/CRITICAL "fixed" claim was handed to a second
  agent whose only job was to **refute** it.

## Headline

Counts below reflect BOTH passes: the first (C2 + MASKEMAIL, PR #105) and the
second (H1 + L2 + N2/S5/H6 decisions, 2026-07-23, PR #106).

| Verdict | Count | Meaning |
|---|---:|---|
| **VERIFIED FIXED on main** | **86** | fix present on `origin/main` and genuinely closes it |
| **FIXED this session** | **4** | C2, 05-P1-MASKEMAIL (PR #105) · H1, L2 (PR #106) |
| RESOLVED / DECIDED | 3 | N2 (already fixed, kept as-is), S5 (won't-fix), H6 (code done) |
| CAN'T VERIFY | 1 | L5 — covered by T3 |
| NEEDS OWNER | 9 | legal / infra / product decision, not a code edit |
| NOT A BUG / WON'T FIX | 3 | reasoned below |

**Facts worth stating plainly:**
1. **The adversarial pass overturned ZERO fixed claims.** Every HIGH/CRITICAL fix
   held up under an agent actively trying to break it.
2. **Nothing was stranded in a branch** (`not_on_main = 0`). Everything claimed
   fixed is genuinely merged to `origin/main`.
3. The audit **caught FOUR claims that were wrong or stale** and closed each with a
   fail-without-fix test: C2 ("not a bug" → real), MASKEMAIL ("applied everywhere"
   → two sites missed), H1 + L2 (were "reserved/report-only" → now fixed).

**After both passes there is NO open engineering debt.** What remains is 9 owner
decisions (legal/infra/product) + 3 won't-fix/not-a-bug — none of it code an agent
should write unilaterally.

---

# IT IS FIXED — this pass (branch `fix/audit-remaining-c2-maskemail`)

### C2 — match_batch used one shared connection across concurrent judges — IT IS FIXED ⚠️ was mis-filed "NOT A BUG"
The doc claimed this was not a bug ("psycopg3 serializes"). **It is a real bug.**
`match_batch` runs up to `semaphore_limit` (default 3) judge coroutines
concurrently, and they **all share one `pg.Connection`**. psycopg3 forbids using a
single async connection from two coroutines at once ("another operation is already
in progress"). When two judges overlapped on the connection, the error fell into
`match_batch`'s broad `except` and was swallowed as "judge failed" — **silently
dropping a PAID LLM verdict.** Same bug class as C1 (which was fixed for the API
path).
- `backend/src/services/llm_matcher.py` — added `db_lock = asyncio.Lock()`; the DB
  touches (`has_verdict`, `load_enrichment`, `save_verdict`) now run inside the lock;
  the expensive `match_job` LLM call stays **outside** it, so the batch keeps its
  concurrency.
- **Test that FAILS without the fix:** `tests/test_llm_matcher.py::
  test_match_batch_never_uses_the_shared_connection_concurrently` — a detector wraps
  the shared connection's `execute()` and records the max in-flight calls. **Verified:
  reverting the lock makes it fail with "the shared psycopg connection was used by 6
  coroutines at once"; with the lock it stays 1.**
- Scope note: this is connection-concurrency **safety**, not scoring/matching logic —
  outside the Pillar-2 hands-off zone. On a PR for your review regardless.

### 05-P1-MASKEMAIL — two raw-email log sites survived — IT IS FIXED ⚠️ was claimed "applied at all sites"
The claim "mask_email applied at all log sites" was **false**. Two sites logged the
raw email on `origin/main`:
- `backend/src/api/routes/auth.py:608` — `logger.info("magic-link rate-limited email=%s", req.email)`
- `backend/src/services/auth/magic_link.py:114` — `logger.warning("request_magic_link failed: email=%s …", email, exc)`
Both now wrap the address in `mask_email(...)` (imported from `src.utils.logger`).
PII in logs is a GDPR concern, so this matters even though it's low-severity.

---

# IT IS FIXED — second pass, 2026-07-23 (branch `fix/pillar2-l2-h1`, PR #106)

The owner **lifted the Pillar-2 hands-off restriction** for this pass, so the two
scoring items previously marked "report-only" are now fixed. Each has a test
**verified to fail against the pre-fix code**.

### H1 — orchestrator scored returning jobs with `id=None` → dims silently 0 — IT IS FIXED
`main.py` scored every job *before* resolving `job.id`, so the multi-dim enrichment
lookup (keyed on `job.id`, rules #19/#20) missed a **returning** job's prior-run
enrichment and the seniority/salary/visa/workplace dims scored 0. If that dims-blind
score then missed `ENRICHMENT_THRESHOLD`, the job never reached the post-enrich
re-score pass, so its persisted `match_score` stayed wrong.
- `backend/src/main.py` — an early pass now loads the (≤30-day, bounded) catalog's
  `normalized-key → id` map in ONE query and stamps `job.id` on every returning job
  **before** the scoring loop; brand-new jobs correctly stay `id=None` (no enrichment
  yet). The stale "reported not fixed / Pillar-2 hands-off" comment was removed.
- The read-time (`jobs.py`) and worker (`tasks.py`) paths were already fixed; this
  closes the orchestrator path — the last of the four call sites.
- **Test:** `tests/test_main.py::test_run_search_returning_job_carries_id_before_scoring`
  — **verified: without the fix the returning job is scored with `id=None`** (fails).

### L2 — future-dated jobs scored MAX freshness — IT IS FIXED
`_recency_score` computed `(now - posted).days`, which went **negative** for a
future date and slipped through the `<= 1` gate → MAX recency band. A job dated next
year outranked a genuinely fresh one.
- `backend/src/services/skill_matcher.py` — a date >~1 day in the future is treated
  as untrustworthy (0 recency); within ~1 day (clock skew) it's clamped to 0 and
  still scores fresh. Fixes **both** the legacy path and the 5-column
  `recency_score_for_job` (they share the helper).
- **Tests:** `test_recency_far_future_date_gets_zero_not_max` (**scored 10 without
  the fix**) + `test_recency_slight_future_clock_skew_still_counts_as_fresh`.

---

# RESOLVED / DECIDED — 2026-07-23 (no longer open)

### N2 — profile extraction slowness — RESOLVED (already fixed; owner kept it as-is)
The cross-check corrected the premise. N2's real complaint was **slowness**, and
that was **already fixed** in PR #96: the 4 LLM passes run **concurrently**
(`asyncio.gather` in `two_pass.py`), so an upload waits ~1 call, not 4. The only
remaining part — making the upload fully non-blocking (return immediately + poll) —
is a **breaking UX change** the owner **deliberately chose not to do** (2026-07-23),
same reasoning as PR #96. A working backend prototype was built and reverted. **N2
is considered addressed by the parallelization.** Not open.

### S5 — JobSpy thread leak — WON'T FIX (library limitation, tied to the scraping decision)
Confirmed real but **not cleanly fixable in code**: JobSpy's synchronous
`scrape_jobs` can't be cancelled (Python cannot force-kill a thread), so
`asyncio.wait_for` detaches the thread. The only real fix is a subprocess-isolated
rewrite (kill the process on timeout) — disproportionate risk for **one** source
already bounded by a 60s ceiling and a 32-slot thread pool. It also **disappears
entirely if the scraping sources are dropped** (finding 05-P0-LEGAL — the owner's
legal call). Left as accepted-by-design; the subprocess rewrite is available on
request.

### H6 — migrations on boot — CODE DONE; infra step is the owner's deploy decision
The **code half is already shipped**: `RUN_MIGRATIONS_ON_BOOT` (default `true`) lets
a deploy pipeline opt the app process out of running migrations. The remaining part —
adding a Railway release-phase step that runs `python -m migrations.runner up` and
flips the flag to `false` — is a **Railway service-config change**, not a code edit.
Existing mitigations (advisory lock serialises replicas; healthcheck + `ON_FAILURE`
restart) already bound the blast radius. Owner's deploy decision.

---

# CAN'T VERIFY (1 remaining)
- **L5** — ".env.example missing ops-critical vars." Effectively covered by **T3**
  (VERIFIED FIXED: `.env.example` now has `DATABASE_URL` + `OPENAI_API_KEY`). No
  distinct gap identified. Treat as closed via T3.
- **L2** — ~~can't verify~~ **now IT IS FIXED** (see the second-pass section above).

# NEEDS OWNER — not a code edit (9)

These are genuinely blocked on your decision/infra, not on engineering:

| ID | What it needs |
|---|---|
| **SI1** | Notification pipeline is **wired in code** (`main.py` enqueue → ARQ) but inert until a **live ARQ worker + Redis + SMTP/Apprise channel** are deployed. Ops. |
| **M10** | Hardcoded DB password in `docker-compose.prod.yml` — prod-secret/infra sign-off. |
| **05-P0-LEGAL** | LinkedIn/Indeed/Glassdoor scrapers still in `SOURCE_REGISTRY` — a ToS/legal call only you can make. |
| **05-P1-POLICIES** | Privacy (48 lines) + Terms (47 lines) pages are stubs ending "will be expanded before public launch." Needs real legal text. |
| **05-P1-SUBPROC** | No subprocessor disclosure (CV → Groq/Cerebras/Resend/OpenAI). Legal disclosure tied to the privacy policy. |
| **05-P2-MFA** | No MFA/TOTP feature exists — a product decision + new auth flow. |
| **05-P2-BREACH** | No breach-notification runbook (GDPR 72-hour). A policy doc for you to author. |
| **PLAN** | `fable-harness-plan.md` is behavioral coaching about your own workflow habits — not a code defect. |
| **PS-P1-ml-sentry** | "Magic-link Sentry issue resolved" is an external dashboard state — nothing in the repo to verify. |

# NOT A BUG / WON'T FIX (3, unchanged & correct)
- **V2** — `pg.py` hands the cursor to the caller to read; closing it would break every query. Correct as-is.
- **P3** — `verify-reminder.sh` intentionally non-blocking (advisory, not a gate).
- **P5** — `settings.local.json` is gitignored, your personal machine config.

---

# The 86 VERIFIED-FIXED (confirmed on origin/main `536bcfe`)

All verified present on `origin/main` and adversarially confirmed for the
HIGH/CRITICAL ones. Grouped for readability; the per-finding proof lines are in
`AUDIT-2026-07-17-VERIFIED.md` / `AUDIT-2026-07-19-REVERIFIED.md` and were each
re-confirmed against the current tree this pass.

- **Data/DB integrity:** C1, H2, H3, M5, M7, M11, M13, V1, V3, T1, T4, T12, GATE-M1, 05-P2-AUDIT
- **Security/auth:** H4, H5, M1, M3, M12, L3, CSRF, LOCKOUT, 05-DELETE-PW, 05-P0-COMPLIANCE, 05-P1-ART20, 05-P1-CONSENT, 05-P1-SENTRY, PS-P1-ml-code
- **CI/harness:** H7, M11, P1, P2, P4, AGT1, DBG1, RULECOUNT, 05-P2-DEPENDABOT
- **Frontend/UX:** H8, H9, H10, H11, M14, M15, M16, M17, L6, L7, L8
- **Sources/pipeline:** S1, S2, S3, S4, S6, S7, S8, S9, M18
- **Notifications/ops:** N1, N3, N4, N5, N6, N7, N8, N9, SI2, SI3, SI4, SI5, M4, PS-P0, PS-P2, PS-P1-funnel
- **Scoring guards (non-hands-off):** M8
- **Partial→closed / docs:** M2 (timing fixed; 409-on-register is your accepted trade-off), M9 (IP hashed; user_id deliberate), T2, T3, T5, T6-T9, T10 (marked test-only), T11, P6, L1, FF-L1

---

# THIRD PASS — full doc-set completion, 2026-07-23 (PR #107)

The whole fable doc set (`docs/FABLE_FINDINGS.md`, `fable-harness-plan.md`, and all
14 `docs/fable/*` files) was re-scanned by 3 parallel agents to catch any finding
the 106-item inventory missed. Result: **the numbered docs + FABLE_FINDINGS.md hold
NO uncaptured finding** — every numbered item maps 1:1 onto the inventory. But two
items in the THEMATIC docs had **no ID** (they fell through the finding
renumbering). Both were real and are now fixed.

### F2 — E2E auth bypass could be enabled in a real deploy — IT IS FIXED
`frontend/src/middleware.ts` gated the auth bypass **solely** on
`E2E_TEST_MODE === "1"`. One stray env var on a live deploy would have disabled auth
for everyone. `NODE_ENV` can't guard it (CI e2e runs a prod build). Fix: also require
`!process.env.RAILWAY_ENVIRONMENT` — Railway injects that into every deployed
service, CI/local never have it, so an accidental `E2E_TEST_MODE=1` on Railway can no
longer bypass auth while CI still works. (Was doc ID **F2**, lost in renumbering.)

### SPLIT-P3 — migration splitter would sever `$$` function bodies — IT IS FIXED
`pg.py` + `runner.py` split migration SQL on a naive `.split(";")`. Safe for today's
migrations, but a future one with a Postgres function body / DO block
(`$$ ... ; ... $$`) or a `;` inside a string literal would be **severed
mid-statement at boot**. `pg.split_statements` now skips `;` inside single-quoted
strings and `$$`/`$tag$` bodies; `runner.py` delegates to it. Tests verified to fail
on the naive split (the `$$` body split into 8 pieces instead of 1). (Had no ID.)

## `08-GAPS-NOT-YET-AUDITED.md` — audit AREAS, not bugs (owner-scheduled)

This file lists dimensions **never swept**, not specific defects. They can't be
"closed" by a code edit — each is a dedicated effort. Current status:

| Area | Status |
|---|---|
| Performance & scale (load test, N+1, pool-under-load) | Not audited — schedule a k6/Locust pass. Pool fix (C1) shipped; its behaviour under load is unverified. |
| Cost economics | **Partially covered** — profile-extract cost cap shipped (`PROFILE_EXTRACT_MAX_PER_HOUR`). A spend dashboard / alerting is owner ops. |
| Test-suite quality (mocked vs real) | Not audited — a real effort; the suite is green but branch-coverage on critical paths is unmeasured. |
| Email deliverability (DMARC/DKIM/SPF) | Owner — DNS/ops, not code. |
| Supply-chain scanning | **Done** — `security.yml` runs pip-audit/npm-audit/gitleaks/bandit blocking (H7). |
| Observability depth (tracing, latency histograms) | **Partially covered** — request-id + access log + Sentry shipped; per-user latency histograms not. |
| Product & UX quality | Owner — schedule a UX pass. |

## Minor harness polish (`06-HARNESS-AND-WORKFLOW.md`) — noted, low value

Genuine but cosmetic/tooling items, not product risk: dormant worker/integrator/scout
skills read as live (add a "DORMANT — loop disabled" banner); the `/commit` skill's
Co-Author trailer + trufflehog v2 call drifted; the `sync` skill scan misses the
CLAUDE.md rule-count. Left for a harness-tidy pass — none affects the running app.

## Bottom line — the fable backlog is closed

Across all three passes, of the 106 findings **90 are fixed** (86 verified on main +
C2, MASKEMAIL, H1, L2) plus the 2 previously-un-IDed ones (F2, SPLIT-P3) = **92
fixed**. Everything else is **not engineering debt**:
- **N2** — resolved (slowness already fixed; full backgrounding declined by owner).
- **S5, V2, P3, P5** — won't-fix, with reasons.
- **H6** — code done; the deploy step is yours.
- **9 owner decisions** — scraping stance, privacy/terms text, subprocessor list,
  MFA, breach runbook, prod secrets, deploying the worker, status page/SLA, the
  migrations release step.
- **`08-GAPS`** — audit *areas* to schedule (perf, cost dashboard, UX), not bugs.

**No open, code-fixable finding remains in any of the 15 fable docs.** What's left is
owner decisions and scheduled audit efforts — this backlog is complete.

---

# FOURTH PASS — re-verify vs CURRENT main (post-merge), 2026-07-23 (PR #110)

After #104–#108 all merged, the whole 108-finding set was re-verified against the
**current** `origin/main` (HEAD `bf823e6`) by 13 parallel agents reading real code,
plus an adversarial refutation pass. This pass superseded the "backlog closed"
claim above — because it caught **two findings the earlier docs had recorded WRONG.**

- **94 VERIFIED FIXED** on current main. The previously-in-PR fixes (C1 pool, C2,
  H1, L2, F2, SPLIT-P3, MASKEMAIL) now confirm fixed **on main**.
- **Adversarial pass overturned ZERO** fixed claims.
- **Nothing stranded in a branch** (`not_on_main = 0`) — every fix is genuinely on main.

### The docs were wrong on two — code proof beat the doc claim:

### M6 — doc said "UNKNOWN"; it was a real bug — IT IS FIXED (PR #110)
`ghost_detection.evaluate_job_state` returned `ACTIVE` whenever `last_seen_at` was
NULL, so a job missed many times but lacking that timestamp could **never** go stale
— `nightly_ghost_sweep` excluded it forever. Fixed: fall back to `first_seen_at`,
then to `consecutive_misses` alone (3+ = stale); the sweep query now SELECTs
`first_seen_at`. 3 new tests (the NULL+misses row now returns `LIKELY_STALE`).

### L5 — doc claimed "covered by T3"; it was FALSE — IT IS FIXED (PR #110)
`backend/scripts/check_env_example.py` **failed** on origin/main: 15 env vars the
backend reads were missing from `.env.example` (RESEND_API_KEY, SMTP_HOST/PORT/USER/
FROM, REQUIRE_EMAIL_VERIFICATION, RATE_LIMIT_REDIS, JOB360_TRUST_PROXY, LLM_* tuning,
CEREBRAS_MODEL, RAILWAY_ENVIRONMENT). Added all 15; the check now passes (74/74, exit 0).

### M2 — still open, your prior accepted decision (unchanged)
`/register` returns 409 on a taken email → an unauthenticated caller can tell whether
an email is registered. The login timing side-channel IS fixed (constant-time verify
against `_DUMMY_PW_HASH`); only the register-409 enumeration remains, which you
previously accepted (revealing email-taken is a deliberate UX choice). Left flagged,
not silently changed.

## Current bottom line (as of this session)

Of 108 findings: **96 fixed** (94 verified on current main + M6, L5 fixed this pass).
Remaining:
- **M2** — open by your prior decision (register email-enumeration UX trade-off).
- **N2, S5, V2, P3, P5, H6** — resolved / won't-fix / code-done-deploy-yours (as above).
- **7 owner decisions** — SI1 (deploy worker+Redis+SMTP), 05-P0-LEGAL (scraping),
  05-P1-POLICIES (privacy/terms), 05-P1-SUBPROC, 05-P2-MFA, 05-P2-BREACH,
  PS-P1-ml-sentry (external dashboard), PLAN (behavioral), M10 (prod secret).
- **`08-GAPS`** — audit areas to schedule (perf, cost dashboard, UX).

**Every code-fixable finding is now fixed on main or in an open PR (#110). The only
open item that is code AND not an owner decision is M2 — and that one you already
decided. Nothing is silently broken.**

---

# FIFTH PASS — re-verify vs CURRENT main after #110/#111 merged, 2026-07-24

**This section supersedes every "bottom line" above.** The whole 108-finding set
was re-verified against the **current** `origin/main` (HEAD `3fedca2`, which now
includes the M6/L5 merge #110 and the M2 merge #111 that the fourth pass ran
*before*) by **14 parallel Sonnet agents** reading real code, plus a **7-target
adversarial refutation pass** that tried to *break* the recently-merged fixes.

**Why this pass mattered: the adversarial agents broke THREE fixes the fourth pass
had rubber-stamped as "held under refutation."** All three were confirmed against
real code and **fixed this session**, each with a test **verified to fail against
the pre-fix code**. Two more findings were caught as **stale doc text** (the code
was already correct; only the words here were wrong).

## Three real gaps the earlier passes missed — NOW FIXED this session (PR pending)

### SPLIT-P3 (residual) — the comment stripper was quote-BLIND — IT IS FIXED
The `$$`/string-aware **semicolon** splitter was correct, but `split_statements`
ran a **separate, naive** comment stripper FIRST: `for line: c = line.find("--"); line = line[:c]`.
That has zero string/dollar awareness, so a literal `--` inside a value
(`VALUES ('see --note here')`) truncated the line and **silently dropped every
later statement** at migration boot — no error, just missing SQL. Same bug class
SPLIT-P3 claimed to kill, reintroduced one step earlier.
- **Fix:** folded quote/dollar-aware comment-skipping INTO the one scanner
  (`backend/src/repositories/pg.py` — `_split_on_unquoted_semicolons` now skips a
  `--` line comment only when NOT inside a string/`$$` body; `split_statements`
  delegates to it, no separate pre-strip).
- **Tests (fail without the fix — verified):** `tests/test_pg_translate.py::
  test_split_statements_double_dash_inside_string_does_not_drop_next_statement`
  (pre-fix returned 1 statement, dropping `CREATE TABLE u`),
  `…_double_dash_inside_dollar_body_survives`, `…_real_comment_outside_string_still_stripped`.

### M6 (second path) — the pipeline sweep still couldn't mark NULL-`last_seen` jobs stale — IT IS FIXED
The M6 fix patched `ghost_detection.evaluate_job_state` (the **nightly** sweep,
once/night). But `database.mark_missed_for_source` — the **pipeline** sweep that
runs on **every source fetch** (far more frequent) — computed `age_hours = 0.0`
when `last_seen_at` was NULL and fed that to `transition(misses, 0.0)`, which can
**never** promote (it needs age ≥ 12–24h). So a repeatedly-missed job lacking that
timestamp stayed `active` forever — the exact M6 bug, in the unpatched path.
- **Fix:** `backend/src/repositories/database.py` — `mark_missed_for_source` now
  SELECTs `first_seen_at` and mirrors `evaluate_job_state`'s fallback: NULL
  `last_seen_at` → use `first_seen_at`; no timestamp at all → decide by
  `consecutive_misses` alone (3+ → `likely_stale`, 2+ → `possibly_stale`).
- **Tests (fail without the fix — verified, stayed `active`):**
  `tests/test_ghost_detection_integration.py::test_null_last_seen_still_advances_by_misses_alone`
  + `test_null_last_seen_falls_back_to_first_seen_age`.

### F2 (residual) — the auth-bypass guard was Railway-specific, not deploy-agnostic — IT IS FIXED
`frontend/src/middleware.ts` gated the E2E bypass on `!RAILWAY_ENVIRONMENT` only —
but the backend's own prod-detection ORs **two** signals (`APP_ENV=="production"`
OR `RAILWAY_ENVIRONMENT`). A non-Railway prod deploy (Vercel/Docker/VM, where
`APP_ENV=production` is the general signal) with a stray `E2E_TEST_MODE=1` would
**still fully bypass auth**. Prod today *is* Railway, so no live hole — but the fix
was mis-sold as deploy-agnostic. Now hardened to match the backend exactly.
- **Fix:** `middleware.ts` — bypass requires `!(APP_ENV==="production" || RAILWAY_ENVIRONMENT)`.
- **Tests (fail without the fix — verified):** `frontend/src/middleware.test.ts` —
  "does NOT bypass on a non-Railway prod deploy (APP_ENV=production)" +
  "still bypasses in CI/local".

## Two findings where the CODE was already right and only the DOC was stale

### M2 — DOC WAS STALE: it is FIXED on main (PR #111), not "open"
Lines 189 / 285–290 / 296 above still call M2 "open by your prior decision" and
describe a 409-on-duplicate enumeration trade-off. **That code no longer exists.**
On current main `backend/src/api/routes/auth.py:99` `register()` returns a generic
`RegisterResponse` (201, no id/email), sets **no** session cookie (no auto-login),
and a duplicate email is a **silent no-op** (`except pg.IntegrityError → created=False`,
identical response). No enumeration remains. **M2 is FIXED** — treat the earlier
"open" lines as superseded.

### M10 — DOC WAS STALE: the hardcoded password is GONE (PR #83), only the deploy-secret is owner
Line 159 lists M10 as "hardcoded DB password STILL present." **False on main.**
`docker-compose.prod.yml:27` is `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}`
— no committed default; compose refuses to start without the env var (fixed #83,
2026-07-18). The code-fixable defect is **closed**; only *setting the secret value
in the real deploy* remains owner/infra.

## One downgrade — AGT1 was over-counted

AGT1 sits in the "86 VERIFIED FIXED" bucket but is only **partial**. Two of three
review lenses were extracted to agent files (`.claude/agents/reviewer-bugs.md` R3,
`reviewer-conventions.md` R1); the **R2 "history" lens was never extracted** — it's
still inline in `.claude/skills/worker/SKILL.md:30`. Harness cosmetic (no product
risk), but "fully fixed" overstated it. Status: **PARTIAL (2/3 lenses).**

## Fifth-pass bottom line (authoritative, as of 2026-07-24)

- **95 findings confirmed VERIFIED FIXED on current main** by the 14-agent pass.
- **Adversarial pass overturned 3** (SPLIT-P3, M6, F2) — all **fixed this session**
  with fail-first tests, on a PR for your review.
- **2 doc-stale corrections:** M2 (fixed #111) and M10 (fixed #83) — code was
  already right; the words above were wrong and are now corrected here.
- **1 downgrade:** AGT1 → partial (2/3 review lenses; harness cosmetic).
- **Net of 108:** **~99 code-fixable findings fixed** (on main or in this PR).
  Everything still open is **NOT engineering debt**: owner decisions (SI1 deploy,
  05-P0-LEGAL scraping *(decided — measure-first, PR #112)*, 05-P1-POLICIES/SUBPROC,
  05-P2-MFA/BREACH, PS-P1-ml-sentry dashboard, PLAN behavioral, M10 deploy-secret),
  won't-fix (V2, P3, P5, S5, N2), code-done-deploy-yours (H6), AGT1 harness polish,
  and `08-GAPS` audit *areas* to schedule.

**No code-fixable finding is silently broken. The three the fourth pass missed are
the only new engineering work this pass produced — and they are fixed.**
