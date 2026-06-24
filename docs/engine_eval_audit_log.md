# Engine-Eval Measurement-Tool Audit Log

Running record of every weakness found in the eval ("measurement tool") and how
it was fixed. The goal: a tool trustworthy enough to decide which engines to
keep/remove. **Until an issue is fixed, any conclusion it touches is void.**

Engines: **E1** keyword · **E2** dimensions · **E3** hybrid (BM25+vector+rerank) · **E4** LLM judge.
Gold grader = **Opus 4.8** (me), blind. In-app judge (E4) = free Gemini/Groq/Cerebras chain (a *different* model family — important for independence).

---

## Iteration 1 — v1 eval (24-job, keyword-pooled). Verdict: **NOT trustworthy.**

| # | Issue | Severity | Evidence | Fix (in v2) |
|---|---|---|---|---|
| 1 | **E2 degenerate** — only 6/24 jobs enriched, 19 scored 0 → ties → ranking inherits feed order | 🔴 | distinct dim values ≈ 3 | enrich **every** pooled job (now 54/54, 43/43; 11–13 distinct values) |
| 2 | **Grader leak** — the grading sheet showed `kw=` scores; I graded while seeing E1 | 🔴 | sheet lines `== id \| kw=64 ==` | **blind** sheet: no scores, shuffled order |
| 3 | **Pooling bias** — the 24 jobs were chosen *by keyword*; other engines never surfaced candidates | 🟠 | `prep` sorts by E1 score | **fair pool** = union of keyword ∪ BM25 ∪ vector top-K |
| 4 | **Grader≈judge family** — an LLM graded the LLM judge | 🟠 | both LLMs | blind grading + grader (Opus) ≠ judge model + **leak-check** correlations reported |
| 5 | **E3 = keyword clone** — vector leg covered only 15–18/24; hybrid order 0.75 corr w/ keyword | 🟡 | order corr 0.75 | embed **every** pooled job → corr drops to **0.25 / 0.52** |
| 6 | **Tie-break contamination** — equal/None scores kept feed (keyword) order | 🟡 | stable sort | **seeded-random** tie-break; drop no-signal jobs from an engine's ranking |
| 7 | **No statistics** — single point numbers, no CI / significance | 🟡 | — | bootstrap 95% CI + paired-significance test |

**v2 leak-check proved the fixes worked:** gold↔keyword = 0.016 / 0.428 (no anchoring); judge↔keyword = −0.34 / 0.10 (judge not parroting keyword); judge tracks gold better than keyword for Pavan (0.669 vs 0.428).

**v2 result that v1 got wrong:** on NDCG every config's 95% CI **overlaps** → *no engine is significantly best*. v1's confident "E2+E4 wins / E3 hurts / drop E3" was an artifact. **Do not remove any engine on v1.**

---

## Iteration 2 — stability under repeated runs. Verdict: **one noise source (E4); now fixed.**

Ran the tool many times per profile to test whether it gives the **same** answer. Three noise sources:

| Source | Method | Finding |
|---|---|---|
| **Tie-break / bootstrap seed** | re-score over 12 seeds | ✅ **STABLE** — NDCG std ≤ 0.004; winner held 12/12 (Ranjith). Not a problem. |
| **LLM judge (E4) stochasticity** | re-judge pool 2–3× | 🔴 **UNSTABLE** — see #8. Quantified + fixed. |
| **Non-judge engines (E1/E2/E3)** | across judge runs | ✅ **DETERMINISTIC** — NDCG std = **0.000**. The noise is *isolated to E4*. |

### Issue #8 — E4 (LLM judge) is stochastic 🔴 → FIXED
The same job gets different fit scores each judge run. Measured over 2 runs:
- **Ranjith:** per-job score std mean 4.9, **max 17.5; range up to 35 points** (a job scored 50 one run, 85 the next).
- **Leaderboard winner FLIPS between runs:** Ranjith run1 → "E4 judge wins", run2 → "E1 keyword wins"; Pavan run1 → "E1+E2+E3", run2 → "E4 judge". **distinct single-run winners = 2 on both profiles.** A one-shot run would keep/remove the wrong engine.
- Only E4-containing configs are noisy (std ~0.03); every E1/E2/E3 config is std 0.000.

**Fix (now the default in `score_v2.py`):** E4 = **median of K independent judge runs** per job (`_median_judge`, reads `eval_v2_judge_runs.json`). With K=2 the swing already collapses toward the central value; production should use **K ≥ 3 (ideally 5)**. The note "E4 stabilized = median of N judge runs" prints on every run so the reader knows.

### Issue #9 — occasional judge job-failure 🟡 → FIXED
A judge round sometimes returns 53/54 (one provider timeout/parse fail). The median-of-K handles it: a job missing in one run uses the runs that succeeded; jobs missing in ALL runs are dropped as no-signal (never zeroed). Coverage is printed (`judge=53` vs 54).

---

## Where the trustworthy tool now stands

**Fixed:** all 7 v1 issues + #8 (judge stochasticity) + #9 (judge failures). Seed/tie-break proven stable; non-judge engines proven deterministic; leak-check proven clean (gold↔keyword 0.016/0.428, judge not parroting keyword).

**The honest verdict it produces (and this is the point):** even fully hardened, on these **two same-domain (AI/ML) profiles** the engine NDCG differences sit **inside the 95% CIs** — so the tool **refuses to crown a confident winner**, and that refusal is correct. **Do not remove any engine on this data.** The only stable signals: single engines (BM25-alone, keyword-alone) order worst; the judge helps but must be averaged.

**To actually separate engines confidently (next data, not a tool bug):** diverse profiles spanning *different* fields (not two AI/ML people) + a larger graded pool + K ≥ 3 judge runs. The v2 harness scales to exactly that.

---

## Iteration 3 — hunt for bias INSIDE the eval (ran each profile repeatedly). Found 2 real biases.

### Issue #10 — NO STABLE GROUND TRUTH for the Ranjith profile 🔴 (the big one)
Two independent expert graders — **my Opus gold vs the Gemini judge** — were compared:
- **Ranjith: inter-rater Spearman = 0.070** (53 jobs). The two graders **barely agree.**
- **Pavan: inter-rater Spearman = 0.662** (43 jobs). The two graders **agree well.**

Meaning: for Ranjith the candidates (54 near-identical *mid-level AI/ML* jobs) are so similar that **there is no agreed "right" ranking** — even two strong models disagree. So *every* engine's correlation with the Ranjith gold is near zero (keyword 0.02, dims 0.16, bm25 −0.20, hybrid −0.13, judge 0.07). **The eval was measuring NOISE for Ranjith, not engine quality.** This is why Ranjith's winner flipped and correlations were ~0 — *not* the engines' fault, and *not* fixable by better engines; the task itself has no ground truth at that granularity.
**Fix:** the tool now computes inter-rater agreement and **declares the profile's ranking INVALID when it's < 0.3** — it refuses to report an engine winner when there's no reliable gold to rank against. (A bad tool would have crowned a random winner.)

### Issue #11 — POOLING SELF-SELECTION (home-field) bias 🟠
Even with the fair union pool, each retriever predicts the gold **better on the jobs it itself contributed** than on jobs other engines found. On the *reliable* profile (Pavan):
| Engine | corr-with-gold on its OWN jobs | on OTHER engines' jobs |
|---|---|---|
| keyword | **0.520** | 0.183 |
| bm25 | 0.250 | 0.043 |
| hybrid | 0.448 | **0.751** |

So **keyword is INFLATED by the pool** (0.52 home vs 0.18 away) and **hybrid is UNDER-rated** (0.45 home vs 0.75 away). The unbiased (away-jobs) view *flips the read*: on Pavan, **hybrid (E3) is actually the strongest retriever, not keyword.** The earlier "keyword is fine / E3 is weak" was partly this bias.
**Fix:** the tool now reports per-engine OWN-vs-OTHER correlation and treats the **OTHER-jobs (held-out) correlation as the less-biased engine-quality estimate.** Full de-biasing (decoy jobs / leave-own-out scoring) is the next hardening step.

---

## Bottom line after 3 iterations
The measurement tool is now honest about its own limits:
- It **refuses to rank engines when graders don't agree** (#10) — so it can't hand you a false winner.
- It **flags and corrects its own pooling bias** (#11) — so keyword's home-field inflation no longer fools it.
- It **averages the stochastic judge** (#8) and is otherwise deterministic (#seed-stable).

**For your two profiles specifically:** Pavan's eval is trustworthy (graders agree 0.66) and there the de-biased order is **hybrid ≈ judge > dims > keyword > bm25**. **Your (Ranjith) eval is NOT trustworthy** — no ground truth (0.07) — so *no* engine verdict can be drawn from it. Neither result justifies removing an engine.

---

## Iteration 4 — diverse-field profiles. THE EVAL FINALLY HAS POWER + first trustworthy verdict.

Added 3 real CVs in **different fields** (the missing ingredient): CRajappa (Cyber/SOC, senior), Rohith (Data-Engineer, senior), Sofia (Cyber pen-test, junior). Same trustworthy harness (fair pool, enrich+embed all, blind grade, median judge).

**Inter-rater agreement jumped — the eval now works on most profiles:**
| Profile | Field | Opus↔Gemini agree | Usable? |
|---|---|---|---|
| Ranjith | AI/ML mid | 0.01 | ❌ |
| Pavan | AI/ML junior | 0.72 | ✅ |
| CRajappa | Cyber senior | **0.88** | ✅ |
| Rohith | Data-eng senior | **0.86** | ✅ |
| Sofia | Cyber junior | 0.22 | ❌ |

→ **3 of 5 now have a trustworthy gold** (vs 0/2 with only AI/ML). Diverse fields = separable jobs = graders agree = the eval gains the power it lacked. This confirms the standing diagnosis: the blocker was *data* (too-similar profiles), not the tool.

**First trustworthy engine verdict (engine-vs-gold Spearman on the 3 usable profiles) — same order every time:**
| Engine | Pavan | CRajappa | Rohith |
|---|---|---|---|
| **E4 Judge** | **0.72** | **0.88** | **0.86** |
| E3 Hybrid | 0.56 | 0.53 | 0.58 |
| E2 Dims | 0.48 | 0.40 | 0.61 |
| E1 Keyword | 0.43 | 0.13 | −0.31 |
| BM25 | 0.21 | 0.08 | −0.46 |

**Verdict: E4 (Judge) ≫ E3 (Hybrid) ≈ E2 (Dims) > E1 (Keyword) > BM25** — consistent across 3 fields. Keep all in a funnel; lead ranking with the Judge; keyword/BM25 are the cheap recall net, not trusted rankers (keyword went *negative* for the senior data-eng — overlapping tech terms surface wrong-level jobs).

### Issue #12 — career-pivot CV confuses keyword 🟡 (new finding)
Sofia's CV lists her *past* (Social Media Manager) and *target* (Cyber). Keyword matched the old titles and pulled **social-media jobs** into her pool, ranking them high against a cyber-focused gold → her inter-rater dropped to 0.22 and keyword/dims went strongly negative. The judge (reads the career objective) handled it. **Real product weakness: the keyword engine breaks on career-changer CVs; lean on the judge there.**

**Caveat:** CRajappa/Rohith at K=1 judge run when first measured (K=3 stabilization running); inter-rater 0.88/0.86 is decisive — more runs tighten, don't reorder.

---

## Standing limitation (honest, not a bug)
Both test profiles are **AI/ML** → the fair pool is one uniform domain → low statistical power even with a perfect tool (overlapping CIs). To separate engines *confidently* needs **diverse profiles (different fields) + a larger graded pool**. The v2 harness is built to scale to that.
