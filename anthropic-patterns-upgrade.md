# Anthropic-Patterns Upgrade — apply AFTER Phase 3 is installed and one integrator round has run clean

This patches the installed kit (worker / integrator / health skills + adds two new artifacts) to mirror the five production patterns Anthropic uses internally. Apply as edits to the existing skills — do not rewrite them. Everything here keeps the existing hard rules (gate stamp, no push, NEEDS-HUMAN walls) intact.

---

## Upgrade 1 — Adversarial review waves (Cherny pattern)
**Where:** worker skill, step 4 (self-audit) and integrator skill, integration sweep step 3.

Replace the single self-audit with a two-wave review:

- **Wave 1 (find problems):** spawn 3 parallel Sonnet subagents against the diff, each with ONE lens:
  R1 conventions — violations of CLAUDE.md's 28 rules, style, lazy-import rules (#11/#16)
  R2 history — read git log/journal for the touched files; does this change re-break something previously fixed or contradict a documented decision?
  R3 bugs — edge cases, error handling, swallowed exceptions, gamed/weakened tests
- **Wave 2 (kill false positives):** spawn 2 fresh Sonnet subagents whose ONLY job is to attack Wave 1's findings — for each finding, prove it wrong or confirm it with a file:line reference. Only findings that SURVIVE Wave 2 block the commit.

Rationale: a model grading its own output is too generous, and a single reviewer over-reports. Findings that survive an adversarial pass are nearly all real. Cost control: waves run only when the diff touches >1 file or any file on the CORE list (Upgrade 3); single-file mechanical changes keep the existing lone self-audit.

## Upgrade 2 — Instrument everything (TELEMETRY.jsonl)
**Where:** new file `docs/harness/maintenance/TELEMETRY.jsonl`; worker + integrator skills append one JSON line per unit of work.

Schema (one line per task/round):
```json
{"ts":"...","agent":"worker-a|integrator|scout|health","mission":"M1","task":"comeet slugs",
 "outcome":"DONE|FAILED_REVERTED|BLOCKED|NEEDS_HUMAN","attempts":1,
 "gate_runs":2,"gate_failures":1,"review_findings_raw":4,"review_findings_survived":1,
 "wall_minutes":23,"commit":"sha|null","notes":"one line"}
```

Health skill gains a weekly section computed from this file: first-attempt success rate (Anthropic's RL team baseline: ~1/3 for fully-autonomous — if yours is far below, missions are scoped too big; far above, too small), gate-failure rate per agent, reverts per week, NEEDS-HUMAN aging. This is how you see drift in numbers before you feel it in the product.

## Upgrade 3 — Edges vs core: two autonomy speeds
**Where:** new section in MISSIONS.md header + worker skill rule.

Define the CORE list (changes here always get full adversarial review + integrator E2E flavor, never bundled with other changes in one commit):
```
backend/migrations/**  backend/src/core/database.py  backend/src/api/auth.py
backend/src/main.py    backend/src/api/models.py     core/settings.py
frontend/src/lib/types.ts
```
Everything else is EDGE: workers run at full speed (auto-accept equivalent), checkpoint by committing small and often, and rely on cheap revert. Anthropic's rule: full autonomy on the product's edges, never on core business logic. Their measured trade: peripheral features ~70% autonomous with frequent checkpoints so reverting is painless. Your 3-strikes-revert already implements the revert half; this adds the speed split.

## Upgrade 4 — Cleanup campaigns (the 800-fix pattern)
**Where:** new mission template in MISSIONS.md.

A CAMPAIGN is a mission defined by a metric, not a task list:
```
## C1 — Error-budget campaign: source error lines
metric: error-level lines per pipeline run attributable to enabled sources
baseline: <measured at campaign start>   target: 0   owner: worker (EDGE files only)
loop: pick the single most frequent remaining error pattern → fix → gate → commit → re-measure → repeat
exit: target hit for 3 consecutive fresh runs, or remaining patterns are all NEEDS-HUMAN/upstream-dead
```
This is the shape behind Anthropic's famous result — one engineer pointed Claude at a persistent error class, it shipped 800 small fixes and cut the error rate 1000x. The insight: aim autonomous agents at closed-loop painstaking cleanup with a measurable number, not at speculative features. Your log analysis (198× rate-limits, 168× HTTP 500, 138× non-JSON…) is a ready-made first campaign once M1's known fixes land.

## Upgrade 5 — Review packet (make the human gate fast)
**Where:** integrator skill, end of every round that lands anything on staging.

Generate/refresh `docs/harness/maintenance/REVIEW-PACKET.md`:
```
# Staging review packet — <date>
Since your last merge to main:
1. <mission/task> — what changed (2 lines) — risk: low/med/high — evidence: <journal anchor>
2. ...
CORE files touched: <list or NONE>
Survived review findings you should eyeball: <list or NONE>
NEEDS-HUMAN queue: <current list>
Suggested verdict: merge-all | merge-except(<items>) | hold(<why>)
```
Anthropic's stated lesson: at high autonomy, human review becomes THE bottleneck, so invest in making review focused. Your 15 minutes should be spent on the 2 risky items, not reconstructing what happened from raw diffs. The packet is a recommendation — the merge decision stays yours.

---

## What is deliberately NOT copied (honesty section)
- Their scale (80%+ of merged code AI-authored, lab-size CI farms, dedicated harness teams) — not replicable solo, and not the goal.
- Fully autonomous merge-to-production — Anthropic keeps human architects/auditors in the loop; so do we, at the staging→main gate.
- Multi-hour Mythos-class unattended runs — your token windows and one machine cap session length; the worktree + journal design already makes interruption cheap, which is the same property achieved differently.

## Apply order
1. Confirm Phase 3 complete + one clean integrator round in the journal.
2. Add Upgrades 2 and 5 first (pure additions, zero risk).
3. Add Upgrade 3 (CORE list) — it only tightens, never loosens.
4. Add Upgrade 1 wiring to worker + integrator.
5. Create campaign C1 only after M1 is DONE (otherwise they fight over the same files).
Gate + commit each upgrade like any other change. The system upgrades itself under its own law.
