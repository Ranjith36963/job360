# Fable findings — RE-VERIFIED against main, 2026-07-19

**This file supersedes `AUDIT-2026-07-17-VERIFIED.md`.** That document's verdicts had
drifted badly out of date and were actively misleading (details below).

Every verdict here was produced by reading the **current code on main** and quoting the
decisive line. No verdict was copied from the previous audit; several contradict it.

## Headline

| Verdict | Count | Meaning |
|---|---:|---|
| **FIXED** | **57** | the described problem no longer exists in the code |
| **PARTIAL** | **9** | the main risk is closed; a named sub-part remains |
| **OPEN** | **9** | still exactly as described |
| | **75** | (+ H7 counted twice in the source doc = 77 sections) |

The previous doc claimed **83 OPEN**. The true number is **9**.

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

## OPEN (9) — still exactly as described

| ID | What | Proof |
|---|---|---|
| **L6** | Frontend image ships full `node_modules`; Next standalone output never configured | `frontend/Dockerfile:41-42` states it outright; no `output: "standalone"` in `next.config.ts` |
| **S8** | `indeed`/`glassdoor` share one class with a hardcoded `name = "indeed"`, so the `glassdoor` rate-limit entry is dead code | `sources/other/indeed.py:16`; `core/settings.py:221` |
| **N2** | Two-pass profile extraction (~8 sequential LLM calls) runs **inline**, blocking the HTTP response | `api/routes/profile.py:327` `await run_two_pass_extraction(profile)` |
| **SI4** | `FeedService.list_for_user` is dead code with zero production callers, still ordering by keyword score only while calling itself the "single source of truth" | `services/feed.py:60,77` |
| **P3** | The verify-reminder hook only prints a message; nothing enforces the verification skill | `.claude/hooks/verify-reminder.sh:6`; no `verify-job360` reference in `agent-gate.sh` |
| **P4** | Commit-gate hook matches the literal string `git commit` and **fails open** if JSON parsing yields an empty command | `.claude/hooks/commit-gate.sh:12-14` |
| **P5** | `settings.local.json` has grown to 224 lines and still carries dead `chrome-devtools` permissions for a server that isn't enabled | `.claude/settings.local.json:81-88,199-203` |
| **H6** | DB migrations still run **in-process** inside the FastAPI lifespan at every boot | `api/dependencies.py:22` `await runner.up(...)`; `api/main.py:113` |
| **S5** | JobSpy scrape runs in a detached `asyncio.to_thread`; on timeout the OS thread leaks | `services/scheduler.py:160-161`; `sources/other/indeed.py:41` |

**H6 is mitigated, not unguarded:** `backend/railway.json` sets `healthcheckPath`
`/api/health` + `restartPolicyType: ON_FAILURE`, so a failed migration keeps the old
container serving rather than going live broken. The literal fix (a separate release
step) is the owner's call — it matters most the day the API scales past one replica.

**S5 is accepted-by-design**, documented in the code comment itself.

---

## PARTIAL (9) — main risk closed, named remainder

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

## Not covered by any finding — worth knowing

- **Backup/restore has never been mentioned anywhere in this audit.** Real users' CVs,
  profiles and applications live in one Railway Postgres. Does a *tested restore* exist?
- **Nothing detects "process not running."** Sentry catches exceptions; the ARQ worker
  died silently once already (commit `7f906c6`, "revive dead ARQ worker (P0)"). M4 above
  plus an external uptime check on `/api/readyz` would close that class.
- **In-memory state won't survive replicas** — login lockout and the per-user concurrent
  search cap are per-process. Fine at one replica; a trap at two.
