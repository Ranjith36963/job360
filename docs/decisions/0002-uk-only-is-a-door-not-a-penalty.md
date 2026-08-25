# ADR-0002 — UK-only is a door, not a penalty
<!-- doc: LIVING -->

**Status:** Accepted · **Date:** decision 2026-08-12, backfilled 2026-08-25
from hard rule #30 and `services/uk_gate`

## Decision

Exactly **one** chokepoint decides whether a job is UK-eligible:
`services/uk_gate.check_uk`. It admits or refuses. There is no scorer penalty
for being foreign, and no per-source filtering.

Foreign places are never enumerated. UK places are, because that set is finite
and comes from data (`src/data/uk_gazetteer/`). Countries stay enumerated only
because that set is genuinely closed, and the country override runs *before*
gazetteer matching.

## The problem it solved

Two problems, and the second is the subtle one.

**A penalty is not a filter.** A −15 "foreign" score adjustment means a foreign
job with an otherwise excellent match still outranks a mediocre UK one. The
product promise is UK-only; a penalty makes it UK-mostly, unpredictably.

**Hand-typed exclusion lists rot silently.** The naive location rule — written
by listing non-UK cities — blocked **48%** of the live catalog on a dry run.
An open set cannot be enumerated: any list of "foreign cities" is a sample, and
samples of open sets are wrong immediately and get wronger.

## Alternatives considered

- **Per-source filtering.** Rejected: N places to get right, N places to drift,
  and a new source silently arrives with no filter at all.
- **Scorer penalty.** Deleted 2026-08-12 for the reason above.
- **A city denylist.** Deleted the same day. This is the "never hand-enumerate
  an unbounded set" rule in its original form.

## Consequences

- One place to reason about, one place to test, one place to break.
- The gate must be *right*, because nothing downstream compensates. Hence the
  requirement to dry-run any location rule over the live catalog before shipping.
- **Known gap:** the dual-site escape still admits "London, Ontario". The fix is
  data (a disambiguating gazetteer), not another special case — a hand-typed
  exception here would be the exact mistake this record exists to prevent.

## Still valid?

Yes.

## Enforced by

`tests/test_uk_gate.py` and `tests/test_scorer.py`. The fetch-time skip in
`sources/base._is_uk_or_remote` asks `uk_gate.names_foreign_place` — it does not
re-implement the judgement.
