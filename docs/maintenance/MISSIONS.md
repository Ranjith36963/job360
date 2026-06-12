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

## M2 — Pillar 2: the judge stays correct over time  [worker-parallel, one item NEEDS-HUMAN]
claimed-by: -   status: OPEN
Backlog: #7 enrichment merge, #8 re-judge on profile change, (#9 telemetry = M2-3 blocked)
files-owned: src/services/job_enrichment.py (merge logic), src/services/llm_matcher.py, src/api/profile.py (re-judge trigger), scripts/ (accuracy harness), their tests
DoD:
- [ ] enrichment L1 merge no longer prefers weak rules-seniority over LLM "unknown"; accuracy harness shows the measured −10pt regression recovered (run the harness, paste numbers)
- [ ] profile version change clears/invalidates llm_matched_at for that user; next feed read triggers re-judge; proven by test + a live re-judge demonstration at integration
- [ ] M2-3 (run_log telemetry columns) requires a migration → logged as NEEDS-HUMAN, not attempted
- [ ] full backend suite green in the worktree

## M3 — Pillar 3: frontend carry-overs  [worker-parallel, frontend-only]
claimed-by: -   status: OPEN
Backlog: #12 V-01..V-03 RHF+zod validation, #13 C-07 kanban keyboard a11y, #11 V-04 CV upload cap+MIME (backend route + frontend)
files-owned: frontend/src/** (forms, KanbanBoard), src/api/profile.py upload route (V-04 only), their tests
DoD:
- [ ] all auth/profile forms validate with RHF+zod; invalid submits blocked client-side with messages; vitest coverage for each form
- [ ] KanbanBoard fully keyboard-operable via @dnd-kit (documented key map); a11y assertions in tests
- [ ] CV upload enforces size cap + MIME allowlist server-side; rejecting test + accepting test
- [ ] vitest, type-check, lint all green in the worktree

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

## M7 — OpenAPI→TS codegen  [SERIALIZED — integrator only]
Backlog #14, report problem #7. status: RESEARCH-DONE, AWAITING-OWNER-APPROVAL (research round 2026-06-12). Comparison + recommendation written to docs/maintenance/M7-codegen-research.md.
**Recommendation: openapi-typescript (types-only).** 3-line rationale: (1) fixes the actual defect — types drift — with the smallest change, keeping our working hand-written api.ts; (2) uniquely zero-runtime-deps (criterion a), one-CLI-line build (b), near-zero maintenance + the tool's upkeep increasing in 2026 (c); (3) the only reason to pick Hey API instead is M3 zod synergy, which is thin (M3 zod is for form inputs, not 35-field API responses). Owner fork documented in the research file. DO NOT implement until owner picks openapi-typescript vs Hey API.

## M8 — End-to-end pipeline offline coverage  [worker-parallel, claim after M1 ships]
Report problem #10. files-owned: backend/tests/test_main.py, tests/fixtures
DoD:
- [ ] the 13 skip-marked scaffolding tests replaced with mocked offline E2E tests of run_search()
- [ ] no live network calls in the canonical suite; suite green

---

## NEEDS-HUMAN queue (answer in the morning)
1. M2-3 migration approval (telemetry columns)
2. DfE "Display Adverts" replacement API subscription key — register it to revive gov_apprenticeships, or leave the source quarantined (M1 closing note)
3. M6 scope expansion proposal: bundle comeet + gov_apprenticeships + aijobs_global into the rotation alongside jobtensor (50→46 instead of 50→49)? All four are now proven-dead quarantines. Your approved scope is jobtensor-only; expanding needs your word.
4. API keys: jsearch / jooble / careerjet / findwork — provide or mark permanently-skipped
5. SMTP / channel credentials for real notification sends
