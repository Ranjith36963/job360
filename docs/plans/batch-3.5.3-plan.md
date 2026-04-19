# Pillar 3 — Batch 3.5.3 Conditional-Cache Pilot Plan

> **For agentic workers:** REQUIRED SUB-SKILLS: `superpowers:test-driven-development`, `superpowers:verification-before-completion`. Steps use checkbox syntax.

**Goal:** Validate that Batch 3's `_get_json_conditional` / `ConditionalCache` works under live ETag conditions by adopting it on a proven-qualifying source. Today the helper has zero callers — pure dead path per CurrentStatus.md §3 + §13 issue #3.

**Architecture:** Add a sibling `_get_text_conditional` for RSS/XML sources (the existing helper only handles JSON). Migrate `nhs_jobs_xml` (the sole preflight qualifier — see `batch-3.5.3-preflight.md`). Add hit/miss counters on `ConditionalCache` so cache effectiveness can be observed in logs without Prometheus wiring.

**Tech stack:** existing — aiohttp, aioresponses, stdlib `sqlite3`, pytest.

---

## POST-BATCH-3.5.2 BASELINE

Run 2026-04-19 on `pillar3/batch-3.5.3` HEAD (branched from `origin/main` @ `297cd61`).

```
Command: cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q
Result:  24 failed, 571 passed, 3 skipped in 245.01s
Log:     /tmp/pytest_baseline_3_5_3.log
```

---

## Pre-flight verdict (STEP 1, blocking gate — already run)

See `docs/plans/batch-3.5.3-preflight.md` for the full report.

**1 of 5 candidates qualifies:** `nhs_jobs_xml` (ETag → 304 roundtrip verified live).

Rejected with reasons:
- `jobs_ac_uk` — upstream sends neither ETag nor Last-Modified.
- `biospace` — same.
- `realworkfromanywhere` — same.
- `weworkremotely` — ETag present, but server ignores `If-None-Match` and replies 200 on conditional GET.

Per the decision rule: **proceed with just `nhs_jobs_xml`. Don't pad scope with rejected sources.**

## Scope-out list (explicit)

- **No migration of `jobs_ac_uk`, `biospace`, `realworkfromanywhere`, `weworkremotely`** — upstream doesn't honor conditional GET today. Revisit via preflight re-run if headers change.
- **No Prometheus / KPI exporter wiring** for the new `hit_count`/`miss_count` metrics. Exposed as a method only; wiring is Batch 4 observability scope.
- **No live 24-hour cache-hit-rate validation.** User spec asks for ≥50% hit rate after 24h; generator can't wait. Deferred to first-prod-boot observation (same shape as Batch 3.5 P3 Redis smoke).
- **No refactor of `_get_json_conditional` body** — Batch 3's implementation is unchanged; we only add a sibling for the text path and instrument the shared `ConditionalCache`.

---

## File-level plan

### Created files

| Path | Responsibility |
|---|---|
| `scripts/preflight_conditional_cache.py` | Live-probe script + one-shot gate (already written in STEP 1) |
| `docs/plans/batch-3.5.3-preflight.md` | Pre-flight report (already written) |

### Modified files

| Path | Change |
|---|---|
| `backend/src/services/conditional_cache.py` | Add `hit_count`/`miss_count` counters + `get_metrics()` method + `reset_metrics()` for tests |
| `backend/src/sources/base.py` | Add `_get_text_conditional()` sibling method mirroring `_get_json_conditional` but returning `str` |
| `backend/src/sources/feeds/nhs_jobs_xml.py` | `self._get_text(...)` → `self._get_text_conditional(...)` |
| `backend/tests/test_conditional_fetch.py` | Extend with new tests (text path + migrated-source proof + metrics) |

---

## Phase A — Plan + baseline + preflight artefacts

**Commit:** `docs(pillar3): Batch 3.5.3 plan + preflight gate + baseline`

- [ ] Step A1: Write this plan + preflight report (done)
- [ ] Step A2: Commit after baseline numbers land

---

## Phase B — `_get_text_conditional` + cache instrumentation (one commit)

### Tasks

- [ ] **B-Step 1: RED test for the text path** — add to `test_conditional_fetch.py`:

```python
def test_get_text_conditional_roundtrip_with_etag():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            url = "https://example.test/rss.xml"
            captured = []

            def _capture(url_, **kwargs):
                captured.append(kwargs.get("headers") or {})

            with aioresponses() as m:
                m.get(url, body="<rss/>",
                      headers={"ETag": 'W/"v1"'},
                      content_type="application/xml",
                      callback=_capture)
                m.get(url, status=304, callback=_capture)

                src = _Probe(session)
                first = await src._get_text_conditional(url)
                second = await src._get_text_conditional(url)

                assert first == "<rss/>"
                assert second == "<rss/>"  # cached
                assert captured[1].get("If-None-Match") == 'W/"v1"'
        finally:
            await session.close()
    _run(_t())
```

- [ ] **B-Step 2: RED test for cache metrics** — `test_cache_metrics_count_hits_and_misses`.
- [ ] **B-Step 3: RED test for aioresponses 304 primitive** — one throwaway sanity test verifying `m.get(url, status=304)` works end-to-end with the conditional helper.

- [ ] **B-Step 4: Run RED** → all new tests should fail (missing method / metrics).

- [ ] **B-Step 5: GREEN — add `_get_text_conditional` to `BaseJobSource`.** Mirror `_get_json_conditional` but call `await resp.text()` instead of `await resp.json(content_type=None)`. Same cache semantics. ~30 lines.

- [ ] **B-Step 6: GREEN — instrument `ConditionalCache`.**
  - Add `hit_count: int = 0` and `miss_count: int = 0` attributes.
  - `get()` bumps `hit_count` when the key is found, `miss_count` when absent.
  - Add `get_metrics() -> dict[str, int]` returning `{"hits": ..., "misses": ..., "size": len(self)}`.
  - Add `reset_metrics()` for test isolation.

- [ ] **B-Step 7: Run GREEN** → all tests pass.

- [ ] **B-Step 8: Commit** `feat(sources): conditional-fetch for RSS/XML (_get_text_conditional) + cache hit/miss metrics`

---

## Phase C — Migrate `nhs_jobs_xml` + observability log

### Tasks

- [ ] **C-Step 1: RED test.** Add to `test_conditional_fetch.py`:

```python
def test_nhs_jobs_xml_uses_conditional_fetch():
    async def _t():
        session = aiohttp.ClientSession()
        try:
            from src.sources.feeds.nhs_jobs_xml import NHSJobsXMLSource
            src = NHSJobsXMLSource(session)
            with aioresponses() as m:
                m.get(NHSJobsXMLSource.FEED_URL,
                      body="<?xml version='1.0'?><vacancies/>",
                      headers={"ETag": 'W/"nhs1"'},
                      content_type="application/xml")
                m.get(NHSJobsXMLSource.FEED_URL, status=304)

                jobs1 = await src.fetch_jobs()
                jobs2 = await src.fetch_jobs()

                assert jobs1 == []  # empty feed
                assert jobs2 == []
                # Cache stored the ETag on the first fetch
                entry = src._conditional_cache.get(
                    (NHSJobsXMLSource.FEED_URL, ())
                )
                assert entry is not None
                assert entry.etag == 'W/"nhs1"'
        finally:
            await session.close()
    _run(_t())
```

- [ ] **C-Step 2: Run RED** → fails because `fetch_jobs` currently calls `_get_text`, not `_get_text_conditional`; cache stays empty.

- [ ] **C-Step 3: GREEN — patch `nhs_jobs_xml.py`**:

```python
# backend/src/sources/feeds/nhs_jobs_xml.py
async def fetch_jobs(self) -> list[Job]:
    xml_text = await self._get_text_conditional(self.FEED_URL)
    if not xml_text:
        return []
    # Observability: log the cache state so prod operators can measure
    # the hit rate without a metrics exporter (Batch 4 scope).
    from src.services.conditional_cache import CONDITIONAL_CACHE_METRICS
    metrics = CONDITIONAL_CACHE_METRICS()  # module-level singleton / aggregator
    logger.info(
        "nhs_jobs_xml conditional fetch complete — cache %s",
        metrics,
    )
    return self._parse_xml(xml_text)
```

(The exact metrics-singleton shape depends on the Phase B decision — instance-level vs module-level. The log statement surfaces whatever ships.)

- [ ] **C-Step 4: Run GREEN** → test passes + existing `test_nhs_jobs_xml_parses_feed` still passes.

- [ ] **C-Step 5: Full suite.**

- [ ] **C-Step 6: Commit** `feat(source): nhs_jobs_xml adopts conditional fetch (Batch 3.5.3 pilot)`

---

## STEP 5 — Verify before completion (no reviewer)

- [ ] **Pre-flight verdict** — paste the script output + table
- [ ] **Migrated sources** — file:line of each `_get_text_conditional()` call
- [ ] **Cache instrumentation** — file:line of `hit_count`/`miss_count` + `get_metrics()`
- [ ] **Test names + counts** — list new tests + grep proof
- [ ] **aioresponses 304 verdict** — does it work, or did we switch primitive?
- [ ] **Pytest delta** — BEFORE / AFTER / NEW / REGRESSIONS
- [ ] **Live-validation acceptance gate** — document deferral (≥50% hit rate over 24h → first-prod-boot observation)

---

## STEP 6 — Handoff

- [ ] `git push -u origin pillar3/batch-3.5.3`
- [ ] Report final SHA
- [ ] STOP

---

## Self-review

**Spec coverage.** Every spec item has a task:
- Pre-flight gate → STEP 1 (already run, 1/5 qualifies)
- Migrate qualifying source(s) → Phase C (single source: `nhs_jobs_xml`)
- `_get_text_conditional` sibling → Phase B-Step 5
- Instrumentation → Phase B-Step 6
- Tests → Phase B and C
- Deferrals (24h cache-hit validation) → scope-out + STEP 5 deferral note

**Placeholder scan.** Line-number placeholders in STEP 5 get filled at verification time. Plan code snippets use concrete names.

**Scope honesty.** 4 of 5 candidates explicitly rejected with reasons. The migration is scoped to the 1 qualifier; no scope padding.

---

_Last updated: 2026-04-19_
