# Slice 8 — CV diff: original vs tailored, no Keep button (#515)
<!-- doc: PLAN | written 2026-09-11 from the owner's question on the goal -->

## What the owner asked (2026-09-11, voice)

> Keep the CV difference: original versus the tailored one. It can be any
> version. We can tailor it many times, but the one we apply, that is what we
> keep as the tailored one. Do we really need the Keep button? Do we always
> need to go back and forth between the web and the chat? The one that has
> been applied is the version that counts.

## The decision (VISION.md decision 26)

**No Keep button.** The version that counts is the one named on the receipt
(`application_receipts.cv_artifact_id` / `cover_letter_artifact_id`), written
by `record_application` the moment the agent or the human says "I applied".
That fact already exists (migration `0037`, `spine.py`); a Keep button would
be a second door to the same fact, and a "do" door on the web — exactly what
rule 5 says we do not build: the agent already holds both texts and saves
versions through `save_artifact`. Pressing Keep on the web would force the
chat ↔ web round trip the owner does not want.

So the web page stays the **record**: it shows the difference between the
original CV and any tailored version, and it marks which version was
applied. Version numbers stay internal — the human sees "Original",
"Applied", "Latest".

## Business intent

- The seeker sees, at a glance, what the agent changed before it went out.
- The seeker never has to leave the chat to "approve" anything.
- Later (slice 9 "flag for next time") the same page is where a lesson is
  written — the diff is the evidence the lesson points at.

## Not in scope

- Any write from the diff view. No new MCP tool (rule M2 — the agent has both
  texts). No migration. The web tailor fallback's own Keep (`POST
  /tailor/{job_id}/{kind}/keep`, the learn-from-kept trigger) is untouched.
