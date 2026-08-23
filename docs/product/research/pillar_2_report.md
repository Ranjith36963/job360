# Open-source patterns to supercharge Job360's Search & Match Engine

**Job360's Pillar 2 can leap from a 4-dimension regex scorer to a production-grade semantic matching engine by adopting proven patterns from HiringCafe, career-ops, JobFunnel, and the sentence-transformers ecosystem.** The highest-impact upgrades — LLM enrichment via GPT-4o-mini ($54/month for 10K jobs), gate-pass scoring from career-ops, and hybrid BM25+semantic retrieval using RRF — are all documented in open-source code with clear implementation paths. This report maps each technique to Job360's codebase, provides concrete code patterns, and sequences 14 improvements by impact-per-effort ratio.

---

## How HiringCafe solved ghost detection and LLM enrichment

HiringCafe, built by a Stanford PhD data scientist, processes **1.6M+ job listings** using two techniques directly applicable to Job360.

**Ghost detection via embedding similarity.** Rather than tracking job disappearance, HiringCafe detects *repeated reposting* — the strongest ghost job signal. For each company, it generates text embeddings of all job descriptions and computes cosine similarity between postings from the same company. When two postings exceed a similarity threshold (~0.90), the system preserves only the **earliest listing's date**, effectively "aging" the reposted job. Users then filter out jobs older than 30 days, automatically excluding ghost listings. This approach avoids the complexity of URL-based disappearance tracking while catching the most common ghost pattern: companies refreshing stale listings.

**GPT-4o-mini structured extraction with 20 fields.** HiringCafe feeds raw HTML from career pages to GPT-4o-mini using OpenAI's strict JSON schema mode (`strict: true`, `temperature: 0`). The schema extracts `title`, `category` (16-value enum), `employment_type`, `workplace_type` (Remote/Onsite/Hybrid), `locations`, a nested `salary` object (min/max/currency/frequency), `skills` array, `experience_min_years`, `requirements_summary` (≤250 chars), `language` (ISO 639-1), and `employer_type` (Internal/External). Only four fields are required; all others are nullable. The `additionalProperties: false` constraint prevents hallucinated fields. The system prompt instructs the model to prioritize `job_description` over other fields when contradictions exist.

**Cost at Job360's scale:** ~$0.00036 per job via standard API, or **~$0.00018 per job via OpenAI's Batch API** (50% discount, 24-hour turnaround). At 10,000 jobs/day, that's **$54/month**. At 100,000 jobs/day: $540/month.

**What Job360 should copy:** The exact JSON schema (extended with `required_skills`/`preferred_skills` split and `visa_sponsorship` enum). The same-company embedding similarity pattern for ghost detection — scope comparisons within each company to avoid O(n²) explosion. The `date_confidence` concept: track whether dates are source-provided or estimated.

---

## Career-ops introduced gate-pass scoring and 10-dimension evaluation

Career-ops by Santiago Fernández (30K+ stars, 740+ offers evaluated) uses a fundamentally different scoring architecture than Job360's flat weighted sum.

**The gate-pass system prevents false positives.** Two of career-ops' 10 dimensions — Role Match and Skills Alignment — are designated "gate-pass." If either scores below threshold, the final score drops regardless of how well other dimensions perform. This eliminates the pathology where a job scores well on salary, location, and recency but is fundamentally mismatched on role and skills. Job360's current system allows this: a job with 0 title match and 0 skill match can still score 50/100 from location (10) + recency (10) + no penalties (0) + incidental partial matches. **Gate-pass logic would suppress these false positives.**

**The 10 dimensions provide much richer signal than Job360's 4:**

| Dimension | Weight tier | Job360 equivalent |
|-----------|------------|-------------------|
| Role Match | Gate-pass | Title match (40pts substring) |
| Skills Alignment | Gate-pass | Skill match (40pts regex) |
| Seniority | High | Experience detection (regex, not scored) |
| Compensation | High | salary_in_range() (tiebreaker only) |
| Interview Likelihood | High | Not implemented |
| Geographic | Medium | Location match (10pts) |
| Company Stage | Medium | Not implemented |
| Product-Market Fit | Medium | Not implemented |
| Growth Trajectory | Medium | Not implemented |
| Timeline | Low | Recency (10pts) |

**The archetype system adjusts weights per role type.** Career-ops classifies jobs into archetypes (e.g., "LLMOps", "Agentic Workflows", "Technical PM") and adjusts which proof points and scoring weights apply. A PM role weights management experience higher; an engineering role weights technical skills higher. This concept maps to Job360's planned source routing — if you know a user's professional domain, you can adjust scoring weights accordingly.

**Critical architectural note:** Career-ops delegates all scoring to Claude (the LLM), not deterministic code. This is too slow and expensive for Job360's batch processing. The recommended hybrid: use Job360's fast deterministic scorer (expanded to 7+ dimensions with gate-pass) as Stage 1, then optionally use LLM-based deep evaluation for top-scoring results only.

**Concrete pattern for Job360:**
```python
def score_job(job, profile):
    title_score = compute_title_score(job, profile)    # 0-1
    skills_score = compute_skills_score(job, profile)  # 0-1
    
    # Gate-pass: if core match fails, suppress final score
    if title_score < 0.15 or skills_score < 0.15:
        return max(10, (title_score + skills_score) * 25)  # Suppressed
    
    raw = (title_score * 30 + skills_score * 30 + seniority_score * 10 
           + salary_score * 10 + location_score * 10 + recency_score * 10)
    return clamp(raw - penalties, 0, 100)
```

---

## JobFunnel's TF-IDF dedup and Levergreen's disappearance tracking fill Job360's biggest gaps

**JobFunnel's three-tier deduplication** is the most sophisticated approach found in the open-source job search ecosystem. Tier 1 matches on source-specific job IDs. Tier 2 matches on URLs. Tier 3 uses **scikit-learn's TF-IDF vectorizer + cosine similarity** on concatenated (company + title + location) text to catch semantic duplicates across sources. This catches "Senior Software Engineer" on LinkedIn matching "Sr. Software Eng." on Indeed — cases that Job360's current normalized-string approach misses. The similarity threshold starts at ~0.85 and is tunable. Dependencies are minimal: scikit-learn (~3MB).

**Levergreen's scrape-and-diff pattern is the gold standard for disappearance tracking.** Each daily scrape captures the *complete* set of jobs visible on a company's career page. The dbt transformation layer compares today's scraped jobs against yesterday's known jobs. A job present yesterday but absent today is flagged as disappeared/filled. A `compare_workflow_success.py` script guards against false positives from scrape failures — it validates that the expected number of career pages was successfully scraped before flagging disappearances.

**What Job360 should implement:**

- **Add `first_seen` and `last_seen` columns** to the jobs table, updated on every scrape cycle where the job is confirmed present
- **Add a `scrape_run_id` or timestamp** to each scrape batch for audit and diff logic
- **Flag ghost probability in tiers:** not seen for 1 day = "possibly filled"; 3+ days = "likely filled"; 7+ days = "expired"
- **Scrape completeness check:** Before flagging disappeared jobs, verify the source scrape succeeded (prevents entire source going "filled" due to scrape failure)
- **Replace `INSERT OR IGNORE`** with an upsert that updates `last_seen` on every encounter

**JobSpy's contribution** is smaller but practical: its **Pydantic model pattern** enforces strict schema validation across all sources, and its per-source date parsers (especially LinkedIn's relative-date-string parser using `timedelta`) are well-maintained. JobSpy performs no deduplication or scoring — it's purely a scraping library.

---

## Semantic matching: the bi-encoder → cross-encoder pipeline

Job360's title matching (substring) and skill matching (regex word boundaries) miss every synonym, abbreviation, and related concept. "React" doesn't match "React.js". "AWS" doesn't match "Amazon Web Services". "Software Engineer" doesn't match "Developer". Semantic matching fixes this.

**The recommended architecture is a three-stage pipeline:**

1. **Stage 1 — Keyword retrieval (existing):** Job360's current BM25/regex system retrieves candidates quickly. Keep this as-is.
2. **Stage 2 — Bi-encoder semantic search:** Encode all job descriptions offline using `all-MiniLM-L6-v2` (22M parameters, 384 dimensions, **14K sentences/sec on CPU**, 80MB model). Encode user profile at query time. Retrieve top-100 by cosine similarity using ChromaDB or FAISS.
3. **Stage 3 — Cross-encoder reranking:** Pass top-50 candidates through `cross-encoder/ms-marco-MiniLM-L-6-v2`. This model reads the full (profile, job_description) pair with cross-attention, producing a relevance score. Adds **~100-200ms for 50 candidates** but improves accuracy by **+33% on average**.

**Combine Stage 1 and Stage 2 results using Reciprocal Rank Fusion (RRF):**
```python
def reciprocal_rank_fusion(ranked_lists, k=60):
    scores = {}
    for ranking in ranked_lists:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

The constant **k=60** is empirically validated across many datasets (Cormack et al., SIGIR 2009). It balances rank contributions without any single top result dominating. Hybrid retrieval improves recall **15-30%** over either method alone with minimal added complexity.

**For asymmetric search** (short user profile → long job description), use `msmarco-MiniLM-L6-v3` or chunk long descriptions into 300-token segments and match against the best chunk. The `multi-qa-mpnet-base-dot-v1` model is specifically trained for this query-document asymmetry.

**Vector database choice for Job360's scale (~50K jobs):** Start with **ChromaDB** (lightweight, easy setup, metadata filtering). Migrate to FAISS or Qdrant only if scaling beyond 100K jobs.

---

## Skill matching can improve dramatically with three lightweight approaches

**Approach 1 — Static synonym table (1-2 days, zero infrastructure):** Build a curated dictionary of ~500 tech skill synonyms mapping variations to canonical forms. "js" → "javascript", "k8s" → "kubernetes", "aws" → "amazon web services", "react.js" → "react". Normalize both user skills and job skills before matching. Keep existing Primary/Secondary/Tertiary weighting.

**Approach 2 — Embedding-based matching (3-5 days):** Pre-compute embeddings for ~2,000 known skills using `all-MiniLM-L6-v2`. At match time, compute cosine similarity between user skills and job skills. Threshold of **0.65-0.75** distinguishes related skills ("Docker" ↔ "containerization" ≈ 0.78) from unrelated ones ("Docker" ↔ "accounting" ≈ 0.12).

**Approach 3 — ESCO alternative labels (medium effort, highest coverage):** The ESCO taxonomy provides **13,896 skills with ~130,000 "surface forms"** (synonyms). Download the free ESCO CSV, extract alternative labels into a flat lookup table, and use it as a comprehensive synonym dictionary. The `esco-skill-extractor` Python package on PyPI does this mapping automatically. Nesta's `ojd_daps_skills` library has processed 7M+ UK job adverts using this approach.

**Required vs. preferred skills split:** LLM enrichment (from HiringCafe's approach) can separate must-have from nice-to-have skills in job descriptions. This enables a scoring formula where required skill matches contribute 40 points and preferred skill matches contribute 15 points — far more nuanced than Job360's current flat 40-point cap.

---

## Date accuracy, source routing, and salary normalization

**Date accuracy** requires three changes. First, change the 14 sources hardcoding `date_found=now()` to return `None` instead, then store a separate `first_seen` timestamp when the crawler first discovers each listing. Second, fix the 3 sources using wrong date fields (Jooble's "updated", Greenhouse's "updated_at", NHS Jobs' "closingDate"). Third, add a `date_confidence` field: "high" (real source date), "medium" (parsed relative date like "3 days ago"), "low" (first_seen only). Update recency scoring to apply a confidence discount for estimated dates and handle `None` gracefully by falling back to `first_seen` with a slight penalty.

**Source routing** has no established open-source framework, but a simple configuration-based approach works. Map each of 47 sources to domain tags ("tech", "healthcare", "finance", "general"). Classify users into domains via keyword matching on their target role and skills. At query time, only run sources tagged with the user's domain(s) plus all "general" sources (Indeed, LinkedIn, Reed). This alone could **reduce source queries by 50-70%** and eliminate noise from irrelevant boards. Track source-domain hit rates over time and auto-disable sources that consistently return zero results for a given domain.

**Salary normalization** requires currency detection (symbol/word → ISO 4217 code, defaulting to GBP for UK), frequency normalization (hourly×2080, daily×260, weekly×52, monthly×12 to annual), and range extraction via regex. Once normalized, salary can contribute to scoring as a 10-15 point dimension using overlap percentage between job salary range and user salary preference, with `0.5` (neutral) when salary data is missing. The HiringCafe schema already handles this with a nested salary object containing min, max, currency, and frequency fields.

---

## Search infrastructure: Meilisearch over Elasticsearch at Job360's scale

At ~50K jobs, Elasticsearch's distributed architecture is massive overkill. **Meilisearch** (Rust-based, single binary, MIT licensed) delivers sub-50ms search responses, built-in typo tolerance, faceted search, and hybrid keyword+vector search support. It's used by Hugging Face for 300K+ models. **Typesense** (C++ based) is equally viable and adds built-in clustering for high availability and auto-embedding generation (can compute embeddings internally without a separate pipeline). Either handles 50K jobs trivially. Both integrate with InstantSearch.js for frontend search UIs.

If Job360's existing PostgreSQL is performing adequately, an even lighter path is **pg_trgm + PostgreSQL full-text search** — zero new infrastructure, trigram similarity for fuzzy matching, and `ts_vector`/`ts_query` for ranked text search.

---

## Deduplication should use a multi-layer approach

Academic research consistently shows that **no single dedup method works alone** for job postings. Engelbach et al. (2024) achieved **F1=0.94** with a three-component hybrid: string comparison, deep textual embeddings, and curated weighted skill lookup lists. The recommended multi-layer approach for Job360:

- **Layer 1 — Exact key match (current, keep it):** Normalized (company, title) hash. Catches ~60% of duplicates.
- **Layer 2 — Fuzzy string match:** Use **RapidFuzz** (10x faster than fuzzywuzzy, C++ backend) with `fuzz.token_set_ratio` on titles and `fuzz.ratio` on company names. Company similarity >85 AND title similarity >80 AND same location → duplicate.
- **Layer 3 — Content-based match:** TF-IDF + cosine similarity on (company + title + first 200 words of description), per JobFunnel's pattern. Threshold ~0.85.
- **Layer 4 — Embedding-based match (for repost detection):** Sentence-transformer embeddings within same-company blocking groups. Cosine similarity >0.92 → repost. Preserve earliest `first_seen` date.

**Fix the documented divergence** between Job360's dedup key and DB unique key by adding a `dedup_hash` column storing the hash of the canonical dedup key, and aligning the DB unique constraint with it.

---

## State of the art: learning-to-rank and uncertainty quantification

The most sophisticated production job matching system with openly documented methodology is **Torre.ai**, which uses a **Random Forest model** trained on millions of applications, matches, hires, and disqualifications. Its three-phase architecture — Score → Filter (threshold) → Rank — provides a clear upgrade path beyond Job360's linear weighted sum.

Torre's most innovative contribution is **uncertainty quantification**: when a user profile lacks information, the system outputs a score *range* [lower_bound, upper_bound] rather than penalizing with a low point estimate. This directly addresses the cold start problem and would replace Job360's current behavior of implicitly scoring sparse profiles poorly.

**Learning-to-Rank (LTR) via LambdaMART** is the industry standard for personalized ranking (used at LinkedIn, Indeed, and major job platforms). It uses gradient-boosted decision trees to learn optimal ranking from engagement features (clicks, applications, saves). Libraries like LightGBM (`lambdarank` objective) make this accessible. However, LTR requires feedback data that Job360 may not yet have — making it a medium-term upgrade after tracking user interactions.

**For multilingual job descriptions**, the **JobBERT-V3** model (TechWolf, 2025, freely available on HuggingFace) is purpose-built for cross-lingual job title matching, trained on 21M+ job titles. For full descriptions, **multilingual-e5-large** (Microsoft) handles cross-language semantic matching without requiring translation.

---

## Ranked improvements for Pillar 2 by impact per effort

| Rank | Improvement | Impact | Effort | Reference repos |
|------|------------|--------|--------|----------------|
| 1 | **Fix date accuracy**: Return `None` not `now()` in 14 sources; fix 3 wrong fields; add `first_seen`/`last_seen`/`date_confidence` | High | Low | Levergreen, JobSpy |
| 2 | **Add gate-pass scoring**: Title and skills must exceed minimum threshold before weighted sum | High | Low | career-ops |
| 3 | **Static skill synonym table**: ~500 curated entries mapping skill variations to canonical forms | High | Low | ESCO alt labels |
| 4 | **Source routing by domain**: YAML config mapping 47 sources to domain tags; keyword-based user classification | High | Low | Custom (no direct repo) |
| 5 | **LLM enrichment via GPT-4o-mini Batch API**: Extract 20 structured fields including required/preferred skills split, salary, workplace type | Very high | Medium | HiringCafe gist |
| 6 | **Ghost detection — disappearance tracking**: Add `last_seen` column; flag jobs missing from scrape runs | High | Medium | Levergreen job-board-scraper |
| 7 | **TF-IDF content-based dedup**: scikit-learn TfidfVectorizer + cosine similarity on (company+title+location) | Medium | Low | JobFunnel |
| 8 | **Semantic matching — bi-encoder**: `all-MiniLM-L6-v2` + ChromaDB for embedding-based job retrieval | Very high | Medium | SBERT, JobMatchAI |
| 9 | **Hybrid retrieval with RRF**: Combine keyword + semantic results using Reciprocal Rank Fusion (k=60) | High | Low | Weaviate, Elasticsearch docs |
| 10 | **Salary normalization + scoring**: Currency/frequency detection; salary as 10-15pt scoring dimension | Medium | Medium | HiringCafe schema |
| 11 | **Ghost detection — embedding-based repost detection**: Same-company cosine similarity on descriptions | Medium | Medium | HiringCafe |
| 12 | **Cross-encoder reranking**: `ms-marco-MiniLM-L-6-v2` reranks top-50 candidates; +33% accuracy | High | Low | SBERT retrieve-and-rerank |
| 13 | **Expand to 7+ scoring dimensions**: Add seniority match, salary fit, visa compatibility, workplace match | Medium | Medium | career-ops |
| 14 | **Fuzzy dedup with RapidFuzz**: Layer 2 dedup using token_set_ratio on titles, ratio on companies | Medium | Low | RapidFuzz library |
| 15 | **Configurable MIN_MATCH_SCORE per user** | Low | Low | career-ops profile config |
| 16 | **ESCO taxonomy integration**: Map skills to ESCO URIs for hierarchical matching | High | High | Nesta ojd_daps_skills |
| 17 | **Learning-to-Rank (LambdaMART)**: Train on user engagement data for personalized ranking | Very high | High | LightGBM, Torre.ai |
| 18 | **Multilingual embeddings**: JobBERT-V3 or multilingual-e5 for non-English job descriptions | Low | Medium | TechWolf JobBERT-V3 |

---

## Recommended implementation sequence

**Sprint 1 (Week 1-2): Foundation fixes — low effort, high impact.**
Fix date accuracy across all 47 sources (return `None` not `now()`, fix Jooble/Greenhouse/NHS wrong fields, add `first_seen`/`last_seen`/`date_confidence` columns). Implement gate-pass logic for title and skills dimensions. Build the static skill synonym table (~500 entries). Create the source-domain YAML mapping and keyword-based user domain classifier. Add `scrape_run_id` tracking for disappearance detection. These five changes require no new dependencies and address Job360's most fundamental data quality issues.

**Sprint 2 (Week 3-4): LLM enrichment and content-based dedup.**
Deploy GPT-4o-mini enrichment pipeline using OpenAI Batch API with HiringCafe's schema (extended with required/preferred skills split). Process all existing jobs as a one-time backfill (~$180 for 1M jobs), then enrich new jobs daily. Add TF-IDF dedup (scikit-learn) as Layer 3 on top of existing normalized-key dedup. Implement disappearance tracking by comparing `last_seen` against latest scrape run per source.

**Sprint 3 (Week 5-6): Semantic search pipeline.**
Install sentence-transformers and ChromaDB. Embed all job descriptions offline using `all-MiniLM-L6-v2`. Implement hybrid retrieval: existing keyword search + semantic vector search, fused with RRF (k=60). This replaces substring-based title matching with semantic similarity that understands "Software Engineer" ≈ "Developer" ≈ "SDE". Add salary normalization (currency detection, frequency-to-annual conversion) and include salary as a 10-point scoring dimension.

**Sprint 4 (Week 7-8): Precision refinements.**
Add cross-encoder reranking (`ms-marco-MiniLM-L-6-v2`) on top-50 hybrid results. Implement embedding-based repost detection within same-company groups for ghost scoring. Expand scoring formula to 7 dimensions using LLM-enriched fields (required skills, preferred skills, experience fit, salary fit, visa compatibility, workplace match, recency). Make MIN_MATCH_SCORE configurable per user via profile settings.

**Future sprints:** Integrate ESCO taxonomy for hierarchical skill matching. Implement Learning-to-Rank once sufficient user engagement data is collected. Add multilingual support via JobBERT-V3. Evaluate Meilisearch or Typesense to replace direct SQL queries for search. Train embedding model on domain-specific job data for improved recall.

---

## Conclusion

The gap between Job360's current Pillar 2 and production-grade job matching is large but closable with known patterns. The three highest-leverage changes are **LLM enrichment** (transforming unstructured job descriptions into 20 structured fields for $54/month), **gate-pass scoring** (eliminating false positives from fundamental role mismatches), and **hybrid BM25+semantic retrieval with RRF** (catching every synonym and related concept that regex misses). Each of these has working open-source reference code. The entire upgrade path from regex-based matching to a bi-encoder → cross-encoder semantic pipeline can be completed in 4 two-week sprints, with the earliest sprints delivering the highest impact through data quality fixes that require no new infrastructure.