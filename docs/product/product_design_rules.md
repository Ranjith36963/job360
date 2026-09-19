# Product Design Rules
<!-- doc: LIVING | last-verified: 2026-09-19 by the daily truth check -->

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
catalog left for **UK-only is a door, not a penalty** to act on: it was
enforced at ingestion by `services/uk_gate.py`, and that file, its gazetteer
data and every caller are deleted. Its reasoning — never hand-enumerate an
unbounded set — is worth reapplying if catalog-shaped features ever return.

**Visa is a spotlight, not a wall** did NOT retire with it. The rule came
back country-agnostic in slice 7: `VISION.md` decision 27, pinned by
`backend/tests/test_visa_signal.py`.
