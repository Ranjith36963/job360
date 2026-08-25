# Fable findings — RE-VERIFIED against main, 2026-07-19
<!-- doc: LOG -->

**This file supersedes `AUDIT-2026-07-17-VERIFIED.md`.** That document's verdicts had
drifted badly out of date and were actively misleading (details below).

Every verdict here was produced by reading the **current code on main** and quoting the
decisive line. No verdict was copied from the previous audit; several contradict it.

## Headline

Two columns: what re-verification found, and where it stands after the fix batch
in PR #94.

| Verdict | On re-verify | After PR #94 | Meaning |
|---|---:|---:|---|
| **FIXED** | 57 | **67** | the described problem no longer exists in the code |
| **PARTIAL** | 9 | **3** | main risk closed; a named sub-part remains |
| **OPEN** | 9 | **3** | still exactly as described |
| **WON'T FIX** | 0 | **2** | fixing it would do harm; reasoned below |
| | **75** | **75** | (+ H7 counted twice in the source doc = 77 sections) |

The previous doc claimed **83 OPEN**. The true number was **9**, and is now **3**.

**The table counts only the 75 original findings, and is unchanged by the later
erasure/silent-failure batch** (branch `fix/erasure-and-data-integrity`, section
immediately below). That batch fixed **8 further bugs that no finding had named** —
including a GDPR-relevant one where account erasure could report success while data
survived. They are counted separately on purpose: quietly folding them into "FIXED"
would inflate the audit's score with work the audit never found. Two items on that
batch's own list turned out **not to be bugs** and were left alone, which is recorded
there too.

**Everything still open needs a decision or infrastructure that code cannot
supply** — see "Genuinely yours" at the bottom.

## Why the old doc was dangerous, not merely out of date

A findings document that is never re-verified stops being a record and becomes
misinformation — and because it *looks* authoritative, it stops people from checking.
Two concrete costs, both incurred on 2026-07-19:

1. **H5 / `JOB360_ENV`** — the old doc (and the root `CLAUDE.md` env table) stated the
   session cookie only gets `Secure` when `JOB360_ENV=prod`, which was never set on
   Railway. A session was spent preparing to raise this as a live security hole.
   **It was fixed a week earlier.** `auth.py:121` now reads
   `secure = _is_production()`, which is `APP_ENV=production OR RAILWAY_ENVIRONMENT`
   (`middleware.py:34-38`) — and Railway injects `RAILWAY_ENVIRONMENT` automatically.
   `JOB360_ENV` survives only in a comment at `auth.py:120`. The `CLAUDE.md` row has
   now been corrected.
2. **M9** — listed as "client IP written unredacted to disk". Actually fixed:
   `middleware.py:112` hashes it via `mask_ip()` (`logger.py:245`, SHA-256 → `ip_<12hex>`).

The same staleness ran deeper: several sections near the top of the old file were
already superseded by "CLOSED in Batch N" sections **further down the same file**
(N4, T6-T9, T5, T12, S4). A reader taking the top-level verdicts at face value would
have re-done finished work.

**Rule going forward: re-verify against code before acting on any finding here.**

---

---

# IT IS FIXED — erasure + silent-failure batch (2026-07-19, branch `fix/erasure-and-data-integrity`)

Theme of this batch: **the dominant bug class in this codebase is silence.** Every
item below was code that failed without saying so. Nothing here changes a happy
path; each change makes an already-broken case audible.

Each fix has a test that was **verified to fail against the un-fixed code**. That
check is the point — two tests in this batch passed against the broken code on the
first try and were rewritten. A test that cannot fail is not coverage, it is
decoration.

### Account erasure could silently leave data behind — IT IS FIXED ⚠️ GDPR
`hard_delete_user` deleted from each per-user table inside a loop. A failure on one
table was swallowed, the function returned normally, and the user was told their
account was erased while rows survived. Under GDPR that is a false statement to a
data subject, and nothing anywhere recorded it.
- `backend/src/repositories/database.py:1516-1521` — per-table failures are now
  **collected** (`deletion_errors`) instead of discarded.
- `backend/src/repositories/database.py:1565-1575` — after the commit, every table
  is **re-counted** (`survivors`); the code checks the outcome rather than trusting
  that the DELETE it issued did anything.
- `backend/src/repositories/database.py:1577-1579` — if anything failed *or*
  anything survived, it **raises**. Erasure now either completes or says it didn't.
- Test: `backend/tests/test_erasure_is_verified.py` (2) — asserts the OUTCOME
  (rows gone / exception raised), not that the code contains a try/except.
- **Why verify instead of trusting the DELETE:** the count is the only evidence that
  survives a partial failure. A DELETE that matched zero rows and a DELETE that was
  never reached look identical from the caller's side.

### Signing up with `Ranjith@…` then logging in with `ranjith@…` locked you out — IT IS FIXED
Email was compared with `=`, which is case-sensitive in Postgres (unlike SQLite,
where this code originally ran). A user who typed their address with different
capitalisation was told "no such account" — for login, magic-link, and
password-reset alike. Reset and magic-link fail **silently by design** (they must
not reveal whether an address exists), so the user had no way to tell the difference
between a typo and a real lockout.
- `backend/src/api/routes/auth.py:230` — login lookup → `WHERE LOWER(email) = LOWER(?)`
- `backend/src/api/routes/auth.py:411` — email-change uniqueness check, same
- `backend/src/services/auth/magic_link.py:178` — magic-link lookup, same
- `backend/src/services/auth/password_reset.py:103` — reset lookup, same
- Registration now stores `req.email.lower()`, so new rows are canonical.
- Test: `backend/tests/test_email_case_insensitive.py` (3)
- **Deliberately not done:** no migration to lowercase existing rows. That is a
  destructive one-way write over live user data and belongs to the owner, not to me.
  The `LOWER()` reads make existing mixed-case rows findable *without* touching them.

### A failed down-migration left the schema half-reverted, forever — IT IS FIXED
`up()` already had this fix (finding H2); `down()` never did, and it is the more
dangerous of the two. The connection is autocommit, so `executescript()` commits
each statement separately. Migration 0011's down path is
`DROP INDEX ×5 → rebuild jobs → CREATE INDEX ×5`. If anything after the first
statement failed, the dropped indexes stayed dropped, the exception propagated, and
the `DELETE FROM _schema_migrations` never ran — so the migration was still recorded
as **applied** and `up()` would never re-run it to repair. Nothing errors afterwards.
Every query against `jobs` just does a sequential scan from then on.
- `backend/migrations/runner.py:326` — body + bookkeeping now commit in **one**
  `async with db.transaction():`. Either both move or neither does.
- Test: `backend/tests/test_migration_down_atomic.py` (2)
- **This is the near-miss worth recording.** The first two versions of that test
  passed against the broken code. The mock raised *before* anything committed, so
  both versions behaved identically and the bookkeeping assertion could never fire.
  Only a mock that **partially applies and then fails** — and an assertion on the
  leaked table rather than on the migration row — actually separates the two.

### A search's notification enqueue could vanish mid-flight — IT IS FIXED
`asyncio.create_task(...)` was called without keeping a reference. Python's garbage
collector is free to collect a task nothing holds, so the notification enqueue could
disappear part-way with no error. Rare, load-dependent, and completely invisible —
the worst combination to debug from a bug report.
- `backend/src/api/routes/search.py:137` — module-level `_search_bg_tasks` set
- `backend/src/api/routes/search.py:255-256` — task added to the set, and
  `add_done_callback(_search_bg_tasks.discard)` removes it on completion so the set
  cannot grow without bound.

### The PRIMARY LLM provider was never installed, and the log looked normal — IT IS FIXED
`openai` was absent in production. The import raised, the provider chain caught it
with `except Exception`, logged `"openai failed, trying next provider: No module
named 'openai'"` at **WARNING**, and fell through to a free tier. The documented
primary provider ran zero times for months. That line was indistinguishable from a
provider having a bad minute.
- `backend/src/services/profile/llm_provider.py:71-109` —
  `_is_permanent_provider_failure()` separates *will recur until a human acts*
  (missing module, 401/403/404, bad key, wrong model name) from *transient*
  (429/500/503, timeouts).
- `backend/src/services/profile/llm_provider.py:222,287` — permanent failures log at
  **ERROR** so Sentry sees them; transient stay at WARNING.
- **Control flow is unchanged.** Falling through to the next provider is correct
  resilience and stays. Only the volume changes.
- The classifier is deliberately **conservative**: anything unrecognised counts as
  transient. Missing a permanent failure costs one log level; crying wolf on every
  blip would make ERROR worthless, which is the exact failure being fixed.
- Test: `backend/tests/test_provider_failure_classification.py` (13), including an
  explicit case asserting an unknown error is *not* flagged permanent.

### A paid LLM verdict could be written nowhere — IT IS FIXED
`save_verdict` issued an UPDATE and never checked whether it matched a row. If the
`user_feed` row had been purged between the judge call and the write, the money was
spent, the verdict was discarded, and the logs said nothing.
- `backend/src/services/llm_matcher.py:175` — warns when `rowcount == 0`.

### Frontend: four silent failures — IT IS FIXED
- `frontend/src/app/dashboard/page.tsx` — the search poll had no ceiling. A stuck run
  polled forever. Now bounded by `MAX_POLL_MS` (10 min) and 20 consecutive errors.
- `frontend/src/app/jobs/[id]/JobDetailClient.tsx` — an empty `catch` meant a failed
  action looked like a successful one. Now toasts.
- `frontend/src/app/pipeline/page.tsx:129-136` — advancing a stage **succeeded**, then
  a blip on the follow-up counts request threw into the outer catch and showed
  "Failed to advance stage". Users saw the card move *and* a red error, so they either
  distrusted the board or re-advanced the same application twice. The cosmetic counts
  refresh now has its own try/catch and stays silent — the user's action worked.
- `frontend/src/components/profile/PreferencesForm.tsx` — a failed save left the form
  looking saved. Now tracks `saveFailed`.

### Two items from the goal list — NOT BUGS (checked, not assumed)
- **`applications` rows "orphaned" by erasure.** Not a defect. `database.py:24-29`
  excludes them **deliberately** under hard rule #3. "Fixing" it would delete users'
  job-application history — real data loss dressed as a cleanup.
- **"Two different date formats."** Cosmetic only. Every expiry write and every
  comparison uses the same `Z`-suffixed format, so nothing mis-compares. Left alone.

---

# IT IS FIXED — closed in PR #94 (2026-07-19)

Ten findings moved from OPEN/PARTIAL to fixed. Each line is the code that
settles it, not a claim.

### L6 — IT IS FIXED
Frontend now emits Next standalone output; the runner copies `.next/standalone`
+ `static` + `public` instead of the whole `node_modules`.
- `frontend/next.config.ts` — `output: "standalone"`
- `frontend/Dockerfile` — `COPY --from=builder /app/.next/standalone ./` and
  `CMD ["node", "server.js"]`
- **Proof it runs:** a real `npm run build` produced `.next/standalone/server.js`
  — the exact file the new CMD executes. (First build attempt was interrupted and
  produced no `server.js`; that was caught rather than assumed.)

### M4 — IT IS FIXED (the code half)
- `backend/src/workers/settings.py` — `health_check_interval = 30` (arq's default
  is **3600**, so a worker dead at 00:01 still looked alive at 00:59), plus an
  explicit `max_tries = 5`.
- `backend/Dockerfile.worker` — `HEALTHCHECK ... CMD ["arq","--check", ...]`.
- **Why it stayed open so long:** the file's own comment asserted *"No EXPOSE /
  HEALTHCHECK: the worker does not serve HTTP"*, which reads as a reason it is
  impossible. It is not — `arq --check` reads a Redis heartbeat and needs no HTTP.
  The false comment was the blocker; it is corrected in place.
- **Honest caveat, kept in the Dockerfile:** Railway does not honour Dockerfile
  HEALTHCHECK for non-HTTP services, so this does not by itself restart a dead
  worker there. External uptime monitoring still belongs on the owner's list.

### N7 — IT IS FIXED
`notification_tick` ran `SELECT timezone FROM users` once per rule inside its
loop — on a cron that fires every 5 minutes, growing linearly with users.
- `backend/src/workers/tasks.py` — single `LEFT JOIN users u ON u.id = r.user_id`.
- LEFT, not INNER, so a rule whose user row is missing still evaluates and falls
  back to UTC exactly as the old per-row `except` did.

### M7 — IT IS FIXED
`mark_missed_for_source` issued its UPDATEs under autocommit, so a crash mid-sweep
left a source half-swept and the halves drifted apart on the next run.
- `backend/src/repositories/database.py` — the loop is wrapped in
  `async with self._db.transaction():`; the explicit trailing `commit()` is gone
  (the context manager commits on exit).

### S2 — IT IS FIXED
Reed fanned out 8 titles × 3 locations = 24 requests × 2.0s = **~48s** of enforced
delay against a 60s `SOURCE_FETCH_TIMEOUT`. It did not always blow the ceiling,
which is worse than always failing: the source died intermittently, and a timeout
looks identical to "Reed had nothing today".
- `backend/src/sources/apis_keyed/reed.py` — the cap is now DERIVED from the
  budget (`SOURCE_FETCH_TIMEOUT * 0.6 / delay`), so changing the delay or the
  timeout cannot silently push it back over. Measured: 18 requests, 36s of delay,
  24s of headroom.

### SI4 — IT IS FIXED
`feed.py` described itself as *"single source of truth for the per-user job view …
read by both the FastAPI dashboard endpoints AND the notification worker"*.
`list_for_user` has **zero** production callers; the dashboard reads via
`database.py`.
- `backend/src/services/feed.py` — docstring now states what is actually true, and
  names which methods ARE live (`get_score`, the write helpers).
- Ordering aligned to the live query: `COALESCE(llm_fit_score, score) DESC`
  (matches `database.py:1217`). Two different orderings over one table is how
  "the dashboard and the email disagree" bugs begin. With `MATCHER_ENABLED` off
  every `llm_fit_score` is NULL, so COALESCE collapses to `score` — behaviour
  unchanged unless the judge is running.

### S8 — IT IS FIXED (documented, deliberately not deleted)
The `glassdoor` RATE_LIMITS row is unreachable: `indeed` and `glassdoor` are two
registry keys pointing at one class that hardcodes `name = "indeed"`.
- `backend/src/core/settings.py` — the row now carries a comment explaining it is
  never read and why it stays: `tests/test_cli.py:49` names the RATE_LIMITS entry
  as one of the FIVE surfaces that must move together (rule #8), so deleting it
  would break a documented contract to remove a zero-cost line.
- Job rows are still labelled `glassdoor` correctly — that comes per-row from
  JobSpy's own `site` column, not from `self.name`.

### P4 — IT IS FIXED
**The commit gate failed OPEN.** An unparseable payload left `CMD` empty, fell
through the `*)` arm, and ALLOWED the commit — silently, because `2>/dev/null`
hid the reason. The one input the gate could not understand was the one it waved
through.
- `.claude/hooks/commit-gate.sh` — falls back to scanning the raw payload when
  the JSON parse yields nothing. It cannot block *every* unparseable Bash call
  (this hook sees all of them; that would wedge the session), so it fails toward
  safety only for payloads that mention a commit.
- **Verified, 4 cases:** valid non-commit → 0; **unparseable + commit → 2 (was 0)**;
  unparseable + non-commit → 0; valid commit without a stamp → 2.
- It blocked its own test payload during development — the fix demonstrating
  itself before the test was even finished.

### H5 / JOB360_ENV doc drift — IT IS FIXED
`CLAUDE.md`'s env table still documented `JOB360_ENV` as the live gate for the
session-cookie `Secure` flag, warning it was "NOT set on Railway".
- Root `CLAUDE.md` — row rewritten: the variable is dead (`auth.py:120` comment
  only); the flag gates on `_is_production()` (`auth.py:121`), which Railway
  satisfies automatically via `RAILWAY_ENVIRONMENT`.
- The corrected row explicitly records that it previously said the opposite,
  because that row is what cost a session.

---

# IT IS FIXED — bugs the mypy drain surfaced (not in the original audit)

Each was invisible at runtime because something caught and swallowed it.

### Recency silently scored ZERO — IT IS FIXED ⚠️ changes rankings
`workers/tasks.py` built `Job(date_found=_parse_dt(...))` — a `datetime` — while
`Job.date_found` is typed `str` (`models.py:23`). `skill_matcher._recency_score`
calls `datetime.fromisoformat(date_found)`; a datetime raises `TypeError`; its own
`except (ValueError, TypeError): return 0` ate it.

**Every job scored through `score_and_ingest` lost its whole freshness component.**
A job posted an hour ago ranked identically to one from last week. Nothing errored.

- `backend/src/workers/tasks.py` — extracted `_job_from_row()`, which emits
  `.isoformat()`.
- `backend/tests/test_recency_date_type.py` — **verified to FAIL with the bug
  reintroduced** (`AssertionError: ... got datetime`) and pass with the fix. A test
  asserting only "returns an int" would have passed against the bug (rule #21).
- **Effect:** fresh jobs now earn up to +10 they had been losing, so feeds re-rank.

### tailor 500 → 404 — IT IS FIXED
A delete landing between the existence check and the UPDATE returned `None`, and
`_doc_out(None)` raised `TypeError` → HTTP 500 where 404 is correct.
- `backend/src/api/routes/tailor.py` — both `save_edit` and `keep` guard it. The
  `keep` guard also protects `_learn_universal`, which would otherwise be fed
  `None` and pollute the learned-patterns store.

### github_enricher BaseException gap — IT IS FIXED
`gather(return_exceptions=True)` also returns `BaseException`s (e.g.
`CancelledError`), which are truthy — so neither `isinstance(x, Exception)` nor
`not content` caught them, and a cancelled fetch was parsed as if it were data.
- `backend/src/services/profile/github_enricher.py` — both unsafe guards widened
  to `BaseException`. The third site (`:291`) was already safe via its
  `not isinstance(result, dict)` check and was left alone.

### Job.id missing — IT IS FIXED (field only)
`Job` had no `id`; it was stapled on at runtime, which worked only because the
dataclass is not slotted, and forced `getattr(job, "id", None)` across the codebase.
- `backend/src/models.py` — `id: Optional[int] = None`, declared last so
  positional construction is unaffected.
- Removed **10 now-stale `# type: ignore`** comments across `main.py`, `jobs.py`,
  `tasks.py`, `rescore.py` — mypy's `warn_unused_ignores` flagged every one, which
  is that mechanism working.
- **Deliberately NOT wired further:** declaring the field does not fix the
  dim-scoring bug. The real defect is the ORDER of "set id" vs "score", which
  lives in the scoring path — the owner's domain.

### salary.py — NOT A BUG
Reported as an `int > None` comparison. `backend/src/services/salary.py:64-65`
returns early when both bounds are `None`, so after that at least one is set and
the backfill mirrors it. Verified before "fixing" working code. **1 in 5
agent-reported findings was wrong — verify before you fix.**

---

## WON'T FIX (2) — fixing would do harm

### V2 — unclosed client-side cursor
`pg.py:544` returns `Cursor(cur, lastrowid)` — that cursor is handed to the CALLER
to read from. Closing it there breaks every query in the codebase. A real fix means
changing the cursor lifecycle contract across the whole shim, for psycopg3
client-side cursors that are cheap. Wrong trade.

### P3 — verify-reminder hook is non-blocking
The finding asks for enforcement. The hook's own comment explains why it must not
block: *"a blocking Stop hook on dirty code would loop forever"* — the hook fires
BECAUSE code is dirty, so blocking on it can never clear. Implementing the
suggested fix would wedge every session. The reminder is the correct shape.

### P5 — settings.local.json bloat (out of scope, not declined on merit)
`.claude/settings.local.json` is untracked and gitignored — it is the owner's
personal machine config, not repository code. Not an agent's file to rewrite.

## OPEN (9 → 3) — still exactly as described

| ID | What | Proof |
|---|---|---|
| **N2** | Two-pass profile extraction (~8 sequential LLM calls) runs **inline**, blocking the HTTP response | `api/routes/profile.py:327` `await run_two_pass_extraction(profile)` |
| **H6** | DB migrations still run **in-process** inside the FastAPI lifespan at every boot | `api/dependencies.py:22` `await runner.up(...)`; `api/main.py:113` |
| **S5** | JobSpy scrape runs in a detached `asyncio.to_thread`; on timeout the OS thread leaks | `services/scheduler.py:160-161`; `sources/other/indeed.py:41` |

*(L6, S8, SI4, P4 moved to **IT IS FIXED** above. P3 and P5 moved to **WON'T FIX**
with reasons.)*

**All three remaining OPEN items need a decision or infrastructure, not code:**
- **N2** — backgrounding extraction changes WHAT the upload endpoint returns, so
  the frontend must poll for completion. That is a full-stack UX change, not a bug
  fix, and it alters what the user sees immediately after uploading a CV.
- **H6** — the fix is "run migrations as a release step", which is a Railway
  deploy-pipeline change, not a repo change. Mitigation already in place:
  `backend/railway.json` healthcheck + `restartPolicyType: ON_FAILURE` keeps the
  old container serving if a migration fails.
- **S5** — killing a runaway JobSpy scrape needs a process pool instead of a
  thread; the code comment marks the thread-leak as accepted-by-design. Changing
  it alters how the scheduler handles timeouts on a live scraper.

**H6 is mitigated, not unguarded:** `backend/railway.json` sets `healthcheckPath`
`/api/health` + `restartPolicyType: ON_FAILURE`, so a failed migration keeps the old
container serving rather than going live broken. The literal fix (a separate release
step) is the owner's call — it matters most the day the API scales past one replica.

**S5 is accepted-by-design**, documented in the code comment itself.

---

## PARTIAL (9 → 3) — main risk closed, named remainder

**Six of these are now CLOSED by PR #94** — see "IT IS FIXED" above:
`S2`, `N7`, `M7`, `M4`, `AGT1` fixed; `V2` recorded as WON'T FIX with reasoning.

**The three that genuinely remain, and why each needs you rather than code:**

- **M2** — `/register` returns 409 on a taken email, which leaks whether an
  account exists. Hiding it means registration can no longer tell a user "you
  already have an account". That is a real UX cost traded against a real
  enumeration risk — a product call, not a bug fix.
- **M9** — `user_id` is logged unmasked. This is **deliberate and documented**
  (`middleware.py:105-109`): an opaque internal UUID kept for correlation, far
  lower PII than the raw IP that is now hashed. Listed for visibility, not as a
  defect.
- **T10** — the dead `score_job()` still exists (`skill_matcher.py:403`).
  Deleting it means editing `skill_matcher.py` — Pillar 2, the owner's domain —
  and updating `test_scorer.py` + `test_live_pipeline.py`, which exercise it.

The original PARTIAL table follows, kept for the record.


| ID | Closed | Still remaining |
|---|---|---|
| **S2** | Adzuna fan-out bounded to 8 queries | Reed still issues **24 sequential requests × 2.0s delay ≈ 48s**, tight under the 60s `SOURCE_FETCH_TIMEOUT` once real latency is added (`sources/apis_keyed/reed.py:35`) |
| **N7** | 2 of 3 N+1s fixed (`main.py:833`, `tasks.py:593`) | `notification_tick` still does a per-rule `SELECT timezone FROM users` inside its loop (`tasks.py:843`) |
| **M2** | Login timing side-channel closed via dummy-hash compare (`auth.py:233-235`) | `/register` still returns 409 on a taken email → email enumeration (owner-accepted) |
| **M7** | `upsert_tailored_doc` DELETE+INSERT now in one transaction (`database.py:799`) | `mark_missed_for_source` still loops bare `UPDATE`s under autocommit (`database.py:489-521`) |
| **V2** | Always-on `lastval()` round-trip now gated on `cur.rowcount` (`pg.py:531-535`) | Client-side cursor still not closed before return (`pg.py:544`) — low impact |
| **AGT1** | R1 (conventions) + R3 (bugs) extracted to agent files | R2 (history) never extracted; still inline in `worker/SKILL.md:30` |
| **M4** | `job_timeout = 600` + app-level DLQ for notification bundles | **No Docker HEALTHCHECK, no `health_check_interval`, no global `max_tries`** (`Dockerfile.worker:3`; `workers/settings.py`) |
| **M9** | Client IP SHA-256 hashed before logging (`middleware.py:112`) | `user_id` still logged unmasked — **deliberate**, documented at `middleware.py:105-109` as an opaque internal UUID kept for correlation |
| **T10** | `CLAUDE.md:221` now marks legacy `score_job()` test-only | The dead function itself still exists (`skill_matcher.py:403`) |

---

## FIXED tonight (2026-07-19) — with the PR that did it

| ID | Fix | PR |
|---|---|---|
| **H7** | All 4 security scanners BLOCKING with **zero** waivers; mypy BLOCKING via ratchet at **0 errors** (was 803, non-blocking) | #89, #90, #91, #92 |
| **H7 (cont.)** | 11 aiohttp CVEs genuinely closed — bumped to >=3.14.1 behind a test-only `conftest.py` shim, because `aioresponses` never passes the now-required `stream_writer` | #93 |
| — | `openai` declared as a dependency. It had **never been installed in production**: the import failed, a broad `except` swallowed it, and every CV parse silently fell back to a free tier — while `openai` was the documented *primary* provider | #91 |

Proof, on main:
```
security.yml   — 4/4 jobs blocking, 0 --ignore-vuln flags
pip-audit (CI) — "No known vulnerabilities found"  (with aiohttp-3.14.1 installed)
ci.yml         — runs scripts/mypy_ratchet.py, no continue-on-error
mypy_baseline  — "# total errors: 0"
full suite     — 1936 passed, 3 skipped
```

## Bugs found by the type-drain (not in the original audit)

Surfaced while draining 803 mypy errors — each was invisible at runtime because
something swallowed it.

| Bug | Status |
|---|---|
| `workers/tasks.py` passed a `datetime` into `Job.date_found` (typed `str`); `skill_matcher.py:298` caught the `TypeError` and returned **0**, silently zeroing the recency component of **every** job scored via `score_and_ingest` | **Fixed** — `_job_from_row()` now emits ISO strings; pinned by `tests/test_recency_date_type.py`, which fails if reintroduced |
| `tailor.py` — delete landing mid-request returned `None` → `TypeError` → HTTP 500 where 404 is correct (2 endpoints) | **Fixed** |
| `github_enricher.py` — `gather(return_exceptions=True)` guards tested `Exception`, missing `BaseException` (e.g. `CancelledError`) | **Fixed** (2 sites; a third was already safe) |
| `models.py` — `Job` had no `id` field; it was stapled on at runtime, forcing `getattr(job, "id", None)` everywhere. Root of the documented dim-scoring-0 bug | **Fixed** — field declared. The scoring-order defect itself remains the owner's call |
| `salary.py:91` — reported as an `int > None` comparison | **NOT A BUG** — `salary.py:64-65` returns early when both bounds are `None`. A reminder that agent-reported findings need verifying before "fixing" |

---

# Genuinely yours — everything code alone cannot close

Every item an agent could fix without a product or infrastructure decision is
fixed. What is left needs you, and each is stated as a question rather than a task.

| # | Item | The question only you can answer |
|---|---|---|
| 1 | **Backups** | Does a **tested restore** of the Railway Postgres exist? Real users' CVs, profiles and applications live in one database, and no finding in this entire audit ever mentioned backups. This is the largest unexamined risk in the product. |
| 2 | **Uptime alerting** | Sentry catches exceptions; nothing catches "the process is not running". The ARQ worker died silently once already (`7f906c6`, "revive dead ARQ worker (P0)"). M4 makes the worker *checkable*; something still has to *watch* it and tell you. |
| 3 | **N2** | Backgrounding CV extraction changes what the upload endpoint returns — the frontend must poll. Do you want that UX (instant response, skills appear later) or the current one (slow response, skills ready)? |
| 4 | **H6** | Moving migrations to a Railway release step is a deploy-pipeline change. Worth doing before you ever run two API replicas; unnecessary while you run one. |
| 5 | **M2** | Accept email enumeration on `/register`, or lose the "you already have an account" message? |
| 6 | **Recency re-ranking** | The fixed recency bug means fresh jobs now earn up to +10 they were losing. Scores and feed order will shift. That is the bug being fixed, but it is your scoring domain. |
| 7 | **S5** | JobSpy thread-leak on timeout is marked accepted-by-design. Replacing the thread with a killable process pool changes scheduler behaviour on a live scraper. |
| 8 | **In-memory state** | Login lockout and the per-user concurrent-search cap are per-process. Correct at one replica, wrong at two. Not urgent; a trap to remember before scaling. |

## Not covered by any finding — worth knowing

- **Backup/restore has never been mentioned anywhere in this audit.** Real users' CVs,
  profiles and applications live in one Railway Postgres. Does a *tested restore* exist?
- **Nothing detects "process not running."** Sentry catches exceptions; the ARQ worker
  died silently once already (commit `7f906c6`, "revive dead ARQ worker (P0)"). M4 above
  plus an external uptime check on `/api/readyz` would close that class.
- **In-memory state won't survive replicas** — login lockout and the per-user concurrent
  search cap are per-process. Fine at one replica; a trap at two.
