# ADR-0001 — The storage floor and the display floor are different numbers
<!-- doc: LIVING -->

**Status:** Accepted · **Date:** backfilled 2026-08-25 from `core/settings.py`
and `src/main.py` comments

## Decision

A job enters the shared catalog if it beats `MIN_STORE_SCORE` (default **1**).
A job is *shown* to a user if it beats `MIN_MATCH_SCORE` (default **30**).
These are deliberately two knobs, and only the first one deletes anything.

## The problem it solved

There used to be one floor, applied **before** the job was persisted. Anything
under 30 was dropped at ingestion and never stored.

Scores are not stable across runs — they depend on the profile, the enrichment
state, and the scorer version. So a posting that scored 35 one week scored 25
the next and **silently vanished from the catalog**. Not ranked lower: gone,
with nothing in the database to show it had ever been seen. The comment in
`src/main.py` above the store filter records exactly this.

## Alternatives considered

- **Keep one floor, raise stability.** Rejected: the instability is inherent —
  the score legitimately changes when a user edits their profile.
- **Keep one floor, lower it.** This is effectively what happened, but naming
  the two floors separately is what stops the next person "tidying" them back
  into one.

## Consequences

- The catalog is larger and holds low-scoring rows. Accepted: storage is cheap,
  and a row that exists can be re-scored later. A row that was never stored
  cannot.
- Read-time filtering costs a little more work per request.
- **The two numbers look redundant to a newcomer.** That is the standing risk
  this record exists to prevent — merging them re-creates the data-loss bug.

## Still valid?

Yes. Three LIVING docs were found in 2026-08 still describing the OLD behaviour
("jobs below 30 are silently dropped"), which would have taught a reader to
reinstate it. `docs/product/PRD.md` carried the same claim and it was withdrawn.

## Enforced by

`MIN_STORE_SCORE` in `core/settings.py`, applied at the store filter in
`src/main.py`. `MIN_MATCH_SCORE` is a plain constant, **not** environment-
readable — setting it in `.env` does nothing, which the env-var guard in
`scripts/doc_sync_check.py` now enforces after eight docs claimed otherwise.
