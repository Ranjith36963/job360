# Product Design Rules
<!-- doc: LIVING | last-verified: 2026-09-07 by chore/post-pivot-prune -->

Owner-set rules that shape every feature. Each rule records WHY it exists so
a future session applies it rather than rediscovering it. When code
contradicts a rule here, the finding goes to the owner — do not silently
"fix" in either direction.

---

## Rule — Filled shelves work harder; empty shelves stay silent

*Set by the owner, 2026-08-07. Survives the 2026-09-03 pivot for the one
place it still applies: the profile the agent reads.*

**The rule.** What the user actually gave Job360 is what the agent gets back.
An empty preference means **"not stated"** — never a guess, never a default
we invent, never a penalty. A filled field is present in the profile the
agent reads (`get_profile`); an unfilled field is simply **absent**, not a
zero or a made-up value.

**The test for new code:** read `get_profile` for a user who filled only one
field. Every other field must be ABSENT from the response, not a default.

The half of this rule that used to govern scoring dimensions, prefilters and
the LLM judge went with the sourcing pipeline — see below.

---

## Retired 2026-09-05 with slice 5

Job360 never sources, ranks or judges jobs any more (VISION.md rule 4) — a
job enters only because the user or their agent brought it. There is no
catalog left for these two rules to act on, so they no longer apply to
anything in this codebase:

- **UK-only is a door, not a penalty.** Used to be enforced at ingestion by
  `services/uk_gate.py`, one chokepoint before a scraped job reached storage.
  That file, its gazetteer data, and every caller are deleted.
- **Visa is a spotlight, not a wall.** Used to rank sponsors up in a feed via
  `services/visa_signal.py`. That file and the feed it spotlighted in are
  both deleted.

Verify before citing either as live: `git ls-tree -r origin/main --name-only
backend/src` lists neither file. If catalog-shaped features ever come back,
the reasoning behind both rules (never hand-enumerate an unbounded set;
a toggle must never shrink what the user sees) is worth reapplying — but
nothing in the current code implements them today.
