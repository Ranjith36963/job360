# Job360 Project Status
<!-- doc: LIVING | last-verified: 2026-09-05 by slice 5 (delete the sourcing era) -->

## Current State: PIVOTED (2026-09-02) — the memory layer for the seeker's AI agent

> **Direction lives in [`docs/product/VISION.md`](docs/product/VISION.md).** Read it
> first. In one line: the user (or their agent) brings the job; the agent
> judges fit, writes the CV, reads the inbox, applies; **Job360 keeps the
> structured profile, every artifact version, every event and the receipt.**
> We never source, rank or recommend jobs (product rule 4).
>
> **Live on `main`:** bring-a-job (paste) + append-only
> receipts (#469); personal API tokens + MCP server at `/api/mcp` (#473; tool count grows per slice — measure it, never quote it);
> token-cap race fix (#476); mypy at 0 (#477). Railway runs backend + frontend +
> Postgres only — **worker and Redis were deleted 2026-09-02**, so nothing runs in
> the background (no notifications, no crons; Redis-unreachable log lines are expected).
>
> **Slice order and what each one is: [`docs/product/VISION.md`](docs/product/VISION.md).**
> A slice that has shipped is described by its code and its migration, not by a
> line here — `git log` and the issue are the record of when. Measure: the owner
> uses it daily for his own hunt.

## Sourcing era — deleted 2026-09-05 (slice 5, #483)

Everything this file used to say about the job-search-and-score product
(Phases 1–3, the four matching engines, ops-hardening roadmap, fragility
notes, known issues, quick-verification commands) described code that no
longer exists. It is not archived in-tree — `git show` a pre-2026-09-05
commit of this file for that history, never as a guide for what to build next.

For the current architecture see [`ARCHITECTURE.md`](ARCHITECTURE.md); for
current test/lint commands see [`backend/README.md`](backend/README.md) and
[`frontend/README.md`](frontend/README.md).
