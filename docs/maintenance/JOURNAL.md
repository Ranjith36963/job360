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

## 2026-06-12 ~04:55 UTC — INTEGRATOR ROUND 4 (heartbeat; P1 backlog item)

Outcome: **backlog 3a DONE (a7e6af1)** — workable mystery solved, 7 dead slugs pruned.

- Nothing to integrate (no new worker commits; worker-a correctly stopped after M4). P1 item 3a existed → not a minimal round (throttle carve-out).
- Diagnosis by live probes (all 25 slugs): the Workable API is healthy — huggingface 200 with 9 jobs; SEVEN account slugs return HTTP 404 (labelbox, adept, livinglens, legalandgeneral, vorboss, welcometothejungle, rateit) = accounts gone; the remainder are valid-but-empty boards or US-only listings the UK/remote filter correctly drops. Conclusion: 0 jobs was CORRECT behavior over a stale slug list, not a code bug.
- Fix: pruned the 7 dead slugs (evidence comment in companies.py, comeet-pattern); kept empty-but-valid boards (can repopulate). Gate: 1291 passed/3 skipped in 100.8s. Live proof: `Workable: found 0 relevant jobs across 18 companies` — one clean line, no 404 churn.
- Direct implementation by integrator (mechanical single-file list prune, "fix one-liners yourself" rule); no executor dispatch — cheaper than a Sonnet spec for a 12-line diff.
- skipped: aging applied to passed-over P1 #7 (M2's files — worker mission, not started unattended per throttle).

## 2026-06-12 ~02:55 UTC — INTEGRATOR ROUND 3 (heartbeat; first full adversarial-wave run)

Outcome: **M4 INTEGRATED (83864b2)** — docs synced to reality, 5 review findings fixed at merge.

- Heartbeat at ~23:11 found the lock held (40m, second /integrator invocation elsewhere) → exited per law. That round died WITHOUT releasing the lock or committing anything (stale 22:01 stamp, no commits) — likely the model-availability outage; lock replaced at 02:40 under the 3h staleness rule. Lesson holds: the advisory lock self-heals.
- Integration sweep: worker-a's M4 (cb350db, 7 doc files, +276/−177) merged --no-ff --no-commit to staging.
- **Adversarial waves (Upgrade 1, first real run): raw 11 → survived 5 → fixed 5.** Survivors, all confirmed with file:line by Wave 2: (1) STATUS.md still carried "hybrid not wired to HTTP routes" contradicting the same merge's README/ARCH (HIGH); (2) README bullet "8 RSS feeds" vs graph's 10; (3) slug-count: ground truth measured **264** — the old docs' 268 AND the merge's new 266 were both wrong; (4) STATUS.md:153 untouched 268; (5) ARCHITECTURE scheduler tree comment stale/incomplete. All five fixed at integration (mechanical), staged into the merge, gated, committed.
- Wave 2 killed 6 false positives (numbered-rule-per-flag not a real convention; anchored "as of a4fe829" test counts are intentional snapshots; backend/CLAUDE.md count-embedding predates the merge). The find→refute pattern worked exactly as designed: half of Wave 1 was noise.
- Gate: docs-only diff → stamp on the 1291-passing tree. No server restart needed (no code).
- New fact: live slug ground truth = 264 (post comeet-pruning). Any doc claiming 266/268 is wrong.

## 2026-06-11 ~21:30 UTC — INTEGRATOR ROUND 2 (heartbeat + owner directives)

Outcome: **M1 INTEGRATED + M5 DONE + token-economy enforced + upgrades applied.**

### A. Integration sweep — M1 (worker-a, commits 1f30608 + fc55fb8)
- Diff reviewed personally: quarantines follow the b7b2c60 jobtensor pattern exactly (canary probe, single INFO, auto-resume); comeet slugs pruned with per-slug probe evidence; glassdoor disabled with anti-bot diagnosis + explicit re-enable path. APPROVED.
- Merged --no-ff --no-commit to loop/staging; FULL gate on merged tree: **1291 passed / 3 skipped in 143.64s**; merge committed 3315fb3 (gated).
- Server RESTARTED on staging code; live run b0250268211a @2026-06-11T21:10:39Z:
```
total_found: 6064 | duration: 246.7s | errors: {}
M1 four: comeet 0, gov_apprenticeships 0, aijobs_global 0 (quiet quarantines), glassdoor absent (disabled), indeed 4
ATS run 2/2: greenhouse 1335, lever 175, workday 99, ashby 403, smartrecruiters 124
bad log lines for the four THIS run: 0 (only matches are pre-merge run 9673808f @19:36)
```
- **M1 → DONE.** Worker handoffs absorbed: STATUS.md fragile rows → added to M4's plate (worker-a now owns M4); DfE subscription key → NEEDS-HUMAN #2; rotation bundling proposal → NEEDS-HUMAN #3 (scope expansion needs owner word).

### B. M5 completed
Run 2/2 clean (above) — two consecutive clean live runs ticked. **M5 → DONE.**

### Owner token-budget audit (answered + enforced)
- Model map reported: integrator session Fable (stays — review/delete judgment is the quality gate); worker-a session inherits the Fable default unless the owner runs /model there (flagged as the #1 manual lever); all implementer subagents tonight were already Sonnet by explicit dispatch.
- ENFORCED in skills: worker rule 7+8 (all subagents model:"sonnet" explicit; mission-done = STOP, no overnight auto-claim), integrator B (overnight throttle: idle heartbeat = minimal round; no new serialized missions unattended unless P1; all dispatches Sonnet), scout/health model notes.
- Note: worker-a claimed M4 at ~20:00 (before the stop rule existed). Owner is awake and can halt that terminal if the spend isn't wanted.

### Owner MODEL POLICY encoded (supersedes economy v1, same round)
- Bound into all four loop skills: integrator=Fable (high effort only on merge/live-verify rounds, default effort on minimal rounds); worker leads=Opus (Fable only for CORE-touching missions, escalation journaled); ALL implementation+wave subagents=Sonnet explicit; scout=Sonnet; health checks=Sonnet with integrator-written verdict; clerical=Haiku-else-Sonnet; degradation=one tier down journaled, integrator floor=Opus, minimal rounds preferred over degraded judgment.
- Dispatch audit: every Agent-dispatch instruction in worker/integrator/scout/health now names a model explicitly; the integrator's dangling "as the old /maintain specified" executor reference made self-contained (Sonnet executor spec inline). No accidental-inherit paths remain in loop skills.
- BEFORE→AFTER map: worker-a session Fable-by-default-inherit → Opus; scout/health Fable-session-implicit → Sonnet; subagents already Sonnet (kept); integrator Fable (kept, effort-split added). Estimated Fable-token reduction: the worker session was the dominant unattended Fable spender (continuous grinding vs the integrator's episodic rounds) — moving it plus scout/health off Fable cuts roughly 50–70% of daily Fable tokens, leaving Fable only for integrator judgment turns; the effort-split trims the integrator's own idle-round spend further. ACTION OWNER MUST TAKE: run /model in the worker-a terminal (session model can't be changed from here).

### Anthropic-patterns upgrades applied (owner order, file at repo root)
- U2 TELEMETRY.jsonl created + seeded retroactively for tonight's rounds. U5 REVIEW-PACKET.md generated for the morning merge. U3 CORE list added to MISSIONS header + worker rule. U1 review waves wired into worker step 4 + integrator sweep step 3 (cost-controlled: waves only for multi-file or CORE-touching diffs; all wave agents Sonnet).
- C1 campaign: NOT created yet — M1 just closed this round; C1 becomes eligible next owner-awake round per the apply-order rule and the overnight throttle.

### Round 1 addendum 2 — owner conditions applied retroactively (prove-before-delete)
- **WIP #2 CONDITIONAL KEEP — conditions verified as satisfied** (already committed as 2ee6a89 before the condition arrived): diff personally reviewed = exactly the audited change (dispatcher "delivery failed - check the channel URL and credentials" ×2 + matching test + run_log.user_id kwarg); full suite green 3× since commit (1284, 1285, 1285); LIVE proof user_id populated in run c7345ff349d8. Isolated commit — trivially revertable if the owner doesn't recognize it.
- **Worktree-branch deletions audited retroactively**: generator (8b005ae) PROVEN merged into origin/main → safe. reviewer (d5047bf) had UNIQUE commits and was force-deleted without proof — caught by the owner's gate, local branch ref RESTORED (`git branch worktree-reviewer d5047bf`); listed for owner. Lesson: prove-then-delete, even for "obviously stale" things.
- **11 remote worktree-agent branches proven**: 10 fully merged into origin/main (deletion command handed to owner); 1 with unique commits kept: origin/worktree-agent-a9516ffa (f4df828 "wip(step-3): pre-relocation snapshot"). origin/worktree-generator proven merged (deletable); origin/worktree-reviewer has the unique d5047bf (keep).
- **M6 (jobtensor 50→49 rotation): owner GO** — integrator-serialized, next round's mission. **M7 (OpenAPI→TS codegen): owner APPROVED with conditions** — research comparison (openapi-typescript vs alternatives) on zero-runtime-deps / build-step fit / maintenance burden, 3-line rationale journaled BEFORE implementation; adversarial review; full suite + live verify.

### Round 1 addendum — NEAR-MISS during pruning (owner-ordered "stray root data/chroma" deletion)
Deletion FAILED with a file lock — investigated before retrying, and the "stray" is the LIVE vector index: `vector_index.py:19` uses `parents[3]` (repo root), contradicting its own docstring (`backend/data/chroma`). Root `data/chroma` = 5 files/0.6MB = the only copy of the 92 embeddings; `backend/data/chroma` is EMPTY. Deleting it would have silently degraded hybrid to keyword (graceful fallback hides the loss). Deletion ABORTED; new backlog item 7b (fix path to parents[2] + move index + live-verify). LESSON for all agents: a file lock during cleanup is a STOP signal, not an obstacle — something live owns that file; identify the owner before retrying.

## 2026-06-12 ~07:11 UTC — INTEGRATOR ROUND 5 (heartbeat) — MINIMAL
minimal round — board state: nothing to integrate (no worker commits since M4); no actionable P1 (item 7 = M2 worker files, aged not started per throttle; 7b is P2); M2/M3/M8 OPEN for workers; M6/M7/C1 await owner-awake. No code touched, no commit. Lock released.

## 2026-06-12 ~09:11 UTC — INTEGRATOR ROUND 6 (heartbeat) — policy edit
Was a minimal round (nothing to integrate, no actionable P1). Owner directive mid-round: move the integrator seat OFF Fable to **Opus 4.8** to cut burn (owner already set the session default). Encoded: integrator skill MODEL POLICY now Opus 4.8 (degradation floor unchanged — it starts at Opus); worker skill notes Fable is not used in the loop at all. No product code touched; docs/skills only. Board otherwise unchanged: M2/M3/M8 open for workers, M6/M7/C1 await owner-awake, NEEDS-HUMAN queue intact.

## 2026-06-12 ~11:11 UTC — INTEGRATOR ROUND 7 (heartbeat) — MINIMAL
minimal round — board state: no DONE-PENDING-INTEGRATION missions, no new worker commits since 3685677, no actionable P1 (backlog 3a closed last round; 7/7b are worker/P2). M2/M3/M8 OPEN for workers; M6/M7/C1 await owner-awake. No code, no commit (docs-exempt). Lock released. Loop now fully on Opus+Sonnet+Haiku — idle ticks are near-free.

## 2026-06-12 ~14:00 UTC — INTEGRATOR ROUND 8 (owner-directed) — M6 DONE
Owner awake, expanded scope + GO: bundled rotation 50→46, removing all 4 proven-dead quarantined sources (jobtensor, comeet, gov_apprenticeships, aijobs_global).
- Surface map traced by integrator first (5 rule-#8/#13 surfaces + SOURCE_INSTANCE_COUNT + companies.py COMEET list + company_discovery ATSProbe + 4 doc files + per-source test funcs + 4 source files). Sonnet executor did the mechanical removal from a precise checklist; slice tests 105 green.
- INTEGRATOR REVIEW caught a surface the executor's slice-set missed: test_domain_classifier.py imported 3 of the 4 deleted classes (would fail full gate) — fixed by integrator. This is exactly why the full-suite gate + review exist.
- CORE files touched (main.py, settings.py) → adversarial waves run. Wave 1 (R1 conventions + R3 bugs): 2 raw findings. R3 clean. Verified directly (token-thrift vs spawning Wave 2 agents): R1-1 CONFIRMED — backend/scripts/discover_companies.py still mapped "comeet"→"COMEET_COMPANIES" (deleted); fixed. R1-2 REFUTED — the "104→268" line is Batch-3 phase HISTORY (records that phase's state) and already carries the executor's appended M6 current-state note.
- Gate: 1272 passed / 3 skipped (down 19 from 1291 = the removed sources' tests, expected). Commit 1e709f7 (19 files, +65/−1226).
- LIVE VERIFY: server restarted (commits don't exist until restart); GET /api/sources = 46 sources, the 4 dead confirmed absent, all 6 lookalikes (aijobs/aijobs_ai/nhs_jobs/nhs_jobs_xml/hackernews/hn_jobs) present.
- New fact: source count is now 46 registry / 45 unique classes (indeed+glassdoor still alias JobSpySource). Any doc/test asserting 50/49 is stale.
- Backlog 16b CLOSED by this. M6 DONE.

## 2026-06-12 ~15:30 UTC — INTEGRATOR ROUND 9 (owner-directed) — M7 RESEARCH (no implementation)
Owner directed: research M7 codegen, recommend with 3-line rationale, do NOT implement.
- Grounded in our stack first: types.ts = 389 lines/35 hand-mirrored types (the drift surface); api.ts hand-written client we like; TanStack Query v5 present; zod/RHF NOT installed (arrive with M3).
- Researched openapi-typescript vs Hey API vs Orval (WebSearch + fetched the openapi-ts 2026 roadmap). DECISIVE FACT: openapi-fetch/openapi-react-query RUNTIME helpers go to maintenance mode in 2026, but the openapi-typescript TYPES generator does NOT (maintenance increasing, 8.0 roadmap) — and the maintenance caveat doesn't touch us since we don't use those runtime helpers.
- RECOMMENDATION: openapi-typescript (types-only) — zero runtime deps, one-CLI-line build, minimal change (keep api.ts). Documented owner fork: Hey API only if they want a generated SDK+zod platform and will replace api.ts. Written to docs/maintenance/M7-codegen-research.md; M7 status = RESEARCH-DONE, AWAITING-OWNER-APPROVAL.
- No code, no deps installed, nothing implemented (owner gate). Research+docs only.

## 2026-06-12 ~16:00 UTC — INTEGRATOR ROUND 10 (heartbeat) — board hygiene
No integration pending, no new worker commits. The "actionable P1" list was STALE: items 3/4/5/6 described sources that M1 disabled (glassdoor, fc55fb8) and M6 deleted (comeet, gov_apprenticeships, aijobs_global, 1e709f7). Marked all four DONE with their resolving commits so future rounds don't chase dead sources. No code touched.
- After cleanup the only open P1 is 7b (vector_index.py path bug). NOT auto-taken: (a) it's Pillar-2 semantic-search code → owner's hands-off rule; (b) the fix MOVES the only live copy of 92 embeddings — the exact data op that nearly caused loss at round 1. Needs explicit owner OK before an integrator round attempts it. Flagged to owner.
- #7 enrichment-merge is also Pillar-2 + M2 worker territory — not integrator work.
- Net: board is now honest (4 stale P1s closed); no actionable non-Pillar-2 P1 remains for the loop. Future heartbeats → minimal until a worker ships or owner directs.

## 2026-06-12 ~17:00 UTC — INTEGRATOR ROUND 11 (owner-directed) — M7 DONE
Owner approved openapi-typescript (types-only). Implemented under CORE rules (types.ts is CORE).
- Integrator set up infra: installed openapi-typescript (devDep), wrote scripts/gen-api-types.sh (offline schema export via app.openapi() — no running server needed → CI-safe), npm gen:types + check:types-drift, and wired the drift check into agent-gate.sh (a backend API-model change OR frontend change that desyncs frontend/src/lib/api-types.ts now FAILS the gate).
- Mapped 52 backend schemas vs 35 frontend types: 26 alias 1:1, 1 name-bridge (RecentRunsResponse→RunsListResponse), 7 genuinely frontend-only kept hand-written. Sonnet executor did the types.ts migration (net −249 lines) + got the frontend gate green.
- ADVERSARIAL WAVES (CORE, 2 lenses R1+R3, Wave-2 verified directly): real findings, all resolved —
  * 3 call-site drift fixes the swap surfaced: ActionRequest.notes is required (dashboard + JobDetailClient), NotificationLedgerEntry.sent_at/error_message nullable (notifications page). Real API/frontend disagreements the hand-mirror hid.
  * skill_tiers LATENT CRASH (high): backend ProfileResponse.skill_tiers = dict[str,list[str]] defaulting to {}; profile page did skill_tiers.primary.length/.map unguarded — type-checked (index signature) but crashes at runtime for any user with no extracted tiers. M7's honest types exposed it. Fixed with (.. ?? []) defaults on all 3 tiers.
  * REFUTED-as-fix / filed as M7a: api.ts hand-mirrors SourceHealthEntry/Response/Channel, but the frontend intentionally tightens backend `str` fields (health, channel_type) into literal unions — aliasing as-is would DOWNGRADE precision. Not pure drift; needs Omit-and-intersect or backend enums. Backlog M7a.
- Gate: backend 1272 + drift check + frontend 65/type-check/lint, all green (commit 53e2020).
- LIVE VERIFY: profile page renders (skill_tiers fix, no crash) + dashboard renders (JobResponse generated type + notes fix); only console error is the documented benign dark-mode hydration badge. Screenshots test-artifacts/m7-{profile,dashboard}-render.png.
- New durable fact for CLAUDE.md candidates: frontend types are now GENERATED — never hand-edit frontend/src/lib/api-types.ts or types.ts aliases; after any backend API-model change run `npm run gen:types` (the gate enforces it). M7 DONE.

## 2026-06-12 ~19:00 UTC — INTEGRATOR ROUND 12 (owner-directed) — M3 (slice) + M8 DONE
Owner directed "do m3 and m8". Ran sequentially on the main checkout (disjoint files; parallel writes would collide).

### M3 (slice) — commit 53e2020... see actual: committed separately as the M3 commit
Installed zod@4 + react-hook-form@7 + @hookform/resolvers@5. Sonnet executor migrated the 4 auth forms with input fields (login/register/forgot-password/reset-password; verify-email has none) to RHF+zod, and added V-04 upload caps (10MB + PDF/DOCX) on both CV + LinkedIn routes with frontend pre-check + tests.
- INTEGRATOR FIX (security): the executor read the whole file into memory before the size check — a memory-DoS hole on a security feature. Changed both endpoints to bounded `read(10MB+1)` so a malicious 1GB body can't be materialised before rejection. Upload tests still 12/12.
- Gate green (backend 1277 + frontend 74/74 + drift). LIVE: login form blocks invalid input with a message (test-artifacts/m3-login-validation.png) — though it's the browser's NATIVE validation firing first (inputs kept required/type=email); zod is wired+vitest-tested but needs `noValidate` to be the visible layer. Filed M3-rem.
- DEFERRED transparently (M3-rem backlog): 3 settings forms, kanban a11y (C-07), noValidate, stale "50 sources" footer.

### M8 — DONE
Sonnet executor un-skipped all 13 `_PRE_STEP_1_5_SCAFFOLDING_DEBT` tests in test_main.py and made them run OFFLINE. The documented blocker was JobSpySource's sync `requests` (live Indeed, ~32 min) — stubbed via autouse fixture patching JobSpySource.fetch_jobs→[]. Plus a profile-stub fixture (so jobs clear the score gate) and a circuit-breaker-registry reset (test isolation), and 10 missing URL mocks.
- INTEGRATOR REVIEW verified the one judgment call: 2 tests wrap run_search in `patch(classify_user_domain→set())` to exercise the include-all-sources path (domain filtering legitimately narrows to 38 for a tech profile). Confirmed the patch wraps the run_search CALL (not just the assert) — legitimate, documented, NOT a weakened test.
- PROVEN OFFLINE: timed run 14 passed in 11.1s (a single live JobSpy call alone is 30s+). Canonical suite still 1277 green. No src/ changes, no real run_search bug masked.
- FOLLOW-UP M8a: un-ignore test_main.py everywhere now that it's fast+offline, so the E2E tests are gated and can't rot.
- New durable fact: test_main.py is now OFFLINE (JobSpy stubbed) — the old "hits live Indeed, ~32 min" warning in CLAUDE.md/STATUS is stale once M8a lands.
