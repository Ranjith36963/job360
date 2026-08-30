<!-- doc: LIVING | last-verified: 2026-08-24 by /sync -->
# Job360 — The Three Pillars

Job360 is built as three architectural pillars. This folder documents each one from the code up — what it does, how it works, and where it stands today.

| # | Pillar / Doc | What it owns | Doc |
| --- | --- | --- | --- |
| 1 | **User Side** | Identity, profile (CV/LinkedIn/GitHub), per-user delivery (feed, channels, notifications, pipeline), the Next.js dashboard | [`01-user-pillar.md`](./01-user-pillar.md) |
| 2 | **Search & Match Engine** | The 6-stage pipeline: fetch → prefilter → score → dedup → enrich → store. Scoring, embeddings, retrieval, scheduler, breakers | [`02-search-and-match-engine.md`](./02-search-and-match-engine.md) |
| 3 | **Job Providers** | The 40 source classes, the shared `BaseJobSource`, the ATS company catalog, the source roster | [`03-job-providers.md`](./03-job-providers.md) |
| — | **Glossary** | Plain-English definition of every domain term used across the three pillar docs | [`glossary.md`](./glossary.md) |
| — | **Runbook** | "I see a problem, what do I do?" — operational answers across all three pillars (DB queries, debug commands, error→fix table) | [`runbook.md`](./runbook.md) |

Each pillar doc also has three "manual" sections inside it:
- A **walkthrough** (worked example) early — Pillar 1 traces one user (Alice) from signup to notification; Pillar 2 traces one posting through all 6 engine stages with a final score of 93; Pillar 3 traces one source's fetch cycle with breaker + retry behaviour.
- **Environment variables** — every env var that pillar reads with default + effect.
- **Failure modes** — symptom → cause → fix table for the most common breakages.

## How the pillars connect

```
   PILLAR 3                  PILLAR 2                       PILLAR 1
   Job Providers             Search & Match Engine          User Side
   ─────────────             ─────────────────────          ─────────
   41 sources                run_search():                  profile → SearchConfig
      │  fetch_jobs()           prefilter                       │  (feeds keywords IN)
      ▼                         score (9-dim)                   ▼
   list[Job] ───────────────▶  dedup (4-layer)  ──────────▶  user_feed (SSOT)
                               enrich (opt-in)                  │
                               store → jobs catalog             ▼
                                                             dashboard + channels
```

- **Pillar 1 feeds INTO Pillar 2**: the user's profile becomes a `SearchConfig` (`keyword_generator.py`) that tells the engine what to look for and how to score it.
- **Pillar 3 feeds INTO Pillar 2**: every source returns `list[Job]`; the engine takes it from there.
- **Pillar 2 feeds INTO Pillar 1**: scored, deduped jobs land in `user_feed`, which both the dashboard and the notification worker read.
- The **shared `jobs` catalog** is the seam: Pillar 3 + Pillar 2 write it (no `user_id`), Pillar 1 reads it through per-user overlay tables (`user_feed`, `user_actions`, `applications`).

## Cross-pillar status snapshot (2026-05-28, HEAD `a7a2268`)

| Pillar | Core | Advanced / opt-in | Notable gaps |
| --- | --- | --- | --- |
| **1 — User** | ✅ Auth (Argon2id + signed cookies), profile (CV/LinkedIn/GitHub, LLM-only), feed, actions, pipeline, channels, notification rules, ledger | 🟡 ARQ worker deployment install-dependent; ESCO normalisation behind `SEMANTIC_ENABLED` | ✅ password reset (forgot-password, migration 0015); 🟡 email verification (built, not enforced at login); ❌ MFA, OAuth, push notifications; ⚠️ FE/BE types hand-synced |
| **2 — Engine** | ✅ 9-dim scoring, 3-stage prefilter, 4-layer dedup, tiered scheduler, circuit breakers, conditional cache | 🟡 enrichment + embeddings + hybrid retrieval (both flags default OFF) | ⚠️ legacy `score_job()` scores against empty `keywords.py`; ❌ LLM cost tracking, re-embedding on model change |
| **3 — Providers** | ✅ 40 classes / 41 keys, `BaseJobSource` retry+rate-limit, ATS catalog (302 slugs), post-2026-08-10 roster | 🔴 conditional fetch adopted by **no** source — tests are its only callers | ⚠️ HTML scrapers brittle to markup changes; 🟡 `RIPPLING_COMPANIES` slugs remain with no source class; ❌ per-source health dashboard |

**Test baseline:** 600 passed / 0 failed / 3 skipped (post-3.5.4 green baseline).

## The two facts every contributor should internalise

1. **The system requires a user profile.** `backend/src/core/keywords.py` was emptied on 2026-04-09 (commit `3ba1342`). There are no AI/ML defaults any more — without a profile, the legacy scoring path produces near-zero results. Job360 is now a *generic* jobs platform that is *personalised by the profile*, not an AI-jobs aggregator.

2. **Shared catalog, per-user overlay.** The `jobs`, `job_enrichment`, and `job_embeddings` tables have **no `user_id`** by design (hard rules #1, #10, #17). The same job row and the same enrichment serve every user; all per-user state (visibility, actions, applications, notifications) lives in separate user-scoped tables joined by `job_id`. Per-user *scoring* against the shared catalog happens at read time.

---

*Each pillar doc carries its own detailed status matrix, file reference tree, and "what this pillar does not cover" pointers. Start with the pillar that matches your task.*
