# Job360 — Documentation Index
<!-- doc: LIVING -->

A curated map of the docs worth reading under `docs/` (plus the load-bearing
docs at the repo root). Start here to find the right doc fast.

> **NOT a complete listing.** Measured 2026-08-25: 105 `*.md` files are tracked
> under `docs/`, and this index links **41** of them; the other **64** are mostly
> `_archive/` and dated `harness/` records, which are deliberately left out —
> but the gap means "not in this index" does NOT mean "does not exist". Use
> `git ls-files "docs/*.md"` when you need the real list.
>
> This file used to call itself "the map of everything", which is the kind of
> claim that stops people looking further. It then said "links 49" — that was
> every unique `.md` link on the page, 8 of which resolve OUTSIDE `docs/` and so
> are not among the 105: four repo-root files (`CLAUDE.md`, `ARCHITECTURE.md`,
> `CONTRIBUTING.md`, `STATUS.md`) and four under `backend/`, `frontend/` and
> `harness/eval/`. Count only the links that land under `docs/`.

> **Regenerated 2026-06-21** after a docs audit (dead/stale files removed, drift fixed).
> **Legend:** 🟢 current ground truth · 📘 stable reference · 🗄️ historical (append-only / not kept current)

---

## Start here — pick your intent

| I want to… | Read |
|---|---|
| **Know what we are building (and not)** | [`product/VISION.md`](product/VISION.md) — the 2026-09-03 decisions; wins over every older product doc |
| **Understand the project fast** | [`../CLAUDE.md`](../CLAUDE.md) → [`../STATUS.md`](../STATUS.md) |
| **Pick up where work left off** | [`IMPLEMENTATION_LOG.md`](harness/IMPLEMENTATION_LOG.md) (read first) → [`../STATUS.md`](../STATUS.md) |
| **Run it locally** | [`../backend/README.md`](../backend/README.md) · [`../frontend/README.md`](../frontend/README.md) |
| **Contribute / open a PR** | [`../CONTRIBUTING.md`](../CONTRIBUTING.md) |
| **Debug a runtime problem** | [`troubleshooting.md`](product/troubleshooting.md) |
| **Know what to build next** | [`plans/2026-09-03-mission-roadmap.md`](plans/2026-09-03-mission-roadmap.md) — slices 0–6, one issue each |
| **Understand the architecture deeply** | [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — the only architecture reference; the old pillar manuals are archived |
| **Know what's verified working** | [`CHECKLIST_KANBAN.md`](harness/CHECKLIST_KANBAN.md) |

---

## 🟢 Current state — ground truth

| Doc | What it is |
|---|---|
| [`../STATUS.md`](../STATUS.md) | Project phase, what's done/next, known issues. **The single current ground truth.** |
| [`IMPLEMENTATION_LOG.md`](harness/IMPLEMENTATION_LOG.md) | Append-only batch-by-batch completion log (Pillars 1–3 + steps). Read first when resuming. |
| [`CHECKLIST_KANBAN.md`](harness/CHECKLIST_KANBAN.md) | End-to-end live verification + structural audit — what's proven working, with evidence. |

## 📘 Architecture & operations

| Doc | What it is |
|---|---|
| [`llm_prod.md`](harness/llm_prod.md) | Production LLM provider choice (researched 2026-07-08): why the 429s, provider comparison, GDPR split, recommended chain. TL;DR: Gemini 2.5 Flash single / +DeepSeek for cheap batch. |
| [`UPGRADE_PLAN.md`](harness/UPGRADE_PLAN.md) | Phase 1–3 execution plan (Postgres → deploy → monitoring): TDD + multi-agent, money + keys per phase. |
| [`../CLAUDE.md`](../CLAUDE.md) | Load-bearing architecture doc: data flow, module map, **31 hard rules**, scoring, env vars. |
| [`../ARCHITECTURE.md`](../ARCHITECTURE.md) | Deep technical reference: directory tree, DB schema, data-flow diagrams, dependencies. |
| [`product/VISION.md`](product/VISION.md) | **The mission.** Agent thinks, Job360 remembers; never source/rank; build order; decision log Q1–Q18. |
| [`plans/2026-09-03-mission-roadmap.md`](plans/2026-09-03-mission-roadmap.md) | The work list that follows from VISION.md — one issue per slice (#479–#483). |
| [`pillars/README.md`](product/pillars/README.md) | Pointer to the archived pillar manuals (the sourcing-era architecture reference). Archived 2026-09-05, slice 5. |
| [`troubleshooting.md`](product/troubleshooting.md) | Dev-environment FAQ: port conflicts, Postgres connection/schema errors in tests, missing LLM keys, Redis on Windows. |

### The three pillars — archived 2026-09-05 (slice 5, #483)

The pillar manuals, glossary and runbook described the job-search-and-score
product deleted in slice 5. Kept as history, FROZEN, pointing at `VISION.md`:
[`_archive/sourcing-era/01-user-pillar.md`](_archive/sourcing-era/01-user-pillar.md) ·
[`_archive/sourcing-era/02-search-and-match-engine.md`](_archive/sourcing-era/02-search-and-match-engine.md) ·
[`_archive/sourcing-era/03-job-providers.md`](_archive/sourcing-era/03-job-providers.md) ·
[`_archive/sourcing-era/glossary.md`](_archive/sourcing-era/glossary.md) ·
[`_archive/sourcing-era/runbook.md`](_archive/sourcing-era/runbook.md) ·
[`_archive/sourcing-era/CATALOG_STATE.md`](_archive/sourcing-era/CATALOG_STATE.md) ·
[`_archive/sourcing-era/SHELF_FILL_MEASURED.md`](_archive/sourcing-era/SHELF_FILL_MEASURED.md) ·
[`_archive/sourcing-era/UNIVERSAL_SHELF.md`](_archive/sourcing-era/UNIVERSAL_SHELF.md)

## 📘 Product & strategy

| Doc | What it is |
|---|---|
| [`product_design_rules.md`](product/product_design_rules.md) | The owner's product rules — 4 (user brings the job), 5 (agent thinks, Job360 remembers), 6 (free, pull, consent-first) are the mission. |
| [`PRD.md`](product/PRD.md) | 🗄️ Sourcing-era product requirements (superseded by VISION.md). |
| [`MONETIZATION_GAPS.md`](product/MONETIZATION_GAPS.md) | 🗄️ Sourcing-era paywall gap analysis — free for seekers now (rule 6). |
| [`feedback_loops_map.md`](harness/feedback_loops_map.md) | The whole-tool map: 15 honest places to add feedback loops (signal + gate), across Pillars 1–3 + features, all answering to the master outcome loop. |
| [`raw_feedback_loop.md`](harness/raw_feedback_loop.md) | Design: turn the hardcoded skill/company/domain/location lists into self-growing data via a gated LLM feedback loop (kills the tech/UK ceiling). |
| [`peruser_cv_coverletter.md`](product/peruser_cv_coverletter.md) | Design: per-job AI-tailored CV + cover letter that learns from your edits (per-user 2-layer learning + guardrails). ~80% built. |
| [`post_application.md`](product/post_application.md) | 🗄️ "After you apply" co-pilot design — the agent does this now (rule 5); we store what happened. |
| [`plans/2026-06-21-free-premium-plans.md`](product/plans/2026-06-21-free-premium-plans.md) | 🗄️ Free/Premium tier design — not planned (rule 6). |
| [`plans/2026-09-03-oauth-mcp/`](plans/2026-09-03-oauth-mcp/) | Slice 1 of the pivot (shipped, PR #488): OAuth 2.1 authorization server for MCP clients (intent / spec with security section / plan + diff-vs-plan). |
| [`plans/2026-09-04-application-spine/`](plans/2026-09-04-application-spine/) | Slice 2 of the pivot (merged to main): one Application object, an append-only typed event log, versioned artifacts, the stored (never computed) fit verdict, `whats_new` / `export_history`, the applications home (intent / spec with security section / plan). |
| [`plans/2026-09-05-delete-sourcing-era/`](plans/2026-09-05-delete-sourcing-era/) | Slice 5 of the pivot (#483, draft PR): delete the search/score pipeline, its tables, workflows and docs; what survives and why (intent / spec / plan). |
| [`References.md`](harness/References.md) | Source-of-truth list of external references and research links. |

## 📘 Engine evaluation

| Doc | What it is |
|---|---|
| [`engine_eval_report.md`](../harness/eval/engine_eval_report.md) | Canonical ablation results (Run 8, n=100, bootstrap CIs) — drives engine selection. |
| [`evaluation_report.md`](../harness/eval/evaluation_report.md) | 10-gate production-readiness rubric (score banner notes it predates Step 3 — needs re-eval). |

## Roadmap & plans

| Doc | Status | What it is |
|---|---|---|
| [`ExecutionOrder.md`](harness/ExecutionOrder.md) | 🟢 | Seam-by-seam integration order; **Steps 4–6 (ops hardening) still pending.** |
| [`plans/batch-2-decisions.md`](product/plans/batch-2-decisions.md) | 📘 | **Irreversible architectural choices** (ARQ, Apprise, polling, SQLite, session cookies). |
| [`plans/batch-1-plan.md`](product/plans/batch-1-plan.md) · [`batch-2-plan.md`](product/plans/batch-2-plan.md) · [`batch-3-plan.md`](product/plans/batch-3-plan.md) | 🗄️ | Pillar 3 batch plans (shipped — kept as the log's linked history). |
| `plans/batch-3.5*.md` | 🗄️ | Stabilisation sub-batches (IDOR fix, profile storage, conditional-cache pilot, test cleanup). |
| [`step_3_plan.md`](harness/step_3_plan.md) | 🗄️ | Step 3 endpoints + Settings UI — **completed** (closed at `origin/main 7194d0e`). |
| [`superpowers/`](superpowers/) | 🗄️ | Skill-driven plan + spec artefacts (channels/notifications overhaul). |

## 🗄️ Reviews (`reviews/`)

Permanent reviewer verdicts for merged Pillar-3 batches — cited by `IMPLEMENTATION_LOG.md`:
[`batch-1-review.md`](harness/reviews/batch-1-review.md) · [`batch-2-review.md`](harness/reviews/batch-2-review.md) · [`batch-3-review.md`](harness/reviews/batch-3-review.md) · [`batch-3.5-review.md`](harness/reviews/batch-3.5-review.md)

## 🗄️ Research (`research/`)

Background research that `IMPLEMENTATION_LOG.md` bridges to the shipped code (kept as canonical source material):
[`pillar_1_report.md`](product/research/pillar_1_report.md) · [`pillar_2_report.md`](product/research/pillar_2_report.md) · [`pillar_3_report.md`](product/research/pillar_3_report.md) · [`pillar_3_batch_1`](product/research/pillar_3_batch_1.md)–[`4`](product/research/pillar_3_batch_4.md)

## 🤖 Autonomous maintenance loop (`maintenance/`)

**Live working files** for the worker/integrator/scout/health agent loop — not historical, actively read/written:

| Doc | Role |
|---|---|
| [`maintenance/MISSIONS.md`](harness/maintenance/MISSIONS.md) | **Canonical** mission list (the loop's task queue). |
| [`maintenance/BACKLOG.md`](harness/maintenance/BACKLOG.md) | Deferred work. |
| [`maintenance/JOURNAL.md`](harness/maintenance/JOURNAL.md) | Running activity log. |
| [`maintenance/STATUS-DAILY.md`](harness/maintenance/STATUS-DAILY.md) | Daily health report (GREEN/AMBER/RED). |
| [`maintenance/HEALTH-SCHEDULE.md`](harness/maintenance/HEALTH-SCHEDULE.md) | Health-check cadence. |
| [`maintenance/REVIEW-PACKET.md`](harness/maintenance/REVIEW-PACKET.md) | Integrator review bundle. |
| [`maintenance/M7-codegen-research.md`](harness/maintenance/M7-codegen-research.md) | OpenAPI→TS codegen research (decision pending). |

## 🛠️ Known debt


## 🗄️ Archive (`_archive/`)

Superseded plans and progress logs from past pillars — **not kept current**, retained only
because `IMPLEMENTATION_LOG.md` and a few tests link them.

**Still here** (matches [`archive/README.md`](archive/README.md)):
`pillar1_progress.md`, `pillar2_progress.md`, `pillar2_implementation_plan.md`,
`CurrentStatus.md`, `one-shot-scripts/`.

**Deleted 2026-08-25:** the four step plans (Step 0/1/1.5/2) and `batch_prompts.md` — their
output is merged and `docs/harness/IMPLEMENTATION_LOG.md` is the narrative record.
Retrievable with `git show d3cbceb:docs/_archive/<name>`.

---

> **Conventions.** `IMPLEMENTATION_LOG.md` is **append-only** — never edit past entries; append a
> revert note instead. `../ARCHITECTURE.md` is the authoritative architecture reference; `pillars/`
> now only points at the archived sourcing-era manuals. Code is the proof, not docs — verify
> counts/claims against source before trusting any doc.
