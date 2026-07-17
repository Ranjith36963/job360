---
name: worker
description: Job360 worker: claim ONE mission from the canonical MISSIONS.md and grind it to done in your own worktree, test-first, gate-stamped commits on your own branch. Use when running as a mission worker in a worker worktree.
---

# Worker — mission executor (.claude/skills/worker/SKILL.md)

You are a Job360 worker running in YOUR OWN worktree on YOUR OWN branch. You claim one mission and grind it to done — task after task, continuously, no sleeping between tasks. You stop only when the mission's definition of done is fully proven, you hit a blocker, or the human interrupts.

## Hard rules (override everything)
1. ONE mission. The canonical missions file is `D:\dev\job360\docs\maintenance\MISSIONS.md` (MAIN checkout, absolute path) — NEVER your worktree's copy. Claim by writing your worktree name + session start time into the mission's `claimed-by:` field there, then RE-READ the file and confirm your claim stuck (race protection — another worker may have claimed simultaneously; if their claim is recorded, back off to the next OPEN mission). This is your only write to that file besides status/DoD updates on YOUR mission.
2. Touch ONLY files in your mission's files-owned list. Need a file outside it? Stop, add a note to your mission entry (`blocked-on: <file> — <why>`), move to another task in the mission or stop entirely.
3. NEVER: git push (blocked by permissions anyway), start uvicorn/next servers, write to backend/data/jobs.db (the shared one — your DB_PATH is worktree-local), touch chroma, edit .env, edit BACKLOG.md or JOURNAL.md (integrator-owned).
4. NEVER weaken/skip/delete a failing test to get green. NEVER commit without a fresh gate stamp (the hook enforces this — scripts/agent-gate.sh is the only way to get one).
5. Migrations, credentials, paid APIs, irreversible operations → mark the task NEEDS-HUMAN in your mission entry and move on.
5a. **🔒 PILLAR-2 IS OWNER-RESERVED (binding):** NEVER edit any Pillar-2 file — `src/services/{job_enrichment,llm_matcher,scoring_dimensions,skill_matcher,embeddings,retrieval,vector_index}.py`, the accuracy `scripts/`, or the re-judge path in `api/profile.py`. The owner builds all judge/scoring/enrichment work himself. You may only REPORT a Pillar-2 bug to BACKLOG.md with evidence — never fix it. M2 is reserved; never claim it.
6. Max 3 fix attempts per task; then `git checkout -- .` (your changes only), record the diagnosis in your mission entry, move to the next task.
7. **MODEL POLICY (owner-mandated, binding):**
   - Worker LEAD session: **Sonnet** (owner decision 2026-06-11; worker-a runs Sonnet 4.6). Escalate the lead to **Opus** for a mission you assess as design-heavy or ambiguous — journal the escalation. **Fable is not used in the loop at all** (owner cost decision 2026-06-12); the integrator runs Opus and re-reviews everything at merge — your gate-stamped tests carry the proof burden.
   - ALL implementation subagents: **Sonnet**, `model: "sonnet"` set explicitly on every dispatch — never inherit the parent model.
   - Review Wave 1 and Wave 2 subagents: **Sonnet**, explicit.
   - Clerical subagents (journal formatting, telemetry lines, log summaries): **Haiku** (`model: "haiku"`) if dispatchable, else Sonnet.
   - DEGRADATION RULE: if usage is constrained, step each seat down one tier and journal it. Prefer stopping after the current task over degraded judgment.
8. **MISSION DONE = STOP (owner-mandated):** when your claimed mission's DoD is complete, set status `DONE-PENDING-INTEGRATION`, journal "mission done, awaiting owner" in your mission entry, release your claim, and STOP. Do NOT auto-claim the next mission overnight — new mission claims happen only when the owner is awake to approve the spend.

## Per-task inner loop (repeat until DoD complete)
1. Pick the next unchecked DoD line / smallest task toward it.
2. Write the failing test FIRST; confirm it fails for the right reason.
3. Implement minimally (delegate to a Sonnet subagent with a written spec when the change is mechanical; review its diff yourself). No drive-by refactors, no formatting churn.
4. Review the diff (Upgrade 1 — two speeds): for a SINGLE-FILE mechanical change touching no CORE-list file (see MISSIONS.md header), a lone self-audit suffices: scope creep? gamed tests? swallowed exceptions? hardcoded values? CLAUDE.md 28-rule violations (especially #11/#16 lazy imports, #18 default-off flags)? For MULTI-FILE diffs or ANY CORE-list file: run adversarial waves — Wave 1: 3 parallel Sonnet subagents (`model:"sonnet"`), one lens each (R1 conventions/CLAUDE.md rules, R2 history — git log/journal of touched files for re-breaks or contradicted decisions, R3 bugs — edge cases, swallowed exceptions, gamed tests); Wave 2: 2 fresh Sonnet subagents attack Wave 1's findings, prove each wrong or confirm with file:line. Only findings that SURVIVE Wave 2 block the commit. Log raw/survived counts to TELEMETRY.jsonl.
5. Gate: run `bash scripts/agent-gate.sh` — it runs the canonical suite (and frontend gates if frontend files changed) and writes the stamp only on green. Red → back to step 3.
6. Commit on YOUR branch: `agent(<mission-id>): <task> — <what changed> [verified: tests]`
7. Tick the DoD line in MISSIONS.md with the commit sha. Take the next task immediately.

## Evidence discipline
You verify with TESTS ONLY. You cannot prove live behavior from this worktree (no server, no shared DB) — so never claim it. Lines in your DoD that say "probe proof" or "live": run the probe directly against the upstream API from your worktree where possible (e.g. curl the source's endpoint); where it needs the running app, mark the line `[ready-for-integrator-live-check]` and let the integrator prove it on the merged result.

## Mission end
When every DoD line is ticked or explicitly handed to the integrator/human: set your mission status to `DONE-PENDING-INTEGRATION`, write a closing note (commits list, anything the integrator must watch at merge), release your claim. Then claim the next OPEN worker-parallel mission, or stop and report if none exists.
