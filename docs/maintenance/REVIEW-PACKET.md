# Staging review packet — 2026-06-12 (round 8)

**New: M6 source rotation 50→46 (`1e709f7`)** — removed 4 upstream-dead sources (jobtensor, comeet, gov_apprenticeships, aijobs_global) across all 5 load-bearing surfaces + docs + 4 file deletions. **risk: low-med** (CORE files main.py/settings.py touched, but removals only; full adversarial waves run, 1 finding fixed; gate 1272 passed; live /api/sources=46 verified). Net −1,226 lines. Evidence: JOURNAL round 8. CORE files touched: backend/src/main.py, backend/src/core/settings.py.

---

# Staging review packet — 2026-06-12 (round 3)

**New since round 2:** M4 docs-sync integrated (`83864b2`): all project docs (CLAUDE/STATUS/ARCHITECTURE/README/IMPLEMENTATION_LOG) now match audited reality — **risk: low** (docs only). First full adversarial-wave run: 11 raw findings → 5 survived → 5 fixed at merge (incl. an internal contradiction and the slug-count ground truth: **264**, not 268/266). Evidence: JOURNAL round 3.

---

# Staging review packet — 2026-06-11 (round 2)

Since your last merge to main (origin/main 7194d0e — everything below is on `fix/per-user-search-and-scoring-gate` / `loop/staging`):

1. **Funnel→judge LLM matcher** (migration 0017, llm_matcher service, pipeline stage, API fields, dashboard badge + sort) — per-user fit verdicts re-rank the feed; measured 18/18 judged in 89.8s, verdicts 10/10 on the labeled sample — **risk: medium** (new LLM call path; flag-gated MATCHER_ENABLED, default off) — evidence: JOURNAL bootstrap entry + a925f42..d801f78, 6974bb6.
2. **Source repairs: jobicy fixed; jobtensor, comeet, gov_apprenticeships, aijobs_global quarantined; glassdoor disabled** — all upstream-dead diagnoses probed live with per-slug/term evidence — **risk: low** (quarantines auto-resume if upstreams revive) — evidence: JOURNAL iterations 2-3 + round 2; e054ec7, b7b2c60, 1f30608, fc55fb8.
3. **M5 ATS timeout fix** (SOURCE_FETCH_TIMEOUT_ATS=240, per-category resolution) — recovered ~1,300 greenhouse + 175 lever + 100 workday jobs per run (+55% total haul), two consecutive clean runs — **risk: low** — evidence: JOURNAL rounds 1-2; 89fa0e0.
4. **Channels WIP (yours, kept on your order)** — friendlier delivery errors + run_log.user_id — **risk: low** — 2ee6a89, live-proven.
5. **Loop infrastructure** — hardening (gate-stamped commits, push denied), agent team (worker/integrator/scout/health), token economy + overnight throttle, Anthropic-pattern upgrades U1/U2/U3/U5 — **risk: low** (process only, no product code) — e5ca963, e9d435a, this round's commits.

**CORE files touched:** backend/src/main.py (matcher stage + run_log user_id), backend/src/api/models.py (llm fields), core/settings.py (timeouts), frontend/src/lib/types.ts (llm fields). All gated + live-verified individually.

**Survived review findings you should eyeball:** vector-index path bug (backlog 7b — root data/chroma is the LIVE index, code contradicts its docstring; fix queued, not yet landed).

**NEEDS-HUMAN queue:** see MISSIONS.md bottom — telemetry migration, DfE key, M6 scope expansion (50→46 proposal), 4 API keys, SMTP creds.

**Suggested verdict: merge-all** — every item above carries tests + live evidence; the one open risk (7b) is a queued fix, not a regression in this set.
