# Job360 — Documentation Index
<!-- doc: LIVING -->

A complete map of every doc under `docs/` (plus the load-bearing docs at the
repo root). Rewritten 2026-09-05 after the harness + sourcing-era cleanup —
every link below resolves; nothing is left out.

---

## Start here — pick your intent

| I want to… | Read |
|---|---|
| **Know what we are building (and not)** | [`product/VISION.md`](product/VISION.md) — the 2026-09-03 decisions; wins over every older product doc |
| **Understand the project fast** | [`../CLAUDE.md`](../CLAUDE.md) → [`../STATUS.md`](../STATUS.md) |
| **Understand the architecture deeply** | [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — the one architecture reference |
| **Run it locally** | [`../backend/README.md`](../backend/README.md) · [`../frontend/README.md`](../frontend/README.md) |
| **Contribute / open a PR** | [`../CONTRIBUTING.md`](../CONTRIBUTING.md) |
| **Debug a runtime problem** | [`product/troubleshooting.md`](product/troubleshooting.md) |
| **Know what to build next** | [`plans/2026-09-03-mission-roadmap.md`](plans/2026-09-03-mission-roadmap.md) — slices 0–6, one issue each |
| **Deploy / run a backup / restore** | [`product/DEPLOY.md`](product/DEPLOY.md) · [`product/RUNBOOK-backups.md`](product/RUNBOOK-backups.md) |
| **Respond to a security incident** | [`product/BREACH-RUNBOOK.md`](product/BREACH-RUNBOOK.md) |

---

## Product

| Doc | What it is |
|---|---|
| [`product/VISION.md`](product/VISION.md) | **The mission.** Agent thinks, Job360 remembers; never source/rank; build order; the 18-decision interview log. |
| [`product/product_design_rules.md`](product/product_design_rules.md) | The owner's product rules in full — rules 4–6 (never source, agent thinks, free/pull) are the mission. |
| [`product/peruser_cv_coverletter.md`](product/peruser_cv_coverletter.md) | Design: per-job AI-tailored CV + cover letter that learns from your edits — the tailor web-fallback feature. |
| [`plans/2026-09-03-mission-roadmap.md`](plans/2026-09-03-mission-roadmap.md) | The work list that follows from VISION.md — one issue per slice (#479–#483). |

### Slice specs (design records — shipped code + `VISION.md` win over these)

| Slice | Spec | Shipped |
|---|---|---|
| 1 — OAuth 2.1 for MCP clients | [`plans/2026-09-03-oauth-mcp/spec.md`](plans/2026-09-03-oauth-mcp/spec.md) | PR #488 |
| 2 — Application spine | [`plans/2026-09-04-application-spine/spec.md`](plans/2026-09-04-application-spine/spec.md) | PR #480 |
| 3 — URL fetch on the web | [`plans/2026-09-04-url-fetch/spec.md`](plans/2026-09-04-url-fetch/spec.md) | PR #496 |
| 4 — Contacts, stats, `update_profile` | [`plans/2026-09-05-contacts-stats/spec.md`](plans/2026-09-05-contacts-stats/spec.md) | PR #498 |
| 5 — Delete the sourcing era | [`plans/2026-09-05-delete-sourcing-era/spec.md`](plans/2026-09-05-delete-sourcing-era/spec.md) | #483 |
| — Bring a job, keep the receipt | [`plans/2026-09-02-bring-a-job/spec.md`](plans/2026-09-02-bring-a-job/spec.md) | PR #469 |
| — Personal tokens + MCP server | [`plans/2026-09-03-mcp-server/spec.md`](plans/2026-09-03-mcp-server/spec.md) | PR #473 |

### Decisions (`decisions/`)

[`decisions/README.md`](decisions/README.md) explains why this folder exists — the reasoning
behind a choice, which code alone never shows.

| Record | Decided |
|---|---|
| [`0003-empty-preferences-stay-silent.md`](decisions/0003-empty-preferences-stay-silent.md) | An empty preference is "don't care", never a penalty or a guessed default. |
| [`0004-jobs-is-a-shared-catalog.md`](decisions/0004-jobs-is-a-shared-catalog.md) | `jobs` holds no `user_id`/`tenant_id` — a brought ad is a catalog row too. |
| [`0005-no-hardcoded-skill-lists.md`](decisions/0005-no-hardcoded-skill-lists.md) | Zero hand-typed skill/keyword dicts in profile extraction — LLM + structural passes only. |
| [`0006-docs-are-generated-or-deleted-not-reworded.md`](decisions/0006-docs-are-generated-or-deleted-not-reworded.md) | A countable doc claim is generated from code or deleted — never hand-edited back into sync. |

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

> **Conventions.** `../ARCHITECTURE.md` is the authoritative architecture reference — its
> generated blocks (`scripts/gen_doc_blocks.py --write`) are the source of truth for counts.
> Code is the proof, not docs — verify counts/claims against source before trusting any doc.
> A doc's own type header (`<!-- doc: LIVING|PLAN|LOG|REFERENCE -->`) says how it should be
> read; see `harness/maintenance/DOC-MAINTENANCE.md` §1.
