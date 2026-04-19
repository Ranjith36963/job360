# Batch 3.5.3 — Conditional-Fetch Pre-Flight Report

**Run:** 2026-04-19 by `scripts/preflight_conditional_cache.py`
**Log:** `/tmp/preflight_3_5_3.log`

## Goal

Before migrating any source to `_get_json_conditional` /
`_get_text_conditional`, prove that the upstream actually honors
`If-None-Match` or `If-Modified-Since` headers by returning `304 Not
Modified` on a conditional re-fetch. Sources that send validator
headers but ignore `If-*` on re-fetch are **worse off** migrated — we
pay cache maintenance cost with zero bandwidth savings.

## Candidates tested (5)

| Source | ETag | Last-Modified | Conditional GET | Verdict |
|---|---|---|---|---|
| `jobs_ac_uk` | — | — | _not attempted_ | **REJECTED** (no validators) |
| `biospace` | — | — | _not attempted_ | **REJECTED** (no validators) |
| `weworkremotely` | `W/"193e5b86..."` | — | **HTTP 200** | **REJECTED** (server ignores `If-None-Match`) |
| `nhs_jobs_xml` | `W/"3c2a-o+HuAu..."` | — | **HTTP 304** | **QUALIFIES (etag)** |
| `realworkfromanywhere` | — | — | _not attempted_ | **REJECTED** (no validators) |

### Raw probe output

```
## jobs_ac_uk
  url              : https://www.jobs.ac.uk/feeds/subject-areas/computer-sciences
  ETag             : None
  Last-Modified    : None
  conditional-GET  : not attempted
  VERDICT          : REJECTED (no validators)

## biospace
  url              : https://www.biospace.com/rss/jobs/data-science
  ETag             : None
  Last-Modified    : None
  conditional-GET  : not attempted
  VERDICT          : REJECTED (no validators)

## weworkremotely
  url              : https://weworkremotely.com/remote-jobs.rss
  ETag             : 'W/"193e5b8635f98ada46dbc2967cfa89a4"'
  Last-Modified    : None
  conditional-GET  : HTTP 200
  VERDICT          : REJECTED (server returned 200 on conditional GET — ignores If-*)

## nhs_jobs_xml
  url              : https://www.jobs.nhs.uk/api/v1/feed/all_current_vacancies.xml
  ETag             : 'W/"3c2a-o+HuAu9IGvtr7Zx1XlwYqirfKio"'
  Last-Modified    : None
  conditional-GET  : HTTP 304
  VERDICT          : QUALIFIES (etag)

## realworkfromanywhere
  url              : https://www.realworkfromanywhere.com/rss.xml
  ETag             : None
  Last-Modified    : None
  conditional-GET  : not attempted
  VERDICT          : REJECTED (no validators)
```

### Summary

**1 of 5 qualifies.** Per the decision rule:
> If only 1 QUALIFIES → proceed with that one + write a TODO in the
> plan listing why the others were rejected. Don't pad scope.

**GATE: PROCEED (minimum — `nhs_jobs_xml`).**

## Why the 4 rejections aren't a migration target

- `jobs_ac_uk`, `biospace`, `realworkfromanywhere` — server sends
  neither `ETag` nor `Last-Modified`. Migrating these would create a
  cache that can never be validated, consuming memory with no
  `If-*` to send on subsequent requests. Pure cost, zero benefit.
- `weworkremotely` — server DOES send an `ETag`, but ignores
  `If-None-Match` on the conditional GET (replies 200 with a full
  body). Migration would cache the validator but pay bandwidth +
  parse cost on every poll anyway. Net: zero saving, plus cache
  eviction thrash at 256 entries.

These 4 source could become migration candidates if their upstreams
add validator support in the future; re-run this preflight before
moving them.

## Live validation caveat

The preflight shows the servers return 304 on a single conditional
re-fetch made seconds after the initial probe — the content hadn't
changed in that window, so 304 is expected. A more conservative
validation is to observe **actual cache-hit rate over 24h of polling**
against each source. The user spec calls for ≥50% hit rate; the
generator can't wait 24h, so that acceptance gate is deferred to
first-prod-boot observation (same deferral shape as Batch 3.5 P3
"ARQ boot against real Redis"). Recorded in the plan doc.

_Last updated: 2026-04-19_
