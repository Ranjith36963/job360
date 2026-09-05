# Job360 — The Three Pillars (archived)
<!-- doc: LIVING | last-verified: 2026-09-05 by slice 5 (delete the sourcing era) -->

> **The sourcing era ended 2026-09-05 (slice 5, #483).** Job360 no longer sources,
> ranks or recommends jobs — the seeker's own AI agent does that; Job360 is the
> agent's memory (profile, artifact versions, typed events, receipts). Read
> [`../VISION.md`](../VISION.md) first — it wins over anything below or in the
> archive.

This folder used to hold three architectural pillar manuals (User Side, Search &
Match Engine, Job Providers) plus a glossary and a runbook, all written for the
job-search-and-score product that shipped before 2026-09-03. That product is
gone. The manuals are kept as history, not as a reference to build against:

| Doc | Where it lives now |
| --- | --- |
| Pillar 1 — The User Side | [`../../_archive/sourcing-era/01-user-pillar.md`](../../_archive/sourcing-era/01-user-pillar.md) |
| Pillar 2 — Search & Match Engine | [`../../_archive/sourcing-era/02-search-and-match-engine.md`](../../_archive/sourcing-era/02-search-and-match-engine.md) |
| Pillar 3 — Job Providers | [`../../_archive/sourcing-era/03-job-providers.md`](../../_archive/sourcing-era/03-job-providers.md) |
| Glossary | [`../../_archive/sourcing-era/glossary.md`](../../_archive/sourcing-era/glossary.md) |
| Runbook | [`../../_archive/sourcing-era/runbook.md`](../../_archive/sourcing-era/runbook.md) |
| Catalog state | [`../../_archive/sourcing-era/CATALOG_STATE.md`](../../_archive/sourcing-era/CATALOG_STATE.md) |
| Shelf fill measurement | [`../../_archive/sourcing-era/SHELF_FILL_MEASURED.md`](../../_archive/sourcing-era/SHELF_FILL_MEASURED.md) |
| Universal shelf contract | [`../../_archive/sourcing-era/UNIVERSAL_SHELF.md`](../../_archive/sourcing-era/UNIVERSAL_SHELF.md) |

Every file above carries a FROZEN header pointing back at `VISION.md`.

## Where the live architecture reference is now

[`../../../ARCHITECTURE.md`](../../../ARCHITECTURE.md) describes what remains: profile
extraction, the application spine (applications, events, artifacts, receipts,
contacts), URL fetch, the web CV-tailoring fallback, MCP + OAuth, and auth. There
is no scoring, ranking, deduplication or job fetching left to document.
