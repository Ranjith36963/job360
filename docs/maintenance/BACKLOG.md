# Job360 Maintenance Backlog

Worked by the `/maintain` loop (one item per round, top TODO first).
Statuses: TODO / DOING / DONE (sha) / BLOCKED(reason) / NEEDS-HUMAN / DEAD.
Aging: every round increments `skipped: N` on items it passes over; at `skipped: 3` an item must be taken or escalated to NEEDS-HUMAN with justification. Keep newest discoveries at the right priority, not at the bottom.

## P1 — bugs and broken behavior (live evidence)

0. **DONE (6974bb6) — dashboard client sort defeats the judge's ranking.** Fixed: comparator now `(llm_fit_score ?? match_score)`, mirrors server COALESCE; vitest judge-ranking-sort.test.tsx; live-verified (test-artifacts/judge-ranking-fixed.png — fit 92 renders first). The "stale navbar email" sub-observation was a transient react-query cache on the same page session — resolved itself on fresh navigation, NOT a bug. Live screenshot (test-artifacts/matcher-badge-demo-user.png, 2026-06-10): server returns jobs ordered by COALESCE(llm_fit_score, score) — fit 92 first — but the dashboard page re-sorts client-side by match_score, putting keyword-43 "Poor fit · 20" interns above the 92-fit job. Fix: in the dashboard's sort (frontend/src/app/dashboard/page.tsx or wherever rows are sorted), use `(job.llm_fit_score ?? job.match_score)` as the primary key, keyword score as tiebreak. TDD vitest like __tests__/uses-hybrid.test.tsx. ALSO check: navbar email showed the previous user after re-login (stale auth/me react-query cache) — verify and fix invalidation on login if real.

1. **DONE (e054ec7) — jobicy source broken: HTTP 400.** Root cause: Jobicy added validation rejecting `tag` values under 3 chars; we sent `tag=ai`. Dropped the tag param (industry filter + downstream scorer cover relevance). Regression test asserts no tag param is sent. Live-proven: 6 jobs fetched post-fix.
2. **DONE (b7b2c60) — jobtensor source broken: HTTP 400.** Upstream pivoted to a JS-rendered German app; /ajax/search/ removed (400 for any request), UK page is an empty shell. Quarantined: dead AJAX call dropped (saves 3 retries/run), HTML probe kept as canary, STATUS.md fragile row added. Live-proven: one request, one INFO line, 0 jobs, no warnings.
3a. **DONE (a7e6af1) — workable 0-jobs diagnosed: not a bug.** All 25 slugs probed live: API healthy (huggingface 200/9 jobs); 7 accounts are HTTP 404 (gone) → pruned; the rest are empty boards or US-only results correctly dropped by the UK/remote filter. Live-proven: 18 companies, no 404 churn, single clean INFO line.

3. **DONE (1e709f7, M6) — comeet** removed entirely in the source rotation (was: dead slugs HTTP 400). No longer in the registry — nothing left to fix.
4. **DONE (1e709f7, M6) — gov_apprenticeships** removed entirely in the source rotation (was: non-JSON / v1 API retired). Gone from the registry. (NEEDS-HUMAN #2 still tracks the optional DfE-key revival if you ever want it back.)
5. **DONE (1e709f7, M6) — aijobs_global** removed entirely in the source rotation (was: non-JSON / board abandoned). Gone from the registry.
6. **DONE (fc55fb8, M1) — JobSpy/Glassdoor 400** resolved by disabling glassdoor querying (JobSpySource default sites = ["indeed"]); the anti-bot 403 was term-independent. Live-confirmed: no glassdoor error lines in run b0250268211a. Re-enable path documented in indeed.py.
7. **TODO `skipped: 1` — enrichment accuracy: L1 merge prefers a weak rules seniority over LLM "unknown"** (measured: costs L1 10 points, 80%→90% if fixed). Fix `merge_rules_llm` in `backend/scripts/compare_enrichment_levels.py` to prefer "unknown"-tolerant merge (LLM unknown → keep rules ONLY if rules confidence is word-boundary-exact; else unknown), re-run the scorer, journal the new numbers. If the merged logic ships anywhere in `src/`, port the same fix there.

## P3 — M3 / M8 follow-ons

M3-rem. **TODO — M3 remaining: settings forms (account/channels/notifications) RHF+zod, kanban a11y (C-07), and add `noValidate` to the 4 migrated auth forms** so zod's custom messages show instead of the browser's native validation (currently native pre-empts zod in the browser; zod is wired + vitest-tested but not user-visible). Also: footer hardcodes "50 sources" — now 46 after M6. `skipped: 0`
M8a. ~~**DONE — un-ignore test_main.py**~~ Removed `--ignore=tests/test_main.py` from Makefile (6 targets), agent-gate.sh, agentic-loop/hardening.md, root CLAUDE.md (3 spots), backend/CLAUDE.md, STATUS.md, and updated the stale comment block in test_main.py itself. Full suite: 1,333p / 3s / 0f confirmed green. `skipped: 0`

## P3 — frontend type hygiene (M7 follow-ons)

M7a. **TODO — `lib/api.ts` still hand-mirrors 3 API types** (SourceHealthEntry, SourceHealthResponse, Channel). NOT pure drift: the frontend intentionally tightens backend `str` fields into literal unions (`health: "ok"|"warning"|"critical"`, `channel_type: "email"|"slack"|...`) that the backend schema declares as plain `string` — aliasing as-is would DOWNGRADE precision. Correct fix: either (a) `Omit<Schemas["SourceHealthResponse"], "..."> & {tightened fields}` to kill drift on the mirror fields while keeping the unions, OR (b) tighten the BACKEND to enums (channel_type, health) so the generated types are precise, then plain-alias. Found by M7 adversarial waves; judged out-of-M7-scope (api.ts, not types.ts). `skipped: 0`

## P2 — engine correctness

7b. **🔒 OWNER-RESERVED (Pillar-2 hands-off, 2026-06-12) — vector index lives at the WRONG path (near-data-loss found at integrator round 1).** vector_index.py is Pillar-2; agents report only, owner fixes. Evidence below kept for the owner. `src/services/vector_index.py:19` computes `Path(__file__).parents[3]/data/chroma` = the REPO ROOT — but its own docstring, CLAUDE.md, and CODEBASE_REPORT all say `backend/data/chroma/`. Root `data/chroma` holds the ONLY live index (92 embeddings); `backend/data/chroma` is empty; a hygiene prune nearly deleted the live one. Fix: change to `parents[2]` (backend/data/chroma), MOVE the existing index dir while the backend is stopped, restart, live-verify hybrid mode still reorders (non-fallback log line), and ensure root `data/` stays deleted. One integrator round. `skipped: 0`

## P2 — matcher (funnel→judge) follow-ons

8. **DONE (2026-06-13, migration 0018 + rescore.py + profile.py trigger) — re-judge policy:** re-score on profile-version change implemented. When a user saves a new profile, the system detects the content change, clears all LLM verdicts (`clear_user_verdicts`), and re-scores the full 30-day catalog in the background (`rescore_user_feed`). `user_feed` rows are now stamped with `profile_version`. LLM re-judge fires only if `MATCHER_ENABLED=true`; keyword re-score always runs.
9. **TODO — matcher telemetry:** count judged/skipped/failed per run into `run_log` extras (mirrors enrichment telemetry) so morning review can see judge health without grepping logs.
10. **TODO — Level 6 experiment:** one combined LLM call returning facts + fit (salary still from source), measured head-to-head against L1+L4 in the harness; journal accuracy + latency + calls saved. Decision input for replacing two calls with one.

## P3 — Step-3 carry-overs (known tech debt)

11. **TODO — V-04: CV upload size cap + MIME allowlist** on `POST /api/profile` (and LinkedIn upload). Reject >10MB and non-PDF/DOCX with 422 + tests.
12. **TODO — V-01..V-03: RHF + zod form validation** on settings + profile forms (frontend). Split per page when picked.
13. **TODO — C-07: @dnd-kit keyboard a11y** on KanbanBoard.
14. **TODO — V-05: OpenAPI → TS codegen** so `lib/types.ts` stops drifting from `api/models.py` (the llm_* fields were mirrored by hand again).

## P4 — docs and hygiene

15. **TODO — document the matcher batch:** STATUS.md (current phase + new flag), docs/IMPLEMENTATION_LOG.md (batch entry: migration 0017, llm_matcher service, pipeline stage, API fields, badge, measured 18/18 in 89.8s), CLAUDE.md phase summary + `MATCHER_ENABLED`/`MATCHER_THRESHOLD`/`MATCHER_MAX_JOBS` in the env-var table.
16. **TODO — README/ARCHITECTURE sweep:** ensure the three-engine + judge picture (keyword funnel → enrichment facts → semantic → LLM judge) is described once, correctly, with the measured accuracy numbers; remove stale references.
16b. **DONE (1e709f7) — bundled source rotation 50→46.** Removed jobtensor + comeet + gov_apprenticeships + aijobs_global (owner expanded scope from jobtensor-only). 5 surfaces + docs + 4 file deletions; gate 1272; live /api/sources=46. Superseded the original jobtensor-only plan below.

16b-orig. ~~remove jobtensor for real (5-surface rotation)~~ Quarantined in b7b2c60; upstream is gone for good. Full removal per rules #8/#13: SOURCE_REGISTRY, _build_sources, RATE_LIMITS, test_cli count/set, test_api == N checks (50→49), plus CLAUDE.md/STATUS.md/ARCHITECTURE.md count references. Do as ONE deliberate iteration; consider bundling with any other dead-source removals found by then.

17. **TODO — repo tidy:** `test-artifacts/` screenshots accumulate at root; ensure gitignored and pruned. Check for stray experiment outputs outside `backend/data/`.

## Done

(moved here by the loop, newest first)
