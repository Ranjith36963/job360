# LAUNCH_PLAN.md — From verified app to live SaaS

> Updated 2026-06-21: Phase −2/−1 build+verify items are shipped (see docs/harness/CHECKLIST_KANBAN.md).

> **What this is.** The ordered roadmap from *"the code is written"* (today) to *"the SaaS is operating, serving real users, generating revenue"* (target). Eight phases, with explicit dependencies and exit criteria per phase.
>
> **What this is NOT.** A status doc (see `STATUS.md` for what's already merged). A rule book (see `CLAUDE.md` for what you must not break). An architecture reference (see `docs/product/pillars/`). This document only answers *"what's next, in what order, and why"*.
>
> **When to read.** Before starting any work that could be a step toward launch — to make sure you're picking up the *right* next step. Also before any "should I work on X or Y" decision — the dependency graph below decides for you.
>
> **When to update.** Whenever a phase ships, mark its items done. Whenever scope changes (e.g. a P0 turns out to be unnecessary, or a new P0 emerges from Phase −1), edit the affected phase and note the change at the bottom. Treat it like `STATUS.md` — it stays current or it stops being useful.

---

## At a glance

```
Phase −2: Build the verification-blocker set         ← INSERTED 2026-05-28
      ↓ (1 week — must finish so Phase −1 verification can run end-to-end)
Phase −1: Manual verification + bug-fix sprint
      ↓ (only after every user flow drives end-to-end and produces sensible output)
Phase  0: ICO + privacy lead-time + repo cleanup
Phase  1: Email backbone (SES)
Phase  2: Auth loop closure (password reset, email verification, lockout)
Phase  3: Production notifications (ARQ + Redis + per-source health monitoring)
Phase  4: Soft launch
Phase  5: Engineering hygiene
Phase  6: Feature-flag rollouts (post-launch)
Phase  7: Growth features
```

Each box assumes the previous one has cleared its exit criterion. Skipping a phase or starting two in parallel without explicit dependency analysis is how launches break.

> **Phase −2 vs Phase 2 — what's the difference?** Phase −2 is a tight subset of *verification blockers* — features without which Phase −1 cannot succeed. Phase 2 is the full auth-loop closure including session rolling renewal, MFA, OAuth — the rest of the auth surface that ships later. Same domain, different urgency.

---

## Phase −2 — Build the verification-blocker set

**What.** Build the four missing features without which Phase −1 manual verification can't succeed end-to-end. Identified via systematic audit of all three pillar docs' status matrices on 2026-05-28.

**Why.** The 1000+-test suite verifies code correctness given mocked inputs. Phase −1 verification requires *the SaaS to actually work* for a real human driving real user flows. Four documented gaps gate that:

1. **Password reset** — Verification Section D ("forgot-password recovery") cannot pass. Any verification user who locks themselves out has no recovery path.
2. **ARQ + Redis worker running locally** — Verification Section D ("notification arrives") cannot pass — `score_and_ingest` enqueues vanish silently until a worker process consumes them.
3. **Email verification on registration** — Best built alongside #1 (shares token-email infrastructure). Not strictly a verification blocker but essential for SaaS shipping.
4. **Per-source health visibility** — Without it, you can't tell during verification whether bugs are in scoring or in a silent source failure.

**Time.** ~1 week of focused work. The auth pair (#1 + #3) takes ~1.5 days because they share token infrastructure. Worker dev runner is ~half day. Per-source health page is ~1 day. Buffer for testing + integration.

### Items

| # | Item | Effort | Files touched |
| --- | --- | --- | --- |
| A ✅ | **Password reset flow** — migration + token service + 2 routes + frontend page + email template | ~1 day | `migrations/0015_password_resets.up.sql`, `services/auth/tokens.py`, `services/auth/password_reset.py`, `services/auth/email_sender.py`, `api/routes/auth.py`, `frontend/src/app/(auth)/forgot-password/page.tsx`, `frontend/src/app/(auth)/reset-password/page.tsx`, `tests/test_password_reset.py` |
| B ✅ | **Email verification on registration** — migration + verification service + 2 routes + register-time hook + frontend confirm page | ~1 day | `migrations/0016_email_verification.up.sql`, `services/auth/email_verification.py`, `api/routes/auth.py`, `frontend/src/app/(auth)/verify-email/page.tsx`, `tests/test_email_verification.py` |
| C ✅ | **ARQ worker local-dev runner + docs** — docker-compose for Redis, Makefile target, README updates so a developer can spin up the worker in one command | ~half day | `docker-compose.dev.yml` or equivalent, `Makefile`, `backend/README.md`, `docs/product/pillars/runbook.md` (worker section) |
| D ✅ | **Per-source health admin page** — backend route reading existing `run_log.per_source_errors` + `per_source_duration` columns (from migration `0010`), frontend admin page | ~1 day | `api/routes/admin.py` or extend `runs.py`, `frontend/src/app/admin/sources/page.tsx`, `tests/test_admin_source_health.py` |

### Optional bundle (cheap to add while in this code)

| # | Item | Effort | Why bundle |
| --- | --- | --- | --- |
| E | Brute-force lockout on `/api/auth/login` | half day | You're already in auth code |
| F | Rippling + Comeet slug expansion (5 → 25 each) | half day | Source breadth helps Phase −1 verification feel realistic |
| G | Notification dry-run preview before save | half day | Helps Phase −1's Section D testing |

### Exit criterion ✋

All four items A–D shipped, tests passing, manually smoke-tested by registering a user → forgetting password → resetting → verifying email → configuring a channel → triggering a notification → confirming it arrives → opening admin/sources to see source health. **Then move to Phase −1.**

---

## Phase −1 — Manual verification + bug-fix sprint

**What.** Drive every user flow as a real human would. Find the bugs. Fix them. Re-drive. Repeat until each flow produces *the experience you want a real user to have*.

**Why this is first.** The 1000+-test suite verifies *code correctness* (given mocked inputs, functions produce expected outputs). It does NOT verify *feature correctness* (when a real human uploads their real CV, does the system actually extract their skills, score sensibly, send notifications that arrive, and render the right thing on the right page). Launching without this phase = launching with bugs the suite was never designed to catch.

**Time.** 3–5 working days realistic. Could stretch if a deep issue (e.g. multi-user isolation broken somewhere) surfaces.

### The five sections to drive

#### Section A — Solo CLI flow

1. Fresh clone, fresh venv, fresh `.env`. **Don't set any LLM keys yet.** Run `python -m src.cli setup-profile --cv your-real-cv.pdf`. **Expected to fail with a clear error.** If it crashes with a stack trace, log bug.
2. Set `GEMINI_API_KEY`. Retry. CV should parse. Verify in the DB that your *actual* skills + job titles got extracted — not garbage, not empty.
3. Add LinkedIn PDF (`--linkedin`). Verify the `is_linkedin_pdf` 2-of-3 detector recognised it. Verify merge.
4. Add GitHub (`--github`). Verify languages + frameworks merged.
5. `python -m src.cli run --dry-run --log-level DEBUG`. Read the log. Watch for silent source failures, breaker trips, score distribution.
6. `python -m src.cli view --hours 24 --min-score 50`. **Sanity-check the scores.** Do the top-ranked jobs look like jobs *you* would want? If not, scoring is broken FOR YOU — and that's the only correctness metric that matters.
7. Run again *without* `--dry-run`. Verify rows in `jobs` and `user_feed`.

#### Section B — Web flow (single user)

1. `python main.py` + `npm run dev`. Open `localhost:3000`.
2. Register with a real email. Upload the CV through `/profile`. Verify the parsed profile renders — skill tiers, completeness %, badges.
3. `/dashboard` → click "Run search." Watch the polling. Verify jobs appear.
4. Like a job. Mark applied. Refresh — verify it persisted.
5. `/pipeline` → confirm the applied job appears in "applied" column. Drag to "interview". Refresh — verify it stuck.

#### Section C — Multi-user isolation (highest IDOR risk)

1. Register Alice. Upload Alice's CV.
2. Incognito window. Register Bob with a *different* CV (different profession).
3. Run the pipeline once.
4. As Alice: check feed. Like job #X.
5. As Bob: check feed. **Verify Bob does NOT see Alice's likes.** **Verify Bob's top jobs differ from Alice's.**
6. In Bob's window with Bob's real cookie, try URL-tampering: `GET /api/profile?user_id=alice`, modify other endpoints, etc. **Verify** none returns Alice's data (rule #12 IDOR guard).

#### Section D — Notification end-to-end

1. As Alice, `/channels` → add an email channel with your *real* email (Gmail App Password).
2. "Test Send" → **verify** the email actually arrives. If not, broken before launch.
3. Configure a notification rule: threshold 60, instant.
4. Run pipeline → ingest a high-scoring job for Alice.
5. **Verify** email arrives within minutes. If not, ARQ isn't running — confirm that's the bug.
6. Run pipeline again with the same job → **verify NO second email** (idempotency).

#### Section E — Edge cases

1. Empty 1-page PDF. Does LLM fail? Does API surface the error?
2. CV with non-ASCII (é, ñ, 中). Round-trip cleanly?
3. Malformed PDF (`.txt` renamed `.pdf`). Crash or graceful fail?
4. CV with no clear sections (just prose). Does LLM extract anything?
5. Profile claims `senior` but CV says junior. Which wins?
6. Soft-delete a user. Try to login again. Verify lockout.
7. Stop ARQ worker mid-pipeline. Run something that should notify. Where does it queue? Does it eventually fire when worker comes back?
8. Boot API without `SESSION_SECRET`. **Expected:** clean `RuntimeError` on startup, not cryptic stack later.

### Predicted bugs (where to look first)

Based on what I read while writing the pillar docs — *predictions*, not confirmations. Investigate these early.

| # | Predicted issue | Where I'd look |
| --- | --- | --- |
| 1 | CV parse silently returns near-empty when LLM JSON is malformed | `cv_parser.py:parse_cv_async` — the 2-retry self-correction loop may converge on minimal output |
| 2 | Profile page shows 0% completeness after a successful upload | `frontend/src/app/profile/page.tsx` — completeness calc may not match new `CVData` shape |
| 3 | "Run search" button polls forever | `getSearchStatus()` worker — if ARQ isn't up, search never finishes |
| 4 | Domain classifier returns empty `{}` for unusual profiles | `domain_classifier.py:classify_user_domain` — verify the empty-domain fallback keeps all sources |
| 5 | `setup-profile` CLI writes to `DEFAULT_TENANT_ID`, then logging in as a fresh user shows no profile | Two storage paths; the CLI's profile is orphaned from any web user |
| 6 | Channel test-send fails with cryptic Apprise error on first use | First call triggers 30 MB lazy import — possible timeout under default request limit |
| 7 | Frontend `/jobs/[id]` renders blank | Next.js 16 `params: Promise<...>` trap (rule #22) — if any page missed `await`, silent blank |
| 8 | All scores come back as 30-ish, no variation | `MIN_TITLE_GATE=0.15` suppression — if target titles don't match anything, every job hits the floor |
| 9 | Time-bucket filter shows wrong jobs | `time_buckets.py` boundary math — jobs at exactly 24h could land in either bucket |
| 10 | Pipeline page shows old state after stage drag | TanStack Query cache invalidation — optimistic update may not match server response |

### Day-by-day cadence

- **Day 1**: Drive Sections A + B front to back. Log everything wrong in a single `docs/verification-log.md` checklist.
- **Day 2**: Triage. Categorise: 🔴 launch-blocker / 🟠 launch-degrader / 🟡 post-launch fix / ✅ false alarm.
- **Days 3–4**: Fix 🔴s and worst 🟠s. Re-verify each fix immediately, don't batch.
- **Day 5**: Drive Sections C + D + E with the fresh build. Repeat triage + fixes if needed.

### Exit criterion ✋

You can drive **all five sections** through to *"this is the experience I want a real user to have"*. Until then, do not move to Phase 0.

---

## Phase 0 — Legal lead-time + repo cleanup

**What.** Independent admin items with long lead-times. Doing nothing else, do these.

**Why now.** Legal paperwork has weeks of lead-time you don't see if you wait. Doc cleanup has zero risk and 1-day debt.

**Time.** ~1 day of actual work + 1–2 weeks of waiting on external parties.

### Items

| # | Item | Time | Note |
| --- | --- | --- | --- |
| 1 | Submit ICO registration | 30 min + £40 | UK legal requirement to process personal data (CVs) |
| 2 | Brief lawyer on privacy notice + LIA (Legitimate Interests Assessment) | 30 min sync; days of return time | They need 1–2 weeks — start the clock |
| 3 | Open the doc branch `claude/job-logistics-pillars-docs-H9zcw` as a PR + merge to main | 15 min | 8 commits of doc work sitting unmerged — drift risk |
| 4 | Move 3 stray root files to `docs/_archive/` | 15 min | `planning_report.md`, `joproviderlayerReport.md`, `DEADCODE.md` — completed work + stale audit |

### Exit criterion ✋

ICO confirmation in hand, privacy notice draft in review, repo cleaned, doc PR merged.

---

## Phase 1 — Email backbone (SES)

**What.** Replace Gmail SMTP with Amazon SES so every email-using feature downstream (password reset, email verification, channel notifications) ships against production-grade infrastructure.

**Why now.** Phase 2 and Phase 3 both depend on email. Wiring SES once at the dispatcher means every downstream feature inherits it. Wiring Gmail App Password into password-reset code and rewriting later is the failure pattern to avoid.

**Time.** ~1–2 days (most of it is SES account verification waiting).

### Items

| # | Item | Time |
| --- | --- | --- |
| 5 | Amazon SES account verification + DKIM + DMARC | 30 min config + ~24h account approval |
| 6 | Swap Apprise email URL in `user_channels` admin tooling and `channels/dispatcher.py` from `mailtos://gmail` to `mailtos://email-smtp.<region>.amazonaws.com:587?...` | 1–2 hours |
| 7 | Send-test through new SES via `POST /api/settings/channels/{id}/test` | 5 min smoke |

### Exit criterion ✋

Any email leaving Job360 goes through SES. Verified by send-test endpoint + receipt.

---

## Phase 2 — Auth loop closure

**What.** Close the user-visible gaps in the auth surface: password reset, email verification, brute-force lockout, rolling session.

**Why now.** Phase 1's SES backbone exists. The auth gaps (especially password reset) are absolute blockers to real users — without them, the first user who forgets their password is stranded forever.

**Time.** ~3 days.

### Items

| # | Item | Time | Note |
| --- | --- | --- | --- |
| 8 | Password reset (backend route + `password_resets` table + token email + frontend `/forgot-password` page) | ~1 day | Rides on Phase 1's SES |
| 9 | Email verification on registration (token email + `/verify` route + `users.email_verified_at` column + gate sensitive features on verified) | ~1 day | ~50% code reuse with #8 |
| 10 | Brute-force lockout on `/api/auth/login` (in-memory sliding window if Redis isn't deployed yet) | ~half day | Independent |
| 11 | Session rolling renewal (extend `expires_at` on `last_seen` update within a 7-day buffer) | ~half day | Quick win once you're in auth code |

### Exit criterion ✋

The auth surface is genuinely safe for real users: forgotten passwords recoverable, registration emails verified, login rate-limited, sessions don't expire on active users.

---

## Phase 3 — Production notifications

**What.** Deploy ARQ worker against managed Redis so notifications actually fire in production. Add per-source health monitoring so silent breakages are caught.

**Why now.** Until ARQ is deployed, the entire notification pillar is dead code in production. Until per-source health is monitored, a LinkedIn markup change = users see 0 LinkedIn jobs for weeks before anyone notices.

**Time.** ~2 days.

### Items

| # | Item | Time |
| --- | --- | --- |
| 12 | Provision managed Redis (Upstash, ElastiCache, Render add-on — whatever fits your stack) | 30 min |
| 13 | Deploy ARQ worker (`arq src.workers.settings.WorkerSettings`) as a separate process under your process manager | 2–4 hours |
| 14 | Prod-Redis smoke test: register a test user, configure email channel, trigger real `score_and_ingest` → expect real email | 30 min |
| 15 | Per-source health monitoring: tiny `/admin/sources` view reading `run_log.per_source_errors` (migration `0010` already provides it) + Slack alert when a source returns 0 jobs 3 runs in a row | ~half day |

### Exit criterion ✋

Notifications fire end-to-end in production. Source health is observable, not silently broken.

---

## Phase 4 — Soft launch

**What.** Privacy notice live, terms of service live, 10–30 invited users on the system, monitor everything for a week.

**Why now.** All P0 blockers cleared. Real human usage will surface issues no test suite can.

**Time.** ~1 week (more elapsed than worked).

### Items

| # | Item |
| --- | --- |
| 16 | Privacy notice live on `/privacy`, footer link, copy reviewed by lawyer (Phase 0 work landing) |
| 17 | Terms of service live on `/terms`, lawyer-reviewed |
| 18 | Soft launch: 10–30 invited users (friends, ex-colleagues, target persona testers) |
| 19 | Triage anything that surfaces. The runbook (`docs/product/pillars/runbook.md`) failure-mode tables become real bug reports |

### Exit criterion ✋

Real human beings are using Job360 in production and the failure rate is bounded. **This is the hard line** — every change after this needs more care than every change before.

---

## Phase 5 — Engineering hygiene

**What.** Picked off opportunistically post-launch. Not blockers, but accumulated debt that compounds if left.

**Time.** ~1 week of total work spread over 2–3 calendar weeks.

### Items

| # | Item | Why |
| --- | --- | --- |
| 20 | `test_main.py` Indeed mock (eliminate the one rule-#4 violator) | Test suite stops being slow + flaky |
| 21 | Conditional fetch opt-in for 5 RSS/ATS feeds that honour ETag/Last-Modified | Free win — upstream load + bandwidth |
| 22 | Rippling + Comeet slug expansion (5 → 25 each) | Both Batch-3 sources stay starter-list until expanded |
| 23 | Frontend TS codegen from Pydantic (datamodel-code-generator → OpenAPI → TS) | Stops the manual `types.ts` drift problem |

### Exit criterion ✋

No hard one. Move forward when you've cleared the high-leverage items.

---

## Phase 6 — Feature-flag rollouts (POST-LAUNCH)

**What.** Activate `ENRICHMENT_ENABLED` and `SEMANTIC_ENABLED` in production. This unlocks the multi-dimensional scoring + hybrid retrieval + ChromaDB embeddings + cross-encoder rerank — currently all written, tested, default OFF.

**Why deferred to here.** Flipping these flags introduces LLM costs + ChromaDB infrastructure + cross-encoder cold-start latency *during the period when you're least equipped to debug them*. Pre-launch is the worst time. Post-launch, after the user base is settled and your monitoring shows you what normal looks like, you can flip them and isolate the impact.

**Time.** ~1 week per flag, plus 1–2 weeks of observation between.

### Items

| # | Item | Pre-requisite |
| --- | --- | --- |
| 24 | Add `llm_usage` table + cost tracking in `llm_provider.py` | Before any paid LLM tier |
| 25 | Flip `ENRICHMENT_ENABLED=true` for small user cohort, monitor cost + quality | LLM budget + #24 |
| 26 | Embedding-model versioning plan: what happens when MiniLM-L6-v2 ships v2 | Before flipping semantic |
| 27 | ChromaDB persistent-volume sized for production; flip `SEMANTIC_ENABLED=true` | #26 + Phase 3 baseline |
| 28 | Activate Layer-4 embedding repost dedup (`enable_embedding_repost=True`) | Rides on #27 |

### Exit criterion ✋

Full Pillar-2 stack live for all users with measured cost/quality impact.

---

## Phase 7 — Growth features

**What.** User-visible features that drive registration + retention.

**Why last.** Each is independently valuable but none is blocking. Time-to-revenue beats time-to-features.

### Items (priority order)

| # | Item | Why this order |
| --- | --- | --- |
| 29 | OAuth (Google + GitHub) | Registration friction drops fastest |
| 30 | MFA / TOTP | Security signal for power users; trust signal for enterprise |
| 31 | Push notifications (FCM/APN) | Only meaningful once PWA or mobile surface exists |
| 32 | ATS catalog auto-discovery (script that scrapes "companies hiring on Greenhouse") | Compounds source coverage; not user-visible directly but visible in feed quality |

---

## Hard dependency graph

Some things genuinely block other things. Don't try to parallelise across these lines:

```
Phase −1 ───▶ Phase 0      (don't legal-commit before app works)
Phase  1 ───▶ Phase 2      (need email backbone for password reset / verification)
Phase  1 ───▶ Phase 3      (same — for notification delivery)
Phase  2 ───▶ Phase 4      (need safe auth before real users)
Phase  3 ───▶ Phase 4      (need notifications firing before launch)
Phase  4 ───▶ Phase 6      (need stable launch baseline before flag rollouts)
```

Outside these arrows, items can be picked up in any order. Specifically: Phase 0 items 3 + 4 (PR + cleanup) can happen alongside Phase −1. Phase 5 hygiene items can happen during slow weeks at any point post-launch.

---

## What to do this week

1. **Today**: Open `claude/job-logistics-pillars-docs-H9zcw` as a PR and merge. (Phase 0 #3.)
2. **Today**: Move the 3 stray root files to `docs/_archive/`. (Phase 0 #4.)
3. **Today**: Submit ICO registration form (£40, ~30 min). (Phase 0 #1.)
4. **This week**: Set up a fresh test environment for Phase −1 — fresh clone, fresh `data/` dir, fresh `.env` with real LLM keys and real Gmail credentials.
5. **This week**: Create `docs/verification-log.md` and start driving Phase −1 Section A.

You don't need permission for #1 through #5. They unblock the rest of the plan.

---

## Status tracker

> Mark items done as they ship. When all items in a phase are done, mark the phase ✅ and proceed to the next.

- [x] **Phase −2** — Build the verification-blocker set ✅ DONE (A–D all shipped)
- [ ] **Phase −1** — Manual verification + bug-fix sprint
- [ ] **Phase 0**  — ICO + privacy lead-time + repo cleanup
- [ ] **Phase 1**  — Email backbone (SES)
- [ ] **Phase 2**  — Auth loop closure
- [ ] **Phase 3**  — Production notifications
- [ ] **Phase 4**  — Soft launch
- [ ] **Phase 5**  — Engineering hygiene (rolling)
- [ ] **Phase 6**  — Feature-flag rollouts (post-launch)
- [ ] **Phase 7**  — Growth features (post-launch)

---

## Changelog

- *2026-05-28* — Initial plan drafted. Eight phases (−1 through 7). Phase −1 added as the explicit pre-launch manual verification sprint after recognising the test suite alone doesn't prove feature correctness.
- *2026-05-28* — Phase −2 inserted after systematic audit of all three pillar status matrices identified four verification-blocker features (password reset, ARQ worker dev runner, email verification, per-source health page) that must ship before Phase −1 can succeed. ~1 week of work; same tight feedback loop as Phase −1.

---

*See also: `STATUS.md` (what's done), `CLAUDE.md` (the rules), `docs/product/pillars/` (the architecture reference), `docs/product/pillars/runbook.md` (operational answers during execution).*
