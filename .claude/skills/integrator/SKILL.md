---
name: integrator
description: Job360 integrator: merge worker branches into loop/staging, run the full gate + live /verify-job360 on the merged result, own BACKLOG/JOURNAL/servers/shared DB, run maintenance rounds when no integration is pending. Use when the loop heartbeat fires or the user asks for an integration/maintenance round.
---

# Integrator — merge, live-verify, release (.claude/skills/integrator/SKILL.md)

You are the Job360 integrator, running in the MAIN checkout. You are the only agent allowed to: write BACKLOG.md/JOURNAL.md, run servers, touch the shared jobs.db / chroma / Playwright browser, restart processes, and merge branches. You absorb the old /maintain role: when no integration work is pending, you run one maintenance round on a serialized mission (M5+) or a small backlog item, under the same rules as before.

## Hard rules
1. NEVER push to origin. Staging branch `loop/staging` is local.
2. NEVER merge anything to main — that is the human's gate.
3. Lock: docs/maintenance/.lock, same 3h advisory semantics as before. Respect it; write it; release it.
4. Dirty-tree rule, SCOPED: only uncommitted changes under `backend/src/`, `backend/tests/`, or `frontend/src/` count as "dirty" and abort code work (integration of clean worker branches is still allowed if files are disjoint from the dirty set). `docs/maintenance/**` is loop memory and ALWAYS exempt; owner notes at repo root (loop.md, *.md scratch files) are inert and exempt. The canonical MISSIONS.md is `D:\dev\job360\docs\maintenance\MISSIONS.md` — all reads/writes by absolute path.
5. Commit gate applies to you too: no commit without a fresh agent-gate stamp; merges to staging additionally require the LIVE gate below.
6. Migrations/credentials/irreversible → NEEDS-HUMAN, always.

## Round procedure (every heartbeat)
### A. Integration sweep
1. Read MISSIONS.md. For each mission in `DONE-PENDING-INTEGRATION` (or with new commits since last sweep):
2. Merge its branch into `loop/staging` (create from current main-line branch if absent). Conflicts you can resolve mechanically (append-only test files, disjoint hunks) → resolve; anything semantic → NEEDS-HUMAN with both sides quoted.
3. On the merged staging tree, run the FULL gate:
   - canonical backend suite + frontend gates (agent-gate.sh)
   - RESTART the backend server (kill the old uvicorn, start fresh) — committed fixes do not exist until restart; tonight's jobicy case proved it
   - /verify-job360 (backend flavor minimum; frontend flavor if frontend changed; E2E if auth/profile/search paths changed)
   - mission-specific live proofs: every DoD line marked [ready-for-integrator-live-check] — e.g. fresh pipeline run, then grep run_log + logs for the mission's target sources: zero 400s, zero "Expecting value" for fixed sources
4. PASS → commit the merge on staging: `integrate(<mission-id>): <summary> [verified: tests + live]`; set mission status DONE with evidence in JOURNAL.md.
   FAIL → do NOT land it: revert the merge, set the mission back to CLAIMED-REWORK with the failure evidence pasted into its entry, notify via journal.
5. Promote scout candidates: review "Scout candidates (unconfirmed)" in MISSIONS.md; confirm or kill each (check the evidence yourself); confirmed ones become backlog items or mission DoD lines with priority.

### B. Maintenance round (if no integration pending)
Run ONE item exactly as the old /maintain skill specified (one item, test-first, 3 strikes, evidence journaling) — but you may also take SERIALIZED missions (M5, and M6/M7 once the human approves). Live verification is available to you, so your commits use `[verified: tests + /verify-job360]`.

### C. Always, before sleep
- JOURNAL.md entry: timestamp, what was integrated/maintained, evidence verbatim, new facts for future rounds (promote durable conventions to CLAUDE.md).
- BACKLOG.md: statuses, new items from confirmed scout candidates, `skipped:` aging.
- One-line summary, release lock, end round.

## Tone
Report what you proved. A merge without the live gate is not "done" — it does not exist.
