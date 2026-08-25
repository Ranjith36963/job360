# ADR-0003 — An empty preference means "don't care", never a penalty
<!-- doc: LIVING -->

**Status:** Accepted (owner rule, non-negotiable) · **Date:** backfilled
2026-08-25 from hard rule #29 and `docs/product/product_design_rules.md`

## Decision

If a user has not filled in a preference — salary, locations, workplace,
experience level, about-me — the system treats it as **no opinion**. Dimension
scorers return a constant, prefilters pass everything, the LLM judge prompt
omits the unset field, and the frontend never blocks on it.

Never a penalty. Never a per-job zero. Never a guessed default.

## The problem it solved

An unfilled field is ambiguous, and the two readings produce opposite products:

- *"I don't care about salary"* → showing all salaries is correct.
- *"I forgot to say £60k"* → showing all salaries is noise.

Guessing the second silently punishes users for not filling in a form. Scoring
an empty preference as zero is worse than not scoring it: it drags good matches
below bad ones for a reason the user never expressed and cannot see.

This is how Indeed and LinkedIn behave, and it is the owner's product rule.

## Alternatives considered

- **Sensible defaults** (e.g. assume the median salary for the role). Rejected:
  a guess the user never made, applied invisibly, that they cannot correct
  because they do not know it happened.
- **Require every field before searching.** Rejected: the funnel already has
  ~0 users; a wall of required fields is not the fix.

## Consequences

- Empty-preference users get a broader, flatter feed. Correct: the system knows
  nothing about their preferences, so it should not pretend to.
- Every new dimension scorer must decide what its neutral value IS, and get it
  right — a returned `0` looks like a legitimate score and will not stand out.

## Still valid?

Yes.

## Enforced by

`tests/test_design_rules.py`. **Coverage bound, stated honestly:** that test
covers the dimension scorers and the prefilter only. The judge prompt and the
frontend are **not** guarded and must be checked by hand — recorded here so the
gap is a known gap rather than an assumed absence.
