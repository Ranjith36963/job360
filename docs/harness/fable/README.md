# Fable audit — index
<!-- doc: LOG -->

> **DATED RECORDS.** Everything in this folder is a snapshot of what was true on
> the day it was written. Numbers and statuses are historical — do not read any
> of them as current state, and do not "fix" them: a stale number in a dated
> record is correct for its date, and rewriting it destroys the evidence. <!-- banner: auto -->

**Start here:** [`AUDIT-2026-07-23-FULL-REVERIFY.md`](AUDIT-2026-07-23-FULL-REVERIFY.md)
— the last full re-verification, and the only file here whose verdicts were
checked against live `main` code rather than against earlier docs. It closes 92
of 106 findings; the rest are owner decisions or scheduled audit areas.

It is also **load-bearing**: four comments in `backend/` cite it as the reason
that code is shaped the way it is (the S3, S6 and S7 fixes). Do not move or
rename it without repointing those.

## The files

| File | What it is |
|---|---|
| `AUDIT-2026-07-23-FULL-REVERIFY.md` | **The current one.** Final re-verify, every verdict against live code. |
| `AUDIT-2026-07-19-REVERIFIED.md` | Earlier re-verify. Superseded by the above. |
| `AUDIT-2026-07-17-VERIFIED.md` | First pass that checked findings against real `file:line` instead of trusting the original write-up. |
| `00-EXECUTIVE-SUMMARY.md` | The original audit's summary. |
| `01-SECURITY.md` … `06-HARNESS-AND-WORKFLOW.md` | The original findings, by area. |
| `08-GAPS-NOT-YET-AUDITED.md` | What the audit deliberately did not cover — still the honest list of blind spots. |
| `09-PRODUCTION-SIGNALS.md` | What production was showing at the time. |
| `SCRAPING-DECISION.md` | A decision record: whether to scrape, and why. |

## Deleted 2026-08-25

`PROGRESS.md` and `07-ROADMAP.md` — a live tracker and a 30/60/90-day plan, both
superseded by the full re-verify above and referenced from nowhere outside this
folder. Retrievable: `git show d3cbceb:docs/harness/fable/PROGRESS.md`.

`FABLE_FINDINGS.md` and the root `fable-harness-plan.md` went the same day; both
said "CLOSED / SUPERSEDED" in their own first line. The four backend comments
that cited `FABLE_FINDINGS.md` now cite the re-verify instead.
