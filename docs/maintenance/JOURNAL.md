# Maintenance Journal (append-only)

## 2026-06-10 ~21:50 — bootstrap (manual, by the orchestrator)

- Created `/maintain` skill + this backlog/journal pair; loop armed every 2h.
- Context for future iterations: branch `fix/per-user-search-and-scoring-gate` carries the funnel→judge matcher (commits a925f42..d801f78 + 76f6ca7 compat fix). Live-verified: 18/18 jobs judged in 89.8s for demo user e34aeb69e9bf4680bd143e1f3756140a; verdicts persisted to user_feed; API returns llm_* fields ranked by COALESCE; canonical suite 1281 passed/3 skipped; frontend 64/64.
- Source-health evidence for P1 items came from run_uuid 0656b8c0-d333-4e5d-9133-ec8ed17928d9 (2026-06-10 21:37).
- Gemini free tier is quota-dead (429); provider chain degrades to Groq/Cerebras — expected, not a bug.
- Live verify finding (added as backlog P1 #0): dashboard client sort uses match_score and overrides the server's judge ranking; badge itself renders correctly (red "AI: Poor fit · 20" on intern cards). Screenshot: test-artifacts/matcher-badge-demo-user.png.
- Measured cost of one judged search: 18 LLM calls, 89.8s wall (concurrency 3, Groq/Cerebras), zero failures.

## 2026-06-11 ~00:55 — iteration 1 (manual /maintain fire)

- Item #0 DONE (6974bb6): dashboard client sort now `(llm_fit_score ?? match_score)`. TDD red→green (judge-ranking-sort.test.tsx); gates: 65 unit / type-check / lint all clean; live screenshot proves fit-92 card first.
- Dirty-tree note: tree carries ANOTHER session's WIP (main.py `user_id=` kwarg into the notify call, dispatcher friendlier error string + matching test edit) + a 0-byte `backend/None` junk file. Left strictly untouched; item #0 was frontend-only so no conflict. Future iterations: if that WIP is still uncommitted AND the picked item touches backend, skip to the next non-conflicting item instead of exiting outright — or exit if everything conflicts.
- Backend canonical gate NOT run this iteration (frontend-only change; backend tree had foreign WIP that would muddy attribution).
- loop.md (user note, repo root): the user's statement of the agentic-loop philosophy this skill implements; suggests adding a fresh-context adversarial audit pass — consider as a P4 backlog item.
- Next hint: item #1 (jobicy HTTP 400) is the top TODO; it's backend — check whether the foreign WIP is committed first.

## 2026-06-11 ~02:50 — iteration 2 (cron fire)

- Item #1 DONE (e054ec7): jobicy 400 root-caused by live probe — API now requires tag length 3-50, we sent `tag=ai`. Dropped the param; TDD regression test (no tag sent); test_sources.py 82/82; live fetch returned 6 jobs.
- Canonical baseline ON THE DIRTY TREE (incl. foreign WIP): 1281 passed / 3 skipped — foreign WIP is self-consistent. Post-fix gate: targeted test_sources.py only (change isolated to one source's params; count untouched so the 5-surface rule doesn't trigger).
- Gotcha for future iterations: `python -m src.cli run --source X --dry-run` EXITS(2) without `data/user_profile.json` (CLI loads the file profile, not the web user_profiles row). For a one-source live proof, call the source class directly with an aiohttp session instead.
- Foreign WIP still uncommitted (same 3 files + backend/None). Next top TODO: item #2 jobtensor 400 (backend scraper, different files — workable the same way).

## 2026-06-11 ~04:55 — iteration 3 (cron fire)

- Item #2 DONE (b7b2c60): jobtensor diagnosed by live probes — upstream pivoted to a JS-rendered German app; /ajax/search/ 400s for ANY params, UK page is a 7.9KB shell (no var context, no /uk/ links). Decision: quarantine (drop dead AJAX call, keep HTML canary, STATUS.md fragile row) rather than 5-surface removal mid-loop. test_sources.py 84/84; live run: one request, one INFO, 0 jobs, zero warnings.
- New backlog item 16b: full 5-surface jobtensor removal as a deliberate rotation batch.
- Foreign WIP unchanged. Next top TODO: item #3 comeet dead slugs (riskified, lightricks).

## 2026-06-11 (cron fire, post-hardening) — round BLOCKED, by design

- Outcome: BLOCKED — (a) rule 5: tree dirty with the 3-file human WIP (backend/src/main.py, services/channels/dispatcher.py, tests/test_channels_dispatcher.py — decision on the owner's desk); (b) the owner ordered a STOP after the hardening proof, pending Phase-3 approval (install team skills, retire /maintain, repoint this cron to /integrator).
- No item taken, no code touched, no commit. Lock not held.
- State for the next round: hardening is LIVE and proven (commit e5ca963) — gate-stamped commits enforced by PreToolUse hook, git push permission-denied. Any future round's commit requires `bash scripts/agent-gate.sh` first, staged-then-gated, no edits after.
- This entry is intentionally left uncommitted (docs/maintenance/** is exempt loop memory; the integrator's first round will sweep it).
- Later cron fire, same session: state unchanged (WIP still uncommitted, Phase-3 approval still pending) → BLOCKED again, no action. The loop stays correctly parked until the owner moves.
- After 6 consecutive identical BLOCKED fires, the orchestrator DELETED cron c96d6940 (owner's standing token-thrift rule; the loop was hard-deadlocked on owner decisions, so fires were pure cost). To resume: resolve the 3-file WIP, approve Phase 3, and re-arm — the new cron should point at /integrator per the installed plan (agentic-loop/README-SETUP.md step 4).

## 2026-06-11 ~18:45 UTC — INTEGRATOR ROUND 1 (manual, owner-witnessed)

Outcome: **DONE — M5 slices 1+2 (root cause + fix + live run 1/2)**, plus Phase-3 install housekeeping.

Owner decisions executed first: hardening VERDICT PASS; WIP KEPT and committed (2ee6a89, gated 1284-pass); team installed (e9d435a: worker/integrator/scout/health skills + canonical MISSIONS.md, /maintain retired); worker-a worktree + own venv built on branch agent/m1-sources (M1 not yet claimed — owner launches that terminal).

### A. Integration sweep
No missions DONE-PENDING-INTEGRATION; agent/m1-sources has no new commits; loop/staging not yet needed. → Section B.

### B. Maintenance: M5 (serialized, integrator-only) — ATS sweeps vs the 60s timeout

Root cause MEASURED (unbounded direct sweeps, concurrent, 2026-06-11):
```
workable:   0 jobs   in  41.2s   (completes; its 0 is NOT timeout — new candidate below)
ashby:      403 jobs in  46.1s   (fits alone; slips past 60s under pipeline contention)
greenhouse: 1331 jobs in 138.2s  (the smoking gun — 60s cap discarded 1,331 jobs EVERY run)
```

Fix (commit 89fa0e0, gate: 1285 passed/3 skipped in 115.18s): `SOURCE_FETCH_TIMEOUT_ATS` (default 240s, env-tunable) in settings.py; `TieredScheduler.resolve_fetch_timeout(source)` — ats category → ATS ceiling, others → 60s, explicit ctor arg still wins (test contract). TDD: test_resolve_fetch_timeout_per_category red→green; scheduler+breaker slice 15/15; ruff clean.

LIVE VERIFY (server restarted BOTH times — commits don't exist until restart; run c7345ff349d8, run_log @ 2026-06-11T18:40:04Z):
```
total_found: 6180 (prev run: 3994, +55%) | new: 2 | duration: 243.4s | errors: {}  <- ZERO
ATS: greenhouse 1331 (was 0), lever 175 (was 0), workday 101 (was 0), ashby 403,
     smartrecruiters 124, pinpoint 3 | workable/recruitee/personio/rippling/comeet 0 (see below)
jobicy: 6 (e054ec7 confirmed live) | run_log.user_id populated (2ee6a89 confirmed live)
```
M5 DoD: root-cause line DONE; fix line: live run 1/2 clean — needs ONE more consecutive clean run (next heartbeat) to tick fully.

### New facts / candidates for the backlog
- workable returns 0 in 41s WITHOUT timing out → separate cause (query/slug config) — added as backlog candidate.
- recruitee/personio/rippling/comeet still 0: comeet is backlog #3 (dead slugs); others unverified — scout material.
- GOTCHA promoted: NEVER chain `agent-gate.sh && git commit` in one shell command — the PreToolUse hook evaluates the whole command BEFORE the gate inside it runs. Gate and commit must be separate tool calls.
- Housekeeping: stale generator/reviewer worktrees + 11 worktree-agent remote-tracking refs pruned; stray root data/chroma removed; .claude/gate-stamp gitignored; backend/None + bash.exe.stackdump deleted.
- BLOCKED-ON-OWNER: anthropic-patterns-upgrade.md (directive step 5) does not exist anywhere in the repo — cannot apply until the owner provides it.

Next round (cron, /integrator): M5 live run 2/2 → tick DoD; then integration sweep for worker-a's M1 commits if any; else next serialized/backlog item.
