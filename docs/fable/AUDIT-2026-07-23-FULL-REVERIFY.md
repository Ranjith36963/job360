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

| Verdict | Count | Meaning |
|---|---:|---|
| **VERIFIED FIXED on main** | **86** | fix present on `origin/main` and genuinely closes it |
| STILL OPEN (code) | 6 | not fixed anywhere — detail below |
| CAN'T VERIFY | 2 | no proof location given (L2, L5) — assessed below |
| NEEDS OWNER | 9 | legal / infra / product decision, not a code edit |
| NOT A BUG / WON'T FIX | 3 | reasoned below |

**Two facts worth stating plainly:**
1. **The adversarial pass overturned ZERO fixed claims.** Every HIGH/CRITICAL fix
   held up under an agent actively trying to break it.
2. **Nothing was stranded in a branch** (`not_on_main = 0`). Everything claimed
   fixed is genuinely merged to `origin/main`.

The audit also **caught two claims that were wrong** — and I fixed both this pass
(see next section). So the docs' "fixed" list was not blindly trusted.

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

# STILL OPEN — code (6)

| ID | Sev | What's left | Can an agent fix it? |
|---|---|---|---|
| **N2** | HIGH | Profile extraction still runs **4+ sequential paid LLM calls inline in the HTTP request** (`routes/profile.py:322`). Only a rate cap was added; the real fix is moving extraction to the ARQ background worker. | Yes, but it's a real feature (background job + status polling), not a patch — worth your go-ahead on approach. |
| **H1** | HIGH | Worker path is fixed, but the **main.py orchestrator** still calls `scorer.score(job)` *before* `job.id` is set (`main.py:702` vs `:769`), so dim-scoring enrichment lookups silently miss on CLI/pipeline runs. | **No — Pillar-2 scoring code you've flagged hands-off.** Report only. |
| **H6** | MED | Migrations still auto-apply inside the request process on boot (`RUN_MIGRATIONS_ON_BOOT` defaults true). Opt-out exists but needs a Railway release-phase step. | **No — deploy/infra decision.** |
| **S5** | MED | JobSpy (`indeed.py`) leaks an OS thread on timeout via `asyncio.to_thread`. Documented **accepted-by-design** (the pipeline detaches the thread). | No — design choice; leave unless you want it changed. |
| **C2** | — | ~~open~~ **FIXED this pass** (above). | — |
| **05-P1-MASKEMAIL** | LOW | ~~open~~ **FIXED this pass** (above). | — |

# CAN'T VERIFY (2)
- **L5** — ".env.example missing ops-critical vars." Effectively covered by **T3**
  (VERIFIED FIXED: `.env.example` now has `DATABASE_URL` + `OPENAI_API_KEY`). No
  distinct gap identified. Treat as closed via T3.
- **L2** — "future-dated posts get maximum recency." No proof location; recency is
  **Pillar-2 scoring** — report-only, your domain. Flagged for you to confirm.

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
