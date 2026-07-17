# Fable Findings — Verified Audit against production `main`

**Audited:** 2026-07-17 · **Base:** this worktree == `origin/main` == live Railway code (`bc3904a`).
**Method:** 11 parallel verifier agents, each opening the REAL current code (not the docs' stale July-10 line numbers) and quoting the actual lines. Every claim below was checked against code, not against the tracker.

## Tally

| Verdict | Count |
|---|---:|
| ⛔ NOT CONFIRMED (doc claims FIXED — NOT found in code) | 2 |
| 🔴 OPEN BUG (genuinely unfixed, never claimed fixed) | 20 |
| 🟡 PARTIAL (half fixed — the other half is open) | 12 |
| 🧑 NEEDS YOU (non-code: deploy / legal / dashboard) | 2 |
| 🗑 DEAD CODE (harmless, unused) | 1 |
| ⚪ OPEN — ACCEPTED (by-design / deferred, code state matches) | 14 |
| ✅ CONFIRMED FIXED (proof in main) | 50 |
| **TOTAL** | **101** |

## ⛔ NOT CONFIRMED (doc claims FIXED — NOT found in code) — 2

### RULECOUNT — CLAUDE.md rule-count drift: header says 28, worker skill says 27 — P6's fix text said "reconcile"
- **Area:** Harness & Workflow (docs/fable/06-HARNESS-AND-WORKFLOW.md + fable-harness-plan.md + FABLE_FINDINGS.md H7)
- **Claimed:** Implied FIXED as part of P6 ("reconcile 27/28")
- **Proof (current code):** .claude/skills/worker/SKILL.md:30 still reads "...CLAUDE.md 27-rule violations (especially #11/#16 lazy imports, #18 default-off flags)?" while CLAUDE.md itself (line 25 header "## Hard Rules" + numbered list through `28.` confirmed via grep) and fable-harness-plan.md:11/128 both say "28 numbered hard rules".
- **Note:** This specific reconciliation from P6's fix text was NOT done — worker/SKILL.md still cites the stale 27 count. Small but real leftover drift; flagging loudly per instructions since P6 is otherwise marked fixed.

### M12 — Uptime monitor pings liveness (/livez), so real outages stay green
- **Area:** OPS & RELIABILITY
- **Claimed:** OPS P1 claims error-rate alerting FIXED via live Sentry alerts (owner-side); readyz swap implied
- **Proof (current code):** .github/workflows/uptime.yml:16-19 STILL pings `URL="https://backend-production-80e8e.up.railway.app/api/livez"` — unchanged. A dependency-checking `/readyz` endpoint DOES exist and probes DB+Redis: backend/src/api/routes/health.py:45-89 `@router.get("/readyz")`. So the literal fix (point uptime at /readyz) was trivially possible but was NOT applied.
- **Note:** LOUD: the M12 code fix was NOT done — uptime.yml still hits /api/livez even though a working /readyz (DB+Redis probe) exists in health.py:45. So Postgres/Redis can be down while the monitor stays green. The doc's compensating control (Sentry Issue Alert 700530 + Metric Alert per OPS P1 / PROGRESS) is owner-side and CANNOT be verified from code. Code state = still livez = finding still open.

## 🔴 OPEN BUG (genuinely unfixed, never claimed fixed) — 20

### M1 — Authenticated SSRF via webhook channel
- **Area:** SECURITY
- **Claimed:** OPEN (MEDIUM, no fix claimed)
- **Proof (current code):** NO private/loopback/link-local/metadata IP validation anywhere — grep for ipaddress/169.254/is_private/is_loopback across backend/src = 0 hits in any auth/channel path. create_channel webhook validation is ONLY a scheme check: channels.py:128 `if not (cred.startswith('http://') or cred.startswith('https://'))` then converts to json[s]:// (:136-139). POST /channels/{id}/test (channels.py:221-239) calls dispatcher.test_send which makes the server-side request with no host filtering.
- **Note:** Confirmed still vulnerable. A logged-in user can register a webhook at http://169.254.169.254/ or http://127.0.0.1:* and trigger a blind server-side request from inside the private network via the /test endpoint. Never claimed fixed; remains OPEN as an authenticated SSRF. Recommend DNS-resolve + reject private/reserved ranges + port allowlist.

### M16 — A 403 in the shared fetch wrapper force-navigates and discards unsaved input
- **Area:** FRONTEND & AUTH-UI
- **Claimed:** No FIXED claim found in either doc
- **Proof (current code):** frontend/src/lib/api.ts:100-109 — inside the low-level `request<T>()` function: `if (res.status === 403 && (code === "email_not_verified" || detail === "email_not_verified")) { if (typeof window !== "undefined" && !window.location.pathname.startsWith("/verify-email")) { window.location.href = "/verify-email"; } }`
- **Note:** Code is unchanged from what the finding describes — the redirect is still a side effect buried in the shared fetch client (not lifted to a top-level AuthProvider decision as the finding's fix suggested). A background refetch triggering a 403 can still yank the user off a half-filled form. Confirmed still OPEN, correctly not claimed as fixed anywhere.

### M17 — Pipeline board renders off counts but data comes from a separate applications fetch
- **Area:** FRONTEND & AUTH-UI
- **Claimed:** No FIXED claim found in either doc
- **Proof (current code):** frontend/src/app/pipeline/page.tsx:136 `const total = Object.values(counts).reduce((sum, n) => sum + n, 0);` then :278 `{total === 0 && !loading && !error && (<EmptyState .../>)}` and :291 `{total > 0 && (<KanbanBoard applications={applications} .../>)}` — gating is still on `total` (derived from the separate `counts` state), not `applications.length`.
- **Note:** Partial mitigation not present in the finding text: applications/counts/reminders are now fetched together via `Promise.all` in one `fetchData()` (pipeline/page.tsx:92-96) rather than as two fully independent useQuery hooks, which reduces (but does not eliminate) temporal-staleness risk. The core bug — gating render on `counts` totals instead of `applications.length` — is unchanged and still open exactly as described.

### L6 — Frontend image ships full node_modules instead of Next standalone
- **Area:** FRONTEND & AUTH-UI
- **Claimed:** No FIXED claim found in either doc
- **Proof (current code):** frontend/next.config.ts has no `output: "standalone"` key (grep for 'standalone' across frontend/ hits only frontend/Dockerfile). frontend/Dockerfile:34-35 explicitly states: '# Next.js standalone output is NOT configured in next.config.ts, so we ship the full .next/ build output + production node_modules.' and copies the full node_modules (line 40) into the runner stage.
- **Note:** Confirmed still open — the Dockerfile comment itself documents the gap has not been closed. No accepted-risk rationale found elsewhere in the repo.

### M8 — Negative per-dimension scores persisted unclamped
- **Area:** DATA & DB
- **Claimed:** OPEN (MEDIUM, no FIXED claim in docs)
- **Proof (current code):** scoring_dimensions.py:138-139 seniority_score returns `-int(round(max_pts*0.5))` / `-max_pts` (negative). skill_matcher.py:551 clamps ONLY the composite: `match = min(max(total,0),100)`, but line 557 stores `seniority_score=seniority_pts` RAW into ScoreBreakdown (and thence the dim columns). No per-dimension clamp anywhere.
- **Note:** Confirmed still-open exactly as the finding describes. Radar-chart dim columns can be negative (rule #27 hazard for anything reading dims directly). Never claimed fixed — reporting current state.

### S1 — Circuit breaker counts an empty result as a failure
- **Area:** Sources & pipeline (S1-S9)
- **Claimed:** OPEN (no FIXED claim found anywhere for S1 — checking true code state)
- **Proof (current code):** backend/src/services/scheduler.py:175-179 — `fetch_failed = isinstance(result, BaseException) or result is None or not result` then `if fetch_failed: breaker.record_failure() else: breaker.record_success()`. An empty list (`not []` is True) still counts as a failure fed straight to the breaker.
- **Note:** NOT fixed. Code is byte-identical to what the finding describes — no separate zero-result tracking, no baseline comparison. A quiet legit source still trips the breaker exactly as described.

### S2 — Reed/Adzuna title fan-out always exceeds fetch timeout
- **Area:** Sources & pipeline (S1-S9)
- **Claimed:** OPEN
- **Proof (current code):** backend/src/sources/apis_keyed/reed.py:33 — `queries = self.job_titles[:12]` × `locations = ["London", "UK", "Remote"]` (still 12×3=36 fan-out). backend/src/sources/apis_keyed/adzuna.py:29 — `queries = self.job_titles` — still unbounded, no slice at all (siblings jsearch/careerjet/jooble do slice).
- **Note:** NOT fixed. Adzuna still iterates the full unbounded job_titles list; Reed still does 12x3. Neither timeout nor fan-out was adjusted.

### S8 — Dead glassdoor rate-limit entry
- **Area:** Sources & pipeline (S1-S9)
- **Claimed:** OPEN
- **Proof (current code):** backend/src/core/settings.py:213 — `"glassdoor": {"concurrent": 1, "delay": 3.0},` still present. backend/src/sources/other/indeed.py:14 — `class JobSpySource(BaseJobSource): name = "indeed"` is still a hardcoded class attribute, so RATE_LIMITS lookup at base.py:73 (`RATE_LIMITS.get(self.name, ...)`) always resolves to "indeed" regardless of whether the instance was built via the 'glassdoor' registry key.
- **Note:** NOT fixed. RATE_LIMITS["glassdoor"] is still dead code — never read because self.name is permanently "indeed".

### S9 — Auth failures (401/403/404/422) logged at debug
- **Area:** Sources & pipeline (S1-S9)
- **Claimed:** OPEN
- **Proof (current code):** backend/src/sources/base.py:129-131 — `if resp.status in _NO_RETRY_STATUSES: logger.debug("[%s] HTTP %s from %s", self.name, resp.status, url); return None`. _NO_RETRY_STATUSES (base.py:20) is still `(401, 403, 404, 422)`.
- **Note:** NOT fixed. Still logs at debug and returns None with no auth-failure counter — an expired API key still looks identical to a quiet source.

### SI1 — Notification pipeline disconnected in prod — no user is ever notified
- **Area:** SERVICE-INTERNALS (SI1-SI5)
- **Claimed:** OPEN (HIGH, needs fix — wire search path to enqueue send_notification/score_and_ingest)
- **Proof (current code):** `score_and_ingest` is DEFINED at backend/src/workers/tasks.py:87 and only REGISTERED as an ARQ function (settings.py:38,143) — a tree-wide grep `grep -rn score_and_ingest src/ --include=*.py` returns ONLY the definition/registration/comment lines, NO enqueue call anywhere. The real per-user search path in main.py:750-775 writes straight to user_feed via `feed.upsert_feed_row(...)` (line 765) then calls `_run_matcher_stage` (line 780); main.py contains ZERO notification/dispatch/enqueue calls (grep of main.py for enqueue/send_notification/score_and_ingest = none). The only instant-send enqueue lives INSIDE score_and_ingest itself (tasks.py:182 `enqueue("send_notification", ...)`), which is never reached because score_and_ingest is never enqueued. So dispatch()/user_notification_digests writer is dead in prod.
- **Note:** STILL OPEN — the fix is NOT present in code. This is a code-wiring gap, not merely a missing Railway worker deploy: nothing in the entire codebase enqueues score_and_ingest, so even a running worker would never get the job. The dispatcher, the per-user score_threshold gate, and the instant-send path are all dead code on the real search path. Highest functional impact — confirmed present.

### SI5 — prefilter.experience_ok symmetric ±1 band contradicts one-directional docstring
- **Area:** SERVICE-INTERNALS (SI1-SI5)
- **Claimed:** OPEN (LOW)
- **Proof (current code):** backend/src/services/prefilter.py:113 still `return abs(job_level - user_level) <= 1` (symmetric band). The docstring immediately above, lines 110-112, still asserts the rule is 'junior candidates skip senior roles — not the reverse' — the same one-directional claim the symmetric code contradicts.
- **Note:** STILL OPEN — neither the code nor the docstring was changed. A senior user is still filtered away from entry/junior roles (job_level below user_level by >1). Low impact; unfixed.

### N2 — Profile extraction runs ~8 sequential LLM calls synchronously inside the HTTP request
- **Area:** SECOND-OPINION FLEET (N1-N9)
- **Claimed:** OPEN bug
- **Proof (current code):** backend/src/api/routes/profile.py:320-328 `_extract_save_trigger` (the shared tail every profile-input route awaits): line 326 `await run_two_pass_extraction(profile)` runs inline, then 327 `save_profile`, then 328 `_maybe_trigger_rescore`. upload_cv (line 331+) calls this tail directly in the request handler. Not moved to ARQ; no 202 + job-id + poll pattern.
- **Note:** NOT FIXED. Two-pass extraction still blocks the HTTP request synchronously. Only the downstream rescore is backgrounded; the extraction itself (the 8 LLM round-trips) is still awaited inline.

### N3 — Job-detail page loads the ENTIRE job_enrichment table on every request
- **Area:** SECOND-OPINION FLEET (N1-N9)
- **Claimed:** OPEN bug
- **Proof (current code):** backend/src/api/routes/jobs.py:625 `enrichment_lookup_dict = await _build_enrichment_lookup(db._conn) if enrichment_on else {}` on the authenticated job-detail scoring path. _build_enrichment_lookup (job_enrichment.py:295-320) is a full-table scan: `SELECT ... FROM job_enrichment` with NO WHERE/LIMIT (docstring line 298 'Bulk-load every persisted job_enrichment row'). The single-row `load_enrichment` exists at job_enrichment.py:376 but is NOT used here.
- **Note:** NOT FIXED. The suggested fix (load_enrichment(db._conn, row['id'])) was not applied; the detail GET still deserializes every catalog enrichment row.

### N5 — Two spots leak raw exception text to clients
- **Area:** SECOND-OPINION FLEET (N1-N9)
- **Claimed:** OPEN bug
- **Proof (current code):** backend/src/api/routes/tailor.py:160-161 `except Exception as exc: raise HTTPException(status_code=503, detail=f"Generation failed: {exc}")` — raw exc still in response detail. backend/src/api/routes/search.py:82 `_runs[run_id].update(status="failed", progress=str(e))` — raw exception string stored in the run record and returned by GET /search/{run_id}/status (payload built at search.py:112).
- **Note:** NOT FIXED. Both leak sites are unchanged; no logger.exception + generic-message swap was applied at either spot.

### N6 — search.py _runs in-memory store grows unbounded
- **Area:** SECOND-OPINION FLEET (N1-N9)
- **Claimed:** OPEN bug
- **Proof (current code):** backend/src/api/routes/search.py:28 `_runs: dict[str, dict] = {}` module-level. Entries added at line 63 `_runs[run_id] = {...}`; completed/failed updated at 76/82 but NEVER removed. grep for `del _runs`/`pop(`/`_evict`/`TTL`/`maxlen`/`OrderedDict`/`_prune` in search.py = 0 hits.
- **Note:** NOT FIXED. No eviction/TTL/LRU exists; the dict grows for the process lifetime and still holds the leaked N5 exception text.

### N7 — Redundant/N+1 DB round-trips in hot paths (main.py + tasks.py)
- **Area:** SECOND-OPINION FLEET (N1-N9)
- **Claimed:** OPEN bug
- **Proof (current code):** main.py: job.id already resolved at 683-689 (`SELECT id FROM jobs WHERE normalized_company=? AND normalized_title=?`), yet the feed-write loop re-issues the IDENTICAL query per job at 758-762 instead of reusing job.id. tasks.py notification_tick still does per-rule `SELECT timezone FROM users WHERE id=?` inside the loop (line 782), not a join. tasks.py send_bundle still fetches job details one-by-one in a loop: line 555 `for jid in {...}:` then 556-557 `SELECT title, company, apply_url FROM jobs WHERE id = ?` — no `WHERE id IN (...)`.
- **Note:** NOT FIXED (all three sub-parts). main.py redundant re-query, per-rule timezone SELECT, and one-by-one send_bundle fetch are all unchanged.

### N8 — _safe_fetch swallows asyncio.CancelledError
- **Area:** SECOND-OPINION FLEET (N1-N9)
- **Claimed:** OPEN bug
- **Proof (current code):** backend/src/services/scheduler.py:152-168. Handler order is `except asyncio.TimeoutError` (162) then `except BaseException as e` (166) which returns the exception as a breaker failure. There is NO `except asyncio.CancelledError: raise` before the BaseException catch, so cancellation is still caught and recorded as a failure.
- **Note:** NOT FIXED. The recommended `except asyncio.CancelledError: raise` guard was not added; BaseException still swallows cancellation.

### N9 — GET /profile/versions limit unbounded
- **Area:** SECOND-OPINION FLEET (N1-N9)
- **Claimed:** OPEN bug
- **Proof (current code):** backend/src/api/routes/profile.py:441 `limit: int = 20` — plain default, no `Query(20, ge=1, le=100)`. Passed straight to `list_profile_versions(user.id, limit=limit)` at line 453.
- **Note:** NOT FIXED. No ge/le bound; a caller can still pass an arbitrarily large limit.

### T4 — No migration test runs against a populated table
- **Area:** TEST-QUALITY / DOC-DRIFT / HYGIENE (T1-T12)
- **Claimed:** OPEN, no FIXED tag in doc
- **Proof (current code):** backend/tests/test_migrations.py — test_0017_adds_llm_verdict_columns (line 186), test_0018 (246), test_0019 (303), test_0020 (379) all CREATE TABLE IF NOT EXISTS with zero rows inserted, then runner.up() + PRAGMA table_info column-existence assert only. The one test that does INSERT rows, test_identity_sequence_resynced_after_id_copy (line 87-118), inserts rows AS PART OF the migration SQL itself (testing sequence resync), not pre-existing data on an old schema before a later ALTER — it does not test 'old data survives a column add/rebuild migration'.
- **Note:** Still open exactly as described. No test seeds rows in the OLD schema before running a migration and then asserts the old rows survived/were correctly defaulted.

### T10 — Dead score_job() called a live 'path' in CLAUDE.md
- **Area:** TEST-QUALITY / DOC-DRIFT / HYGIENE (T1-T12)
- **Claimed:** OPEN, fix = delete or mark test-only
- **Proof (current code):** backend/src/services/skill_matcher.py:402 `def score_job(job: Job) -> int:` still exists. Grep across all of backend/src for score_job( calls returns only the definition itself (no callers anywhere in src/). Only caller in the whole repo: backend/tests/test_live_pipeline.py:28 `from src.services.skill_matcher import score_job`. CLAUDE.md:221 still describes it as one of 'Two paths' for prod scoring.
- **Note:** Confirmed dead in prod code, and CLAUDE.md still misrepresents it as a live path. Not fixed.

## 🟡 PARTIAL (half fixed — the other half is open) — 12

### LOCKOUT — Lockout DoS (email-only) + IP-only reset limit
- **Area:** SECURITY
- **Claimed:** FIXED
- **Proof (current code):** Login lockout FIXED: auth.py:191 `throttle_key = f"login:{str(req.email).lower()}:{_ip}"` — now keyed email+IP, so an attacker only locks their own (email,IP) bucket. XFF only trusted behind JOB360_TRUST_PROXY (auth.py:187-190). BUT password-reset throttle is STILL IP-hash-only: auth.py:445 `key = f"password-reset:{ip_hash}"` (:444 sha256 of client IP), no email dimension.
- **Note:** Half fixed. The doc's own '01-SECURITY.md' fix claim only covered the login lockout key, which IS fixed. The second 'why it matters' point — reset throttle bypassable by IP rotation because it is not keyed on email — remains UNaddressed. Email-bomb-via-IP-rotation is still possible.

### M2 — Register email enumeration (409) + login timing oracle
- **Area:** SECURITY
- **Claimed:** PARTIAL (timing fixed, register 409 deliberate)
- **Proof (current code):** Timing FIXED: auth.py:44 `_DUMMY_PW_HASH = hash_password(...)`; login always runs exactly one verify — :215 `stored_hash = row['password_hash'] if row is not None else _DUMMY_PW_HASH` then :216 `pw_ok = verify_password(stored_hash, req.password)`, check at :217. Register 409 enumeration REMAINS: auth.py:148-152 `except pg.IntegrityError: raise HTTPException(status_code=409, detail='email already registered')`.
- **Note:** Matches the doc's own PARTIAL claim. Timing side-channel genuinely closed. Register-existence oracle (409 on duplicate email) still present by design — owner-accepted tradeoff (can't register silently over an existing account).

### M3 — In-memory per-process rate limits; /register unthrottled
- **Area:** SECURITY
- **Claimed:** FIXED (opt-in)
- **Proof (current code):** Redis-backed sliding window shipped opt-in: rate_limit.py:56-79 Lua EVAL scripts (atomic ZREMRANGEBYSCORE/ZCARD/ZADD); gated by RATE_LIMIT_REDIS + REDIS_URL (:96-99); ANY Redis error falls back to in-memory (:170-171,210-211,247-248). Default OFF = in-memory deque (:173-183). BUT /register still has ZERO rate limit — register() auth.py:132-172 makes no auth_rate_limit call.
- **Note:** The replica-sharing half is genuinely fixed (opt-in, byte-identical when off). The doc's other sub-point — '/register has no limit (mass account creation)' — is UNaddressed: mass account creation is still unthrottled. Also note the shared-limiter only helps if RATE_LIMIT_REDIS=true is actually set in prod (verify env).

### M7 — Non-atomic multi-statement writes under autocommit (data loss)
- **Area:** DATA & DB
- **Claimed:** FIXED (b939e29: advance_application) per doc02; FABLE_FINDINGS M7 also names upsert_tailored_doc + mark_missed_for_source
- **Proof (current code):** FIXED: advance_application (database.py:970-993) now does explicit BEGIN → UPDATE → SAVEPOINT _adv_hist → history INSERT → COMMIT, ROLLBACK on error. STILL OPEN: upsert_tailored_doc (database.py:771-784) does DELETE then INSERT with NO transaction (autocommit; commit() is a no-op at pg.py:557-558) — a crash/failure between the two permanently loses the user's existing tailored doc, exactly the M7 data-loss scenario. mark_missed_for_source (database.py:456-498) loops UPDATEs under autocommit with a trailing no-op commit().
- **Note:** LOUD: doc02 only ever claimed advance_application, but FABLE_FINDINGS M7 lists all three and the single highest-impact one (upsert_tailored_doc DELETE-then-INSERT, the named data-loss case) is STILL non-atomic. The delete-then-insert window the finding specifically warns about is unfixed in production code.

### V2 — Connection.execute returns unclosed cursors + unused lastval() round-trip per INSERT
- **Area:** DATA & DB
- **Claimed:** verifier note (no explicit FIXED claim)
- **Proof (current code):** lastval half MITIGATED: pg.py:517-522 no longer fires lastval() on every INSERT — only when the row actually inserted (cur.rowcount) and no RETURNING. Cursor half UNCHANGED: pg.py:482-484/531 execute() creates `cur = self._raw.cursor()` and returns `Cursor(cur, lastrowid)` without closing; callers that don't use the `async with` context (Cursor.__aexit__ at 452-453) leave it open.
- **Note:** The wasteful always-lastval round-trip is gone for non-inserting statements; a real INSERT still pays one lastval() (inherent to lastrowid emulation — RETURNING id would remove it, deferred per M13). Unclosed psycopg3 client-side cursors are cheap (no server-side resource unless named), so low real impact, but the pattern the finding flagged persists.

### P6 — CLAUDE.md is ~500 lines — trim history, keep rules
- **Area:** Harness & Workflow (docs/fable/06-HARNESS-AND-WORKFLOW.md + fable-harness-plan.md + FABLE_FINDINGS.md H7)
- **Claimed:** FIXED (conservatively) — 966d394, all 28 rules kept verbatim
- **Proof (current code):** Commit 966d394 exists (`docs(harness): safe CLAUDE.md trim + PROGRESS final state`) and trimmed CLAUDE.md 350→342 lines while keeping rules #1-28 verbatim (confirmed via grep — rules numbered up to `28.` are present in the current CLAUDE.md, matching the header's "28 numbered hard rules"). Current CLAUDE.md (HEAD) is 362 lines (grew slightly post-trim via later unrelated commits).
- **Note:** The trim commit itself is real and did what it claimed. BUT: git history shows CLAUDE.md was only 350 lines right before 966d394, not ~500 as the doc states (it hit ~500-502 lines much earlier, at commit 5cf60ea, then was independently trimmed to 301 lines at 47172a4 before the Fable audit even ran) — the doc's '500 lines' framing doesn't match the pre-fix git state. Also: the fix's own text said 'reconcile 27/28' — that reconciliation was NOT done (see RULECOUNT finding below), so P6 is only partially complete.

### AGT1 — .claude/agents/ reviewer definitions (currently absent) — codify R1/R2/R3 lenses once instead of re-specifying inline
- **Area:** Harness & Workflow (docs/fable/06-HARNESS-AND-WORKFLOW.md + fable-harness-plan.md + FABLE_FINDINGS.md H7)
- **Claimed:** Gap item #1 in "What Anthropic-internal-grade would add" — codify as 2-3 agent files
- **Proof (current code):** .claude/agents/reviewer-conventions.md (R1 lens, front-matter `model: sonnet`) and .claude/agents/reviewer-bugs.md (R3 lens, `model: sonnet`) both exist and are well-formed agent defs. But there is no reviewer-history.md (R2 lens) anywhere — `find .claude -iname "*history*"` returns nothing. .claude/skills/worker/SKILL.md:30 and .claude/skills/integrator/SKILL.md:23 still spell out all three lenses inline: "Wave 1: 3 parallel Sonnet subagents ... one lens each (R1 conventions/CLAUDE.md rules, R2 history — git log/journal of touched files for re-breaks or contradicted decisions, R3 bugs ...)" — R2 is still re-specified inline exactly as before, the original complaint.
- **Note:** 2 of the 3 lenses got a real agent file; the third (R2 history) did not, so the skills still carry inline prose for it. Doc's own goalpost said "2-3 agent files" so this technically satisfies the letter (2), but not the full intent (consolidate all three, reduce fragmentation).

### H7 — No security scanning anywhere in CI; type-check non-blocking (FABLE_FINDINGS.md)
- **Area:** Harness & Workflow (docs/fable/06-HARNESS-AND-WORKFLOW.md + fable-harness-plan.md + FABLE_FINDINGS.md H7)
- **Claimed:** Fix requested: "Add BLOCKING pip-audit + npm audit --audit-level=high + gitleaks + bandit jobs"
- **Proof (current code):** .github/workflows/security.yml exists with 4 jobs: pip-audit (line 33-56, `continue-on-error: true` at line 37, and the audit step itself ends `|| true` at line 56), npm-audit (line 58-72, step ends `|| true` at line 72, no `continue-on-error` needed since the job would already report success), gitleaks (line 74-86, `continue-on-error: true` at line 78), bandit (line 88-106, `continue-on-error: true` at line 92, though the bandit invocation itself has no `|| true`). File header comment (lines 6-9) states outright: "The dependency-audit jobs are non-blocking for now ... gitleaks + bandit are advisory too until the codebase is clean."
- **Note:** The requested scanners were all added and do run on every push/PR/weekly — real progress. But the fix explicitly asked for BLOCKING jobs and every single one is still advisory (continue-on-error and/or `|| true`), so a vulnerable dependency or a leaked secret still would NOT fail CI today. No CodeQL job was added at all (also named in H7's evidence/fix). PARTIAL, not FIXED.

### M4 — Worker has no health check and no ARQ timeout/retry/DLQ policy
- **Area:** OPS & RELIABILITY
- **Claimed:** MOSTLY FIXED (worker job_timeout 4e41e86); health-check part open
- **Proof (current code):** job_timeout: backend/src/workers/settings.py:169 `job_timeout = 600  # seconds` (+ settings.py:164 `max_jobs = 1`). DLQ (notification path only): backend/src/workers/tasks.py:619 `UPDATE notification_ledger SET status='dlq' WHERE ... retry_count >= ?`. MISSING: no Docker HEALTHCHECK — backend/Dockerfile.worker:3 comment 'No EXPOSE / HEALTHCHECK: the worker does not serve HTTP'; grep for `health_check_interval|max_tries` in settings.py = 0 hits. No ARQ health_check_interval, no global max_tries cap.
- **Note:** The highest-value part (job_timeout=600 > worst-case 240s ATS fan-out, capping runaway retries/LLM spend) IS fixed, and an application-level DLQ exists for notification bundles. But no worker health check, no ARQ `health_check_interval`, and no global `max_tries` were added; railway.json restart ON_FAILURE is the only liveness recovery. Partial fix.

### M9 — PII (client IP + user_id) written to on-disk rotating logs unredacted
- **Area:** OPS & RELIABILITY
- **Claimed:** PROGRESS 'session update 7' addressed M9 — but as EMAIL masking, not IP/user_id
- **Proof (current code):** IP + user_id STILL logged plaintext: backend/src/api/middleware.py:103-104 in AccessLogMiddleware: `"user_id": getattr(request.state, "user_id", None),` and `"client": request.client.host if request.client else None,` — no hashing/drop. These flow to data/logs/*.jsonl via utils/logger.py JSONFormatter. What WAS added is email masking only: backend/src/utils/logger.py:164 `def mask_email(...)` (masks addresses, docstring says 'Audit M9') — NOT applied to client IP.
- **Note:** LOUD: the FABLE_FINDINGS M9 as written (client IP + user_id in the access log) is NOT fixed — middleware.py still writes the raw IP and user_id. PROGRESS.md line 196-216 treats 'M9' as a DIFFERENT leak (plaintext emails in logs, 6 lines) and fixed that with mask_email. The two share a label but are different leaks; the assigned IP/user_id one remains open. Fix suggested ('drop or hash the IP') was not applied.

### N4 — /jobs limit/offset/hours unbounded/unvalidated
- **Area:** SECOND-OPINION FLEET (N1-N9)
- **Claimed:** OPEN bug
- **Proof (current code):** backend/src/api/routes/jobs.py:444 `limit: int = Query(100, ge=1, le=200)` — BOUNDED; line 445 `offset: int = Query(0, ge=0)` — BOUNDED. BUT line 437 `hours: Optional[int] = Query(None)` — still NO ge/le. A negative `hours` still yields days=(hours//24)+1 -> future/odd cutoff at jobs.py:452.
- **Note:** PARTIAL. limit and offset were fixed (bounded exactly as the finding recommended), but `hours` remains unvalidated — the third leg of N4 is still open.

### T6-T9 — CLAUDE.md test-count/SQLite/migration-range drift + broad pytest.raises + weak diff assertions
- **Area:** TEST-QUALITY / DOC-DRIFT / HYGIENE (T1-T12)
- **Claimed:** CLAUDE.md drift sub-part: FIXED-PENDING-MERGE in PR #31; pytest.raises/diff-assertion sub-parts: OPEN
- **Proof (current code):** CLAUDE.md drift — FIXED: root CLAUDE.md now says '~1,716 collected' (not ~1,409), describes backend as 'Postgres via psycopg3' (not SQLite), and 'migration pairs 0000 → 0024' (not 0000→0021). / pytest.raises too broad — STILL OPEN: test_database.py:324 `with pytest.raises(Exception): asyncio.run(_insert_twice())` wraps BOTH inserts (first insert not outside the block); test_tenancy_isolation.py:170 `with pytest.raises(Exception):  # IntegrityError` also unnarrowed. / Weak diff assertion — STILL OPEN: test_discovery.py:291-292 `assert "changed_fields" in body; assert isinstance(body["changed_fields"], list)` — still shape-only, does not assert the specific changed field appears in the list. test_profile_versions_endpoint.py (checked lines 60-89) has no changed_fields value assertion either.
- **Note:** Only the CLAUDE.md doc-drift portion of this bundle is fixed. The two weak-test sub-findings (broad exception, shape-only diff assertion) are unchanged in the code.

## 🧑 NEEDS YOU (non-code: deploy / legal / dashboard) — 2

### PS-P1-magiclink-sentry-issue — Magic-link Sentry issue (PYTHON-FASTAPI-1) resolved live
- **Area:** Production Signals (docs/fable/09-PRODUCTION-SIGNALS.md)
- **Claimed:** CLOSED — resolved live via the Sentry API, explanation posted, 11 days zero events
- **Proof (current code):** NOT VERIFIABLE FROM CODE — this is a claim about external Sentry dashboard state (issue status, comment posted, event count over 11 days), not something grep/read of the repo can confirm.
- **Note:** The underlying code fix is confirmed (see PS-P1-magiclink-code). Whether the actual Sentry issue was closed and stayed quiet is an external-system claim I was scoped not to check live (grep/read only). Recommend confirming directly in the Sentry dashboard if this matters for the audit sign-off.

### PLAN — fable-harness-plan.md — content summary + phase status
- **Area:** Harness & Workflow (docs/fable/06-HARNESS-AND-WORKFLOW.md + fable-harness-plan.md + FABLE_FINDINGS.md H7)
- **Claimed:** N/A — this is a self-audit plan for the human operator (Ranjith), not a code fix
- **Proof (current code):** File is 147 lines, 4 phases: Phase 1 (safety habits — two-question ritual before irreversible actions, throwaway creds), Phase 2 (ship discipline — drain PRs to <=2 open/none >7 days, PR size cap ~20 files), Phase 3 (2-4 weeks: learn git/sessions/secrets/CI/PR-mechanics deeply enough to audit Claude's work), Phase 4 (month 2: automation inventory table + blast-radius notes before re-enabling any loop). Checked one verifiable Phase-2 metric directly: `gh pr list --state open` currently returns 9 open PRs (#56-#64), ALL dependabot-generated, all opened 2026-07-17 (today) — none of them the stale hand-authored PRs (#22/#23/#27/#29/#30/#31) the plan originally flagged.
- **Note:** This whole file is behavioral coaching for the human, not code — nothing in it is "fixable" by editing a repo file, so most checkboxes can't be verified from code. The one code-checkable metric (open-PR count, target <=2) currently reads 9, above target, though all 9 are same-day dependabot bumps rather than the aged hand-made PRs originally called out — so this doesn't confirm the underlying habit (draining PRs promptly) has changed, only that today's snapshot happens to be dependabot noise. Flag to the user rather than mark FIXED or OPEN — this is a "did you do the habit" item, not a "did the code change" item.

## 🗑 DEAD CODE (harmless, unused) — 1

### SI4 — FeedService.list_for_user is dead code ranking by score, not judge verdict
- **Area:** SERVICE-INTERNALS (SI1-SI5)
- **Claimed:** OPEN (MEDIUM, harmless today)
- **Proof (current code):** backend/src/services/feed.py:60 `list_for_user` still orders `ORDER BY score DESC, created_at DESC` (line 77) — never COALESCE(llm_fit_score, score). `grep -rn list_for_user src/ --include=*.py` returns ONLY the definition at feed.py:60 — ZERO callers. The live callers of FeedService only use other methods: jobs.py:637 `get_score`, main.py:765 + rescore.py:211 + tasks.py:175 `upsert_feed_row`. Class docstring (feed.py:1-7) still calls it the 'single source of truth'.
- **Note:** STILL OPEN / unfixed — list_for_user remains unreachable dead code with keyword-score-only ordering and the misleading 'SSOT' docstring. Harmless now, but a future SI1 revival that trusts this method would rank by keyword score, contradicting the judge-outranks-funnel rule. Not addressed.

## ⚪ OPEN — ACCEPTED (by-design / deferred, code state matches) — 14

### 05-P0-LEGAL — Scraping LinkedIn/Glassdoor/Indeed still in SOURCE_REGISTRY
- **Area:** COMPLIANCE & LEGAL
- **Claimed:** OPEN — OWNER DECISION
- **Proof (current code):** backend/src/main.py:118 'linkedin': LinkedInSource, :122 'indeed': JobSpySource, :123 'glassdoor': JobSpySource — all still registered. No decision doc found.
- **Note:** Code state matches doc: scrapers unchanged in registry. Correctly an owner business/legal call, not a code fix.

### 05-P1-POLICIES — Privacy & Terms pages are stubs
- **Area:** COMPLIANCE & LEGAL
- **Claimed:** OPEN — OWNER (writing)
- **Proof (current code):** frontend/src/app/privacy/page.tsx = 48 lines, frontend/src/app/terms/page.tsx = 47 lines — still stub-sized. No lawful-basis/retention detail.
- **Note:** Confirmed still stubs. Owner writing task, matches doc.

### 05-P1-SUBPROCESSORS — Subprocessor disclosure absent (CV→LLM providers)
- **Area:** COMPLIANCE & LEGAL
- **Claimed:** OPEN — OWNER
- **Proof (current code):** grep for subprocessor/Groq/Cerebras/Resend/Railway across frontend/src/app/privacy and /terms returned ZERO matches — no subprocessor list published anywhere.
- **Note:** Confirmed absent. Owner disclosure task, matches doc.

### 05-P2-MFA — No MFA option for accounts
- **Area:** COMPLIANCE & LEGAL
- **Claimed:** OPEN — OWNER
- **Proof (current code):** grep -i mfa/2fa/totp/authenticator across backend/src + frontend/src hit only core/skill_synonyms.py (unrelated vocabulary) — no MFA/TOTP auth feature exists.
- **Note:** Confirmed no MFA feature. Owner decision (magic-link already removes password phishing), matches doc.

### 05-P2-BREACH — No breach-notification runbook
- **Area:** COMPLIANCE & LEGAL
- **Claimed:** OPEN — OWNER
- **Proof (current code):** find for any *breach* file (excluding node_modules) returned nothing — no runbook committed.
- **Note:** Confirmed absent. Owner writing task, matches doc.

### P3 — Gate is enforced but verify-job360 is not (nothing enforces the crown-jewel skill)
- **Area:** Harness & Workflow (docs/fable/06-HARNESS-AND-WORKFLOW.md + fable-harness-plan.md + FABLE_FINDINGS.md H7)
- **Claimed:** OPEN (accepted) — tooling polish
- **Proof (current code):** .claude/hooks/verify-reminder.sh:5-6,10-12 — comment says "Non-blocking: it surfaces a reminder via systemMessage and lets the turn end normally"; the script only ever prints a systemMessage JSON, never `exit 2`. scripts/agent-gate.sh runs pytest + ruff + api-types drift only (lines 114-176) — no call to /verify-job360 anywhere in the file.
- **Note:** Code state matches the doc's claimed OPEN status — no enforcement added since the audit.

### P4 — commit-gate.sh bypass surfaces (matches only literal `git commit`; fails open on parse error)
- **Area:** Harness & Workflow (docs/fable/06-HARNESS-AND-WORKFLOW.md + fable-harness-plan.md + FABLE_FINDINGS.md H7)
- **Claimed:** OPEN (accepted) — tooling polish
- **Proof (current code):** .claude/hooks/commit-gate.sh:10-15 — `CMD="$(echo "$INPUT" | python -c ... 2>/dev/null)"` then `case "$CMD" in *"git commit"*) ;; *) exit 0 ;; esac`. If python isn't on PATH or the JSON parse fails, CMD becomes empty, the case falls to the default `*) exit 0 ;;` branch — i.e. the commit is ALLOWED, not blocked.
- **Note:** Matches the doc's claim precisely: still literal-string matched (a `gh`/MCP-based commit path is uncovered) and still fail-open on parse failure. Unfixed, as claimed OPEN.

### P5 — settings.local.json is a junk-drawer of accreted one-off allows
- **Area:** Harness & Workflow (docs/fable/06-HARNESS-AND-WORKFLOW.md + fable-harness-plan.md + FABLE_FINDINGS.md H7)
- **Claimed:** OPEN (accepted) — 191 lines at time of audit
- **Proof (current code):** .claude/settings.local.json is now 224 lines (`wc -l` confirmed), i.e. it GREW since the audit's 191-line snapshot, not shrunk. Dead entries still present: `mcp__chrome-devtools__navigate_page/take_screenshot/take_snapshot/resize_page/click/evaluate_script` (lines 81-84, 87-88) while `enabledMcpjsonServers` (lines 199-203) only lists `github`, `redis`, `sentry` — chrome-devtools is not a configured server, exactly the dead-entry pattern the doc flagged.
- **Note:** Confirmed still OPEN, and slightly worse than the doc's snapshot (191→224 lines) — the recommended `fewer-permission-prompts` cleanup has not been run.

### H6 — Migrations auto-apply inside the request-serving process on every boot
- **Area:** OPS & RELIABILITY
- **Claimed:** OPEN / partially mitigated (railway.json healthcheck; transactional migrations done ea18e10) — never moved out of lifespan
- **Proof (current code):** backend/src/api/main.py:112 `await init_db()` inside `lifespan()` -> backend/src/api/dependencies.py:19-21 `from migrations import runner` / `await runner.up(str(DB_PATH))`. Migrations STILL run in the serving process at boot. Mitigation present: backend/railway.json healthcheckPath `/api/health` + restartPolicyType `ON_FAILURE` (a failed migration -> app not ready -> Railway keeps old container). PROGRESS.md line 109 confirms migrations were made atomic (ea18e10) but NOT moved to a separate release step.
- **Note:** Code matches the OPEN stance. The fix as written ('run migrations as an explicit pre-deploy/release step, keep boot fail-safe') was NOT done — migrations still execute inside FastAPI lifespan. Only the compounding atomicity issue (H2/V1) was addressed and a healthcheck-gated rollout was added. Reasonable to accept, but the literal H6 recommendation is unimplemented.

### M10 — Hardcoded DB password in prod compose + empty secret defaults outside prod
- **Area:** OPS & RELIABILITY
- **Claimed:** Not claimed fixed anywhere
- **Proof (current code):** Hardcoded password STILL present: docker-compose.prod.yml:22 `POSTGRES_PASSWORD: job360dev`, :58 and :97 `DATABASE_URL: postgresql://job360:job360dev@postgres:5432/job360`. Empty secret defaults STILL present: backend/src/core/settings.py:270-271 `SESSION_SECRET = os.getenv("SESSION_SECRET", "")` / `CHANNEL_ENCRYPTION_KEY = os.getenv(..., "")`; validate_required_env() (settings.py:282-298) only checks when `APP_ENV=production` or `RAILWAY_ENVIRONMENT` is set (settings.py:288-292).
- **Note:** Neither part of M10 was changed. Real-world impact is low: Railway (the actual live deploy) uses managed Postgres + env-injected secrets and railway.json, NOT docker-compose.prod.yml, and secrets ARE enforced in prod via validate_required_env. But as code the finding is fully code-true and unfixed — no FIXED claim was made, so accepting as low-risk/deferred.

### S4 — HTML scrapers die silently on any layout change
- **Area:** Sources & pipeline (S1-S9)
- **Claimed:** OPEN
- **Proof (current code):** backend/src/sources/scrapers/linkedin.py:75,80-81 — `logger.warning(...HTML parsing failed...)` on a parse exception per query, but a clean zero-match run still just logs `logger.info("LinkedIn: found %s relevant jobs", len(jobs))` and returns `[]`. No structural/anchor health check exists in linkedin.py, bcs_jobs.py, aijobs_ai.py, or climatebase.py (grepped for health/anchor/structural/assert/metric — zero hits across all four).
- **Note:** NOT fixed. A DOM change that silently drops all matches (no exception) still logs identically to a genuine zero-result day, exactly as the finding describes.

### S5 — JobSpy (Indeed/Glassdoor) leaks a thread on every timeout
- **Area:** Sources & pipeline (S1-S9)
- **Claimed:** OPEN (acknowledged in code comment, not fixed)
- **Proof (current code):** backend/src/services/scheduler.py:156-160 — comment: 'wait_for cancels the coroutine on timeout; for JobSpy's asyncio.to_thread the OS thread keeps running but is detached — the pipeline no longer waits on it.' backend/src/sources/other/indeed.py:39 — `df = await asyncio.to_thread(scrape_jobs, ...)` has no internal timeout of its own.
- **Note:** NOT fixed, but the maintainers documented the tradeoff in a comment (self-aware, not silent) rather than shipping a killable process pool or an internal scrape_jobs timeout. Behavior matches the finding exactly.

### T5 — Rule #28 lists skill_synonyms.py as 'being removed' but it's still live
- **Area:** TEST-QUALITY / DOC-DRIFT / HYGIENE (T1-T12)
- **Claimed:** OPEN, doc claims cleanup in-progress
- **Proof (current code):** backend/src/core/skill_synonyms.py is still 615 lines (matches doc's cited size exactly) and is still imported by backend/src/services/profile/keyword_generator.py and backend/src/services/skill_matcher.py. CLAUDE.md rule #28 (root) still lists 'core/skill_synonyms.py' under 'Offenders being removed'.
- **Note:** File not shrunk, not removed. Doc's claim of an in-progress removal is unsubstantiated by the code — the rule text is stale/aspirational. Flagging as OPEN_ACCEPTED since scoring-code is explicitly hands-off per user memory (feedback_pillar2_hands_off.md) — but the doc text itself is misleading and should be corrected to scope skill_synonyms.py out or explain the distinction.

### T12 — No real concurrent-write integration test
- **Area:** TEST-QUALITY / DOC-DRIFT / HYGIENE (T1-T12)
- **Claimed:** OPEN, backlog an asyncio.gather two-writer test
- **Proof (current code):** backend/tests/test_db_retry.py — only 3 tests: test_retries_then_succeeds_on_locked, test_gives_up_after_attempts_and_reraises, test_non_lock_error_not_retried (lines 14/29/38). Grep for `asyncio.gather` in this file returns zero matches — confirms it only mocks the lock/retry path, no real two-writer race.
- **Note:** Still open, matches doc exactly. This was explicitly filed as a backlog item by the doc, not claimed fixed — code confirms it's genuinely still missing.

## ✅ CONFIRMED FIXED (proof in main) — 50

### H4 — Sentry send_default_pii ships cookies/passwords to third party
- **Area:** SECURITY
- **Claimed:** FIXED
- **Proof (current code):** backend/src/core/observability.py:80 `send_default_pii=False,` + :81 `before_send=_scrub_pii,` inside sentry_sdk.init(); _scrub_pii (:30-52) pops cookie/authorization/set-cookie headers (:44), drops req['cookies'] (:46), redacts req['data'] (:48), and _strip_password_fields recurses to delete any 'password' key (:18-27). main.py:94-96 delegates to this shared init via _init_sentry(); worker uses same module.
- **Note:** Fully confirmed. Init is also prod-gated (observability.py:68-72) so dev/test never report. Fix is stronger than doc: single shared module used by BOTH api and worker.

### H5 — Session cookie Secure flag gated on wrong env var (JOB360_ENV)
- **Area:** SECURITY
- **Claimed:** FIXED
- **Proof (current code):** backend/src/api/routes/auth.py:121 `secure = _is_production()` inside _set_session_cookie(), importing middleware._is_production (auth.py:23). middleware.py:34-38 `_is_production()` returns True when APP_ENV=='production' OR RAILWAY_ENVIRONMENT set — the SAME signal as HSTS/Sentry/CORS. Old `os.environ.get('JOB360_ENV')=='prod'` gone; grep for JOB360_ENV in auth.py = 0 hits.
- **Note:** Code fix confirmed and unified. Residual deploy dependency: APP_ENV=production OR RAILWAY_ENVIRONMENT must actually be set on the live Railway service for Secure to turn on — that is an ops/env check, not a code gap. On Railway, RAILWAY_ENVIRONMENT is auto-set, so it holds.

### M18 — Untrusted XML feeds parsed with stdlib ElementTree (billion-laughs DoS)
- **Area:** SECURITY
- **Claimed:** FIXED
- **Proof (current code):** All 11 feed/ATS XML sources now import `from defusedxml.ElementTree import fromstring as _safe_fromstring` and call `_safe_fromstring(_sanitize_xml(xml_text))` — e.g. sources/feeds/nhs_jobs.py:5,48; ats/personio.py:7,52; ats/successfactors.py:44; feeds/{biospace,jobs_ac_uk,nhs_jobs_xml,realworkfromanywhere,uni_jobs,weworkremotely,workanywhere}.py all identical. defusedxml forbids entity expansion + DTD.
- **Note:** Fix took a DIFFERENT (stronger) route than the doc described. The doc's claim that _sanitize_xml strips DOCTYPE/ENTITY is FALSE — base.py:23-29 still only escapes bare & and removes control chars, no DOCTYPE strip. But defusedxml.fromstring at every call site closes billion-laughs AND XXE, so the vector is closed. Effective fix confirmed.

### L3 — CORS allow_credentials=True with wildcard/unvalidated origins
- **Area:** SECURITY
- **Claimed:** OPEN (LOW)
- **Proof (current code):** backend/src/api/main.py:56-77 `_resolve_cors_credentials()` fails closed: wildcard '*' origin RAISES RuntimeError in prod (:66-71) / downgrades to no-credentials in dev; empty origin list in prod RAISES (:73-76). Called at main.py:127 `_allow_credentials = _resolve_cors_credentials(_allow_origins, is_prod=_is_prod_env())` before add_middleware.
- **Note:** Doc listed L3 as an open LOW but it is actually fixed in current code — prod can no longer boot with wildcard-plus-credentials.

### CSRF — CSRF: SameSite-only + side-effecting GET download (OriginCheck + GET->POST)
- **Area:** SECURITY
- **Claimed:** FIXED (both halves)
- **Proof (current code):** OriginCheckMiddleware present at middleware.py:136-167 — rejects POST/PUT/PATCH/DELETE (:150) carrying an Origin not in FRONTEND_ORIGIN allowlist with 403 (:163-166); wired at main.py:144. Second half: the download route is now POST — tailor.py:242 `@router.post("/tailor/{job_id}/{doc_kind}/download")` (no GET download route remains), so the middleware covers it.
- **Note:** Both halves confirmed. No-Origin requests (curl/tests) still pass by design; browsers always send Origin so the CSRF vector is blocked.

### H8 — Kanban drag listeners swallow clicks on card's own buttons
- **Area:** FRONTEND & AUTH-UI
- **Claimed:** not explicitly claimed FIXED in 03-doc, but fix approach matches suggestion
- **Proof (current code):** frontend/src/components/pipeline/KanbanBoard.tsx:419 — `useSensor(PointerSensor, { activationConstraint: { distance: 8 } })`. Drag listeners are still spread on the whole card at :203 (`{...listeners}`), but the activationConstraint (8px move required before drag activates) means a plain click/tap on a nested button no longer gets eaten — this is exactly the fix the finding suggested (option 1: activationConstraint).
- **Note:** Card-level listeners were not moved to a dedicated handle (the finding's alternative fix), but the activationConstraint fix was applied instead — functionally resolves the click-swallowing bug.

### H9 — Dashboard queries ignore errors, render failures as 'No jobs found'
- **Area:** FRONTEND & AUTH-UI
- **Claimed:** FIXED (903af8d) per docs/fable/03-FRONTEND-AND-AUTH-UI.md:30
- **Proof (current code):** frontend/src/app/dashboard/page.tsx:120-121 destructures `isError: jobsIsError, error: jobsError` from the jobs useQuery, and :507-513 renders a distinct destructive error banner (`{jobsIsError && (...apiErrorMessage(jobsError,...)...)}`) instead of falling through to the empty state (:516 `{!jobsIsError && (...JobList...)}`). Same pattern for the counts query at :160-161 / :488-492.
- **Note:** Matches doc claim exactly.

### H10 — Every background refetch blanks job list with skeletons
- **Area:** FRONTEND & AUTH-UI
- **Claimed:** FIXED per docs/fable/03-FRONTEND-AND-AUTH-UI.md:30 (bundled with the dashboard error-state fix)
- **Proof (current code):** frontend/src/app/dashboard/page.tsx:530 — `<JobList jobs={jobs} loading={isLoading} onAction={handleAction} />` (was `isFetching`, now `isLoading`). frontend/src/components/jobs/JobList.tsx:52 — `if (loading) { ...skeleton... }` only triggers on true `loading` (now mapped to isLoading = no data yet). Dashboard also adds a non-blocking 'Refreshing…' indicator at :521-528 for `refreshing = isFetching && !isLoading && !jobsIsError` (:131), and the query itself uses `placeholderData: (prev) => prev` (:128) to keep old data visible during refetch.
- **Note:** Fully resolved — background refetches no longer blank the grid.

### H11 — Four auth pages leak raw technical error strings to users
- **Area:** FRONTEND & AUTH-UI
- **Claimed:** docs/fable/03-FRONTEND-AND-AUTH-UI.md P1 says FIXED (1c75b50) but only names 2 of the 4 pages (reset-password, verify-email); FABLE_FINDINGS.md H11 lists all 4 (reset-password, verify-email, forgot-password, auth/magic)
- **Proof (current code):** All four call `friendlyAuthError`: frontend/src/app/(auth)/reset-password/page.tsx:69 `friendlyAuthError(err, "Reset link is invalid or expired. Request a new one.")`; frontend/src/app/(auth)/verify-email/page.tsx:46 `friendlyAuthError(...)`; frontend/src/app/(auth)/forgot-password/page.tsx:52 `setServerError(friendlyAuthError(err, "Something went wrong. Please try again."))`; frontend/src/app/auth/magic/page.tsx:44 `friendlyAuthError(...)` (note: this page lives at src/app/auth/magic/page.tsx, NOT under the (auth) route group — path differs slightly from FABLE_FINDINGS.md's citation).
- **Note:** The 03-doc only mentions 2/4 pages as fixed but the code shows all 4 (matching FABLE_FINDINGS.md's broader H11 list) are actually fixed — doc under-reports the scope of the fix, but the fix itself is more complete than the 03-doc implies, not less.

### M14 — Frontend auth middleware fails open when backend unreachable
- **Area:** FRONTEND & AUTH-UI
- **Claimed:** FIXED (b939e29) per docs/fable/03-FRONTEND-AND-AUTH-UI.md:30 — fail-closed, redirects to /login?error=service_unavailable WITHOUT deleting the cookie
- **Proof (current code):** frontend/src/middleware.ts:55-72 — the fetch to `/api/auth/me` is wrapped in try/catch; the catch block (:62-72) does NOT call the cookie-deleting `bounceToLogin()`, instead builds its own redirect: `loginUrl.searchParams.set("error", "service_unavailable"); return NextResponse.redirect(loginUrl, { status: 307 });` with a comment explicitly citing 'docs/fable/03 F4' and stating the cookie is deliberately NOT deleted.
- **Note:** Matches doc claim exactly, including the code comment referencing the fable finding by name.

### M15 — No security headers from the frontend (Next.js) layer
- **Area:** FRONTEND & AUTH-UI
- **Claimed:** Not explicitly marked FIXED in either doc, but code shows a fix landed
- **Proof (current code):** frontend/next.config.ts:26-33 adds a `headers()` block referencing `SECURITY_HEADERS` from frontend/src/lib/security-headers.ts:31-36, which sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, and a `Content-Security-Policy` including `frame-ancestors 'none'` (security-headers.ts:26) compatible with Sentry/PostHog connect-src (:24).
- **Note:** Fix satisfies the finding's suggested minimum (frame-ancestors, nosniff, CSP compatible with Sentry/PostHog). No HSTS header set here, but HSTS is handled by the backend per CLAUDE.md's APP_ENV note — not flagged as missing by the original finding.

### L7 — Profile page detects 'no profile' by string-matching '404' in the error message
- **Area:** FRONTEND & AUTH-UI
- **Claimed:** No explicit FIXED claim in either doc, but code shows the fix landed
- **Proof (current code):** frontend/src/app/profile/page.tsx:98 — `if (err instanceof ApiError && err.isNotFound) { setProfile(null); }` with a comment at :94-97 explicitly citing 'L7'. `isNotFound` is a real typed getter: frontend/src/lib/api-error.ts:31 `get isNotFound() {`.
- **Note:** Fully matches the finding's suggested fix (use the typed ApiError.isNotFound instead of string-matching).

### L8 — Optimistic like/skip update has no cancelQueries and doesn't patch the bucket-counts query
- **Area:** FRONTEND & AUTH-UI
- **Claimed:** No explicit FIXED claim in either doc, but code shows the fix landed
- **Proof (current code):** frontend/src/app/dashboard/page.tsx:227-230 — `await Promise.all([queryClient.cancelQueries({ queryKey: queryKeys.jobList(filters) }), queryClient.cancelQueries({ queryKey: queryKeys.jobList(allJobsKey) })]);` runs before the optimistic write, and :246 `queryClient.setQueryData<JobListResponse>(queryKeys.jobList(allJobsKey), patchJob);` patches the bucket-counts query alongside the filtered list at :243. Comments at :224-226 and :244-245 explicitly cite 'L8'.
- **Note:** Both halves of the finding (missing cancelQueries + unpatched bucket-counts query) are resolved.

### PS-P0 — Worker had no Sentry — cron crashes invisible
- **Area:** Production Signals (docs/fable/09-PRODUCTION-SIGNALS.md)
- **Claimed:** FIXED (272d29b)
- **Proof (current code):** backend/src/workers/settings.py:103-105 inside worker_startup(): `from src.core.observability import init_sentry` / `init_sentry(component="worker")`. Shares backend/src/core/observability.py:55 `def init_sentry(*, component: str = "api") -> bool`, same function API calls at backend/src/api/main.py:94-96 `init_sentry(component="api")`. Tag set via observability.py:84 `sentry_sdk.set_tag("component", component)`.
- **Note:** Matches doc claim exactly — both API and worker processes now call the same init_sentry() with a component tag.

### PS-P1-magiclink-code — Magic-link 500 (UniqueViolation) — code fix
- **Area:** Production Signals (docs/fable/09-PRODUCTION-SIGNALS.md)
- **Claimed:** already fixed in current code (find-or-create atomic via INSERT OR IGNORE)
- **Proof (current code):** backend/src/services/auth/magic_link.py:173 `"INSERT OR IGNORE INTO users(id, email, password_hash, email_verified_at) " "VALUES (?, ?, ?, ?)"`, preceded by comment block lines 161-169 explaining this replaces the old select-then-insert race that raised UniqueViolation.
- **Note:** Code-side claim fully confirmed. Doc's own flagged GDPR tension is also real in code: lines 180-185 do `UPDATE users SET ... deleted_at = NULL WHERE id = ?` on every consume, unconditionally reactivating soft-deleted accounts — still an open conflict with any erasure guarantee, as the doc itself says needs reconciling (not something I can resolve here).

### PS-P2 — /api/client-log manufacturing Sentry noise
- **Area:** Production Signals (docs/fable/09-PRODUCTION-SIGNALS.md)
- **Claimed:** FIXED — level-gated + ignore_logger('job360.client')
- **Proof (current code):** backend/src/api/routes/client_log.py:24 `_LEVELS = {"error", "warning", "info"}` and lines 49-50 `level = body.level if body.level in _LEVELS else "error"` / `log_fn = getattr(_client_log, level, _client_log.error)`. backend/src/core/observability.py:75 imports `ignore_logger` and line 92 calls `ignore_logger("job360.client")`.
- **Note:** Both halves present. The actual noise-stopper is ignore_logger (line 92) — the level-gate alone wouldn't suppress Sentry events since 'error' is itself a valid/default level; ignore_logger is what keeps job360.client records out of Sentry's logging integration entirely.

### PS-P1-funnel — No product funnel events instrumented in PostHog
- **Area:** Production Signals (docs/fable/09-PRODUCTION-SIGNALS.md)
- **Claimed:** FIXED (63964ec) — 6 events, now consent-gated per fable/05 C3
- **Proof (current code):** signup_completed: frontend/src/app/(auth)/register/page.tsx:60. cv_uploaded + extraction_completed: frontend/src/app/profile/page.tsx:119-120. search_run: frontend/src/app/dashboard/page.tsx:296. job_viewed: frontend/src/app/jobs/[id]/JobDetailClient.tsx:173. application_created: frontend/src/components/jobs/JobCard.tsx:157 and ApplyButton.tsx:39. Consent gate: frontend/src/components/providers/PostHogProviderWrapper.tsx:58-91 (posthog.init only when consent==='accepted', line 64/79) reading frontend/src/lib/consent.ts:23-32 getConsent() which defaults to null (no tracking) until explicit accept.
- **Note:** All 6 events present with real posthog.capture() calls (not stubs/dead code), and consent gating is real — init is withheld until localStorage consent is 'accepted', and posthog-js no-ops capture() before init so pre-consent calls are inert.

### 05-P0-COMPLIANCE — Right-to-be-forgotten: hard_delete_user erases all per-user tables
- **Area:** COMPLIANCE & LEGAL
- **Claimed:** FIXED
- **Proof (current code):** backend/src/repositories/database.py:1413 async def hard_delete_user; loops _PER_USER_TABLES (1433 `for tbl in self._PER_USER_TABLES: DELETE FROM {tbl} WHERE user_id = ?`), anonymises run_log (1442 `UPDATE run_log SET user_id = NULL`) and audit_log (1453 `UPDATE audit_log SET user_id = NULL`), deletes email-keyed magic_link_tokens (1461), drops users row last (1467). _PER_USER_TABLES defined at 1340.
- **Note:** Real erasure present. ONE discrepancy: doc says 'all 17 per-user tables' but _PER_USER_TABLES (1340-1346) lists only 16 (application_stage_history, applications, email_verifications, notification_ledger, notification_rules, oauth_states, password_resets, sessions, tailored_documents, tailored_usage, user_actions, user_channels, user_feed, user_notification_digests, user_profile_versions, user_profiles). Count is 16, not 17 — cosmetic, does not weaken the fix.

### 05-DELETE-PW — DELETE /auth/users/me verifies current password before erasure
- **Area:** COMPLIANCE & LEGAL
- **Claimed:** FIXED (rule #26)
- **Proof (current code):** backend/src/api/routes/auth.py:292 @router.delete('/users/me', status_code=204); line 312 `if not row or not verify_password(row['password_hash'], req.current_password): raise HTTPException(401)`; line 314 `await db.hard_delete_user(user.id)`; line 317 `response.delete_cookie(SESSION_COOKIE_NAME)`.
- **Note:** Password verified before mutation, session cookie cleared after — matches rule #26 pattern.

### 05-P1-ARTICLE20 — Article-20 data export GET /auth/users/me/export
- **Area:** COMPLIANCE & LEGAL
- **Claimed:** FIXED
- **Proof (current code):** backend/src/api/routes/auth.py:260 @router.get('/users/me/export') → db.export_user_data(user.id) (272). DB method at database.py:1375 export_user_data: scoped by user_id (1395 `WHERE user_id = ?`), redacts secrets via _scrub_export_row (1369/_EXPORT_REDACT_COLUMNS at 1363), token tables omitted (_EXPORT_TABLES 1353 excludes sessions/password_resets/email_verifications/oauth_states).
- **Note:** Read-only, session-scoped, secrets redacted, token tables omitted. Confirmed.

### FF-L1 — Magic-link consume resurrects GDPR-deleted account
- **Area:** COMPLIANCE & LEGAL
- **Claimed:** FIXED (closed by hard-delete)
- **Proof (current code):** Deletion is now hard (auth.py:314 hard_delete_user actually DELETEs the users row — database.py:1467), so no soft-deleted row survives for magic-link to revive. soft_delete_user still exists (database.py:1327) but grep shows it has ZERO callers now — dead code.
- **Note:** ADVERSARIAL NOTE: magic_link.py:182-186 STILL runs `UPDATE users SET ... deleted_at = NULL` on every consume and the comment still says 'reactivate a soft-deleted account'. This resurrection logic is literally intact — it is only harmless because nothing sets deleted_at anymore (soft_delete_user is unreachable). If any future path re-introduces a soft-delete, resurrection returns. Also unchanged: magic-link lazily creates an account for ANY address that clicks a link (INSERT OR IGNORE, magic_link.py:172) — the 2nd half of L1, left as by-design for passwordless auth.

### 05-P1-CONSENT — Consent banner gates PostHog (PECR); session recording off
- **Area:** COMPLIANCE & LEGAL
- **Claimed:** FIXED (4af1c7b)
- **Proof (current code):** frontend/src/components/providers/PostHogProviderWrapper.tsx:64 `if (consent !== 'accepted') return;` — posthog.init (79) only runs after accept; disable_session_recording: true (89); capture on decline stopped (67 opt_out_capturing+reset). Consent store frontend/src/lib/consent.ts (getConsent returns null until decided). Banner ConsentBanner.tsx:54-63 gives Decline and Accept identical size='sm' buttons — reject as easy as accept.
- **Note:** Real gate: PostHog never init()-ed pre-consent; session recording disabled; no pre-ticked default; Decline == Accept prominence. Fully matches PECR claim.

### 05-P1-SENTRY — Sentry send_default_pii=False + before_send scrubber
- **Area:** COMPLIANCE & LEGAL
- **Claimed:** FIXED
- **Proof (current code):** backend/src/core/observability.py:77 sentry_sdk.init(..., send_default_pii=False (80), before_send=_scrub_pii (81)). Scrubber _scrub_pii (30) strips auth headers, cookies, request body ('data'→'[redacted]' 48) and password fields.
- **Note:** Doc cited api/main.py:66 — LOCATION DRIFTED: actual init is in core/observability.py, not main.py. Fix itself is present and correct.

### 05-P1-MASKEMAIL — mask_email() applied at all log sites
- **Area:** COMPLIANCE & LEGAL
- **Claimed:** FIXED (all 6 sites)
- **Proof (current code):** backend/src/utils/logger.py:164 def mask_email (alice@x.com → a***@x.com, None→<none>, non-address→***). Applied in email_sender.py at 6 call sites (81,84,89,99,127,129) and the unknown-email path password_reset.py:109. grep shows no raw to_email/email left in those logger lines.
- **Note:** All 6 email_sender sites + the password_reset unknown-email leak (people who aren't users) are masked. Confirmed.

### 05-P2-AUDIT — audit_log migration 0025 + QueueListener DB tee
- **Area:** COMPLIANCE & LEGAL
- **Claimed:** FIXED (b939e29)
- **Proof (current code):** migrations/0025_audit_log.up.sql creates audit_log table + 2 indexes. backend/src/services/audit_trail.py: QueueHandler→QueueListener daemon (134-137), _DBAuditHandler writes rows (81 INSERT INTO audit_log), email denylisted from detail (_DETAIL_DENYLIST 41). Wired in: logger.py:152-154 install_db_audit_trail(audit) called from setup_audit_logger.
- **Note:** Non-blocking (queue+daemon thread), never raises into request path, PII-denylisted, anonymised on erasure. Actually installed at logger setup, not just defined. Confirmed.

### 05-P2-DEPENDABOT — Dependabot config (pip + npm + github-actions)
- **Area:** COMPLIANCE & LEGAL
- **Claimed:** FIXED (4af1c7b)
- **Proof (current code):** .github/dependabot.yml present: version 2, three updates — pip (/backend, weekly monday), npm (/frontend, weekly monday), github-actions (/, weekly monday), grouped minor/patch.
- **Note:** All three ecosystems, weekly. Confirmed.

### C1 — One shared psycopg async connection serves every request (throughput/interleave)
- **Area:** DATA & DB
- **Claimed:** FIXED (15c5b68) — get_request_db per-request connection; 9 route files migrated; get_db stays boot singleton
- **Proof (current code):** api/dependencies.py:33-51 get_request_db() opens a fresh JobDatabase(str(DB_PATH)) + await db.connect(), yields, closes in finally. Wiring confirmed: grep shows 41 uses of get_request_db across 9 route files (actions/auth/health/jobs/notifications/notification_rules/pipeline/runs/tailor); ZERO routes use Depends(get_db). get_db (dependencies.py:25-30) documented as boot/schema-owner only.
- **Note:** Fix is real but NOT the psycopg_pool.AsyncConnectionPool the doc/fix suggested — it opens a brand-new connection per request (connect() at database.py:42). That resolves the concurrency/interleave root cause but adds a full TCP+auth handshake on every request (no pooling). Functionally correct; a connection pool is still the better end-state. Matches the doc's own STATUS wording though.

### H2 — Migrations half-apply then get marked done (silent schema drift)
- **Area:** DATA & DB
- **Claimed:** FIXED (ea18e10) — each migration in its own BEGIN/COMMIT, ROLLBACK+re-raise; DuplicateTable no longer treated as applied
- **Proof (current code):** migrations/runner.py:260-266 `async with db.transaction():` wraps `await _apply_up_sql(db, sql)` AND the `INSERT INTO _schema_migrations ... ON CONFLICT DO NOTHING` together — body+bookkeeping commit atomically. Comment 256-259 confirms DuplicateTable is NO LONGER swallowed-and-recorded. Session pg_advisory_lock (runner.py:240) serialises concurrent booters. lastval poisoned-transaction trap handled via SAVEPOINT in pg.py:524-528.
- **Note:** Confirmed at the runner level, so it covers ALL migrations (V1), not just 0002.

### H3 — UndefinedColumn swallowed → dashboard shows empty instead of erroring
- **Area:** DATA & DB
- **Claimed:** FIXED — UndefinedColumn not remapped to OperationalError; logged loudly and re-raised
- **Proof (current code):** pg.py:93-97 _MISSING_OBJECT_ERRORS deliberately EXCLUDES UndefinedColumn (comment 90-92). pg.py:488-498 `except psycopg.errors.UndefinedColumn:` logs db_undefined_column then `raise` (propagates as ProgrammingError, not OperationalError). Identical guard in the sync shim pgsync.py:72-76. Only UndefinedTable/UndefinedObject/InvalidSchemaName remap to OperationalError (pg.py:499-500).
- **Note:** Both async (pg.py) and sync (pgsync.py) shims fixed consistently.

### M5 — purge_old_jobs deletes still-live jobs on first_seen + orphans child rows
- **Area:** DATA & DB
- **Claimed:** FIXED (both halves, 4af1c7b) — purge keys on last_seen_at + deletes catalog-derived children; user-delete erases all per-user tables
- **Proof (current code):** database.py:595 & 630 purge keys on `COALESCE(last_seen_at, first_seen) < ?` (liveness, not ingestion). Children deleted at database.py:623-628 for _PURGE_CASCADE_TABLES = (job_enrichment, job_embeddings, user_feed, user_notification_digests) [database.py:29-34], guarded by an information_schema.tables/current_schema() presence check (618-622). hard_delete_user (database.py:1413-1468) DELETEs every _PER_USER_TABLES row + anonymises run_log/audit_log + deletes email-keyed magic_link_tokens.
- **Note:** By design applications/user_actions/tailored_documents/notification_ledger are NOT in the purge cascade — user-authored rows survive a catalog purge (rule #3). That matches the doc's stated intent, so the 'applications orphan' risk the M5 text raised is an accepted trade-off, not deleted. Verify FK-integrity expectation with owner if strict referential purity is wanted.

### M13 — lastrowid via session-global lastval() returns wrong ids
- **Area:** DATA & DB
- **Claimed:** doc02 P2: transaction-safe but not switched to RETURNING id
- **Proof (current code):** pg.py:518-522 gates the lastval() probe on `_has_insert(...) and 'returning' not in ... and cur.rowcount` — so an ON CONFLICT DO NOTHING that inserted NOTHING (rowcount 0) no longer returns a stale earlier-session id. Probe is IDLE-direct or SAVEPOINT-isolated (pg.py:524-528). Same guard in pgsync.py:86-98. The cross-request interleave half is closed by C1 (per-request connection, no shared session).
- **Note:** The specific stale-id-after-ON-CONFLICT race is closed and the concurrency half is gone with C1. Residual (accepted, per doc): still emulates via SELECT lastval() rather than RETURNING id — one extra round-trip per real INSERT, but no longer a correctness bug.

### L4 — Global rowid→ctid regex rewrite hits string literals
- **Area:** DATA & DB
- **Claimed:** FIXED (V3/L4) — rewrite only outside string literals
- **Proof (current code):** pg.py:321 `out = _apply_outside_string_literals(out, lambda s: _RE_ROWID.sub('ctid', s))`. _apply_outside_string_literals (pg.py:286-294) + _split_string_literals (247-283) skip quoted-string chunks, honouring '' escapes. _RE_ROWID uses \b word boundaries (pg.py:194) so 'lastrowid' is untouched.
- **Note:** Applies to both shims (pgsync reuses pg.translate at pgsync.py:68).

### V1 — Migration half-apply not unique to 0002 — fix the runner not just 0002
- **Area:** DATA & DB
- **Claimed:** verifier note; addressed by H2 runner-level transaction
- **Proof (current code):** The fix is at the runner level (runner.py:260 `async with db.transaction():` per migration body) so it covers 0000_baseline / 0017 / 0018 / 0021 and every bare-CREATE-TABLE migration, not just 0002. DuplicateTable is no longer recorded-as-applied (comment runner.py:256-259).
- **Note:** Runner-level fix = every migration benefits, exactly what V1 asked for.

### V3 — Blind ?→%s rewrite hits ? inside string literals
- **Area:** DATA & DB
- **Claimed:** FIXED (V3/L4) — placeholder swap only outside string literals
- **Proof (current code):** pg.py:338-341 does the `?`→`%s` swap LAST and via `_apply_outside_string_literals(out, lambda s: s.replace('?','%s'))`, so a `?` inside a quoted value (e.g. WHERE note = 'ok?') is preserved. Comment 338-340 documents the intent. pgsync inherits this through pg.translate() (pgsync.py:68).
- **Note:** Same literal-aware tokenizer as L4; both fixed by one mechanism.

### P1 — git push/merge/git:* auto-approved — unsupervised push possible
- **Area:** Harness & Workflow (docs/fable/06-HARNESS-AND-WORKFLOW.md + fable-harness-plan.md + FABLE_FINDINGS.md H7)
- **Claimed:** FIXED (9fdef61)
- **Proof (current code):** .claude/settings.json:5-19 allow-lists only read-only git (status/log/diff/show/branch/ls-files/fetch/rev-parse/remote -v/stash list) — no push/merge/git:*. .claude/settings.local.json (224 lines, checked in full): only "Bash(git status:*)", "git diff:*", "git log:*", "git add:*", "git commit:*", "git rev-parse *", "git add *", "git stash *" are allowed; deny list has "Bash(git push --force:*)", "-f:*", "--force-with-lease:*" (lines 194-196). Grepped whole file for "git push", "git merge", "git:*" — zero hits.
- **Note:** Confirmed. Note: settings.local.json still allows "gh pr *"/"gh api *" (lines 175-179) which could technically merge a PR via `gh pr merge` — not covered by this finding's scope (git CLI specifically) but worth the owner knowing the allowlist doesn't block every merge path.

### P2 — No secret-scan on the commit path
- **Area:** Harness & Workflow (docs/fable/06-HARNESS-AND-WORKFLOW.md + fable-harness-plan.md + FABLE_FINDINGS.md H7)
- **Claimed:** FIXED (6abce80) — gitleaks wired into .pre-commit-config.yaml
- **Proof (current code):** .pre-commit-config.yaml:16-21 — `- repo: https://github.com/gitleaks/gitleaks\n    rev: v8.18.4\n    hooks:\n      - id: gitleaks`, with a comment citing "docs/fable/06 P2".
- **Note:** Matches claim exactly.

### DBG1 — /debug skill is broken/stale — imports point at the pre-refactor tree
- **Area:** Harness & Workflow (docs/fable/06-HARNESS-AND-WORKFLOW.md + fable-harness-plan.md + FABLE_FINDINGS.md H7)
- **Claimed:** Per-skill verdict table: BROKEN — needs rewrite to src.services.*/src.repositories.* or deletion
- **Proof (current code):** .claude/skills/debug/SKILL.md:30-32 — `from src.services.profile.storage import load_profile`, `from src.services.profile.keyword_generator import generate_search_config`, `from src.services.skill_matcher import JobScorer`. Line 185 — `from src.services.channels.dispatcher import dispatch, ChannelSendResult`. Grepped the whole file for `src.profile.storage`, `src.filters.skill_matcher`, `src.notifications.slack_notify` (the three dead paths the doc named) — zero hits.
- **Note:** All 4 targets (score/logs/notify/dedup) now reference the current module tree. The `notify` target also got a real architecture-note rewrite (lines 174-179) explaining notifications are per-user via the dispatcher, not global channel classes — a correct, non-trivial fix, not just a path swap.

### GATE-M1 — commit-gate M1 fingerprint (bind stamp to pre-run tree) + advisory lock (singleton per DB) + heartbeat (silence readable)
- **Area:** Harness & Workflow (docs/fable/06-HARNESS-AND-WORKFLOW.md + fable-harness-plan.md + FABLE_FINDINGS.md H7)
- **Claimed:** FIXED — commit ee6e190, per its message: fingerprint moved to start+verified at end, pg_try_advisory_lock singleton, 30s heartbeat ticker
- **Proof (current code):** scripts/agent-gate.sh:44-53 — `tree_fingerprint()` defined, then `FP_START="$(tree_fingerprint)"` captured BEFORE any tests run. Lines 55-89 — `pg_try_advisory_lock(732360001)` probe; on BUSY, `echo "[gate] ABORT: another gate is already running..."` + `exit 1`. Lines 95-112 — `_heartbeat()` loop prints `[gate] alive — Nm elapsed` every `sleep 30`, backgrounded with `trap 'kill "$HB_PID"' EXIT`. Lines 183-190 — `FP_END="$(tree_fingerprint)"`; if `FP_START != FP_END`, prints "the working tree CHANGED while the gate was running" and `exit 1` (no stamp written). Line 195 writes the stamp from `FP_END` (the verified value), not a fresh recompute.
- **Note:** All three sub-fixes (M1 fingerprint timing, advisory lock, heartbeat) are present and match the commit message's description exactly.

### H7 — No security scanning anywhere in CI; type-check non-blocking
- **Area:** OPS & RELIABILITY
- **Claimed:** FIXED (security.yml added: pip-audit + npm audit + gitleaks + bandit)
- **Proof (current code):** .github/workflows/security.yml EXISTS with 4 jobs: `pip-audit` (backend deps), `npm-audit` (frontend deps), `gitleaks` (secret scan), `bandit` (python static analysis). Runs on push[main]/pull_request/weekly-cron. Note: they are ADVISORY not blocking — pip-audit runs `python -m pip_audit ... || true`, npm-audit `npm audit --audit-level=high || true`, gitleaks + bandit both `continue-on-error: true`. mypy in ci.yml:99 still `continue-on-error: true`.
- **Note:** Scanning now exists (finding premise 'no security scanning anywhere' is no longer true) -> CONFIRMED_FIXED. But the finding's stronger ask ('add BLOCKING pip-audit + npm audit --audit-level=high + gitleaks + bandit') is only partially met: every scan job is non-blocking, so a vulnerable dep or leaked secret still ships green. Caveat: PROGRESS.md line 275 records that security.yml was once clobbered by a stale-base merge and later restored (git log a42eed2) — it is present in this worktree now.

### M11 — CI never exercises Postgres or the migration SQL
- **Area:** OPS & RELIABILITY
- **Claimed:** Not explicitly claimed; implied by Postgres service in ci.yml
- **Proof (current code):** .github/workflows/ci.yml:28-41 starts a `postgres:16` service, ci.yml:57 exports `DATABASE_URL: postgresql://job360:job360dev@localhost:5432/job360`, ci.yml:91 runs `python -m pytest -q -p no:randomly`. The suite now runs against real Postgres: backend/tests/conftest.py:26-31 'Everything runs on Postgres via src.repositories.pg' `from src.repositories import pg as _pg`, and conftest.py:42 `from migrations import runner` / :269 `await runner.up(db_path)` applies ALL migrations per test schema. So both the prod Postgres path and migrations/*.sql ARE exercised in CI.
- **Note:** The finding's premise ('tests run on SQLite via conftest') is no longer true — conftest routes every test through the pg/pgsync shim against the CI Postgres service and runs runner.up per test. No dedicated standalone migration smoke-test job exists, but the effect (every migration applied against Postgres each run) is achieved. Effectively fixed.

### S3 — _is_uk_or_remote substring match lets foreign jobs through
- **Area:** Sources & pipeline (S1-S9)
- **Claimed:** FIXED (per inline code comment referencing 'S3 fix')
- **Proof (current code):** backend/src/sources/base.py:32-52 — `_is_uk_or_remote` now uses `re.search(rf"\b{re.escape(term)}\b", loc_lower)` for UK_TERMS, REMOTE_TERMS, and FOREIGN_INDICATORS, with a docstring explicitly stating '(S3 fix; see docs/FABLE_FINDINGS.md)' and calling out Milwaukee/Ukraine as the exact false-positive cases the finding named.
- **Note:** Confirmed fixed in code — word-boundary regex replaces the old plain substring `in` check.

### S6 — _conditional_fetch doesn't catch JSONDecodeError
- **Area:** Sources & pipeline (S1-S9)
- **Claimed:** FIXED
- **Proof (current code):** backend/src/sources/base.py:247 — `except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:` inside `_conditional_fetch`. json.JSONDecodeError is now in the tuple, matching `_request`'s exception handling (base.py:112-114).
- **Note:** Confirmed fixed — the except tuple now includes json.JSONDecodeError.

### S7 — eightykhours has no DOMAINS, fires for every user
- **Area:** Sources & pipeline (S1-S9)
- **Claimed:** FIXED (per inline comment referencing 'S7 fix')
- **Proof (current code):** backend/src/sources/scrapers/eightykhours.py:20-24 — `# S7 fix: without an override this inherited the base class's {"general"} default...` followed by `DOMAINS = {"tech"}`.
- **Note:** Confirmed fixed — DOMAINS is now set to {"tech"} exactly as the finding's suggested fix specified.

### SI2 — Quiet-hours flush for stranded instant-mode matches
- **Area:** SERVICE-INTERNALS (SI1-SI5)
- **Claimed:** OPEN (HIGH, rule #24 claimed a flush branch that didn't exist)
- **Proof (current code):** backend/src/workers/tasks.py:789-796 now has an explicit `SI2 — quiet-hours flush` branch inside `notification_tick`: `if not due and rule.get("notify_mode","instant")=="instant": if not _in_quiet_window(rule, now_utc, user_tz) and await _has_pending_digests(db, user_id): due = True`. Helpers exist: `_in_quiet_window` (tasks.py:723) and `_has_pending_digests` (tasks.py:748, `SELECT 1 FROM user_notification_digests WHERE user_id=? AND sent=0`). When due, it enqueues send_bundle (tasks.py:801). `_bundle_due` still returns False for instant (line 685-686) by design — the flush was moved to notification_tick, documented in its docstring lines 669-671.
- **Note:** Fix present and correct. NOTE: this flush only matters once SI1 is fixed — today no instant match is ever queued because score_and_ingest never runs, so there is nothing for this branch to drain in production. The logic itself is sound.

### SI3 — ChromaDB persisted to wrong dir (repo-root data/chroma) — wiped on redeploy
- **Area:** SERVICE-INTERNALS (SI1-SI5)
- **Claimed:** OPEN (HIGH, parents[3]→parents[2])
- **Proof (current code):** backend/src/services/vector_index.py:16 `from src.core.settings import DATA_DIR`, line 28 `_DEFAULT_PATH = DATA_DIR / "chroma"`. settings.py:13 `DATA_DIR = BASE_DIR / "data"` (BASE_DIR = backend/), so it now resolves to backend/data/chroma. The hand-counted `Path(__file__).resolve().parents[3]` is GONE — replaced, and lines 21-27 carry a comment explicitly documenting the old repo-root bug and why deriving from DATA_DIR fixes it.
- **Note:** Fix present. Fixed more robustly than the doc's suggested parents[3]→parents[2]: derives from settings.DATA_DIR (single source of truth) instead of hand-counting parents, so future file-move drift is impossible. The stray root-level data/chroma migration is an ops/owner task, not a code concern.

### N1 — Daily digest never fires unless send-minute is a multiple of 5
- **Area:** SECOND-OPINION FLEET (N1-N9)
- **Claimed:** OPEN bug (reported by second-opinion fleet)
- **Proof (current code):** backend/src/workers/tasks.py:687-707 (_bundle_due daily branch). Now a window check, not exact-minute: line 694 `target_local = now_local.replace(hour=h, minute=m, second=0, microsecond=0)`; line 696 `if now_local < target_local: return False`; then line 702 `if not last_sent_str: return True` and line 707 `return last_sent_local.date() < now_local.date()`. Docstring at 674-677 explicitly names 'finding N1' and self-heals a missed tick.
- **Note:** FIXED. The exact `hour==h and minute==m` compare is gone; it now fires on the first tick at/after send time and once-per-local-day, tolerating non-multiple-of-5 minutes and a missed tick.

### T1 — translate() had zero direct tests
- **Area:** TEST-QUALITY / DOC-DRIFT / HYGIENE (T1-T12)
- **Claimed:** OPEN (fix suggested: add tests/test_pg_translate.py)
- **Proof (current code):** backend/tests/test_pg_translate.py exists (130 lines). Header comment line 20: '# translate() — table-driven (finding T1: translate had no tests)'. Table-driven tests at line 46 `def test_translate_exact(sql, expected): assert pg.translate(sql) == expected`, plus test_translate_insert_or_ignore (line 50), test_translate_autoincrement (line 55), test_translate_leaves_plain_sql_unchanged (line 61), plus 4 more async adversarial tests (undefined column/table, lastrowid, savepoint-safety).
- **Note:** Fully fixed and even cites the finding ID in a comment. This is a real, non-trivial table-driven + adversarial test suite, not a token placeholder.

### T2 — Tailoring feature undocumented in CLAUDE.md
- **Area:** TEST-QUALITY / DOC-DRIFT / HYGIENE (T1-T12)
- **Claimed:** OPEN (impact: future session unaware feature exists)
- **Proof (current code):** CLAUDE.md:347 '### Tailored documents — AI CV & cover-letter generator (2026-07-04)' — full phase-summary paragraph describing services/tailoring/*, routes/tailor.py, migrations 0023/0024, frontend components, TAILOR_FREE_PER_MONTH, and links docs/peruser_cv_coverletter.md.
- **Note:** Confirmed fixed on current CLAUDE.md (root).

### T3 — .env.example + CLAUDE.md omit DATABASE_URL / OPENAI_API_KEY
- **Area:** TEST-QUALITY / DOC-DRIFT / HYGIENE (T1-T12)
- **Claimed:** Partially FIXED-PENDING-MERGE in PR #31/#30
- **Proof (current code):** .env.example:15 `DATABASE_URL=postgresql://job360:job360dev@localhost:5433/job360`; .env.example:36/38 `OPENAI_API_KEY=` / `OPENAI_MODEL=gpt-4o-mini`; also APP_ENV (line 25), LOGIN_MAX_ATTEMPTS (175), SENTRY_DSN (180) all present. CLAUDE.md:255-256 also documents both vars in the env table.
- **Note:** Since this worktree IS main/production, the PR is merged — fully fixed now, not just pending.

### T11 — .gitignore test-artifacts/*.png misses subdirs
- **Area:** TEST-QUALITY / DOC-DRIFT / HYGIENE (T1-T12)
- **Claimed:** OPEN, fix = test-artifacts/**/*.png
- **Proof (current code):** .gitignore:42-48 — comment explicitly says 'The old `test-artifacts/*.png` glob only caught top-level PNGs, letting nested screenshot dirs (design/, tailor/, verify-*/) accumulate untracked'; new pattern is `test-artifacts/*` (line 46) with explicit un-ignores `!test-artifacts/README.md` / `!test-artifacts/sample_cv.pdf` (lines 47-48).
- **Note:** Fixed with a broader pattern than even the suggested fix (test-artifacts/* covers all nested files/dirs, not just *.png).

---

## ✅ CLOSED in the 2026-07-17 follow-up pass

These were OPEN/PARTIAL/NOT-CONFIRMED above and were fixed in the same session as this
audit (safe, self-contained changes; each with a regression test where behaviour changed):

| ID | Was | Now fixed at |
|---|---|---|
| **M12** | NOT_CONFIRMED (uptime pinged `/livez`) | `.github/workflows/uptime.yml:16-18` now pings `/api/readyz` (DB+Redis probe) |
| **RULECOUNT** | NOT_CONFIRMED (skill said "27") | `.claude/skills/worker/SKILL.md:30` now "28-rule" (matches CLAUDE.md) |
| **N4** (`hours`) | PARTIAL (limit/offset bounded, `hours` not) | `backend/src/api/routes/jobs.py:437` `hours: Optional[int] = Query(None, ge=0)` |
| **N5** | OPEN (raw exception text to clients) | `routes/tailor.py:163-164` + `routes/search.py:84-85` — generic client message + `logger.exception` server-side |
| **N8** | OPEN (`_safe_fetch` swallowed cancellation) | `services/scheduler.py:166` `except asyncio.CancelledError: raise` before the broad catch |
| **N9** | OPEN (`/profile/versions` limit unbounded) | `routes/profile.py:441` `limit: int = Query(20, ge=1, le=100)` |
| **S9** | OPEN (auth failures logged at debug) | `sources/base.py:25,136` — `_AUTH_FAIL_STATUSES=(401,403)` logged at `warning`; 404/422 stay debug |
| **T10** | OPEN (CLAUDE.md called dead `score_job` a live path) | root `CLAUDE.md` now states `score_job()` has no production caller (test-only) |

Deliberately **left open** (documented, not rushed into a live app): **SI1** (notification
pipeline needs a real enqueue-wiring change + a Railway worker deploy — highest impact,
dedicated PR), **M1** (webhook SSRF — needs DNS-resolve + private-range block + tests),
**M8** (dim-clamp — Pillar-2 scoring, owner-reserved), **N2** (async extraction — ARQ
refactor), **N3/N7** (Pillar-2-adjacent perf), **M7** (`upsert_tailored_doc` transaction —
data-loss-adjacent, dedicated PR), **M16/M17/L6** (frontend), plus the source-quality
(S1/S2/S4/S5/S7/S8) and test-hygiene (T4/T6-T9/T12) items. Owner decisions unchanged:
scraping sources, privacy/terms, subprocessors, MFA, breach plan, single-region backups,
mypy `continue-on-error`.

---

## ✅ CLOSED in Batch 1 — Security (2026-07-17)

| ID | Was | Now fixed at |
|---|---|---|
| **M1** | OPEN_BUG (authenticated webhook SSRF) | NEW `backend/src/services/channels/ssrf_guard.py::assert_public_http_url` — rejects non-http(s), non-standard ports, and any host (literal or every getaddrinfo-resolved IP) that is private/loopback/link-local/reserved/multicast/unspecified (169.254.169.254 caught). Enforced at CREATE (`api/routes/channels.py:135`, 422) AND re-checked at SEND (`services/channels/dispatcher.py:76` in both `dispatch()` and `test_send()`) — DNS-rebinding defence. Tests: `test_channels_ssrf.py` + `test_channels_routes.py`. |
| **M3** | PARTIAL (`/register` un-throttled) | `api/routes/auth.py:139-156` — per-IP throttle (10/hour) at the top of `register()`, before any DB lookup so it can't leak email existence; 429 when over. Test: `test_auth_throttle.py`. |
| **LOCKOUT** | PARTIAL (reset throttle IP-only) | `api/routes/auth.py:452-456` — password-reset now ALSO throttled per-email (`password-reset-email:<sha256>`, 3/hour) in addition to the IP limit, so IP-rotation can no longer email-bomb one address; still returns 204 silently (no enumeration). Test: `test_auth_throttle.py`. |
| **M9** (IP-in-logs) | OPEN (the original FABLE M9) | `utils/logger.py::mask_ip` (sha256 → `ip_<hex>`) applied in `api/middleware.py` so the access log stores a stable non-reversible token, never the raw client IP. user_id kept (internal opaque uuid). Test: `test_request_id_middleware.py`. |

**Deferred to owner (infrastructure — per the /goal boundary):** **M10** — editing `docker-compose.prod.yml` (drop the hardcoded `job360dev` password) and changing prod secret-defaulting is a production-deploy config change; the safety review correctly flagged it as needing your sign-off. Recommendation ready: env-ref the compose password + generate ephemeral dev secrets. Awaiting your go.

---

## ✅ CLOSED in Batch 2 — Sources / pipeline reliability (2026-07-17)

| ID | Was | Now fixed at |
|---|---|---|
| **S1** | OPEN_BUG (breaker counts empty result as failure) | `services/scheduler.py:179` — dropped the `not result` clause: an empty `[]` records SUCCESS, only exception/None/timeout count as breaker failures. A legitimately-quiet source no longer trips OPEN after 5 cycles. Tests: `test_scheduler.py` (empty never opens; exception still does). |
| **S2** | OPEN_BUG (Reed/Adzuna fan-out always exceeds timeout) | `sources/apis_keyed/adzuna.py:30` `self.job_titles[:8]` (was unbounded); `reed.py:33` `[:8]` (was `[:12]`). Caps title fan-out like siblings so a long title list no longer guarantees a per-tick timeout+cancel. Tests: `test_sources.py` (20 titles → ≤8 queries). |
| **S4** | OPEN_ACCEPTED (scrapers die silently on layout change) | `sources/scrapers/{linkedin,bcs_jobs,aijobs_ai,climatebase,eightykhours}.py` — each now runs a cheap structural health check: on a real (non-trivial) response missing its parser anchor, logs a distinct `STRUCTURE CHANGED … parser may be broken` at ERROR (greppable/alertable), instead of the same "found 0 jobs" as a quiet day. Still returns `[]` (pipeline continues). 10 tests in `test_sources.py`. |

**Deferred to owner (Pillar-2 hands-off — per standing rule):** **SI5** (prefilter
experience band) is scoring/search code the safety review correctly declined to edit
without explicit per-item authorization. Same category as **M8** (dim clamp). Fix is
specified and ready on your word.

**Kept as accepted (not worth the risk):** **S8** (dead `glassdoor` RATE_LIMITS entry) —
harmless (never read; `JobSpySource.name` is hardcoded `"indeed"`), and removing it risks
the five-surface source-count contract (rule #8) for zero functional gain. **S5** (JobSpy
thread leak on timeout) needs a killable process pool — a larger dedicated change.

---

## ✅ CLOSED in Batch 3 — Routes/worker correctness + frontend (2026-07-17)

| ID | Was | Now fixed at |
|---|---|---|
| **N6** | OPEN_BUG (`_runs` grows unbounded) | `api/routes/search.py` — `_prune_runs()` (TTL 1h + max 500), evicts only completed/failed by `created_at`, never active runs; `created_at` stripped from the public response. Tests: `test_search_runs_eviction.py` (5). |
| **M7** | PARTIAL (`upsert_tailored_doc` not atomic) | `repositories/database.py:775` — DELETE+INSERT wrapped in one `async with self._conn.transaction()`; a failure between them rolls back so the user's existing tailored doc survives. Tests: `test_cv_coverletter.py` (atomic replace + INSERT-fails-keeps-original). |
| **M16** | OPEN_BUG (403 force-navigates, discards form) | `lib/api.ts` no longer sets `window.location` on 403 `email_not_verified`; it throws a typed `ApiError.isEmailNotVerified` and emits a notifier. `components/layout/AuthProvider.tsx` subscribes and does the single top-level `router.push("/verify-email")` (guarded). Tests: api-error / api / AuthProvider. |
| **M17** | OPEN_BUG (board renders off `counts` not data) | `app/pipeline/page.tsx` — EmptyState-vs-board now gates on `applications.length` (the data actually rendered), not the independent `counts` total. Test: `pipeline-page-empty-state.test.tsx`. |

---

## ✅ CLOSED in Batch 4 — Test quality + doc (2026-07-18)

| ID | Was | Now fixed at |
|---|---|---|
| **T4** | OPEN_BUG (no migration test on a populated table) | `tests/test_migrations.py::test_0020_notification_rules_rebuild_preserves_existing_row` — seeds a real `notification_rules` row at the pre-0020 schema, runs the 0020 DROP+recreate rebuild, asserts the row survives with correctly-translated (`digest`→`daily`) and correctly-DEFAULTED (`daily_send_time`, `interval_hours`) values. Guards the H2/H6 data-loss class. |
| **T6-T9** | PARTIAL (weak assertions unfixed) | `test_database.py` + `test_tenancy_isolation.py`: `pytest.raises(Exception)` → `pytest.raises(pg.IntegrityError)`, setup insert moved outside the block (so only the duplicate is asserted). `test_discovery.py` + `test_profile_versions_endpoint.py`: shape-only `"changed_fields" in body` strengthened to assert the specific changed field NAME is present (a diff returning `[]` now fails). |
| **T12** | OPEN_ACCEPTED (no real concurrent-write test) | `tests/test_concurrent_writes.py` — two real `pg.connect()` connections race `upsert_feed_row` (INSERT…ON CONFLICT) on the same `user_feed` row via `asyncio.gather`; asserts neither raises, exactly one row survives, and the final state is one of the two valid writes (never a corrupt hybrid). |
| **T5** | OPEN_ACCEPTED (rule #28 lists a live file as "being removed") | root `CLAUDE.md` rule #28 — `skill_synonyms.py` removed from the "offenders being removed" list and scoped OUT with a stated reason (it's scoring/keyword-generation vocabulary in `skill_matcher`/`keyword_generator`, NOT profile extraction under `services/profile/`). Rule text now matches reality. |

---

## 📊 Campaign result (2026-07-18) — self-fixable work COMPLETE

**Confirmed-fixed: 50 → 73** (+23 across 5 batches, each gated + CI-verified on PR #78):
- Batch 0 (8): M12, N4, N5, N8, N9, S9, T10, RULECOUNT
- Batch 1 (4): M1, M3, LOCKOUT, M9
- Batch 2 (3): S1, S2, S4
- Batch 3 (4): N6, M7, M16, M17
- Batch 4 (4): T4, T6-T9, T12, T5

Everything else needs an OWNER decision, an infrastructure change, or is accepted-by-design.
The fixes below are **specified and ready** — I implement the Pillar-2 ones on your word;
the infra/legal ones are yours.

### 🔴 NEEDS YOU — decisions / infra / legal

**Pillar-2 / scoring (hands-off rule — need explicit per-item OK; I'll implement on your go):**
- **SI5** prefilter experience band → make one-directional (senior sees junior roles)
- **M8** clamp stored per-dim scores to [0,100] (radar draw-clamp already done; this is the persisted columns)
- **N3** job-detail: use single-row `load_enrichment` instead of loading the ENTIRE `job_enrichment` table
- **N7** batch the N+1 queries in `main.py` feed-write + `tasks.py` notification tick

**Infrastructure (deploy/config — your sign-off):**
- **M10** drop hardcoded `job360dev` password in `docker-compose.prod.yml` + require secrets / ephemeral dev fallback
- **H6** run migrations as a release/pre-deploy step, not inside the FastAPI serving lifespan
- **H7** make the CI security scanners (pip-audit/npm-audit/bandit/gitleaks) BLOCKING, not advisory
- **M4** worker Dockerfile `HEALTHCHECK` + ARQ `health_check_interval` + Railway restart trigger
- **L6** Next.js standalone Docker image (smaller, less attack surface)
- **SI1** notification pipeline: I can wire the enqueue in CODE, but it only fires once you add a Railway **worker service** + **Redis** + SMTP/channel creds

**Business / legal (your decision, no code):**
- Scraping LinkedIn/Indeed/Glassdoor (drop or accept-in-writing), privacy/terms, subprocessor list, MFA, breach-notification plan, single-region backups

**Ops env (you set on Railway):** `APP_ENV`/`JOB360_ENV` (cookie Secure), `RATE_LIMIT_REDIS=true` + `REDIS_URL`.

### ⚪ Accepted-by-design (left intentionally)
S5 (thread leak — needs process pool), S8 (dead glassdoor RATE_LIMITS — five-surface risk), V2 (RETURNING id — minor), mypy `continue-on-error` (395 grandfathered), the 4 dead score columns + dormant penalty card (PR-2, owner-declined earlier), M2 register-409 (deliberate).
