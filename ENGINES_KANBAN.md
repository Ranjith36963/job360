# Engines Kanban — Job360 scoring/search engines

> **Source of truth = the code, not the docs.** Every claim below is proven with a
> `file:line` you can open. Built from a direct read of the source on 2026-06-16.
> Plain-English: this board tells you, for each engine, what is built and working,
> what is half-built, and what still needs building/enhancing.

## Scoreboard — what is actually ON in the default running app

| Engine | What it does (plain) | Default state | Proof |
|---|---|---|---|
| **1 — Keyword** | Scores each job 0–100 from your profile's titles + skills + location + freshness. Custom code, hand-set weights. | **ON** (no flag) | `skill_matcher.py:488` `JobScorer.score()` |
| **2 — Dimensions** | Adds up to **+30** for seniority/salary/visa/workplace fit, on top of Engine 1. | **OFF** | `skill_matcher.py:519-536` + `ENRICHMENT_ENABLED` default `false` |
| **3 — Hybrid search** | Re-orders jobs by *meaning* (embeddings) fused with keyword order. | **OFF** | `jobs.py:217` early-return unless `SEMANTIC_ENABLED` (default `false`) |
| **4 — LLM judge** | A model reads your full profile + each job and scores fit 0–100 with a reason. | **OFF** | `llm_matcher.py:35` `MATCHER_ENABLED` default `false` |

**Headline fact:** in the default config, **only Engine 1 scores jobs.** Engines 2, 3, 4 are all behind flags that ship `false`.

---

## Engine 1 — Keyword (custom weighted) · `services/skill_matcher.py`

**What it is (plain):** counts how well a job's title and words match your profile's titles/skills, adds a little for UK location and freshness, subtracts for bad-fit titles and foreign locations. Weights are hand-picked: Title 40 / Skill 40 / Location 10 / Recency 10 (`skill_matcher.py:25-28`).

| ✅ Done & working | 🔨 Built but weak | 📋 To build / enhance |
|---|---|---|
| Dynamic keywords from *your* profile, not hardcoded (`JobScorer._title_score` `:454-467`, `_skill_score` `:469-480`) | Title match is substring + word-overlap only (`:459-467`) — can't tell "ML Engineer" from "Sales Engineer" beyond shared words | **No BM25 / TF-IDF term weighting** — a rare, telling word counts the same as a common one. (grep: no BM25 anywhere in code) |
| Skill-synonym expansion (`_text_contains_skill:216`, `skill_synonyms.aliases_for`) | Relevance gate is blunt: suppresses to a floor of 10 (`_gate_suppressed_score:377-399`) | No phrase / proximity matching (e.g. "large language models" as a unit) |
| Negative-title penalty −30 (`:333-338`); foreign-location penalty −15 (`:341-357`) | | No per-term confidence; every primary skill = flat 3 pts (`PRIMARY_POINTS:31`) |
| Date-aware recency that won't trust fabricated dates (`recency_score_for_job:313-330`) | | |
| Final clamp to 0–100 (`:537`) | | |

**Verdict:** Solid, fast, always-on baseline. It is **lexical** (word-matching), not semantic. This is the engine that overlaps with Engine 3's keyword leg.

---

## Engine 2 — Dimensions · `services/scoring_dimensions.py`

**What it is (plain):** four extra checks — does the seniority match you, does the salary overlap your range, does it sponsor a visa if you need one, is the workplace (remote/onsite/hybrid) what you want. Adds up to +30 on top of Engine 1.

> **UPDATED 2026-06-17** — root cause found + the id bug fixed at all sites (strict TDD). Also corrects an earlier mismeasurement (see note).

| ✅ Done & working | 🔨 Built but limited | 📋 Remaining |
|---|---|---|
| 4 scorers coded + unit-tested: seniority `:104-139`, salary `:147-192`, visa `:200-216`, workplace `:231-259` | Dims only contribute when an **enrichment row** exists for the job (`skill_matcher.py:520-521`); enrichment itself is gated by `ENRICHMENT_ENABLED` + `ENRICHMENT_THRESHOLD` | Decide: keep dims as *score adders* or convert to *hard filters* (cleaner ownership) |
| **id bug FIXED at all 3 sites** — `enrichment_lookup` keys on `job.id`, which was unset at score time. Now: run-time re-scores after enrichment with ids (`main.py`); detail recompute uses `_row_to_scoring_job` which sets id (`jobs.py`); `rescore.py` already correct | salary/visa/workplace dims are **computed but have no DB columns** — only seniority is persisted (`main.py:616`); a future migration could store them | Persist salary/visa/workplace dim columns |
| `update_job_scores` persists re-scored dims to the catalog; clamp to 100 (`skill_matcher.py:537`) | **The effect on *ranking* depends on the profile** — a profile with no seniority/salary/workplace preferences makes the dims near-constant, so they don't reorder | |

> **Correction to the earlier eval finding:** the ablation's "E2 dimensions == E1 keyword" did **not** prove dims are dead. The jobs-table dim columns (`role/skill/seniority_score/…`) *sum to `match_score`* — so summing them just reproduced the keyword score. The real issue is narrower: the **enrichment** dims (seniority/salary/visa/workplace) were 0 because of the id bug, now fixed.

**Verdict (now):** The id bug that zeroed the enrichment dims is fixed and unit-proven. And with a **real profile** (graduate / £35–60k / remote / needs-visa), the eval shows the dims are a **genuine ranking signal — Spearman 0.723, beating keyword's 0.688** (Run 5, n=45). With a *sparse* profile they go near-constant and don't reorder — so the dims earn their place only when the user states real preferences.

---

## Engine 3 — Hybrid search · `services/retrieval.py` + live in `api/routes/jobs.py`

**What it is (plain):** instead of pure word-matching, also rank jobs by *meaning* (embeddings) and by BM25 (a smarter lexical score), blend all three rankings, then re-score the top results with a cross-encoder for final precision.

> **UPDATED 2026-06-16** — this engine was just enhanced (strict TDD, full suite green: 1512 passed). BM25 built, cross-encoder wired, the duplicate RRF path merged.

| ✅ Done & working | 🔨 Built but worth watching | 📋 Remaining ideas |
|---|---|---|
| **BM25 lexical ranker** — pure, no new dep (`retrieval.py:bm25_rank`); IDF + tf-saturation + length-norm. 10 TDD tests | Cross-encoder now runs on **every** hybrid request with a profile query — heavy model, but lazy+cached, bounded to top-50, fully guarded | Add a dedicated `RERANK_ENABLED` flag so rerank cost is independently toggleable |
| **3-way RRF fusion** keyword + BM25 + vector (`retrieve_for_user`, extended with `bm25_fn`+`rerank_fn`); 6 new TDD tests, telemetry preserved | Whole engine still gated OFF by default (`SEMANTIC_ENABLED`, rule #18) — by design | Tune RRF `k` / rerank `top_n` empirically (the eval harness) |
| **Cross-encoder reranker WIRED** into the live path (`_hybrid_reorder_rows` → `retrieve_for_user(rerank_fn=...)`); no longer dead code | BM25 indexes `title+description` only — could add company/skills text | Precompute/cache the BM25 corpus instead of per-request |
| **Two RRF paths merged** — live `jobs.py` now calls `retrieve_for_user`; the inline duplicate is gone | | |
| Semantic-leg failure now degrades to keyword+BM25 (not all the way to keyword); 6 TDD tests on the pure reorder core | | |

**Verdict (now):** Engine 3 is the real **"BM25 + vector + RRF + cross-encoder rerank"** — built, wired, tested. Still OFF by default behind `SEMANTIC_ENABLED` (rule #18 preserved: flag off = byte-identical to before). Turning it on needs the `[semantic]` extra installed + a populated vector index.

---

## Engine 4 — LLM judge · `services/llm_matcher.py`

**What it is (plain):** send your full profile + a shortlisted job to a language model; it returns a 0–100 fit score, a short verdict, and a one-line reason.

> **UPDATED 2026-06-16** — judge telemetry added (strict TDD, suite green). You can now see how the judge performed each run.

| ✅ Done & working | 🔨 Built but limited | 📋 Remaining ideas |
|---|---|---|
| `match_job:130` + concurrent `match_batch:180` (semaphore 3, skip-existing) | Only judges jobs with keyword score ≥ `MATCHER_THRESHOLD` (30), max 30/user/run (`:36-39`) — **judge never sees what the funnel dropped** | Tune threshold+max — but **measure first** (eval harness) |
| 4-dimension rubric: domain, seniority, skills, location+visa (`:63-69`) | Uses **free APIs** (Gemini→Groq→Cerebras, `llm_provider.py`) — that is the quality ceiling | Stronger model for the judge (needs a new API key; separate decision) |
| Writes `llm_fit_score`/`verdict`/`reason` to `user_feed` (`save_verdict:153`) | Per-job errors swallowed (`:222-224`) — now **counted** (`failed`), no longer silent | Persist the telemetry into `run_log` (#9 remainder) |
| Re-judge on profile change (`clear_user_verdicts:165`) | | |
| **Judge telemetry (#9)** — `MatcherTelemetry` (judged/skipped/failed + fit min/avg/max), populated by `match_batch`; per-run fit-spread logged by `_run_matcher_stage`. 3 new TDD tests | | |

**Verdict (now):** The funnel→judge plumbing is complete, and you can now **see** its quality each run (how many judged/skipped/failed + the fit-score spread). Two real limits remain by design: it only re-ranks Engine 1's survivors, and it runs on free-tier models.

---

## Overlaps — the "who owns what" / double-counting issue

| Factor | Engine 1 | Engine 2 | Engine 3 | Engine 4 | Risk |
|---|---|---|---|---|---|
| Title/role match | ✅ | | ✅ (keyword leg) | ✅ (rubric #1) | **Triple-counted** if all on |
| Skills | ✅ | | ✅ | ✅ (rubric #3) | Triple-counted |
| Seniority | (penalty only) | ✅ | | ✅ (rubric #2) | Engine 2 ↔ 4 overlap |
| Salary | (tiebreak) | ✅ | | (facts hint) | Engine 2 owns |
| Visa | flag | ✅ | | ✅ (rubric #4) | Engine 2 ↔ 4 overlap |
| Location/remote | ✅ | ✅ (workplace) | | ✅ (rubric #4) | Spread thin |
| Meaning/semantics | | | ✅ | ✅ | Engine 3 ↔ 4 overlap |

**Plain takeaway:** Engine 1's lexical match overlaps Engine 3's keyword leg; Engine 2's dimensions overlap Engine 4's rubric. Turning everything on and summing **double/triple-weights** the same factors. The clean fix is *pipeline, not vote*: search ranks (1 or 3) → judge is final authority (4) → dimensions act as filters (2).

---

## Eval / ablation harness — `scripts/engine_ablation.py`

**Built 2026-06-16 (strict TDD, 13 tests).** Read-only. Ranks jobs by each engine + combinations, scores every ranking against a **Claude-subscription gold** (independent of the free in-app judge). Run: `python -m scripts.engine_ablation --email <you> --golds golds.json` (use `--emit-prompts` first to build the grading prompts).

**Run 5 (n=45, WIDE gold + REAL profile — the trustworthy run):** gold spans fit 5–80 (11 strong / 12 mid / 22 weak, incl. clearly-bad jobs); profile states experience=graduate, £35–60k, remote, needs-visa. NDCG now near-saturated, so **Spearman is the signal**.

| Config | NDCG | **Spearman** | Prec@k | n |
|---|---|---|---|---|
| **E3 hybrid(full)** | 0.956 | **0.815** | 0.244 | 45 |
| **E2+E3** | 0.951 | **0.810** | 0.244 | 45 |
| E1+E3 | 0.953 | 0.798 | 0.244 | 45 |
| E1+E2+E3 | 0.949 | 0.784 | 0.244 | 45 |
| E3+E4 | 0.951 | 0.781 | 0.244 | 45 |
| E1+E3+E4 | 0.945 | 0.772 | 0.244 | 45 |
| All (1+2+3+4) | 0.943 | 0.767 | 0.244 | 45 |
| **E2 dimensions** | 0.928 | **0.723** | 0.244 | 45 |
| E1+E4 | 0.920 | 0.719 | 0.244 | 45 |
| E1+E2+E4 | 0.929 | 0.716 | 0.244 | 45 |
| E1 keyword | 0.924 | 0.688 | 0.244 | 45 |
| E4 judge | 0.922 | 0.604 | 0.533 | 15 |
| - E3 bm25-only | 0.934 | 0.564 | 0.289 | 38 |

**Findings (now trustworthy — Spearman spreads 0.56–0.82):**
1. **E3 full hybrid is the clear winner** (Spearman 0.815) — and **combining other engines into it only dilutes** (E3 alone > E1+E3 > E3+E4 > All). A strong ranker fused with weaker ones via RRF regresses toward the weaker consensus.
2. **Engine 2 dimensions is now a REAL signal — 0.723** — it even **beats keyword (0.688)**. With a real profile the dims differentiate (no longer the flat 12 of Run 4). Validates the Engine-2 id-bug fix AND the dimensions concept.
3. **Keyword (0.688) is the weakest search leg; BM25-alone 0.564** (n=38 — BM25 scores 0 for 7 jobs with no lexical overlap, so they drop out).
4. **Judge has the best precision (0.53) on its top-15** but only judges the shortlist (n=15 coverage) — strong but narrow.
5. Prec@k ≈ 0.244 for the full-45 configs because only 11/45 jobs are "good" (gold≥60) → that's the ceiling; the metric is coverage-bound here, so read Spearman.
6. **Combo sweep (E2+E3, E1+E2+E3, E1+E2+E4, etc.):** every config WITH E3 scores 0.77–0.82; every config WITHOUT it ≤0.72 → **E3 (hybrid) is essential**. The best *combo* is **E2+E3 (0.810)** — dims are the most *compatible* partner to the hybrid (orthogonal preference signal, minimal dilution), whereas keyword/judge dilute it more. Judge-alone (0.604, n=15) and BM25-alone (0.564, n=38) are the weakest single signals.

---

## Run 6 — STRONG: confidence intervals + significance (full report: `docs/engine_eval_report.md`)

**What the Claude benchmark actually is:** a fixed set of **45 `(job → fit 0–100)` judgments** made by **Claude (Opus, this session)** reading each job's full posting and scoring fit against the profile + a fixed rubric (domain/role · seniority · skills · location+remote+visa · salary). It is an **independent, premium reference** — NOT the in-app free-LLM judge (Gemini/Groq/Cerebras). Each engine is scored on how well its ranking orders jobs by *this* fit. The benchmark is saved + reproducible at **`docs/engine_eval_gold.json`**.

**Metrics (both, with 95% bootstrap CIs):** NDCG (best-at-top, the priority) + NDCG@5/@10 (sharper top focus) + Spearman (whole-list order). CIs come from resampling the 45 jobs 2000× (seeded).

**Significance verdict (top config E3 hybrid vs the rest, paired bootstrap of ΔNDCG):**
- **E3 hybrid > E1 keyword: SIGNIFICANT** (ΔNDCG +0.032, 95% CI [0.003, 0.091] — excludes 0).
- **vs everything else: NOT significant** — E1+E3, E2+E3, E3+E4, dims, judge all have CIs that overlap E3. **At n=45 they are statistically tied.**
- Spearman CIs are wide (E3 0.815 **[0.64, 0.90]**; judge 0.604 **[0.02, 0.93]**) → n=45 cannot finely separate the strong configs. A bigger gold (n≈100+) is needed to do that.

**Per-config "why" (from each config's top-5, see report):**
- **E3 hybrid / E2+E3 / E3+E4 (top tier):** top-5 are all genuine AI/ML internships (gold 62–74). The semantic vectors + reranker understand *meaning*, so they avoid title-traps.
- **E1 keyword:** puts job **#26 "Remote AI/ML Engineer" (gold 30 — actually an advisory/tax firm)** in its top-5. Keyword matches the *title* and gets fooled. This trap is exactly why it's the one config E3 significantly beats.
- **E4 judge:** also ranks the #26 trap (gold 30) and a senior role (#1, gold 42) high in its top-5, and only sees 15 jobs → noisy, wide CI.
- **E3 bm25-only:** grabs a senior CUDA/Rust misfit (#2, gold 28) via lexical overlap → noisier than full hybrid.
- **E2 dimensions:** top-5 are clean internships — it ranks by *preference fit* (graduate/remote/salary), which favors the right roles; that's why it scores a real 0.723.

**Bottom line (statistically honest):** E3 hybrid is the **safe top pick** and is the only one *provably* better than plain keyword. Beyond that, **the data can't yet crown a single winner among the E3-based configs** — treat E3, E2+E3, E3+E4 as a tied top tier. To separate them, widen the gold to ~100+ jobs.

**How the gold was built:** profile updated via `save_profile` (version 2); one `run_search` with `MIN_MATCH_SCORE→1` grew the catalog 19→3,536 (real bad/irrelevant spread); `rescore_user_feed` refreshed keyword + re-judged the top vs the new profile; a stratified 45-sample (15 high / 15 mid / 15 low) graded by Claude → fit 5–80. **Caveat:** coverage varies by engine (E4 n=15, BM25 n=38); n=45 is solid but not huge.
