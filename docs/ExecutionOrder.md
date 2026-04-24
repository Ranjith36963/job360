# Job360 Execution Order — End-to-End Sync & Launch Readiness

> **Status:** Planning doc. No code changes executed. Author: synthesised from 6 parallel audit agents on 2026-04-23 after pausing Pillar 3 Batch 4.
>
> **Context:** Pillars 1, 2, 3 are shipped (all 29 batches merged to `origin/main @ 5fb3c07`). Backend engine is ~95% complete, but many capabilities don't flow through to the frontend. Original instinct to "do Pillar 1 → 2 → 3 in order" is incorrect for this state — the gaps are at **integration seams**, not inside pillars.
>
> **Principle driving this ordering:** *Do the seams before the surfaces.* Fix integration points once, then polish UI, then ship ops, then launch.

---

## Why "Pillar 1 → 2 → 3" is the wrong frame

The pillars are *already done in the engine*. The gaps are not "Pillar 2 isn't built" — they're "Pillar 2 is built but its output doesn't flow through the API seam to the UI."

Three-story building analogy: plumbing, electrical, HVAC are all installed on every floor — but the main valves to the street are closed. Going "floor 1 fixtures → floor 2 fixtures → floor 3 fixtures" keeps testing dry taps. **Open the valves first, on all floors, then polish the fixtures.**

**What this sequencing gets you that pillar-by-pillar doesn't:**

| "Pillar 1 → 2 → 3" | Seam-by-seam (this doc) |
|---|---|
| Each pillar's backend→UI fix touches `JobResponse` three times | One response-model edit, everyone benefits |
| Radar lights up weeks later (end of Pillar 2) | Radar lights up end of Step 1 (days) |
| Each pillar has isolated "wow moments" | Wow moments compound — every slice improves the same E2E flow |
| P0 engine-seam bugs surface late during Pillar 3 | All engine-seam bugs surface in Step 1 — fast feedback |
| 3 separate "does auth still work?" regression sweeps | 1 |

---

## Scoreboard (current state)

| Layer | State | Notes |
|---|---|---|
| Backend engine | ~95% | Pillars 1/2/3 all shipped |
| API surface | ~75% | Pillar 2 capabilities (enrichment, semantic, hybrid retrieval) have no HTTP exposure |
| Frontend | ~60% | Templates exist (e.g., 8-dim ScoreRadar) but render zeros — API returns nothing |
| Ops / bootstrap | ~10% | No CI, no Docker, no backup, no healthcheck-depth, no rate-limit, no bootstrap script |

---

## Visual summary

```
Step 0  →  Pre-flight (env, seed, baseline)                 [1-2 sessions]
   │
Step 1  →  Engine→API seam (Batch S1)                       [2-3 sessions]
   │        ALL pillars together, one bundled PR
   │
Step 2  →  API→UI seam (Batches S2 / S3 / S4)               [3-5 sessions]
   │        Pillar 1 → Pillar 2 → Pillar 3, sequential
   │
Step 3  →  New endpoints (versions, export, dedup, ledger)  [1-2 sessions]
   │
Step 4  →  Ops hardening (CI, Docker, smoke, secrets)       [1-2 sessions]
   │
Step 5  →  Batch 4 (now data-informed, split 4A/4B)         [varies]
```

---

## Step 0 — Pre-flight

**Goal:** Make the tool runnable end-to-end from a clean checkout. Without this, every later step is blind.

**Why first:** All downstream steps require (a) env vars that don't exist yet, (b) a reproducibility harness to verify fixes, (c) clean git state to branch from.

### Original scope
- [ ] Commit the two uncommitted `docs/CurrentStatus*.md` files
- [ ] Update `backend/.env.example` with the 5 missing vars (`SESSION_SECRET`, `CHANNEL_ENCRYPTION_KEY`, `FRONTEND_ORIGIN`, `REDIS_URL`, `CEREBRAS_API_KEY`)
- [ ] Write `backend/scripts/bootstrap_dev.py` that: creates a test user → uploads a sample CV → runs the pipeline once → prints the feed rows
- [ ] Record pytest baseline (expect 1,032 collected, 600 pass / 0 fail / 3 skip / rest ignored)

### Gaps surfaced by audit — MUST ADD

- [ ] **`.env.example` doesn't exist at all** — create fresh. Must include `GEMINI_API_KEY` (for CV parsing; free tier is easiest local bootstrap) in addition to the 5 vars I originally listed. Total: **7 vars minimum**.
- [ ] **Sample CV fixture missing** — create `backend/tests/fixtures/sample_cv.pdf` (or reuse an anonymised real CV) so `bootstrap_dev.py` has something to upload.
- [ ] **`data/` subdirectories not auto-created** — on fresh clone, `data/jobs.db`, `data/exports/`, `data/reports/`, `data/logs/` don't exist. Migrations and logger will fail silently. Either add `os.makedirs(..., exist_ok=True)` in `src/core/settings.py` or do it explicitly in `bootstrap_dev.py`.
- [ ] **`REDIS_URL` scoping** — verified `workers/settings.py:99` reads it; Redis is actually needed for ARQ worker dispatch but NOT for the Step-0 dogfood (tests monkeypatch apprise). Document it as "optional for local dogfood, required for notification worker."
- [ ] **Windows Redis story** — user is on Windows 11. Document: (1) Memurai free edition as local Redis, or (2) `docker run -p 6379:6379 redis:7-alpine` via Docker Desktop. Without guidance users can't run the worker.
- [ ] **Frontend `.env.local` template** — `frontend/src/lib/api.ts:22` has `NEXT_PUBLIC_API_URL` fallback to `http://localhost:8000`. Works locally, but create `frontend/.env.local.example` for clarity.
- [ ] **Python version guard** — add a `sys.version_info < (3, 9)` check at top of `bootstrap_dev.py` with a clear error message.
- [ ] **Untracked file hygiene** — `.claude/ralph-loop.local.md` + `.claude/scheduled_tasks.lock` are untracked; add to `.gitignore` if not already.

### Already good (verified)
- Migrations are idempotent (`_schema_migrations` table with `INSERT OR IGNORE`) — safe to re-run
- Both worktrees (`generator`, `reviewer`) are on `main`, no conflicting branches
- `origin/main` is fully synced with local (0 ahead / 0 behind)

### Exit criteria
- [ ] `cd backend && python -m pytest -q` runs cleanly, baseline recorded in `docs/pytest_baseline.txt`
- [ ] `python scripts/bootstrap_dev.py` creates user + profile + 1 pipeline run + prints ≥1 feed row without errors
- [ ] `npm run dev` in `frontend/` and `python main.py` in `backend/` both start without missing-env errors
- [ ] Commit: `chore(ops): bootstrap script + env.example + data dir auto-create`

**Budget:** 1–2 sessions.

---

## Step 1 — Engine→API Seam (Batch S1)

**Goal:** Open the valve. Stop discarding pillar 2 / 3 data at the API serialisation layer.

**Why this is a single batch across pillars:** All five edits touch the same 3 files (`backend/src/api/models.py`, `backend/src/api/routes/jobs.py`, `backend/src/main.py`). Splitting them creates five merge conflicts on the same `JobResponse` model.

### Original scope
- [ ] Add date-model fields to `JobResponse`: `posted_at`, `staleness_state`, `date_confidence`, `first_seen_at`, `last_seen_at` (Pillar 3 Batch 1 surfacing)
- [ ] Populate 7 scoring dimensions in `JobResponse` (Batch 2.9 wiring — stop returning zeros)
- [ ] Invoke `enrich_job()` in `run_search` behind `ENRICHMENT_ENABLED` flag (Batch 2.5 activation)
- [ ] Invoke domain classifier in `_build_sources()` (Batch 2.4 wiring)
- [ ] Wire `mode=hybrid` parameter end-to-end in `/jobs` (Batch 2.7 activation)

### Gaps surfaced by audit — MUST address before coding

- [ ] **`Job` dataclass is missing 3 of 5 date fields** — `src/models.py:36–38` has `posted_at`, `date_confidence`, `date_posted_raw` only. **Missing: `first_seen_at`, `last_seen_at`, `staleness_state`.** DB has these columns but dataclass can't round-trip them. **This is the first edit** — bigger than "just add to JobResponse."
- [ ] **Scorer returns `int`, not a breakdown dict** — `src/services/skill_matcher.py::JobScorer.score()` line 414 returns `min(max(total, 0), 100)`. To populate 7 dimensions in `JobResponse`, scorer must return a dict (or a new `ScoreBreakdown` dataclass). The 7-dim formula is ready at lines 396–411 but the return shape is flat.
- [ ] **Enrichment is single-job + async** — `src/services/job_enrichment.py::enrich_job()` processes one job at a time. Calling it for every job in `run_search` would serialize ~1000 LLM calls per run. **Mitigation:** gate by `match_score >= ENRICHMENT_THRESHOLD` (e.g., ≥60) so only candidates get enriched. Or add a `enrich_batch()` helper.
- [ ] **Hybrid retrieval needs the embeddings index to exist** — `retrieve_for_user()` works only if `services/vector_index.py` has been populated. Fallback-to-keyword path must be verified before shipping, otherwise `?mode=hybrid` returns empty.
- [ ] **Domain classifier is ALREADY wired** — audit revealed `_build_sources()` at line 294 already calls `classify_user_domain()` and filters sources at lines 292–298. **Remove this from Batch S1 scope** — it's already done. Just verify it's working via dogfood.
- [ ] **Test schema fragility** — adding required fields to `JobResponse` without defaults will break 9 tests in `test_api.py` that construct mock `JobResponse`. **Fix:** make all 5 new date fields `Optional[str] = None`.
- [ ] **Frontend types mirror** — `frontend/src/lib/types.ts:7–37` already has the 8 score dimensions declared. Only the 5 date fields need adding to the TS interface.
- [ ] **Feature flags verified** — `ENRICHMENT_ENABLED` (in `job_enrichment.py:31`) and `SEMANTIC_ENABLED` (in `settings.py:70`) both exist, both default false. Keep defaults off in Step 1; flip on during dogfood in Step 2.

### Ordered sub-steps

1. **Job dataclass first** — add `first_seen_at`, `last_seen_at`, `staleness_state` to `backend/src/models.py`
2. **DB→dataclass round-trip** — update `database.py` row-to-Job mapping
3. **JobScorer return shape** — change `score()` to return `ScoreBreakdown` dict; update all callers (grep `JobScorer().score`)
4. **JobResponse schema** — add 5 date fields + 7 dimension fields with `Optional = None` defaults
5. **Serializer** — update `_row_to_job_response()` (`api/routes/jobs.py:51–65`)
6. **`run_search` enrichment invocation** — gate by threshold, behind `ENRICHMENT_ENABLED`
7. **Hybrid mode wiring** — call `retrieve_for_user()` in `api/routes/jobs.py:129` when `mode=hybrid`, with keyword fallback
8. **TS types** — update `frontend/src/lib/types.ts`
9. **Tests** — update `test_api.py` fixtures; add 3 new tests for the surfacing

### Exit criteria
- [ ] `bootstrap_dev.py` returns a `JobResponse` where all 7 scoring dimensions are non-zero
- [ ] `staleness_state` and `date_confidence` are populated in the response
- [ ] `ENRICHMENT_ENABLED=true` + a LLM key causes enriched fields to appear in responses for high-scored jobs
- [ ] `?mode=hybrid` returns results (keyword-fallback if embeddings not built)
- [ ] Full pytest: no regressions from baseline
- [ ] Frontend still shows zeros in the radar *(expected — fixed in Step 2)*
- [ ] Commit: `feat(api): wire Pillar 2/3 data through JobResponse (S1)`

**Budget:** 2–3 sessions.

---

## Step 2 — API→UI Seam (Batches S2, S3, S4)

**Goal:** Make the backend visible. Every pillar's output now lands in the UI.

**Order:** Pillar 1 → Pillar 2 → Pillar 3 (finally, the pillar order matters — but only here, for UI polish).

**Why this order:**
- Pillar 1 surfacing is additive and low-risk (profile page edits)
- Pillar 2 surfacing is the wow-moment (scoring transparency = "intelligent tool" feeling)
- Pillar 3 surfacing is UX hardening (auth guard) + operational transparency (ledger)

### Shared prerequisites (install first)

- [ ] `npm install date-fns` — needed for staleness display (S2+S3)
- [ ] `npx shadcn-ui@latest add progress popover accordion` — missing primitives for confidence bars + dedup group preview
- [ ] Optional but recommended: `npm install @tanstack/react-query` — for paginated ledger (S4); raw fetch-in-useEffect gets ugly for pagination

### Batch S2 — Pillar 1 surfacing (profile)

- [ ] **Skill provenance badges** — new `frontend/src/components/profile/SkillProvenance.tsx`; show "from CV / from LinkedIn / from GitHub / manual" per skill
- [ ] **ESCO canonical labels** — display canonical skill name on hover (tooltip) when an ESCO-normalized skill differs from the raw input
- [ ] **Skill tiering** — primary/secondary/tertiary visual hierarchy (size, color, badge)
- [ ] **Version history UI** — new page `frontend/src/app/profile/versions/page.tsx`; timeline of snapshots; restore button per entry
- [ ] **JSON Resume export button** — add to `frontend/src/app/profile/page.tsx`; calls new endpoint (Step 3)
- [ ] **LinkedIn sub-sections** — render `cv_data.linkedin_positions`, `linkedin_skills`, Languages / Projects / Volunteer / Courses
- [ ] **GitHub temporal view** — show recent vs older repos distinction (timestamps from audit)
- [ ] Types.ts additions: `SkillProvenance`, `ESCOLabel`, `ProfileVersion`

**Note:** SkillProvenance + ESCO + Versions depend on Step 1 exposing these fields in `ProfileResponse`, OR on Step 3 adding version endpoints. Sequence: Step 1 first, then Step 3 endpoints first, then S2 renders them.

### Batch S3 — Pillar 2 surfacing (scoring + jobs)

- [ ] **ScoreRadar with real data** — `frontend/src/components/jobs/ScoreRadar.tsx` already has the 8-dim template. Just pass the actual dimension values from `JobResponse` (post-Step-1). Zero to one-line fix.
- [ ] **Date confidence badges** — new `frontend/src/components/jobs/ConfidenceBadge.tsx`; green "high" / yellow "medium" / red "fabricated" pill on job cards
- [ ] **Staleness warnings** — new `frontend/src/components/jobs/StalenessWarning.tsx`; "Posted 3 days ago" + "Job may be inactive" badge when `staleness_state == 'stale'`
- [ ] **Fabricated-date flag** — inline in `JobCard.tsx`: exclamation icon + tooltip "Source doesn't provide reliable dates"
- [ ] **Dedup-group badge** — new `frontend/src/components/jobs/DedupGroupBadge.tsx`; "Also posted on Indeed + Reed + LinkedIn" collapsible
- [ ] **Hybrid-mode toggle** — add to `FilterPanel.tsx`; calls `/jobs?mode=hybrid`
- [ ] Types.ts additions: `ScoreDimensions`, `DedupGroup`

### Batch S4 — Pillar 3 surfacing (auth + ledger + ghost)

- [ ] **Root-layout auth guard** — edit `frontend/src/app/layout.tsx` to add `useEffect` calling `me()` on mount; redirect to `/login` if 401, except on `/login` + `/register` routes
- [ ] **401 interceptor in api.ts** — wrap the `request()` function in `frontend/src/lib/api.ts`; on 401, trigger global auth-state reset
- [ ] **Auth context provider** — new `frontend/src/context/AuthContext.tsx`; exposes `user`, `loading`, `logout()` across tree
- [ ] **Notification history page** — new `frontend/src/app/notifications/page.tsx` (depends on Step 3 endpoint)
- [ ] **Ghost badge** — new `frontend/src/components/jobs/GhostBadge.tsx`; "Job no longer live" when `staleness_state == 'ghost'` or consecutive_misses > threshold
- [ ] **Pagination pattern** — if using React Query, add to ledger page; else raw offset/limit with "Load more" button
- [ ] Types.ts additions: `NotificationEvent`, `NotificationLedgerResponse`

### Cross-cutting polish

- [ ] Empty states for: 0 jobs, no CV uploaded, no channels, no notifications
- [ ] Mobile-responsive verification on every new component (user wants LinkedIn-shareable)
- [ ] Match existing visual style: dark theme, lime primary, aurora glow backgrounds, glass-card class, stagger animations

### Exit criteria
- [ ] Every backend-exposed field from Step 1 is visible somewhere in the UI
- [ ] Unauthenticated users redirect to `/login` from protected routes
- [ ] Score radar shows real 7-dim breakdown for every job
- [ ] Staleness + ghost badges visible on job cards
- [ ] Notification history page renders ledger rows
- [ ] Mobile layouts don't break
- [ ] Commits (one per slice): `feat(ui): S2 profile surfacing`, `feat(ui): S3 scoring surfacing`, `feat(ui): S4 auth + ledger surfacing`

**Budget:** 3–5 sessions (1–2 per slice).

---

## Step 3 — New Endpoints

**Goal:** Add HTTP surfaces for capabilities that exist in backend logic but have no route.

**Note:** Parts of Step 2 (S2 Version History UI, S4 Notification History page) depend on these endpoints. Either interleave Step 3 into Step 2, or do Step 3 first. Recommend **Step 3 first** — backend endpoints are simpler to ship than UI, and UI becomes a consumer.

### Original scope
- [ ] `GET /profile/versions` + `POST /profile/versions/{id}/restore`
- [ ] `GET /profile/json-resume`
- [ ] `GET /jobs/{id}/duplicates`
- [ ] `GET /notifications` (+ filters, pagination)
- [ ] `GET/POST /settings/enrichment`

### Gaps surfaced by audit

- [ ] **Profile versions backend ready** — `storage.py:list_profile_versions()` + `restore_profile_version()` both exist. Just needs Pydantic model + route. **Quick win.**
- [ ] **JSON Resume backend ready** — `models.py:to_json_resume()` exists. Returns a dict directly; no Pydantic model needed. **Quick win.**
- [ ] **Dedup groups = REDESIGN REQUIRED** — `deduplicator.py:deduplicate()` currently *discards* losers. To expose groups, need to either (a) track group membership in a new table, or (b) recompute grouping at query time. **Design decision before coding.** Recommend (b) for simplicity: on request, re-run the grouping key `(normalized_company, normalized_title)` across the catalog and return all matches.
- [ ] **Dedup scoping subtlety** — `jobs` catalog is shared per rule #10, but dedup ranking depends on the user's scoring context (their profile). Return the group but score/rank members against the authenticated user's `SearchConfig`.
- [ ] **Route ordering risk** — `/jobs/{job_id}` exists at `jobs.py:189`. Adding `/jobs/{job_id}/duplicates` is safe (more specific wins). But don't add `/jobs/{anything}` catch-alls.
- [ ] **Notification ledger cardinality** — high. 1 user × 3 channels × retries × months = thousands of rows. **Pagination mandatory from day one.** Default `limit=50, offset=0`.
- [ ] **Enrichment settings = design choice** — current `SEMANTIC_ENABLED` is global env var. To allow per-user toggles, need new `user_profiles.enrichment_enabled` column (migration 0010). **Design decision:** recommend global env for now (simpler); revisit per-user toggles in Batch 4 if there's a Pro/Free split.

### Per-endpoint checklist

Each endpoint needs:
- [ ] Pydantic response model (add to `src/api/models.py`)
- [ ] `Depends(require_user)` (CLAUDE.md rule #12)
- [ ] Query scoped by `user.id`
- [ ] 3–4 tests: happy path, auth-required, cross-user IDOR negative, edge (empty/not-found)

### Exit criteria
- [ ] All 5 endpoints return 200 with correct shape
- [ ] All 5 reject unauthenticated requests with 401
- [ ] Cross-user IDOR tests pass
- [ ] Route order verified (no shadowing of `/jobs/{id}`)
- [ ] Pagination works on `/notifications`
- [ ] Commit: `feat(api): profile versions + JSON Resume + dedup groups + ledger + settings`

**Budget:** 1–2 sessions.

---

## Step 4 — Ops Hardening

**Goal:** Make the tool deployable and observable in production.

**Severity:** Audit flagged **12 launch-blockers** — Step 4 is heavier than originally scoped. Split into 4-core (must-do pre-launch) and 4-extended (pre-prod hardening).

### 4-core (launch-blockers)

- [ ] **CI pipeline** — `.github/workflows/` is empty. Add `ci.yml`:
  - Backend: `pytest tests/ --ignore=tests/test_main.py -p no:randomly` + `ruff check` + `mypy src/`
  - Frontend: `npm run lint` + `next build`
  - Trigger on PR + push to main
- [ ] **Dockerfile + docker-compose.yml** — no containers exist. Compose stack: backend (FastAPI), worker (ARQ), frontend (Next.js), Redis, SQLite volume mount
- [ ] **Secrets generation script** — `scripts/generate_secrets.sh`:
  ```bash
  SESSION_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(64))')
  CHANNEL_ENCRYPTION_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(64))')
  ```
- [ ] **Healthcheck depth** — `/health` currently returns 200 always. Update to: ping DB (`count_jobs` with 2s timeout) + ping Redis (if `REDIS_URL` set); return 503 if either fails
- [ ] **Rate-limit on auth endpoints** — add `slowapi>=0.1.9`; decorate `/api/auth/register` and `/api/auth/login` with `@limiter.limit("5/minute")` to prevent email-enumeration + brute-force
- [ ] **DB backup script** — `scripts/backup_db.sh`: `cp backend/data/jobs.db backend/data/jobs.db.$(date +%Y%m%d_%H%M%S).bak`; schedule via cron
- [ ] **Frontend prod config** — `next.config.ts` needs `output: "standalone"`; `frontend/.env.local.example` must document `NEXT_PUBLIC_API_URL`
- [ ] **Env validation at boot** — move `SESSION_SECRET` + `CHANNEL_ENCRYPTION_KEY` checks to FastAPI lifespan startup (crash at boot, not at first request)

### 4-extended (pre-prod, optional pre-launch)

- [ ] **Structured JSON logging** — `utils/logger.py:37–48` has `JSONFormatter` class but it's unused. Toggle via `LOG_FORMAT=json` env var
- [ ] **Sentry / error tracking** — ARQ task errors currently write to `notification_ledger.error_message` and die silently. Wire Sentry or a dead-letter queue
- [ ] **Legal doc skeleton** — create `docs/compliance/` folder with empty `privacy-notice.md` + `lia.md` (content in Batch 4A)
- [ ] **Pre-commit hook verification** — `.pre-commit-config.yaml` exists; ensure CI enforces the same checks

### 4-deferred (not Step 4)

These belong in Batch 4A (static launch-readiness), not Step 4:
- SES adapter (Batch 4A — pure-code operational deliverable)
- Prod-Redis smoke script (Batch 4A)
- Privacy notice + LIA content drafts (Batch 4A)

### Exit criteria
- [ ] CI green on a fresh PR
- [ ] `docker-compose up` brings up backend + frontend + worker + Redis cleanly
- [ ] `/health` returns 503 if DB or Redis is down
- [ ] Brute-force test: 10 rapid `/login` hits → 429 after 5
- [ ] Backup script produces a timestamped `.bak` file
- [ ] `next build` produces a standalone frontend
- [ ] Boot with missing `SESSION_SECRET` → clear error, immediate crash (not first-request)
- [ ] Commits: `ops: CI + docker + healthcheck + rate-limit + backup`

**Budget:** 1–2 sessions for 4-core; 1 additional session if tackling 4-extended.

---

## Step 5 — Batch 4 (Launch Readiness)

**Goal:** Ship. With dogfood data from Steps 1–3 + ops baseline from Step 4, the pricing/scope/copy decisions are data-informed, not guesses.

### Rescope: Split into 4A and 4B

**Rationale from audit:** Some Batch 4 deliverables need dogfood data (source cull, freemium caps), others don't (SES adapter, ICO, copy). Split to unblock the static work.

### Batch 4A — Static launch-readiness (executable any time after Step 4)

Doesn't need user data. Can run parallel to Step 4 if resources allow.

- [ ] **Amazon SES adapter** — new `backend/src/services/notifications/ses_notify.py`; lazy-import `boto3` per CLAUDE.md rule #11; replace Gmail SMTP as default transactional path
- [ ] **Prod-Redis smoke script** — `backend/scripts/prod_redis_smoke.py`; enqueue → worker → ledger → cleanup; exit 0/1
- [ ] **ASA-compliant copy sweep** — rewrite `README.md` + `frontend/src/app/page.tsx` + `CLAUDE.md` first line; remove "all UK white-collar domains" + "24-hour freshness"; new `docs/methodology.md` with defensible source list
- [ ] **Privacy notice draft** — `docs/compliance/privacy-notice.md` (content from `docs/research/pillar_3_batch_4.md §Privacy`)
- [ ] **LIA draft** — `docs/compliance/lia.md`
- [ ] **ICO registration checklist** — `docs/compliance/ico-registration.md` with step-by-step (£35 direct debit, register as Tier 1)
- [ ] **Pricing page** — `frontend/src/app/pricing/page.tsx`; Free / Pro £14.99/mo / Pro annual £119/yr / Sprint £24.99 4-week (from research doc)

### Batch 4B — Data-driven calibration (requires dogfood from Steps 1–3)

Depends on at least 1–2 weeks of real usage data in `run_log.per_source` and `user_feed`.

- [ ] **Source cull decision** — query `run_log.per_source` to rank sources by yield × score × reliability; pick final 13; add `ENABLED_SOURCES` env var gating `_build_sources()`
- [ ] **Freemium caps** — query median matches/day per user from `user_feed`; set Free tier cap at ~50th percentile; add `services/quota.py` + migration `0010_user_tier.sql`
- [ ] **Pricing tiers finalization** — confirm £14.99/£119 from research doc; wire Stripe (or defer to post-launch)
- [ ] **Billing routes** — `GET /api/billing/status`; `POST /api/billing/upgrade` (Stripe webhook)

### Operator TODOs (not generator work — user must do these)

- [ ] **Pay ICO £35** via direct debit at ico.org.uk (Tier 1 micro-business)
- [ ] **Legal review** of privacy notice + LIA with solicitor
- [ ] **AWS SES production access** — support ticket to lift sandbox
- [ ] **NHS Jobs Self-Serve API application** — email `nhsjobsintegration@nhsbsa.nhs.uk`
- [ ] **CV-Library Traffic Partner** signup (web form)
- [ ] **Domain + DKIM setup** at registrar / Cloudflare

### Research-doc testable claims

After dogfood, verify these from `docs/research/pillar_3_batch_4.md`:

| Claim | Verifiable via |
|---|---|
| "20 matches/day ceiling" | `user_feed` cohort analysis |
| "13 sources cover 80% of volume" | `run_log.per_source` yield distribution |
| "Healthcare has 85% coverage via NHS" | Per-domain score distribution across users |
| "£14.99/mo breaks even at 9,300 users" | Not testable pre-launch; keep default |

### Exit criteria
- [ ] ICO registered (user action)
- [ ] Privacy notice + LIA published + solicitor-reviewed (user action)
- [ ] Marketing copy ASA-compliant (grep verifies no "all UK" or "24-hour" in user-facing files)
- [ ] SES sending test email successfully
- [ ] Prod-Redis smoke passes
- [ ] Source count reduced to 13 (or final data-driven number) via `ENABLED_SOURCES`
- [ ] Freemium quota enforced for free users
- [ ] Pricing page live
- [ ] `GET /api/billing/status` returns tier + usage
- [ ] Commits: `feat(launch): SES + smoke + copy + compliance + pricing + billing`

**Budget:** Varies. 4A = 2–3 sessions. 4B = 1–2 sessions after dogfood window.

---

## Estimated overall budget

| Phase | Sessions |
|---|---|
| Step 0 | 1–2 |
| Step 1 | 2–3 |
| Step 2 | 3–5 |
| Step 3 | 1–2 |
| Step 4 (core) | 1–2 |
| Step 5 (4A) | 2–3 |
| Step 5 (4B) | 1–2 (after dogfood) |
| **Total** | **11–19 sessions** to "launch-ready with real data" |

Dogfood window (1–2 weeks of real usage) fits inside the 4A work, so total wall-clock is ~3–4 weeks if executed without long pauses.

---

## Running instructions for the executor (when ready)

1. Work on a dedicated branch per step: `step-0-preflight`, `step-1-api-seam`, `step-2-ui-surfacing`, etc.
2. One commit per deliverable inside a step (not one giant commit per step).
3. Run pytest baseline at the start of each step; confirm no regressions at the end.
4. Use `docs/batch_prompts.md` patterns (worktree-based generator/reviewer) for larger slices if preferred.
5. Reference CLAUDE.md rules #10 (shared catalog), #11 (lazy imports), #12 (auth-required routes), #13 (5-surface source count).
6. Update `docs/IMPLEMENTATION_LOG.md` after each step completes.

---

## Parked decisions (revisit during Step 5)

- **Global vs per-user enrichment toggle** — current is global env var; revisit if Pro/Free tiers want per-user control
- **Stripe integration timing** — Step 5 (4B) or post-launch
- **Dead-letter queue** — part of Step 4-extended; defer if not blocking launch
- **Pillar 2 semantic stack full activation** — requires `[semantic]` extras install + ESCO JSON-LD + ChromaDB path; plan during Step 2 (S3) dogfood

---

_Last updated: 2026-04-23 (synthesised from 6 parallel audit agents)._
_Baseline: main @ 5fb3c07, 600p/0f/3s pytest, pillars 1/2/3 fully merged._
