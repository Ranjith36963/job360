# Job360 Missions

**CANONICAL COPY: `D:\dev\job360\docs\maintenance\MISSIONS.md` (the MAIN checkout).** Every agent — worker, scout, integrator, health — reads and writes THIS absolute path, never the copy inside its own worktree (worktree copies are stale checkouts, not coordination state).

**Claim protocol (race protection):** write your claim into `claimed-by:` → re-read the file → confirm YOUR claim is the one recorded → only then start work. If another claim got there first, back off to the next OPEN mission.

Format per mission: id, pillar, owner-type, claimed-by (worktree/session or "-"), status (OPEN / CLAIMED / DONE-PENDING-INTEGRATION / DONE / NEEDS-HUMAN), files-owned (exclusive while claimed), definition of done (DoD — every line must be PROVEN with evidence before DONE).
Rules: a worker may claim ONE mission. Files-owned lists are exclusive locks — never edit files owned by another CLAIMED mission. Serialized missions may only be claimed by the integrator. Backlog item numbers refer to docs/maintenance/BACKLOG.md.

**CORE list (Upgrade 3 — two autonomy speeds).** Changes to ANY of these always get full adversarial review (Upgrade 1 waves) + integrator E2E-flavor live verify, and are never bundled with other changes in one commit:
```
backend/migrations/**      backend/src/repositories/database.py   backend/src/api/routes/auth.py
backend/src/main.py        backend/src/api/models.py              backend/src/core/settings.py
frontend/src/lib/types.ts
```
Everything else is EDGE: full speed, small frequent gated commits, cheap revert.

---

## ✅ ALL MISSIONS COMPLETE (M1–M8) — owner-confirmed 2026-06-13

The mission program is finished. M1, M3–M8 were completed + integrated by the loop (evidence in each mission block + JOURNAL.md). M2 (Pillar-2, owner-reserved): the re-judge piece (#8) was built + live-verified this session with owner authorization (migration 0018 + `rescore.py` + trigger); the owner reports the remaining Pillar-2 items (#7 enrichment-merge, #9 telemetry) completed himself. No pending missions remain; the loop is **paused** (no active cron). The three follow-up items formerly listed as open backlog are now **all DONE**: **M3-rem** (`noValidate` on auth forms ✅ done; homepage copy "50 sources" → 47 ✅ fixed), **M7a** (`api.ts` no longer hand-mirrors types — resolved by M7's codegen ✅), **M8a** (`test_main.py` un-ignored and included in the gate ✅). The standing **Pillar-2 hands-off** rule for all agents stays in force via the worker/integrator skills regardless of M2's status — only the owner edits Pillar-2 code.

> **⛔ SUPERSEDED 2026-07-26 — the Pillar-2 hands-off rule is REPEALED.** The sentence above ("stays in force via the worker/integrator skills") is history, not a live directive. The owner granted full standing permission on Pillar 2 on 2026-07-26: `skill_matcher.py`, `scoring_dimensions.py`, `retrieval.py`, `embeddings.py`, `vector_index.py`, `job_enrichment.py`, `llm_matcher.py`, the accuracy `scripts/`, and the re-judge path in `api/profile.py` are now edited under the SAME rules as the rest of the repo — test-first, gate-stamped commit, PR, evidence — with **no per-item confirmation needed**. The binding rules `.claude/skills/worker/SKILL.md` (5a) and `.claude/skills/integrator/SKILL.md` (7) were rewritten to match. Repo-wide rules #18 / #19 / #20 / #27 still apply, as they do everywhere.

---

## M1 — Pillar 1: every enabled source is healthy  [worker-parallel]
claimed-by: -   status: DONE (integrated 3315fb3, live-verified run b0250268211a @2026-06-11T21:10Z)
closing-note (worker-a, 2026-06-11): commits 1f30608 + fc55fb8 on agent/m1-sources. All four sources probed live 2026-06-11; all four upstreams are gone/blocked, so the pattern everywhere is the b7b2c60 jobtensor quarantine (1 un-retried canary/run, single INFO, auto-resume if upstream revives) rather than repair. Integrator at merge: (1) run the live-pipeline log check for DoD line 4; (2) add STATUS.md fragile rows for comeet, gov_apprenticeships, aijobs_global + a note that glassdoor querying is off (STATUS.md not in M1 files-owned); (3) journal the DfE subscription-key NEEDS-HUMAN; (4) comeet/gov_apprenticeships/aijobs_global are now strong candidates to bundle into the M6 source rotation.
Backlog: #3 comeet, #4 gov_apprenticeships, #5 aijobs_global, #6 glassdoor
files-owned: src/sources/ats/comeet.py, core/companies.py, src/sources/apis_free/gov_apprenticeships.py, src/sources/scrapers/aijobs_global.py, src/sources/other/indeed.py, tests/test_sources.py (append-only)
DoD:
- [x] comeet: ALL 5 slugs probed dead 2026-06-11 (3x "Token is missing" 400, 2x 404; API now needs a non-discoverable per-company token; riskified+lightricks moved to Greenhouse, zero UK locations there) → cleanly quarantined per jobtensor precedent: 1 un-retried canary/run, single INFO, auto-resume on 200+array; slugs pruned to canary (1f30608). Journal note: integrator please add STATUS.md fragile row (file not in M1 files-owned). NEEDS-HUMAN: none.
- [x] gov_apprenticeships DONE in 1f30608 (v1 API retired upstream: 302 → HTML "Page not found" served as HTTP 200; quarantined same pattern, auto-resume on 200+dict; NEEDS-HUMAN: DfE "Display Adverts" replacement API needs a subscription key — register or leave quarantined). aijobs_global DONE in fc55fb8 (board abandoned: all listings status-expired, newest RSS item Oct 2023; suggest endpoint answers JSONP "([])" for every term → "Expecting value" + 3 retries/query. Quarantined: 1 un-retried canary, single INFO, auto-resume on non-empty array with paren-stripping).
- [x] glassdoor: disabled with rationale (fc55fb8). Probed 2026-06-11: findPopularLocationAjax.htm answers anti-bot 403 "Security | Glassdoor" for EVERY term (London, UK / London / London, England) — block is term-independent, no format fixes it. JobSpySource default sites now ["indeed"]; explicit override kept. Registry keys untouched (M6 owns the 5-surface rotation).
- [x] zero HTTP 400 and zero "Expecting value" log lines attributable to these four sources in one fresh pipeline run — integrator CONFIRMED at merge 3315fb3: run b0250268211a @21:10Z has zero such lines; the only matches in the log are pre-merge (run 9673808f @19:36)
- [x] full backend suite green in the worktree (gate: 1291 passed, 3 skipped after fc55fb8); every fix has appended tests (single-probe/no-WARNING + revival/resume per source; site_name==["indeed"] for JobSpy)

## M2 — Pillar 2: the judge stays correct over time  [🔒 OWNER-RESERVED — ⛔ SUPERSEDED 2026-07-26, reservation lifted; see note below]
claimed-by: **owner**   status: **DONE** (owner-confirmed all M2 items complete, 2026-06-13)
**🔒 PILLAR-2 HANDS-OFF — BINDING ON ALL AGENTS (worker, integrator, scout, health, executors).** The owner builds the re-judge trigger + telemetry + enrichment-merge fix HIMSELF. No agent edits ANY Pillar-2 code. The hands-off zone EXPLICITLY includes `llm_matcher.py` (the funnel→judge matcher) AND `job_enrichment.py`, `scoring_dimensions.py`, `skill_matcher.py`, `embeddings.py`, `retrieval.py`, `vector_index.py`, the accuracy `scripts/`, and the Pillar-2 re-judge path in `api/profile.py`. Agents may ONLY **report** judge/scoring bugs to BACKLOG.md with evidence (logs, repro, measured numbers) — never fix, never edit, never "improve". If a heartbeat picks M2, it does NOT start it; it leaves this reservation intact.
**2026-06-13 — owner authorized building the re-score/re-judge portion (backlog #8).** The profile-version re-score is now implemented: migration 0018 (`user_feed.profile_version`), `src/services/rescore.py`, `clear_user_verdicts` in `llm_matcher.py`, change-detector in `profile/storage.py`, and trigger in `api/routes/profile.py`. The broader Pillar-2 hands-off remains fully in force for all other items (#7 enrichment merge, #9 telemetry, all scoring/embedding/enrichment code).
**⛔ 2026-07-26 — SUPERSEDED: the owner repealed the hands-off entirely.** Everything above in this M2 block is history, not a live directive. No file in the `files-owned` line below is owner-only any more; agents edit all of them under the normal rules — test-first, gate-stamped commit, PR, evidence — with **no per-item confirmation needed**, and may start, claim, or dispatch M2 like any other mission. Do not re-impose the reservation. Repo-wide rules #18 / #19 / #20 / #27 still apply.
Backlog (owner's own list): ALL COMPLETE per owner 2026-06-13 — ~~#7 enrichment merge~~ (owner), ~~#8 re-judge on profile change~~ (built + live-verified this session: migration 0018 + `rescore.py` + trigger), ~~#9 telemetry~~ (owner). #8 was built by the loop with owner authorization; #7 + #9 were completed by the owner himself.
files-owned: ~~OWNER ONLY~~ (superseded 2026-07-26 — not owner-exclusive any more) — src/services/{job_enrichment,llm_matcher,scoring_dimensions,skill_matcher,embeddings,retrieval,vector_index}.py, src/api/profile.py (re-judge path), scripts/ (accuracy harness), their tests.

## M3 — Pillar 3: frontend carry-overs  [worker-parallel, frontend-only]
claimed-by: -   status: DONE (worker-a e108eed integrated → merge 25c1b3c; round 15). Settings/account forms (Change password/email, Delete account) RHF+zod; KanbanBoard @dnd-kit keyboard a11y; CV upload caps. Combined with the integrator's earlier M3-slice (auth forms + bounded-read V-04), M3 is fully complete. V-04 reconciled at merge = best of both (bounded memory-safe read + extension-only MIME). Gate green; account forms live-verified (test-artifacts/m3-account-forms.png). M3-rem follow-ups (noValidate, "50 sources" footer) remain in backlog.
closing-note (worker-a, 2026-06-12): commit e108eed on agent/m3-frontend. All four DoD lines proven. Pre-existing test_retrieval_integration::test_mode_hybrid_empty_index_falls_back confirmed failing on clean d97ff88 HEAD (Pillar 2 hands-off zone) — not a regression from M3 changes. Integrator: merge agent/m3-frontend; no migrations, no schema changes. Run live check on the settings/account page to verify form validation UX; drag a card in the pipeline KanbanBoard to verify keyboard a11y.
Backlog: #12 V-01..V-03 RHF+zod validation, #13 C-07 kanban keyboard a11y, #11 V-04 CV upload cap+MIME (backend route + frontend)
files-owned: frontend/src/** (forms, KanbanBoard), src/api/profile.py upload route (V-04 only), their tests
DoD:
- [x] all auth/profile forms validate with RHF+zod; invalid submits blocked client-side with messages; vitest coverage for each form (e108eed: ChangePassword+ChangeEmail use zodResolver; DeleteAccount uses RHF inline validate; 11 vitest tests; messages appear as role=alert)
- [x] KanbanBoard fully keyboard-operable via @dnd-kit (documented key map); a11y assertions in tests (e108eed: DndContext+KeyboardSensor+PointerSensor wired; sr-only hint para with Space/Arrow/Enter/Escape map; 6 vitest a11y assertions)
- [x] CV upload enforces size cap + MIME allowlist server-side; rejecting test + accepting test (e108eed: 413 for >10MB, 415 for non-PDF/DOCX; 6 pytest tests: oversized, boundary, .txt, no-ext, .pdf, .docx)
- [x] vitest, type-check, lint all green in the worktree (e108eed: 71 vitest passed, tsc clean, eslint exit 0)

## M4 — Docs and hygiene match reality  [worker-parallel, no code]
claimed-by: -   status: DONE (worker cb350db; integrated 83864b2 with 5 wave-survivor fixes incl. slug truth 264 — see JOURNAL round 3)
closing-note (worker-a, 2026-06-11): commit cb350db on agent/m4-docs. All four DoD lines proven: (1) test counts 1154→1285/1288, migration count 15→18, env table adds MATCHER_*/SOURCE_FETCH_TIMEOUT_ATS, matcher batch phase summary added to CLAUDE.md; (2) four-engine table in STATUS.md, matcher batch entry in IMPLEMENTATION_LOG.md, engine-stack section + user_feed llm_* columns in ARCHITECTURE.md, matching-engines section + accurate source subgraphs in README.md; (3) no stray images found, test-artifacts/README.md index added; (4) source counts, dir layout, db schema all corrected against CODEBASE_REPORT — no remaining contradictions.
Backlog: #15, #16, #17 (NOT 16b — serialized)
files-owned: CLAUDE.md, backend/CLAUDE.md, STATUS.md, IMPLEMENTATION_LOG.md, README.md, ARCHITECTURE.md, docs/**, test-artifacts/
DoD:
- [x] test counts, env-var tables, and phase history updated to audited reality (cb350db: 1154→1285/1288 collected, migration count 15→18, MATCHER_*/SOURCE_FETCH_TIMEOUT_ATS env vars added, state-of-play current)
- [x] matcher batch + four-engine architecture documented: STATUS.md engine table, IMPLEMENTATION_LOG.md batch entry, ARCHITECTURE.md engine-stack + migration 0017 columns, README.md matching-engines section (cb350db)
- [x] stray screenshots/experiment outputs: no stray images found; test-artifacts/README.md index created; .gitignore already covers *.png patterns (cb350db)
- [x] no claims contradict CODEBASE_REPORT.md: source counts, dir layout, API-key count, db schema, engine flags all corrected (cb350db)

## M5 — ATS sweeps survive the timeout  [SERIALIZED — integrator only]
claimed-by: integrator   status: DONE (run 2/2 b0250268211a @21:10Z: greenhouse 1335, lever 175, workday 99, ashby 403, errors {} — two consecutive clean runs)
Report problem #4. files-owned: core/settings.py, services/scheduler.py, sources/ats/**
DoD:
- [x] confirmed root cause (measured unbounded: greenhouse 138.2s/1331 jobs, ashby 46.1s/403, workable 41.2s/0 — 60s cap truncated sweeps; evidence in JOURNAL 2026-06-11 round 1)
- [x] per-category timeout implemented (89fa0e0: SOURCE_FETCH_TIMEOUT_ATS=240 + resolve_fetch_timeout); TWO consecutive clean live runs: c7345ff349d8 @18:40 (greenhouse 1331) and b0250268211a @21:10 (greenhouse 1335), both errors {}.
- [x] full suite green (1285/3) + live verify (run c7345ff349d8, zero errors)

## M6 — Source rotation 50→46 (bundled: jobtensor+comeet+gov_apprenticeships+aijobs_global)  [SERIALIZED — integrator only]
Backlog 16b. status: DONE (1e709f7, round 8). 5 surfaces + SOURCE_INSTANCE_COUNT 49→45 + 4 files deleted + docs. Gate 1272 passed; live /api/sources = 46, 4 confirmed gone, lookalikes kept. Adversarial waves: 2 raw → 1 confirmed-fixed (discover_companies map) + 1 refuted (phase history). Five surfaces per rules #8/#13 (SOURCE_REGISTRY, _build_sources, RATE_LIMITS, test_cli, test_api) + doc count refs (CLAUDE.md/STATUS/ARCHITECTURE). Proof bar: jobtensor upstream-dead evidence already journaled (iteration 3) — re-cite in the removal commit.
**POST-M6 NOTE (2026-06-16):** `gov_apprenticeships` was restored as a new source on the DfE Display Advert API v2 (different endpoint from the retired v1). SOURCE_REGISTRY is now **47** (not 46). The M6 removal is historical fact; the restoration is a separate commit on `fix/per-user-search-and-scoring-gate`.

## M7 — OpenAPI→TS codegen  [SERIALIZED — integrator only]
Backlog #14, report problem #7. status: DONE (53e2020, owner-approved openapi-typescript). types.ts → thin alias layer over generated api-types.ts (net −249 lines); offline gen script + npm scripts + drift guard wired into agent-gate (backend API change OR frontend change that desyncs types now FAILS the commit). Adversarial waves caught + resolved: 3 call-site drift fixes, a real skill_tiers latent crash (dict defaults to {}, profile page accessed .primary unguarded), 1 follow-up filed (M7a — api.ts hand-mirrors 3 types with intentional tighter unions). Gate: backend 1272 + drift + frontend 65/type-check/lint green. Live-verified: profile + dashboard render with generated types, only the benign dark-mode hydration badge (test-artifacts/m7-{profile,dashboard}-render.png).

## M8 — End-to-end pipeline offline coverage  [SERIALIZED — integrator did it]
Report problem #10. files-owned: backend/tests/test_main.py
DoD:
- [x] 13 skip-marked scaffolding tests un-skipped + rehabbed offline (JobSpy stub + profile stub + breaker-registry reset fixtures; missing URL mocks added). All 14 pass in 11s (was ~32 min live). Assertion fix on 2 tests bypasses domain filtering via classify_user_domain→set() to exercise the include-all-sources path (documented, not weakened).
- [x] no live network: timed run 11.1s (a single live JobSpy call alone is 30s+). Canonical suite still 1277 green, no regressions.
status: DONE (round 12). FOLLOW-UP M8a: ✅ DONE — test_main.py un-ignored; it now runs in the canonical gate (no `--ignore` anywhere in Makefile/CLAUDE.md/agent-gate/pyproject). The 14 tests pass in ~8 s offline.

---

## NEEDS-HUMAN queue (answer in the morning)
1. M2-3 migration approval (telemetry columns)
2. ~~DfE "Display Adverts" replacement API subscription key~~ — **RESOLVED 2026-06-16**: `gov_apprenticeships` restored and active on DfE Display Advert API v2; SOURCE_REGISTRY = 47.
3. ~~M6 scope expansion proposal: bundle comeet + gov_apprenticeships + aijobs_global~~ — **RESOLVED**: M6 shipped (jobtensor+comeet+aijobs_global removed, 50→46); `gov_apprenticeships` subsequently restored 2026-06-16 (46→47). SOURCE_REGISTRY = 47.
4. API keys: jsearch / jooble / careerjet / findwork — provide or mark permanently-skipped
5. SMTP / channel credentials for real notification sends
