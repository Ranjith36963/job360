# Architecture Decision Records
<!-- doc: LIVING -->

**Why decisions were made — the one thing code cannot hold.**

Every other doc in this repo describes *what* the system does, and every one of
those can be checked against the code, generated from it, or deleted in favour
of it. This folder is the exception. A decision's *reasoning* exists nowhere in
the codebase: the code shows the choice, never the alternatives that were
weighed or the failure that forced it.

That makes these the highest-value and least-replaceable docs here — and the
only ones a truth-checker cannot help with, because there is no code fact to
compare them to.

## Why this folder exists

Agents (and humans) re-litigate closed decisions. Without a record, a future
session sees a per-source UK filter that was deliberately removed and helpfully
adds it back; sees `MIN_STORE_SCORE = 1` and "optimises" it to 30, reinstating a
data-loss bug; sees an empty preference and writes a sensible-looking default
that violates a product rule.

Each of those has already happened or was one edit away.

## How to read a record

Each ADR answers: **what was decided**, **what problem it solved**, **what else
was considered**, **what it costs**, and **whether it still holds**. Where a
decision is enforced by code or a test, the record names it — a decision with a
guard is a decision that stays made.

## How to write one

- Number it sequentially, `NNNN-short-slug.md`.
- Record the reasoning, not the mechanism. The mechanism is in the code and will
  drift; the reasoning will not.
- Cite evidence by SYMBOL, not line number — line numbers rot on any edit above
  them.
- Stamp it `<!-- doc: LIVING -->`. A superseded ADR is not deleted: it is marked
  superseded and points at the record that replaced it. The history is the value.

## Status

Backfilled 2026-08-25 from code comments, the hard rules in `CLAUDE.md`, and
session evidence. This is a start, not a complete set — a decision made before
this date with no record here is not thereby unimportant, it is merely unwritten.
