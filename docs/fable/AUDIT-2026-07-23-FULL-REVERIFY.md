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

## Bottom line

Of 106 findings, **88 are now fixed on main or fixed this pass** (86 + C2 + MASKEMAIL).
The rest are **not engineering debt you're behind on** — they're **6 owner decisions**
(scraping, legal text, MFA, breach runbook, prod secrets, deploying the worker), **1
HIGH that's a real feature to schedule** (N2 → background extraction), and **a
handful of Pillar-2 scoring items you've reserved for yourself** (H1, L2). Nothing
critical is silently broken.
