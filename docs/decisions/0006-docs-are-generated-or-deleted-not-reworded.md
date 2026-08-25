# ADR-0006 — Documentation drift is fixed by deleting or generating, never rewording
<!-- doc: LIVING -->

**Status:** Accepted · **Date:** 2026-08-25

## Decision

When a doc is found to contradict the code, it may be corrected in exactly
three ways:

1. **Delete** the sentence.
2. **Replace** it with a pointer to code **by symbol name** — never a line
   number, which rots on any edit above it.
3. **Pin** it with a test, and have the doc cite the test by name.

**Rewording a claim to be accurate is banned.**

Facts that are derivable are **generated** into the doc from code between
`<!-- generated: -->` markers, and CI fails if the file disagrees.

## The problem it solved

A nightly LLM checked the docs against the code for **fifteen consecutive
runs**. Findings per run: 52, 14, 10, 10, 5, 2, 6, 2, 10, 7, 3, 1, 3, 12, 8.
Every finding was real. **None ever reached zero.**

The cause was not sloppy writing. Rewording a lie produces a freshly accurate
sentence that will drift again the next time the code moves — the same claim,
with its expiry date reset. The volume of prose that *could* lie never changed,
so the search never terminated.

A deleted line is a lie that can never be told again. A generated line cannot
be wrong. Only those two moves compound.

## Alternatives considered

- **More guards.** This was the previous strategy and it grew a ~1,900-line
  second codebase that can itself drift. Guards make drift *visible*;
  generation makes it *impossible*. Kept for facts nothing can generate.
- **Better prompts for the fixer.** Tried; the fixer was accurate and the count
  still did not fall, because accuracy was never the bottleneck.

## Consequences

- Docs get shorter and terser. Some navigational convenience is lost.
- Adding genuine *why* still costs surface budget, and should — that is the
  content worth paying for. Raising the ceiling is allowed and must state what
  was added.
- **Generation retires its own guards.** The first retirement happened the day
  this was adopted: the migration head and count became generated, six
  hand-written copies were deleted, and four guard patterns plus their drill
  were removed. Guard count fell 25 → 24, the first fall ever recorded here.

## Still valid?

Adopted today; the first two cycles under it both shrank the surface (−14, −4),
where the previous fifteen left it flat.

## Enforced by

`living_surface()` and its ratchet in `scripts/doc_sync_check.py` (CI fails if
the prose grows), the line-citation ratchet, and `scripts/gen_doc_blocks.py`
(CI fails if a generated block is stale **or** its marker was deleted). The
contract itself lives in the nightly routine's prompt.
