# Engine Evals Plan — making search + match + score trustworthy, accurate, reliable, fast

> Plan for **how to pick engines** and **how to make the measurement (evals) good enough to trust that choice**, plus the accuracy/reliability/latency work that follows.
> Context: see `docs/engine_eval_audit_log.md` for the 11 issues found+fixed across 3 audit rounds, and `engine_eval_gold_v2_blind.json` for the current trustworthy harness.

Engines: **E1** keyword · **E2** dimensions (seniority/salary/visa/workplace) · **E3** hybrid (BM25+vector+rerank) · **E4** LLM judge.

---

## The core insight that drives everything

**Don't pick engines by "who won the eval."** The eval cannot crown a winner yet — the differences between engines are smaller than the noise (overlapping CIs; the single-run winner flips every run). The right rule is:

> **Keep an engine if it adds *independent* signal at an acceptable *cost*.**

An engine earns its place if it sees something the others can't, cheaply enough. That reframes the decision away from a noisy leaderboard.

---

## Part A — Make the eval trustworthy enough to decide

The blocker is the **data**, not the tool. Fix in priority order:

1. **Diverse profiles (5–8, different fields)** — data analyst, cyber, frontend, DevOps… NOT two AI/ML people. Different fields → separable jobs → graders agree → the eval finally gets statistical power. **Biggest lever.**
2. **Bigger graded pool (~100–150 per profile)** → tighter error bars.
3. **Measure recall, not just ranking** — inject good jobs *no engine surfaced* (decoys) and check whether engines miss them. Today we only measure "did it rank the pool well," not "did it find everything."
4. **Stronger gold:**
   - agreement-gated — keep only jobs where **2+ graders agree** (Opus + Gemini, ideally + a human);
   - **coarse buckets** (great / ok / bad) where fine 0–100 ranking is unreliable;
   - add **1 real human label** per profile where possible.
5. **Keep the fixes already shipped:** median-of-K judge (#8), bootstrap CIs + significance (#7), validity gate that refuses to rank when graders disagree (#10), de-biased pooling report (#11).

---

## Part B — How to pick engines (the decision rule)

| Engine | Keep? | Independent signal it adds | Cost |
|---|---|---|---|
| **E1 keyword** | ✅ always | fast, deterministic baseline; cheap recall | ~0 ms |
| **E3 hybrid** | ✅ keep | *meaning* (0.25–0.5 corr w/ keyword = genuinely independent); de-biased, the strongest retriever | low (precompute) |
| **E2 dimensions** | ✅ keep | the **only** engine that reads *level / salary / visa / workplace* — the exact trap that fools keyword (intern vs mid) | low (needs enrichment data) |
| **E4 judge** | ✅ keep — **funnel-only** | best per-job context + nuance | **high** (slow + $ + stochastic) |

**Verdict: keep all four, arranged as a funnel** — cheap engines do the heavy lifting; the judge does the final polish on the few jobs that matter.

---

## Part C — Make search + match + score more accurate & reliable

1. **Funnel architecture (the key):**
   `E1 + E3 retrieve` the long list → `E2` re-ranks cheaply (level/salary/visa fit) → `E4 judges only the top ~20–30`. (Already the design — reinforce it.)
2. **Tame the judge's dice-roll in production:** low temperature + forced structured output + explicit rubric → less variance; **ensemble 2–3 calls + median** for the top jobs only.
3. **Cache judge verdicts** keyed by `(profile_version, job_id)` → never re-judge the same pair; re-judge only when the profile changes (already partly built via profile-version rescore).
4. **Fix keyword's level-blindness:** lean on E2/E4 for seniority, or add a cheap level-match signal so keyword stops ranking interns high for a mid-level candidate.
5. **Calibrate** each engine to a common 0–100 before fusing (RRF is rank-based, so already fairly robust to scale differences).

---

## Part D — Latency / cost

- **E1, E2, E3 = milliseconds** → run on every job, always.
- **E4 = seconds + $ per call** → never run on the full list:
  - judge only the funnel **top-N** (`MATCHER_MAX_JOBS ≈ 30`),
  - **cache** verdicts (re-judge only on profile change),
  - **batch** with bounded concurrency (semaphore).
  - → E4's cost is paid once per profile-change and amortized to ~0 per view.
- **Precompute** embeddings (E3) + the BM25 corpus at ingest, not at query time.
- **Net:** search feels instant (cheap engines) while still getting the judge's quality on the jobs that actually reach the user's screen.

---

## Sequenced next steps

1. **(highest value)** Run the trustworthy eval on **5–8 diverse-field profiles** — collect a few friends' CVs across different fields. This is the one move that turns "can't tell" into a trustworthy engine verdict.
2. Add the **recall / decoy** test to the harness.
3. Add **agreement-gated, coarse-bucket** gold + (where possible) one human label.
4. In production: **cache judge verdicts**, lower judge temperature + structured output, ensemble-median the top-N.
5. Re-run; only then publish an engine keep/drop decision with confidence.

**Until step 1 is done, the standing recommendation stands: keep all four engines in a funnel; do not remove any on current data.**
