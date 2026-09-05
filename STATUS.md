# Job360 Project Status
<!-- doc: LIVING | last-verified: 2026-09-05 by slice 5 (delete the sourcing era) -->

## Current State: PIVOTED (2026-09-02) — the memory layer for the seeker's AI agent

> **Direction lives in [`docs/product/VISION.md`](docs/product/VISION.md).** Read it
> first. In one line: the user (or their agent) brings the job; the agent
> judges fit, writes the CV, reads the inbox, applies; **Job360 keeps the
> structured profile, every artifact version, every event and the receipt.**
> We never source, rank or recommend jobs (product rule 4).
>
> **Live on `main` (89cd1dd, 2026-09-03):** bring-a-job (paste) + append-only
> receipts (#469); personal API tokens + MCP server at `/api/mcp` (#473; tool count grows per slice — measure it, never quote it);
> token-cap race fix (#476); mypy at 0 (#477). Railway runs backend + frontend +
> Postgres only — **worker and Redis were deleted 2026-09-02**, so nothing runs in
> the background (no notifications, no crons; Redis-unreachable log lines are expected).
>
> **The five slices (VISION.md), where each one is:** 1 OAuth 2.1 for ChatGPT/Grok
> clients — **shipped (PR #488)** → 2 the application spine — **shipped (PR #480)**
> (one Application object, typed event log, versioned artifacts, `save_artifact` /
> `record_event` / `whats_new` / `export_history`; home = your applications) → 3 URL
> fetch on the web — **shipped (PR #496)**: `POST /jobs/fetch-url` fills `/bring` from a
> link (JSON-LD → meta → heuristic ladder) under a new `src/services/fetch/` SSRF guard
> drilled ten ways (`scripts/ssrf_drill.py`); paste stays the fallback, no MCP tool → 4
> contacts/outreach, stats, `update_profile` — **shipped (PR #498)** (`add_contact` /
> `stats` / `update_profile` MCP tools, `profile_edits` overlay the web shows and
> clears, People section on the application page) → 5 **delete the sourcing era
> (#483) — this branch**, the flag and the code it hid go together (migration
> `0039_drop_sourcing_tables`, after slice 4's `0038`). Measure: the owner uses it
> daily for his own hunt.

## Sourcing era — deleted 2026-09-05 (slice 5, #483)

Everything this file used to say about the job-search-and-score product
(Phases 1–3, the four matching engines, ops-hardening roadmap, fragility
notes, known issues, quick-verification commands) described code that no
longer exists. It is archived verbatim, FROZEN, at
[`docs/_archive/sourcing-era/STATUS_HISTORY.md`](docs/_archive/sourcing-era/STATUS_HISTORY.md)
— read it as history, never as a guide for what to build next.

For the current architecture see [`ARCHITECTURE.md`](ARCHITECTURE.md); for
current test/lint commands see [`backend/README.md`](backend/README.md) and
[`frontend/README.md`](frontend/README.md).
