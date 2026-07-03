# Job360 — Documentation Index

The map of everything under `docs/` (plus the load-bearing docs at the repo root).
Start here to find the right doc fast.

> **Regenerated 2026-06-21** after a docs audit (dead/stale files removed, drift fixed).
> **Legend:** 🟢 current ground truth · 📘 stable reference · 🗄️ historical (append-only / not kept current)

---

## Start here — pick your intent

| I want to… | Read |
|---|---|
| **Understand the project fast** | [`../CLAUDE.md`](../CLAUDE.md) → [`../STATUS.md`](../STATUS.md) |
| **Pick up where work left off** | [`IMPLEMENTATION_LOG.md`](IMPLEMENTATION_LOG.md) (read first) → [`../STATUS.md`](../STATUS.md) |
| **Run it locally** | [`../backend/README.md`](../backend/README.md) · [`../frontend/README.md`](../frontend/README.md) |
| **Contribute / open a PR** | [`../CONTRIBUTING.md`](../CONTRIBUTING.md) |
| **Debug a runtime problem** | [`troubleshooting.md`](troubleshooting.md) → [`pillars/runbook.md`](pillars/runbook.md) |
| **Understand the architecture deeply** | [`pillars/`](pillars/README.md) (authoritative) → [`../ARCHITECTURE.md`](../ARCHITECTURE.md) |
| **Know what's verified working** | [`CHECKLIST_KANBAN.md`](CHECKLIST_KANBAN.md) |

---

## 🟢 Current state — ground truth

| Doc | What it is |
|---|---|
| [`../STATUS.md`](../STATUS.md) | Project phase, what's done/next, known issues. **The single current ground truth.** |
| [`IMPLEMENTATION_LOG.md`](IMPLEMENTATION_LOG.md) | Append-only batch-by-batch completion log (Pillars 1–3 + steps). Read first when resuming. |
| [`CHECKLIST_KANBAN.md`](CHECKLIST_KANBAN.md) | End-to-end live verification + structural audit — what's proven working, with evidence. |

## 📘 Architecture & operations

| Doc | What it is |
|---|---|
| [`STACK.md`](STACK.md) | Tech stack layer-by-layer: what we have vs what to add (Postgres, Docker, Stripe, etc.) + costs. |
| [`STACK_checklist.md`](STACK_checklist.md) | Code-audited checklist: 27 built ✅ (with file proof) vs 21 to build ❌. |
| [`UPGRADE_PLAN.md`](UPGRADE_PLAN.md) | Phase 1–3 execution plan (Postgres → deploy → monitoring): TDD + multi-agent, money + keys per phase. |
| [`../CLAUDE.md`](../CLAUDE.md) | Load-bearing architecture doc: data flow, module map, **28 hard rules**, scoring, env vars. |
| [`../ARCHITECTURE.md`](../ARCHITECTURE.md) | Deep technical reference: directory tree, DB schema, data-flow diagrams, dependencies. |
| [`pillars/`](pillars/README.md) | **Authoritative** per-pillar deep reference (code-verified). See below. |
| [`troubleshooting.md`](troubleshooting.md) | Dev-environment FAQ: port conflicts, SQLite locks, missing LLM keys, Redis on Windows. |
| [`pillars/runbook.md`](pillars/runbook.md) | "I see a problem at 2am" operational guide — SQL queries + CLI commands. |
| [`pillars/glossary.md`](pillars/glossary.md) | Plain-English definitions of every domain term. |

### The three pillars (`pillars/`)

| Doc | Covers |
|---|---|
| [`pillars/README.md`](pillars/README.md) | Pillar overview + connection diagram (entry point). |
| [`pillars/01-user-pillar.md`](pillars/01-user-pillar.md) | Auth, profile, feed, dashboard, notifications. |
| [`pillars/02-search-and-match-engine.md`](pillars/02-search-and-match-engine.md) | The 6-stage pipeline: prefilter → scoring → dedup → enrich → store. |
| [`pillars/03-job-providers.md`](pillars/03-job-providers.md) | 46 source classes / 47 registry keys, `BaseJobSource`, ATS catalog. |

## 📘 Product & strategy

| Doc | What it is |
|---|---|
| [`PRD.md`](PRD.md) | Product requirements + vision. |
| [`MONETIZATION_GAPS.md`](MONETIZATION_GAPS.md) | What's missing to charge money — payments, paywall, hosting, legal (code-verified gap analysis). |
| [`raw_feedback_loop.md`](raw_feedback_loop.md) | Design: turn the hardcoded skill/company/domain/location lists into self-growing data via a gated LLM feedback loop (kills the tech/UK ceiling). |
| [`peruser_cv_coverletter.md`](peruser_cv_coverletter.md) | Design: per-job AI-tailored CV + cover letter that learns from your edits (per-user 2-layer learning + guardrails). ~80% built. |
| [`plans/2026-06-21-free-premium-plans.md`](plans/2026-06-21-free-premium-plans.md) | Free/Premium tier design (not yet built). |
| [`References.md`](References.md) | Source-of-truth list of external references and research links. |

## 📘 Engine evaluation

| Doc | What it is |
|---|---|
| [`engine_eval_report.md`](engine_eval_report.md) | Canonical ablation results (Run 8, n=100, bootstrap CIs) — drives engine selection. |
| [`evaluation_report.md`](evaluation_report.md) | 10-gate production-readiness rubric (score banner notes it predates Step 3 — needs re-eval). |

## Roadmap & plans

| Doc | Status | What it is |
|---|---|---|
| [`ExecutionOrder.md`](ExecutionOrder.md) | 🟢 | Seam-by-seam integration order; **Steps 4–6 (ops hardening) still pending.** |
| [`plans/batch-2-decisions.md`](plans/batch-2-decisions.md) | 📘 | **Irreversible architectural choices** (ARQ, Apprise, polling, SQLite, session cookies). |
| [`plans/batch-1-plan.md`](plans/batch-1-plan.md) · [`batch-2-plan.md`](plans/batch-2-plan.md) · [`batch-3-plan.md`](plans/batch-3-plan.md) | 🗄️ | Pillar 3 batch plans (shipped — kept as the log's linked history). |
| `plans/batch-3.5*.md` | 🗄️ | Stabilisation sub-batches (IDOR fix, profile storage, conditional-cache pilot, test cleanup). |
| [`step_3_plan.md`](step_3_plan.md) | 🗄️ | Step 3 endpoints + Settings UI — **completed** (closed at `origin/main 7194d0e`). |
| [`superpowers/`](superpowers/) | 🗄️ | Skill-driven plan + spec artefacts (channels/notifications overhaul). |

## 🗄️ Reviews (`reviews/`)

Permanent reviewer verdicts for merged Pillar-3 batches — cited by `IMPLEMENTATION_LOG.md`:
[`batch-1-review.md`](reviews/batch-1-review.md) · [`batch-2-review.md`](reviews/batch-2-review.md) · [`batch-3-review.md`](reviews/batch-3-review.md) · [`batch-3.5-review.md`](reviews/batch-3.5-review.md)

## 🗄️ Research (`research/`)

Background research that `IMPLEMENTATION_LOG.md` bridges to the shipped code (kept as canonical source material):
[`pillar_1_report.md`](research/pillar_1_report.md) · [`pillar_2_report.md`](research/pillar_2_report.md) · [`pillar_3_report.md`](research/pillar_3_report.md) · [`pillar_3_batch_1`](research/pillar_3_batch_1.md)–[`4`](research/pillar_3_batch_4.md)

## 🤖 Autonomous maintenance loop (`maintenance/`)

**Live working files** for the worker/integrator/scout/health agent loop — not historical, actively read/written:

| Doc | Role |
|---|---|
| [`maintenance/MISSIONS.md`](maintenance/MISSIONS.md) | **Canonical** mission list (the loop's task queue). |
| [`maintenance/BACKLOG.md`](maintenance/BACKLOG.md) | Deferred work. |
| [`maintenance/JOURNAL.md`](maintenance/JOURNAL.md) | Running activity log. |
| [`maintenance/STATUS-DAILY.md`](maintenance/STATUS-DAILY.md) | Daily health report (GREEN/AMBER/RED). |
| [`maintenance/HEALTH-SCHEDULE.md`](maintenance/HEALTH-SCHEDULE.md) | Health-check cadence. |
| [`maintenance/REVIEW-PACKET.md`](maintenance/REVIEW-PACKET.md) | Integrator review bundle. |
| [`maintenance/M7-codegen-research.md`](maintenance/M7-codegen-research.md) | OpenAPI→TS codegen research (decision pending). |

## 🛠️ Known debt

- [`mypy_backlog.md`](mypy_backlog.md) — catalogued mypy strict-mode errors + chip-away order.

## 🗄️ Archive (`_archive/`)

Superseded plans/progress logs from past pillars — **not kept current**, retained only because
`IMPLEMENTATION_LOG.md` and a few tests link them: completed step plans (Step 0/1/1.5/2),
Pillar 1/2 plans + progress, the `CurrentStatus.md` re-audit, and `batch_prompts.md`.

---

> **Conventions.** `IMPLEMENTATION_LOG.md` is **append-only** — never edit past entries; append a
> revert note instead. `pillars/` is the authoritative architecture reference (supersedes the older
> `ARCHITECTURE.md` where they differ). Code is the proof, not docs — verify counts/claims against
> source before trusting any doc.
