# Job360 Maintenance Backlog

Worked by the `/maintain` loop (one item per iteration, top TODO first).
Statuses: TODO / DOING / DONE (sha) / BLOCKED(reason). Keep newest discoveries at the right priority, not at the bottom.

## P1 — bugs and broken behavior (live evidence)

0. **DONE (6974bb6) — dashboard client sort defeats the judge's ranking.** Fixed: comparator now `(llm_fit_score ?? match_score)`, mirrors server COALESCE; vitest judge-ranking-sort.test.tsx; live-verified (test-artifacts/judge-ranking-fixed.png — fit 92 renders first). The "stale navbar email" sub-observation was a transient react-query cache on the same page session — resolved itself on fresh navigation, NOT a bug. Live screenshot (test-artifacts/matcher-badge-demo-user.png, 2026-06-10): server returns jobs ordered by COALESCE(llm_fit_score, score) — fit 92 first — but the dashboard page re-sorts client-side by match_score, putting keyword-43 "Poor fit · 20" interns above the 92-fit job. Fix: in the dashboard's sort (frontend/src/app/dashboard/page.tsx or wherever rows are sorted), use `(job.llm_fit_score ?? job.match_score)` as the primary key, keyword score as tiebreak. TDD vitest like __tests__/uses-hybrid.test.tsx. ALSO check: navbar email showed the previous user after re-login (stale auth/me react-query cache) — verify and fix invalidation on login if real.

1. **TODO — jobicy source broken: HTTP 400** from `https://jobicy.com/api/v2/remote-jobs` on every call (seen in run 0656b8c0, 2026-06-10). Check the API's current contract (params likely changed), fix the source + its mocked tests, or mark it fragile in STATUS.md and skip gracefully.
2. **TODO — jobtensor source broken: HTTP 400** from `https://jobtensor.com/ajax/search/` (same run). Scraper endpoint likely changed. Fix or downgrade to fragile.
3. **TODO — comeet ATS slugs dead: HTTP 400** for `riskified` and `lightricks` company slugs. Verify slugs against comeet's careers API, prune/replace dead slugs in `core/companies.py`.
4. **TODO — gov_apprenticeships returns non-JSON** ("Expecting value: line 1 column 1"). Endpoint may now need a key or returns HTML error page. Diagnose, fix or skip gracefully with one info log instead of 3 retry warnings.
5. **TODO — aijobs_global returns non-JSON** (same signature as above). Diagnose, fix or downgrade.
6. **TODO — JobSpy/Glassdoor 400 "location not parsed"** on every run. Either fix the location format passed to JobSpy for glassdoor or stop querying glassdoor (keep indeed) so each run stops logging 6 ERROR lines.
7. **TODO — enrichment accuracy: L1 merge prefers a weak rules seniority over LLM "unknown"** (measured: costs L1 10 points, 80%→90% if fixed). Fix `merge_rules_llm` in `backend/scripts/compare_enrichment_levels.py` to prefer "unknown"-tolerant merge (LLM unknown → keep rules ONLY if rules confidence is word-boundary-exact; else unknown), re-run the scorer, journal the new numbers. If the merged logic ships anywhere in `src/`, port the same fix there.

## P2 — matcher (funnel→judge) follow-ons

8. **TODO — re-judge policy:** verdicts are judge-once (skip_existing). Decide + implement a cheap re-judge trigger when the user's profile version changes (profile upload bumps `user_profile_versions`): clear that user's `llm_matched_at` (set NULL) so the next search re-judges. Tests for the trigger.
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
17. **TODO — repo tidy:** `test-artifacts/` screenshots accumulate at root; ensure gitignored and pruned. Check for stray experiment outputs outside `backend/data/`.

## Done

(moved here by the loop, newest first)
