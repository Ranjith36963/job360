# Batch 3 — Independent Review

**Reviewer:** Claude Opus 4.7 (1M) in `.claude/worktrees/reviewer` on `pillar3/batch-3-review`
**Generator branch (final):** `pillar3/batch-3` @ `65fafc5`
**Review date:** 2026-04-18
**Base:** `main` (Batch 2 merge `6446feb`) … `65fafc5` — 10 commits

---

## Verdict

**APPROVED** at `65fafc5`. Round-1 raised two non-blocking nits (P2 + P3); both were addressed in round 2 with a clean 3-file diff and an honest correction of a false log claim. No new findings.

---

## Round 1 — audit at `31965cd`

### Audit checklist results

#### Source-count integrity ✅

| Surface | Expected | Actual | Location |
|---|---:|---:|---|
| `SOURCE_REGISTRY` | 50 | **50** | `backend/src/main.py:81-134` |
| `_build_sources()` unique instances | 49 (50 − 1 indeed/glassdoor twin) | **49** | `backend/src/main.py:207-268` |
| `RATE_LIMITS` entries | 50 | **50** | `backend/src/core/settings.py:53-106` |
| `test_source_registry_has_50_sources` count | 50 | **50** | `backend/tests/test_cli.py:44-68` |
| Expected-set in the same test | 50 names | **50 names** | same |
| `test_api.py` `== 50` assertions | exists | updated to 50 in 4 call-sites | `backend/tests/test_api.py` |

All four mutually referencing surfaces agree. CLAUDE.md rule #8 satisfied.

#### Tiered polling ✅

- Per-tier intervals respected: `test_ats_source_polled_every_60s` (59 s → no tick, 60 s → tick) and `test_scrapers_polled_every_3600s` exercise the edges (`backend/tests/test_scheduler.py:65-102`).
- Conditional-fetch layer issues ETag + Last-Modified: `BaseJobSource._get_json_conditional` in `backend/src/sources/base.py:158-208` populates both headers from `ConditionalCache`, returns cached body on 304, writes back on 200-with-validator. Covered by `backend/tests/test_conditional_fetch.py` (all 4 tests pass).
- Circuit breaker half-open recovery: `test_half_open_success_closes` + `test_half_open_failure_reopens_with_fresh_cooldown` in `backend/tests/test_circuit_breaker.py`.
- Fairness: `test_multiple_tiers_do_not_starve` verifies a slow-tier (scraper, 3600 s) source does not delay a fast-tier (ats, 60 s) source — both tick together at t=0, then ats alone at t=60 and t=120, scraper still waiting. (`tests/test_scheduler.py:110-131`.)

#### Dropped sources ✅ (with one stale-mock nit)

- Source files deleted: `backend/src/sources/apis_free/yc_companies.py`, `backend/src/sources/other/nomis.py`, `backend/src/sources/feeds/findajob.py` — confirmed absent.
- Registry entries removed from `SOURCE_REGISTRY` and `_build_sources()`.
- `RATE_LIMITS` entries removed (no dangling keys).
- `tests/test_sources.py` — no `findajob`/`yc_companies`/`nomis` class or function remains.
- **P3 nit (round 1):** `backend/tests/test_main.py:52` and `:79` still contained dead mock regexes for `findajob.dwp.gov.uk` and `www.nomis.co.uk`. `test_main.py` is `--ignore`'d in the baseline (live-HTTP JobSpy leak) so dormant, but stale.

#### New sources ✅

| Source | File | Tests (happy / empty / error) | `_is_uk_or_remote` | `search_config` passthrough |
|---|---|---:|---|---|
| `teaching_vacancies` | `apis_free/teaching_vacancies.py` | 3 (`test_sources.py:1853-1902`) | ✅ line 55 | ✅ via default BaseJobSource `__init__` |
| `gov_apprenticeships` | `apis_free/gov_apprenticeships.py` | 3 (`test_sources.py:1929-1984`) | ✅ line 55 | ✅ via default `__init__` |
| `nhs_jobs_xml` | `feeds/nhs_jobs_xml.py` | 3 (`test_sources.py:2007-2056`) | ✅ line 49 | ✅ via default `__init__` |
| `rippling` | `ats/rippling.py` | 3 (`test_sources.py:2082-2135`) | ✅ line 52 | ✅ custom `__init__(companies=None, search_config=None)` forwards to `super().__init__(session, search_config=search_config)` |
| `comeet` | `ats/comeet.py` | 3 (`test_sources.py:2160-2214`) | ✅ line 54 | ✅ same pattern |

All five honour the Batch-1 `posted_at` / `date_confidence` contract — `"high"` when the upstream field is present, `"low"` otherwise. `datetime.now(timezone.utc)` is used only for `date_found`, never for `posted_at`.

### Round-1 findings

#### P1 (blocker) — none

#### P2 — `SOURCE_INSTANCE_COUNT = 47` drift + false log claim

**Where:** `backend/src/main.py:139`.

**What:** After Batch 3, `_build_sources()` returns 49 unique instances (50 registry entries minus the indeed/glassdoor twin). The constant still said `47`.

**Why not P3:** `docs/IMPLEMENTATION_LOG.md:455` justified the drift with *"The constant is not used anywhere in the codebase (grep-confirmed)."* That claim was factually wrong — `grep "SOURCE_INSTANCE_COUNT"` returns 4 usages in `backend/tests/test_main.py` (lines 192, 300, 307, 319), and one (`test_source_instance_count_matches_build`) is a test purpose-built to catch exactly this drift. Those tests don't run in the current CI gate because `test_main.py` is `--ignore`'d over the pre-existing JobSpy live-HTTP leak — but the drift itself was a broken invariant, not an unused constant.

#### P3 — dead mock regexes in ignored test

`backend/tests/test_main.py:52` (`findajob.dwp.gov.uk`) and `:79` (`www.nomis.co.uk`) registered `aioresponses` mock URLs for dropped sources. Harmless but stale.

### Round-1 test-delta verification

```
Baseline (plan §POST-BATCH-2):  24 failed, 498 passed, 3 skipped in 184.91s
Reviewer run (31965cd):          24 failed, 529 passed, 3 skipped in 230.79s
Delta:                           +31 passing, 0 regression, +46s runtime
```

### Round-1 handoff answers

1. **NHS Jobs additive vs replacement** — additive is correct. The 48→50 constraint with 3 drops forces it arithmetically; the two entries also serve distinct upstream endpoints and confidence profiles.
2. **Slug quality — 164 new hand-curated slugs** — spot-check worthwhile but not blocking. `BaseJobSource._request` returns `None` → `[]` on 404/5xx; dead slugs no-op gracefully. Defer to `scripts/validate_slugs.py` in staging as planned.
3. **Scheduler not yet wired to `run_search`** — acceptable Batch-4 scope.
4. **Conditional-fetch not wired to existing sources** — acceptable infra-only ship.
5. **`SOURCE_INSTANCE_COUNT`** — see P2. Update recommended.

---

## Round 2 — re-review at `65fafc5`

**Generator pushed one new commit on top of `31965cd`:**

```
65fafc5 fix(review-response): Batch 3 P2 + P3 (SOURCE_INSTANCE_COUNT + dead mocks)
```

### Diff audit

| File | Change | Verdict |
|---|---|---|
| `backend/src/main.py:139` | `SOURCE_INSTANCE_COUNT = 47` → `49`; comment rewritten to cite the drift-catcher test by name | ✅ Correct — matches actual `_build_sources()` output (50 registry − 1 for indeed/glassdoor twin) |
| `backend/tests/test_main.py:52,79` | `findajob` + `nomis` dead mock regexes deleted | ✅ Matches P3 |
| `backend/tests/test_main.py:77` | `yc-oss.github.io` dead mock deleted (generator's own extra sweep, not in P3) | ✅ Same class of defect; correct to clean up together |
| `docs/IMPLEMENTATION_LOG.md:455` | The false "not used anywhere (grep-confirmed)" sentence replaced with an honest correction that credits the review, cites the drift-catcher test by name, and notes the CI non-impact | ✅ Honest rewrite |

The diff is scope-minimal: exactly 3 files, exactly the fixes the round-1 review called for, plus one extra sweep of the same class of defect. Nothing unrelated bundled in.

### Regression check

Three full pytest runs (`pytest tests/ --ignore=tests/test_main.py -q`) at `65fafc5`:

| Run | Failed | Passed | Skipped | Notes |
|---:|---:|---:|---:|---|
| 1 | 25 | 528 | 3 | One extra failure (transient; did not recur) |
| 2 | 24 | 529 | 3 | Matches round-1 baseline |
| 3 | 24 | 529 | 3 | Matches round-1 baseline |

Runs 2 and 3 confirm zero regression vs the round-1 audit. Run 1's ephemeral +1 is flake, not caused by this commit — the commit touches only a constant literal, three deleted mock regexes in an `--ignore`'d file, and a docs paragraph, none of which can affect an in-gate async test. Pre-existing flake worth isolating in a separate tracker item, but it pre-dates this batch.

### Verdict — unchanged

**APPROVED** at `65fafc5`. All round-1 P2/P3 nits are resolved. The round-1 findings no longer apply. No new findings.

---

_Signed: reviewer session, 2026-04-18 (rounds 1 + 2)_
