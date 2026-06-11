# Job360 Missions

**CANONICAL COPY: `D:\dev\job360\docs\maintenance\MISSIONS.md` (the MAIN checkout).** Every agent — worker, scout, integrator, health — reads and writes THIS absolute path, never the copy inside its own worktree (worktree copies are stale checkouts, not coordination state).

**Claim protocol (race protection):** write your claim into `claimed-by:` → re-read the file → confirm YOUR claim is the one recorded → only then start work. If another claim got there first, back off to the next OPEN mission.

Format per mission: id, pillar, owner-type, claimed-by (worktree/session or "-"), status (OPEN / CLAIMED / DONE-PENDING-INTEGRATION / DONE / NEEDS-HUMAN), files-owned (exclusive while claimed), definition of done (DoD — every line must be PROVEN with evidence before DONE).
Rules: a worker may claim ONE mission. Files-owned lists are exclusive locks — never edit files owned by another CLAIMED mission. Serialized missions may only be claimed by the integrator. Backlog item numbers refer to docs/maintenance/BACKLOG.md.

---

## M1 — Pillar 1: every enabled source is healthy  [worker-parallel]
claimed-by: -   status: OPEN
Backlog: #3 comeet, #4 gov_apprenticeships, #5 aijobs_global, #6 glassdoor
files-owned: src/sources/ats/comeet.py, core/companies.py, src/sources/apis_free/gov_apprenticeships.py, src/sources/scrapers/aijobs_global.py, src/sources/other/indeed.py, tests/test_sources.py (append-only)
DoD:
- [ ] comeet: dead slugs (riskified, lightricks) pruned or replaced with live ones; a direct probe returns jobs or the source is cleanly disabled with a journal note
- [ ] gov_apprenticeships and aijobs_global: non-JSON diagnosed; each either parses real responses again (probe proof) or degrades gracefully (no error-level log lines)
- [ ] glassdoor: location format fixed (probe returns jobs) OR glassdoor querying disabled with rationale
- [ ] zero HTTP 400 and zero "Expecting value" log lines attributable to these four sources in one fresh pipeline run (integrator confirms at merge)
- [ ] full backend suite green in the worktree; new/changed tests cover each fix

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
claimed-by: -   status: OPEN
Backlog: #15, #16, #17 (NOT 16b — serialized)
files-owned: CLAUDE.md, backend/CLAUDE.md, STATUS.md, IMPLEMENTATION_LOG.md, README.md, ARCHITECTURE.md, docs/**, test-artifacts/
DoD:
- [ ] test counts, env-var tables, and phase history updated to audited reality (1287 collected / 1284 passing baseline)
- [ ] matcher batch + four-engine architecture documented in STATUS, IMPLEMENTATION_LOG, ARCHITECTURE, README
- [ ] stray screenshots/experiment outputs removed or moved under test-artifacts/ with an index
- [ ] no claims in docs contradict CODEBASE_REPORT.md findings

## M5 — ATS sweeps survive the timeout  [SERIALIZED — integrator only]
claimed-by: integrator (main checkout, round 1)   status: CLAIMED
Report problem #4. files-owned: core/settings.py, services/scheduler.py, sources/ats/**
DoD:
- [ ] confirmed root cause (instrument one greenhouse/workday sweep)
- [ ] per-category timeout or slug-batch chunking implemented; ashby/greenhouse/lever/workable/recruitee/workday return jobs or clean 0 (no error entries) across two consecutive live runs
- [ ] full suite green + live verify

## M6 — Source rotation 50→49 + jobtensor removal  [SERIALIZED — integrator only, after human approval]
Backlog 16b. status: NEEDS-HUMAN (approve rotation first)

## M7 — OpenAPI→TS codegen  [SERIALIZED — integrator only, after human approval]
Backlog #14, report problem #7. status: NEEDS-HUMAN (architecture choice)

## M8 — End-to-end pipeline offline coverage  [worker-parallel, claim after M1 ships]
Report problem #10. files-owned: backend/tests/test_main.py, tests/fixtures
DoD:
- [ ] the 13 skip-marked scaffolding tests replaced with mocked offline E2E tests of run_search()
- [ ] no live network calls in the canonical suite; suite green

---

## NEEDS-HUMAN queue (answer in the morning)
1. M2-3 migration approval (telemetry columns)
2. M6 rotation approval
3. M7 codegen decision
4. API keys: jsearch / jooble / careerjet / findwork — provide or mark permanently-skipped
5. SMTP / channel credentials for real notification sends
