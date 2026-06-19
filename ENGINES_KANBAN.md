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

| ✅ Done & working | 🔨 Built but blocked | 📋 To build / enhance |
|---|---|---|
| 4 scorers coded + unit-tested: seniority `:104-139`, salary `:147-192`, visa `:200-216`, workplace `:231-259` | **Only contributes when an enrichment row exists** (`skill_matcher.py:520-521`); `ENRICHMENT_ENABLED` defaults `false` → dims = 0 in default app | Confirm + fix the dim-id bug, then add an end-to-end value-presence test (rule #21) |
| Sensible curves (seniority penalises big mismatches `:131-139`; salary band-overlap `:179-192`) | **Known prior-evidence bug:** `job.id` unset at scoring time → dims silently 0 *even when enabled* (saved memory `project_dim_scoring_id_bug` — **re-verify before fixing**) | Decide: keep dims as *score adders* (Engine 1 style) or convert to *hard filters* (cleaner ownership — see Overlaps below) |
| Clamp to 100 keeps the +30 from blowing the scale (`skill_matcher.py:537`) | | |

**Verdict:** The math is built and tested in isolation, but in the live app these scores are effectively **always 0** today (flag off + suspected id bug). This overlaps with Engine 4's rubric (the judge also weighs seniority/visa).

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

**Run 2 (bench user, n=19, fresh live fetch + full Claude gold + full judge coverage):**

| Config | NDCG | Spearman | Prec@k |
|---|---|---|---|
| **All (1+2+3+4)** | **0.948** | **0.464** | 0.579 |
| E1 keyword | 0.939 | 0.427 | 0.579 |
| **E2 dimensions** | **0.939** | **0.427** | 0.579 |
| E1+E3+E4 | 0.938 | 0.436 | 0.579 |
| E1+E3 | 0.936 | 0.213 | 0.579 |
| E3+E4 | 0.924 | 0.242 | 0.579 |
| E1+E4 | 0.918 | 0.427 | 0.579 |
| E3 bm25 | 0.911 | **0.004** | 0.579 |
| E4 judge | 0.884 | 0.373 | 0.579 |

**Findings (n=19 overturns part of the n=13 read):**
1. **Engine 1 (keyword) is the backbone** — alone it nearly matches the full stack (0.939 vs 0.948 NDCG; 0.427 vs 0.464 Spearman). **Dropping it clearly hurts** (E3+E4 Spearman 0.242 ≪ E1+E3+E4 0.436). → **Do NOT drop Engine 1.**
2. **Engine 2 (dimensions) is dead** — row *identical* to keyword in BOTH runs. The four dimension scores contribute literally nothing today (`job.id` bug). Highest-value fix.
3. **BM25 alone is weak** (Spearman 0.004 — near-random). Likely because the profile query is a huge skill list, so BM25 matches common ML words everywhere → poor discrimination. It adds a little only in combination. → Engine 3's BM25 is **not** a substitute for Engine 1.
4. Combining everything wins, but only marginally over keyword alone — the overlap/double-count concern is real: extra engines add little once keyword is present.

**How the gold was built:** live `run_search` for the bench user (5799 fetched → only 6 new — the scoring/persist gate, not fetching, caps the catalog at 19), then all 19 graded by Claude (this session). **Caveat:** n=19 is still modest and the gate keeps only score≥30 jobs, so the gold lacks clearly-bad roles that would sharpen the ranking metrics. A wider gold needs the persist gate relaxed (a Pillar-2 config change).
