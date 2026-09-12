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
| **Look up a count, a route, an endpoint** | [`GENERATED.md`](GENERATED.md) — machine-written from the code, never by hand |
| **Run it locally** | [`../backend/README.md`](../backend/README.md) · [`../frontend/README.md`](../frontend/README.md) |
| **Contribute / open a PR** | [`../CONTRIBUTING.md`](../CONTRIBUTING.md) |
| **Know the rules I must not break** | [`../.claude/skills/hard-rules/SKILL.md`](../.claude/skills/hard-rules/SKILL.md) |
| **Debug a runtime problem** | [`product/troubleshooting.md`](product/troubleshooting.md) |
| **Know what to build next** | [`../STATUS.md`](../STATUS.md) — what is live, what is next, known issues |
| **Deploy / run a backup / restore** | [`product/DEPLOY.md`](product/DEPLOY.md) · [`product/RUNBOOK-backups.md`](product/RUNBOOK-backups.md) |
| **Respond to a security incident** | [`product/BREACH-RUNBOOK.md`](product/BREACH-RUNBOOK.md) · [`../SECURITY.md`](../SECURITY.md) |

---

## Product

| Doc | What it is |
|---|---|
| [`product/VISION.md`](product/VISION.md) | **The mission.** Agent thinks, Job360 remembers; never source/rank; build order; the decision log through 2026-09-11. |
| [`product/product_design_rules.md`](product/product_design_rules.md) | The owner's product rules in full — rules 4–6 (never source, agent thinks, free/pull) are the mission. |

---

## Generated

| Doc | What it is |
|---|---|
| [`GENERATED.md`](GENERATED.md) | Machine-written by `scripts/gen_doc_blocks.py --write`: migration head, route and endpoint counts, test-file count, workflow count, hard-rule count, and the full route table. Never edit it — CI fails if it disagrees with the code. |

---

## Operations

| Doc | What it is |
|---|---|
| [`product/DEPLOY.md`](product/DEPLOY.md) | How a merge to `main` reaches production on Railway. |
| [`product/RUNBOOK-backups.md`](product/RUNBOOK-backups.md) | Postgres backup/restore procedure. |
| [`product/BREACH-RUNBOOK.md`](product/BREACH-RUNBOOK.md) | Security-incident response steps. |
| [`product/troubleshooting.md`](product/troubleshooting.md) | Dev-environment FAQ: port conflicts, Postgres connection/schema errors in tests, missing LLM keys. |

---

## Harness (the doc-maintenance framework itself)

| Doc | What it is |
|---|---|
| [`harness/maintenance/DOC-MAINTENANCE.md`](harness/maintenance/DOC-MAINTENANCE.md) | The framework: doc taxonomy, the tripwire/fixer/auditor tiers, deletion authority. Read this before archiving or deleting any doc. |
| [`harness/maintenance/PARKED.md`](harness/maintenance/PARKED.md) | The "code is behind the doc" list — intentions found in docs that are not yet implemented. |
| [`harness/maintenance/claude-md-proposals.md`](harness/maintenance/claude-md-proposals.md) | Append-only inbox of `CLAUDE.md` drift found mid-session, collated into a PR by a designated session. |

---

> **Conventions.** `../ARCHITECTURE.md` is the authoritative architecture reference for how
> the system fits together; [`GENERATED.md`](GENERATED.md) is the authority for every count
> and route, because the code writes it. Code is the proof, not docs — verify any claim
> against source before trusting it. A doc's own type header
> (`<!-- doc: LIVING|PLAN|LOG|REFERENCE|FROZEN|GENERATED -->`) says how it should be read;
> see `harness/maintenance/DOC-MAINTENANCE.md` §1.
