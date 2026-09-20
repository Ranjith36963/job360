# PARKED — docs ahead of the code
<!-- doc: LOG -->

> **DATED RECORD — true on the day it was written.** Numbers and statuses here are historical. Do not read as current state. <!-- banner: auto -->

When a doc claims something the code does NOT do, the claim is never silently
deleted and the doc is never "fixed" to describe missing code — the gap lands
here instead (DOC-MAINTENANCE.md rule 1). Each row is an intention waiting for
implementation or an explicit decision to drop it. Written by `/sync` and
`/doc-audit`; humans prune it.

| Date | Source doc | Claim | Evidence the code lacks it | Status |
|------|-----------|-------|----------------------------|--------|
| 2026-09-20 | drill | merge-token proof: a machine merge queued with MERGE_TOKEN starts CI, CodeQL and the post-merge watch on main | throwaway row, removed by the next PR | drill |
