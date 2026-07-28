# Observability Plan — seeing what the 47 sources actually do

**Written 2026-07-28. Every number below was read from a live instrument, not from code or memory.**

## 1. What the instruments say RIGHT NOW (verified)

### Sentry (org `job360`, project `python-fastapi`, de.sentry.io) — 2 unresolved
| Issue | Meaning |
|---|---|
| `[aijobs_ai] STRUCTURE CHANGED: expected job-link anchor pattern not found in a 161928-byte response` — 12 events, first seen 3d ago | A scraper is **broken now**. Structure-change detection works; nothing pushed it to a human for 3 days. |
| `ProgramLimitExceeded: index row size 3128 exceeds btree maximum 2704 for index jobs_normalized_company_normalized_title_key` — 3 events | **DATA LOSS.** Jobs with long company+title fail to insert and vanish. Free-tier quality bug. Fix: hash or truncate the unique key. |

### PostHog — the free-tier funnel, 30 days
| Event | Instrumented | Fired |
|---|:—:|---|
| `$pageview` | yes | 248 (30 users) |
| `signup_completed` | yes | **0** |
| `cv_uploaded` | yes | 3 (1 user) |
| `extraction_completed` | yes | 3 (1 user) |
| `search_run` | yes | 7 (2 users) |
| `job_viewed` | yes | **1** |
| `application_created` | yes | **0** |

**Read: 30 visitors → 0 signups → 1 CV → 1 job view → 0 applications.** The funnel is fully
instrumented; the problem is that nobody completes it. That is the free-tier fix list.

## 2. The 7 metrics that matter per source
| Metric | Decision it drives |
|---|---|
| `jobs_fetched` per source per run | fix-or-drop — a source "succeeding" with 0 jobs is broken |
| zero-job **streak** | when to alert (1 = noise, 3+ = dead; already computed in `runs.py`) |
| **new-after-dedup** per source | which ~20 of 47 sources actually add value |
| error rate + last error string | triage without opening logs |
| fetch duration p95 | catches a source drifting toward the timeout ceiling |
| **% of jobs surviving the score gate** | liveness AND relevance in one number |
| 7-day total trend | "is today normal?" |

## 3. Tool split — do not over-tool
| Tool | Owns | Never |
|---|---|---|
| **Postgres + `/admin/sources`** | all per-source health (Sentry/PostHog can't join `run_log`) | — |
| **Sentry** | exceptions + ONE alert: "source critical >24h" | don't send metrics here |
| **PostHog** | user funnel only (signup→CV→search→view→apply) | never pipeline data |
| **Railway** | raw logs, last resort | don't build on it |
| ❌ **Do NOT add** | Grafana, Prometheus, Datadog, OpenTelemetry, a metrics table | pre-launch over-engineering |

Note: `backend/ops/exporter.py` + `grafana_dashboard.json` already exist as an unwired
Prometheus exporter (port 9310, referenced by no deploy config). Leave it dead; don't revive it.

## 4. Build order
1. **FIRST — fix the btree data-loss bug.** It silently deletes good jobs. One migration.
2. **Daily digest email to yourself** (Resend + ARQ cron already exist): 47 rows — jobs fetched,
   new-after-dedup, streak, last error, traffic light. *A dashboard you must remember to open is
   dead; a push you read with coffee is not.* ~1 day. **Highest leverage.**
3. Add `new-after-dedup` + gate-survival % to the existing `/runs/source-health` + admin page.
4. Fix the funnel drop-off the PostHog numbers expose (0 signups is the top of the leak).
5. Later: Sentry alert rule, p95 duration, 7-day sparkline.

## 5. Measuring job QUALITY (not just "did rows come back")
- **Automatic:** % of jobs per source scoring ≥30 — everything is already scored, just `GROUP BY source`.
- **Behavioural:** join `user_actions` to source — dismiss-heavy sources hurt trust.
- **Manual, 15 min weekly:** open 10 random jobs from the top-3 sources; check the link resolves to a
  live UK role. Nothing automated catches "stale 60 days and the URL 404s". `ghost_detection.py`
  exists — surface its per-source hit rate on the same page.

## 6. Known gaps (from a code audit — RE-VERIFY, it was read on a tree 261 commits behind main)
- Per-source counts are **raw fetch counts, pre-dedup** — no "unique jobs contributed by this source".
- Dedup layer hit-rates, score distributions, and LLM cost/token usage are counted but **never read**
  (`enrichment_telemetry` / `matcher_telemetry` have zero consumers).
- Circuit-breaker state is an **in-memory module singleton** — lost on restart, invisible across
  processes, no endpoint exposes it.
- Scheduler runs with `force=True` every run, so the 60s/5m/15m/60m tiers are **defined but not
  enforced** in the single-run path.
- Conditional-cache hit/miss counters reset every run (instances are rebuilt per `run_search`).
- `GET /sources` returns a hardcoded empty `health={}`.

## 7. The trap to avoid
Building a beautiful real-time dashboard nobody opens. **The failure mode is silence, not missing
charts.** One digest email + one Sentry alert covers ~90% of the value. Resist persisting breaker
state and "doing Prometheus properly" until real users complain.
