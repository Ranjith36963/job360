# Job360 — Documentation Index
<!-- doc: LIVING -->

A complete map of every doc under `docs/` (plus the load-bearing docs at the
repo root). Every link below resolves; nothing is left out.

Pruned 2026-09-12 (slice 8) to the minimum set. The shipped per-slice plans
(`docs/plans/`) and the ADR folder (`docs/decisions/`) were deleted, not
archived: a shipped plan describes a decision the code has already made, and a
second description of a decision is the thing that drifts. The reasoning lives
in git log; the binding rules live in `.claude/skills/hard-rules/SKILL.md` and
`product/product_design_rules.md`.

---

## Start here — pick your intent

| I want to… | Read |
|---|---|
| **Know what we are building (and not)** | [`product/VISION.md`](product/VISION.md) — the 2026-09-03 decisions; wins over every older product doc |
| **Understand the project fast** | [`../CLAUDE.md`](../CLAUDE.md) → [`../STATUS.md`](../STATUS.md) |
| **Understand the architecture deeply** | [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — the one architecture reference |
| **Look up a count, a route, an endpoint** | Measure it from the code (`ls`, `grep -n '@router\.' backend/src/api/routes/*.py`); counts are never written down |
| **Run it locally** | [`../backend/README.md`](../backend/README.md) · [`../frontend/README.md`](../frontend/README.md) |
| **Contribute / open a PR** | [`../CONTRIBUTING.md`](../CONTRIBUTING.md) |
| **Know the rules I must not break** | [`../.claude/skills/hard-rules/SKILL.md`](../.claude/skills/hard-rules/SKILL.md) |
| **Debug a runtime problem** | [`product/troubleshooting.md`](product/troubleshooting.md) |
| **Deploy / run a backup / restore** | [`product/DEPLOY.md`](product/DEPLOY.md) · [`product/RUNBOOK-backups.md`](product/RUNBOOK-backups.md) |
| **Respond to a security incident** | [`product/BREACH-RUNBOOK.md`](product/BREACH-RUNBOOK.md) · [`../SECURITY.md`](../SECURITY.md) |

---

## Product

| Doc | What it is |
|---|---|
| [`product/VISION.md`](product/VISION.md) | **The mission.** Agent thinks, Job360 remembers; never source/rank; build order; the decision log. |
| [`product/BROWSER_AGENT.md`](product/BROWSER_AGENT.md) | The Job360 browser agent: what we agreed (2026-10-19, decision 29) and the build plan. |
| [`product/assistant-flow-gates.md`](product/assistant-flow-gates.md) | Every gate that stops the assistant (submit, outreach, pause, caps, browser routes) and the owner's settings decisions, with file:line. |
| [`product/product_design_rules.md`](product/product_design_rules.md) | Owner-set product rules. The mission rules are M1–M5 in [`hard-rules`](../.claude/skills/hard-rules/SKILL.md). |

---

## Operations

| Doc | What it is |
|---|---|
| [`product/DEPLOY.md`](product/DEPLOY.md) | How a merge to `main` reaches production on Railway. |
| [`product/RUNBOOK-backups.md`](product/RUNBOOK-backups.md) | Postgres backup/restore procedure. |
| [`product/BREACH-RUNBOOK.md`](product/BREACH-RUNBOOK.md) | Security-incident response steps. |
| [`product/hunt-kit-smoke-2026-10-20.md`](product/hunt-kit-smoke-2026-10-20.md) | Snapshot: the live hunt-kit smoke test from the owner's PC (coverage table, warnings, 8 problems with fixes). |
| [`product/troubleshooting.md`](product/troubleshooting.md) | Dev-environment FAQ: port conflicts, Postgres connection/schema errors in tests, a CV that uploads but leaves the profile thin. |

---

## Harness (the doc-maintenance framework itself)

| Doc | What it is |
|---|---|
| [`harness/maintenance/DOC-MAINTENANCE.md`](harness/maintenance/DOC-MAINTENANCE.md) | The framework: doc taxonomy, the tripwire/fixer/auditor tiers, deletion authority. Read this before archiving or deleting any doc. |
| [`harness/FAILURE_CATALOG.md`](harness/FAILURE_CATALOG.md) | Every bug class we have hit: symptom, root cause, the machine guard (antidote) for it, and what that guard does NOT cover yet (OWED). Add an entry every time something breaks. |
| [`harness/maintenance/PARKED.md`](harness/maintenance/PARKED.md) | The "code is behind the doc" list — intentions found in docs that are not yet implemented. |

---

> **Conventions.** `../ARCHITECTURE.md` is the authoritative architecture reference for how
> the system fits together; counts and routes are
> measured from the code, never written down. Code is the proof, not docs — verify any claim
> against source before trusting it. A doc's own type header
> (`<!-- doc: LIVING|PLAN|LOG|REFERENCE|FROZEN|GENERATED -->`) says how it should be read;
> see `harness/maintenance/DOC-MAINTENANCE.md` §1.
