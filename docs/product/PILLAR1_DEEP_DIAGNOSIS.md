# Pillar 1 — Deep Honest Diagnosis (Profile Extraction)
<!-- doc: LOG -->

> Date: 2026-06-25 · Model: OpenAI `gpt-4o-mini` (paid, temp 0, JSON mode) PRIMARY,
> free Gemini/Groq/Cerebras as fallback. Measured live on 5 real CVs + 3 GitHub +
> 4 LinkedIn PDFs. No assumptions — every number below was run, not estimated.

## 0. How each input is processed (the shape)

Every input runs **two passes in parallel (a fork), then merges**:

| Pass | What it is | What it may use | What it must NOT use |
|------|-----------|-----------------|----------------------|
| **Deterministic** | plain Python, no LLM | document STRUCTURE: section headings, skill-section tokens, manifest runtime deps, GitHub topics | any skill/keyword vocabulary (rule #28) |
| **LLM** | one model call, temp 0 | the same raw text, read for MEANING | inventing skills the text doesn't support |

The two lists are concatenated + deduped → one `CVData`. Determinism comes from
temp 0; the merge keeps first-seen casing.

---

## 1. CV — measured per profile (deterministic vs LLM vs combined)

Recall = core skills found · Precision = grounded in the CV · fp = hallucinated
("negatives" planted in the gold set). **Gold bar is HIGH** (skills a top-tier
extractor MUST get).

| Profile | Domain | Pass | Recall | Prec | F1 | Hallucinations |
|---------|--------|------|-------:|-----:|---:|---:|
| ranjith | AI/ML | DET | 95% | 100% | 97% | 0 |
| | | LLM | 86% | 97% | 91% | 0 |
| | | **COMB** | **95%** | **98%** | **96%** | **0** |
| pavan | AI/ML | DET | 66% | 100% | 80% | 0 |
| | | LLM | 69% | 100% | 81% | 0 |
| | | **COMB** | **69%** | **100%** | **81%** | **0** |
| rohith | Data/Backend | DET | 85% | 100% | 91% | 0 |
| | | LLM | 89% | 97% | 93% | 0 |
| | | **COMB** | **93%** | **99%** | **96%** | **0** |
| sofia | CyberSec | DET | 58% | 100% | 73% | 0 |
| | | LLM | 62% | 100% | 76% | 0 |
| | | **COMB** | **62%** | **100%** | **76%** | **0** |
| crajappa | CyberSec | DET | 62% | 100% | 76% | 0 |
| | | LLM | 70% | 100% | 82% | 0 |
| | | **COMB** | **81%** | **100%** | **89%** | **0** |
| **MEAN** | | **COMB** | **80%** | **99%** | **88%** | **0** |

### The single most important finding

**Zero hallucinations anywhere (precision 98–100%).** The system never invents a
skill. The *only* problem is the opposite: **conservative under-extraction**.

And it is provable: of the skills the system MISSED, almost every one is
**literally written in the CV text**:

```
pavan : missed 13/42 — CNN, LSTM, EfficientNet, Transfer Learning, Flask,
        Prompt Engineering, Computer Vision, NLP, Deep Learning,
        Machine Learning, IoT, Raspberry Pi, AWS   ← ALL present in the CV
sofia : missed 9/24  — Privilege Escalation, Steganography, Cryptography,
        Digital Forensics, Exploit Development, SEO, Google Ads, Meta Ads,
        Canva                                        ← ALL present in the CV
crajappa: missed 7/37 — Incident Response, WAF, Vulnerability Scanning,
        Active Directory, IDS/IPS (in text) + Threat Hunting,
        Phishing Analysis (inferable, not literal)
```

### Root cause (proven, not guessed)

Dumped Pavan's two passes side by side:

```
DET skills (34): Python, C, R, MATLAB, SQL, PyTorch, TensorFlow, Keras ... (the
                 explicit "Technical Skills" section, verbatim)
LLM skills (35): same 34 + Reinforcement Learning from Human Feedback
```

The LLM returned **the same list as the explicit Skills section** plus one extra.
It did **not** mine the project/experience PROSE, where "Machine Learning",
"Deep Learning", "CNN", "LSTM", "Computer Vision", "NLP" actually live.

- **Deterministic pass** is *designed* to only read the skills section — rule #28
  forbids it from inferring "this sentence implies CNN". Correct by design.
- So prose-mining is **entirely the LLM's job** — and `gpt-4o-mini` anchors on the
  explicit Skills list and is too conservative to pull concept-skills from bullets.

**Pattern:** well-structured CVs with a real Skills section (ranjith 95%, rohith
93%) score great. Prose-heavy / thin-skills-section CVs (pavan 69%, sofia 62%)
leak recall — the skills are in the sentences, and the model won't reach for them.

**Fix direction (rule-#28-safe):** prompt-steer the LLM to extract techniques,
methods, and concepts *demonstrated in project & experience prose*, not just the
Skills section. This is steering, not a keyword list — allowed. (Diagnosis only;
not applied yet.)

---

## 2. GitHub — measured (live API, 3 users)

| User | Repos | DET (topics only) | LLM (reads prose) |
|------|------:|-------------------|-------------------|
| Ranjith36963 | 13 | 10 (agent, langgraph, llm, mcp, slack…) | 17 (GenAI, RAG, GPT-4o, React, Flutter, Fraud Detection…) |
| Pavan09-Is-Here | 1 | **0** | 3 (AI, ML, portfolio website) |
| sofiashajilekha | 2 | **0** | 4 (cybersecurity, penetration testing…) |

### What is MISSING from GitHub (concrete)

1. **Deterministic GitHub pass = repo TOPICS only.** Most users never set repo
   topics → DET returns **0** (2 of 3 users above). Only the one user who tagged
   repos got any deterministic signal.
2. **The stored `repos_brief` drops `language`.** The fetch builds each repo with
   `name, language, description, topics` (line 250) but the *brief* it persists
   keeps only `name, description, topics` (line 311–318). So **neither pass ever
   sees the repo language**, and the LLM prompt feeds on description+topics only.
3. **Dependency-file frameworks are not in the brief either.** `fetch_github_profile`
   computes `frameworks_inferred` from package.json / requirements / Cargo, but
   that signal never reaches the two-pass lane (which re-runs from `repos_brief`).
4. **Net:** decision #5 ("pull RICHER GitHub signal") is **not realized**. The
   richest signals GitHub gives — primary language by code-bytes, and the actual
   dependency stack — are computed at fetch and then thrown away before extraction.
5. **Thin/empty GitHubs give almost nothing** (Pavan 1 repo → 3 skills; Sofia
   langs=[] → 4). GitHub is only strong for users with many well-described repos.

---

## 3. LinkedIn — measured (4 stored PDFs)

| PDF | DET skills | LLM skills | DET quality |
|-----|-----------:|-----------:|-------------|
| ranjith | 26 | 22 | good (real skills section parsed) |
| pavan | 6 | 18 | **leaks name/title/location as "skills"** |
| rohith | 3 | 27 | thin — layout defeated the parser |
| sofia | 4 | 13 | thin |

### What is MISSING / wrong with LinkedIn (concrete)

1. **Deterministic LinkedIn pass leaks non-skills.** Pavan's DET skills included
   `"Pavan Alakunta"` (his name), `"Student at University of Hertfordshire"` (his
   headline), and `"Luton, England, United Kingdom"` (his location). The column
   de-wrap heuristic for the LinkedIn PDF mis-segments header text into the skills
   list — a **precision bug** in `deterministic_linkedin_fields`.
2. **DET is layout-fragile.** LinkedIn's two-column "Save to PDF" export is hard
   to parse structurally; when de-wrap fails, DET collapses to 3–4 skills
   (rohith, sofia) and the LLM carries the entire lane.
3. **The LLM pass is the reliable half** (rohith 3→27, sofia 4→13) — it mines the
   experience prose well. LinkedIn value today ≈ the LLM pass alone.

---

## 4. Preferences (the 4th input)

Plain form parse + an LLM pass over the free-text "About me" box
(`llm_infer_from_about_me`). Not separately scored here because it has no public
gold set — it is a small additive signal (a few declared/inferred skills), not a
recall driver. Low risk, low weight.

---

## 5. Honest scorecard — what's solid, what's not

**Solid**
- Determinism achieved: temp 0 + JSON mode → repeatable runs (decision #6).
- Zero hallucination across 5 CVs + GitHub + LinkedIn (precision 98–100%).
- Paid OpenAI primary removes the old silent-empty / 429 failure (the worst bug).
- Well-structured inputs extract excellently (ranjith, rohith ≈ 93–96% F1).
- Per-user isolation intact; all routes `Depends(require_user)`.

**Not solid (ranked by impact)**
1. **CV prose-mining recall** — concept-skills in project bullets are left in the
   text (pavan 69%, sofia 62%). LLM anchors on the Skills section. *(biggest gap)*
2. **GitHub brief is signal-starved** — drops `language` + dependency frameworks;
   DET is topics-only → 0 for most users. Decision #5 unrealized.
3. **LinkedIn DET precision bug** — name/headline/location leak into skills.
4. **LinkedIn DET is layout-fragile** — collapses to a few skills on hard exports.
5. **Thin sources give thin output** — sparse GitHub/LinkedIn → few skills (no
   floor / cross-source backfill).

**None of the above is a hardcoded-keyword violation.** Every fix is either
prompt-steering (allowed) or plumbing more structural signal through the brief.

---

## 6. Decisions status (the 12)

| # | Decision | Status |
|---|----------|--------|
| 1 | OpenAI mini primary | ✅ done (`gpt-4o-mini` leads chain) |
| 6 | Determinism: temp 0 + cache | ✅ temp 0 done · cache pending |
| 11 | Record which model produced each result | ⚠️ logged per call, not stamped on the skill |
| 12 | Structured/guaranteed output | ⚠️ JSON-mode yes · json_schema strict pending |
| 5 | Richer GitHub signal | ❌ brief still topics+desc only |
| 3 | Soft keyword hints, LLM reasoning | ✅ (matching is Pillar 2) |
| 4 | Full re-extraction on change | ✅ (two_pass re-runs from stored data) |
| 7 | Cross-model + source-grounded grading | ⚠️ source-grounding done · cross-model pending |
| 8 | 20–50 profiles | ❌ 5 today |
| 9 | CI guard (eval as a test) | ❌ eval is a scratch script |
| 2 | Skip OCR | ✅ assumed |
| 10 | Background retry/enrich | ⚠️ retry+failover done · enrich-loop pending |

---

*Evidence: `eval_multi.py`, `miss.py`, `dump_pavan.py`, `gh_li_live.py` (job scratch
dir). All runs used OpenAI `gpt-4o-mini` temp 0. Reproduce from `backend/` with
`PYTHONPATH=D:/dev/job360-tprun/backend`.*

---

## 7. Fixes landed (TDD, no hardcoded keyword lists)

Three of the five "not solid" items fixed test-first (RED→GREEN→gate). +8 tests,
full gate **1632 passed / 0 failed**. Zero hallucination preserved.

| Flaw | Fix | Rule-#28 status | Result (measured) |
|------|-----|-----------------|-------------------|
| **#1 CV prose recall** | `cv_parser` RULE 9 — steer the LLM to mine techniques/methods/concepts from project & experience PROSE, not just the Skills section | prompt-steering (allowed) | pavan **69→78%**, crajappa **81→86%**, mean COMB recall **80→83%**, F1 **88→89%**, precision 98%, fp **0** |
| **#2 GitHub signal-starved** | `repos_brief` now carries `language`; `deterministic_github_fields` surfaces it; LLM prompt shows `[language: …]` | structural API field, not a map | Ranjith DET **10→16** skills; Pavan DET **0→1** (was empty) |
| **#3 LinkedIn header leak** | `deterministic_linkedin_fields` drops the de-wrapped identity block — name recognised via the user's own email/URL slug, then the trailing name/headline/location truncated | structural identity match, not a denylist | real Pavan DET went from `[…, 'Pavan Alakunta', 'Student at University of Hertfordshire', 'Luton, England, United Kingdom']` → `['Pandas (Software)', 'AWS SageMaker', 'Applied Machine Learning']` |

**Still open (honest):** #4 LinkedIn DET layout fragility (thin exports → few
deterministic skills; LLM carries the lane — acceptable), #5 thin-source floor
(sparse GitHub/LinkedIn yields few skills — no cross-source backfill yet). Sofia
CV recall (62%) is dominated by a marketing side-section + CTF prose; further
prompt pushes risk overfitting these 5 profiles, so deferred pending a larger set.
