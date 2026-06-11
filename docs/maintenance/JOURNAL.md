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
