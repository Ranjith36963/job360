# ADR-0004 — `jobs` is a shared catalog; per-user state lives elsewhere
<!-- doc: LIVING -->

**Status:** Accepted · **Date:** backfilled 2026-08-25 from hard rules #10 and
#17

## Decision

The `jobs` table never gets a `user_id` or `tenant_id` column. Neither do
`job_enrichment` or `job_embeddings`.

Per-user state lives in `user_feed`, `user_actions` and `applications`.
Per-user *scoring* happens at read time, by passing the user's preferences and
an enrichment lookup into `JobScorer`.

## The problem it solved

A job posting is the same posting for everyone. Copying a row per user means:

- **N copies to deduplicate** instead of one, and the deduplicator is already a
  four-layer cascade.
- **N copies to enrich**, and enrichment is the expensive LLM path — the cost
  multiplies by users for no gain, since the enrichment of a posting does not
  depend on who is looking.
- **N copies to keep fresh**, so `purge_old_jobs` and staleness detection would
  have to reason per user.

The shared catalog makes the expensive work happen **once** and the cheap work
(scoring, ranking) happen per request.

## Alternatives considered

- **Per-tenant job tables.** Rejected for the cost multiplication above; the
  isolation it buys is not needed because a job posting is public data.
- **A `user_id` column with nulls for shared rows.** Rejected: a nullable
  discriminator invites exactly one INSERT that sets it, and then the invariant
  is gone with nothing to notice.

## Consequences

- Scoring cannot be precomputed and cached in `jobs`. It is a read-time cost,
  which is why the scorer must stay fast and why the event-loop blocking bug
  (a synchronous O(n²) dedup starving status polls) was a real outage class.
- Any feature that wants "this job, but different per user" needs a new table,
  not a new column.

## Still valid?

Yes.

## Enforced by

Convention plus review; **no schema-level guard exists** for the absence of
these columns. That is an honest gap: the rule is stated in `CLAUDE.md` as
rules #10 and #17 and has held so far because it is written down, not because
anything stops it.
