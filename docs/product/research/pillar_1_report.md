# Open-source patterns to supercharge Job360's profile parser

**Job360's Pillar 1 can leap from naive position-based skill extraction to production-grade, domain-agnostic profile parsing by adopting proven patterns from 13 open-source projects and 3 skill taxonomies.** The highest-impact improvements are ESCO-based skill normalization (replacing the position-based thirds split), a strict JSON schema LLM extraction pattern from HiringCafe, and dependency-file parsing for GitHub enrichment. This report maps every relevant repo to concrete, copyable techniques — ranked by impact on engine quality.

The current system's strengths — zero hardcoded keywords, LLM fallback chain, multi-source enrichment — are solid foundations. But the research reveals **five architectural gaps**: no standardized skill taxonomy, no semantic skill matching, primitive skill tiering, shallow GitHub analysis, and a fragile LinkedIn parser. Each gap has a tested open-source solution ready for adaptation.

---

## Repo-by-repo analysis: what to take from each

### 1. JobSpy (github.com/speedyapply/JobSpy) — ~3.2k stars

**What it does for Pillar 1:** Pure job scraper normalizing listings from 8 boards (LinkedIn, Indeed, Glassdoor, Google, ZipRecruiter, Bayt, Naukri, BDJobs) into a unified `JobPost` dataclass. No LLM or NLP — entirely structural.

**Copyable patterns:**
- **Adapter pattern for multi-format normalization.** Each scraper module maps site-specific data to the same `JobPost` dataclass. This directly applies to CV ingestion: create separate adapter modules for PDF (pdfplumber), DOCX (python-docx), LinkedIn export, and plain text, all producing the same canonical `ProfileData` schema.
- **`salary_source` provenance tracking.** JobSpy distinguishes `direct_data` (structured field) from `description` (parsed from text). Job360 should add a `source` field to every extracted value — `"cv_explicit"`, `"cv_inferred"`, `"linkedin"`, `"github"`, `"user_override"` — enabling confidence-weighted merging.
- **`enforce_annual_salary` normalization.** Converts hourly/weekly/monthly to annual. Directly reusable for compensation comparison between CV expectations and job listings.
- **Compensation sub-object design:** `{interval, min_amount, max_amount, currency, salary_source}` — cleanly handles the full range of salary formats.

**How it compares:** Job360 already extracts from multiple sources but lacks formalized provenance tracking and a clean adapter pattern. JobSpy's approach would make the ingestion pipeline more maintainable and extensible.

**Concrete recommendation:** Refactor CV ingestion into an adapter pattern where each format handler implements `extract(input) → ProfileData`. Add a `source` field to every extracted datum. Copy the compensation sub-object schema verbatim for salary expectation handling. **Maps to:** `profile_builder.py`, `cv_parser.py`.

---

### 2. HiringCafe GPT-4o-mini prompt schema — Production-tested on 2.1M+ jobs

**What it does for Pillar 1:** The actual production prompt + JSON schema used by hiring.cafe to structure job postings with GPT-4o-mini. Extracts **19 structured fields** including a `skills` normalized array, 16 `category` enums spanning all white-collar domains, and a `requirements_summary` (≤250 chars).

**Copyable patterns:**
- **Strict JSON Schema mode** (`strict: true`) guarantees LLM output conforms exactly to the schema — no hallucinated fields. This is the single most important pattern for reliable extraction.
- **Nullable field design.** Most fields use `type: ["string", "null"]` rather than forcing the LLM to hallucinate when data is missing. Job360's current prompt likely forces extraction of every field.
- **Source-of-truth prioritization.** The system prompt explicitly says: *"If conflicting information is found, prioritize job_information.job_description."* For CV parsing, this becomes: *"If conflicting information is found between sections, prioritize the most recent work experience."*
- **16 category enums** covering all professional domains: Software Development, Engineering, IT, Product Management, Project/Program Management, Design, Data & Analytics, Sales, Marketing, Customer Service, Business Operations, Finance & Accounting, HR, Legal & Compliance, Healthcare, Other. These are **directly reusable** as Job360's career domain classification.
- **`requirements_summary` field** (≤250 chars): A concise summary generated alongside full extraction. For CVs, this becomes `professional_summary` — useful for quick profile scanning.
- **Minimal required fields** (only 4 of 19): Reduces extraction failures by only mandating what's truly essential.

**How it compares:** Job360 uses `temperature 0.1` and JSON mode but lacks strict schema enforcement, nullable field design, and domain category classification. The HiringCafe pattern is more robust and domain-agnostic.

**Concrete recommendation:** Adopt the strict JSON schema pattern for CV extraction prompts. Copy the 16 category enums as `career_domain`. Add nullable types for all non-essential fields. Create a mirrored CV extraction schema: `title` → `current_title`, `company_name` → `current_employer`, `skills` → `skills[]`, `experience_min_years` → `total_experience_years`, `category` → `career_domain`. **Maps to:** `llm_parser.py`, `profile_schema.py`.

---

### 3. career-ops by Santiago Fernández — ~32k+ stars, built on Claude Code

**What it does for Pillar 1:** AI-powered job search pipeline where the entire system is prompt-driven. Evaluates jobs against a user profile using a **6-block evaluation framework** and **10-dimension weighted scoring** with gate-pass dimensions.

**Copyable patterns:**
- **10-dimension weighted scoring with gate-pass dimensions.** Role Match and Skills Alignment are gate-pass: if they fail, the overall score drops regardless of other high scores. This directly solves Job360's matching quality problem — currently, a job with great location/salary but terrible skill fit might score well.
- **Block B (Background Match):** Maps every JD requirement to specific CV experience, identifying gaps + mitigation strategies. This is structured CV-to-JD matching at its finest.
- **Archetype system.** Classifies jobs into archetypes (6 default, 15 in community version spanning Technology through Government/Nonprofit). Archetypes adjust scoring weights and evaluation language. Job360 could classify both profiles and jobs into archetypes for faster, more contextual matching.
- **`cv.md` as canonical source of truth.** Using markdown with standard sections (Summary, Experience, Projects, Education, Skills) as an intermediate representation is simple, human-readable, and easy to manipulate.
- **Adaptive CV generation logic:** Detect archetype → select relevant projects → reorder bullets → inject keywords (reformulated, never fabricated). This pattern applies to how Job360 generates SearchConfig — the search query generation should be archetype-aware.
- **Ghost job detection (Block G):** Uses JD quality signals and repost history to flag suspicious listings. A differentiating feature Job360 could add.

**How it compares:** Job360 generates SearchConfig with top-8 titles × top-2 locations. career-ops takes a fundamentally different approach — instead of generating search queries, it evaluates individual jobs against a rich profile model. The scoring system is far more sophisticated than anything in Job360's current pipeline.

**Concrete recommendation:** Adopt the gate-pass dimension pattern for Job360's scoring engine. Implement archetype classification using career-ops' community categories. Reference Block B's structured gap analysis pattern for CV-to-JD matching. **Maps to:** `scoring_engine.py`, `search_config.py`.

---

### 4. OpenResume (github.com/xitanggg/open-resume) — ~8.4k stars

**What it does for Pillar 1:** Resume builder + parser. The parser at `/lib/parse-resume-from-pdf` extracts structured data from PDFs using **heuristic section detection** — no ML required.

**Copyable patterns:**
- **PDF.js text extraction with positional metadata.** Extracts text items with font size, weight, and x/y coordinates. This metadata enables layout-aware parsing.
- **Section detection heuristics:** Three signals — bolded text, ALL CAPS text, matches common section keywords. **Subsection detection:** line gap > typical_line_gap × 1.4.
- **Feature scoring for field extraction.** Within each detected section, a scoring system identifies which lines are titles, dates, descriptions, etc.
- **ATS readability testing.** Validates that resume structure is parseable by Greenhouse and Lever — useful for understanding what "good" CV structure looks like.

**How it compares:** Job360 uses pdfplumber for flat text extraction. OpenResume's approach extracts **structured layout information** (font sizes, positions, spacing) that enables much more accurate section detection without LLM involvement.

**Concrete recommendation:** Add layout-aware preprocessing before LLM extraction. Use font size changes and spacing heuristics to identify section boundaries, then pass pre-segmented sections to the LLM for structured extraction. This reduces LLM token usage and improves accuracy. **Maps to:** `pdf_extractor.py`.

---

### 5. SmartResume by Alibaba (github.com/alibaba/SmartResume) — State of the art

**What it does for Pillar 1:** **Layout-aware parsing + efficient LLM** in a two-stage pipeline: YOLO-based layout detection (92.1% mAP) identifies sections visually, then a fine-tuned **Qwen3-0.6B** extracts structured data. Achieves **93.1% overall extraction accuracy**.

**Copyable patterns:**
- **YOLO layout detection → LLM extraction pipeline.** Visual section identification solves multi-column layouts, tables, and complex formatting that pure text extraction misses.
- **Small, fine-tuned model.** Qwen3-0.6B (600M params) outperforms much larger models because it's specialized. Job360 could fine-tune a small model on its specific extraction schema rather than relying on general-purpose LLMs.
- **Configurable extract_types.** Users specify which sections to extract: `basic_info`, `education`, `work_experience`, `skills`, `projects`. This modularity is good API design.

**How it compares:** Job360 uses Gemini/Groq/Cerebras for general-purpose extraction. SmartResume demonstrates that a **600M parameter fine-tuned model beats general-purpose LLMs** at resume parsing — and runs locally with zero API cost.

**Concrete recommendation:** Consider fine-tuning a small model (Qwen3-0.6B or similar) on Job360's specific extraction schema as a local fallback when all LLM providers are unavailable. This directly addresses the PRD requirement for graceful LLM fallback. **Maps to:** `llm_parser.py` fallback chain.

---

### 6. pyresparser + resume parsers ecosystem

**What they do:** Traditional NLP-based resume parsers using spaCy NER + NLTK + regex. The ecosystem has evolved from keyword matching (pyresparser, ~70% accuracy) through custom NER training to LLM-based parsing.

**Key finding — the accuracy hierarchy:**

| Approach | Accuracy | Cost |
|---|---|---|
| Keyword/regex (pyresparser) | ~70% | Free |
| Rule-based NLP (GATE) | ~80-90% | Free |
| Fine-tuned NER (spaCy/BERT) | ~82-88% | Free (GPU) |
| Fine-tuned small LLM (SmartResume) | ~93% | Free (GPU) |
| General LLM (GPT-4, Claude) | ~90-95% | API costs |
| Commercial (Affinda, Textkernel) | ~90-95% | Paid |

**Concrete recommendation:** Job360's LLM-based approach is correct — it's at the accuracy frontier. The key improvement is adding **Pydantic validation** on LLM output (pattern from Datumo's production system): enforce schema, catch missing fields, validate date ranges, retry with error feedback. **Maps to:** `llm_parser.py`.

---

### 7. ESCO taxonomy — The recommended primary skill taxonomy

**What it does for Pillar 1:** **13,900+ skills** across all professional domains, **3,007 occupations**, available in **28 languages**, with essential/optional skill-occupation relationships. Free download as CSV/JSON-LD/RDF.

**Why ESCO over alternatives:**
- **Domain coverage:** Unlike tech-focused skill lists, ESCO covers agriculture, healthcare, legal, finance, manufacturing, education, arts — every white-collar and blue-collar domain.
- **Alternative labels (synonyms):** Each skill has preferred + alternative labels, enabling fuzzy matching without custom alias maps.
- **Essential vs optional marking:** Each skill-occupation relationship is marked essential or optional — this is **built-in skill tiering** that directly replaces Job360's naive position-based thirds split.
- **Skill reusability levels:** Skills classified as transversal (cross-sector), cross-sector, sector-specific, or occupation-specific — enabling intelligent tiering.

**Python tools for ESCO integration:**
- **`ojd-daps-skills`** (Nesta): Best-in-class pipeline — spaCy NER extracts skill spans → sentence-transformers maps to ESCO. **94% extraction accuracy, 88% mapping accuracy**.
- **`esco-skill-extractor`** (ESCOX): Simpler API, uses `all-MiniLM-L6-v2` embeddings + cosine similarity against ESCO.
- **`skills-ml`**: CompetencyOntology class supporting both ESCO and O*NET.

**Concrete recommendation:** Download ESCO v1.2.1 CSV dataset. Precompute embeddings for all ~13,900 skills using `all-MiniLM-L6-v2`. After LLM extracts raw skill strings from CV, map each to nearest ESCO skill via cosine similarity. Use ESCO's essential/optional labels + skill reusability levels for tiering. **Maps to:** New `skill_normalizer.py` module.

---

### 8. O*NET — Supplementary taxonomy for skill importance ratings

**What it adds beyond ESCO:** O*NET rates each of its 35 skill constructs per occupation on **importance (1-5)** and **level (1-7)** scales. Example: for "Software Developer," Programming is rated 4.5/5 importance; Writing is 2.8/5.

**Concrete recommendation:** Use O*NET importance/level ratings as a **secondary signal for skill tiering**. After mapping a user's skills to ESCO, cross-reference their target occupation in O*NET to weight skills by occupational importance. This transforms tiering from position-based guessing to evidence-based ranking. **Maps to:** `skill_tiering.py`.

---

### 9. SkillNER + NER tools — Pre-filtering layer

**What they do:** Rule-based (SkillNER, spaCy PhraseMatcher) and ML-based (Nesta, ESCOXLM-R, TechWolf ConTeXT) skill extraction from text.

**The recommended hybrid architecture:**

```
CV Text → spaCy NER (fast, free) → Skill candidate spans
                                         ↓
                              LLM validates + structures ambiguous spans only
                                         ↓
                              Sentence-transformers → ESCO taxonomy mapping
```

**Best models by use case:**
- **TechWolf ConTeXT-Skill-Extraction-base** (109M params): SOTA accuracy, maps directly to ESCO, fast inference. Best for production skill matching.
- **ESCOXLM-R** (XLM-RoBERTa-large): Multilingual, taxonomy-aware pre-training. Best for non-English CVs.
- **resume-ner-bert-v2** (BERT-base): 90.87% F1 across 25 resume entity types. Best for full resume NER beyond just skills.

**Concrete recommendation:** Add a **pre-filtering NER step** before LLM extraction. Use `TechWolf/ConTeXT-Skill-Extraction-base` to extract skills from CV text, then use the LLM only for structuring experience, education, and ambiguous fields. This **reduces LLM token usage by ~60-70%** and provides deterministic skill extraction for common skills. **Maps to:** New `skill_ner.py` module.

---

### 10. LinkedIn parsers — Defensive parsing and expanded field coverage

**Key findings from analyzing linkedin-zip-parser, linkedin-to-jsonresume, and the Voyager API data model:**

Job360 currently parses 5 CSV files. The LinkedIn export contains **at least 12 parseable files**:

| Currently parsed | Missing but available |
|---|---|
| Profile.csv | Courses.csv |
| Positions.csv | Languages.csv |
| Skills.csv | Projects.csv |
| Education.csv | PhoneNumbers.csv |
| Certifications.csv | Recommendations_Received.csv |
| | Connections.csv (for network analysis) |
| | Registration.csv |

**Defensive parsing patterns observed across tools:**
- Case-insensitive column matching
- Fallback column names (`Started On` vs `Start Date`)
- Graceful handling of missing CSV files in the ZIP
- Storing raw CSV headers alongside parsed data for format change detection

**Concrete recommendation:** Parse all 12 LinkedIn CSV files. Add case-insensitive column matching and fallback column names. Store the raw header row from each CSV to detect format changes. Output to JSON Resume schema as intermediate representation. **Maps to:** `linkedin_parser.py`.

---

### 11. GitHub analyzers — Dependency parsing is the biggest gap

**Key finding:** Job360's 32 language + 50 topic mappings miss the most valuable signal: **dependency files reveal specific frameworks and libraries**.

**gitparse** (von-development/gitparse) provides typed Python dependency extraction:
```python
deps = repo.get_dependencies()
# Returns parsed: pyproject.toml, requirements.txt, package.json, Cargo.toml
```

**CodeTrace's temporal weighting** adds another dimension: a "temperature" metric (0-100°) where recent activity scores higher than old contributions. This directly addresses the PRD requirement for recency-weighted skill tiering.

**Framework detection from dependency files** (pattern from multiple tools):
- `package.json` → React, Next.js, Express, Vue, Angular, Svelte
- `requirements.txt` / `pyproject.toml` → Django, Flask, FastAPI, pandas, scikit-learn, TensorFlow
- `Cargo.toml` → Actix, Rocket, Tokio
- `Gemfile` → Rails, Sinatra
- `go.mod` → Gin, Echo, Fiber
- `composer.json` → Laravel, Symfony

**Concrete recommendation:** Add dependency file fetching for top repos (via GitHub API Contents endpoint). Parse `package.json`, `requirements.txt`, `pyproject.toml`, `Cargo.toml`, `Gemfile`, `go.mod` to extract specific frameworks. Add temporal weighting: skills from repos pushed in last 12 months get 3× weight vs older repos. Expand from 82 static mappings to **200+ framework-level skill inferences**. **Maps to:** `github_enricher.py`.

---

### 12. JSON Resume — The canonical profile schema

**JSON Resume** (jsonresume.org) is the de facto standard for structured resume data, with 400+ themes and wide ecosystem support. Its schema covers: basics, work, volunteer, education, awards, certificates, publications, skills (with `level` and `keywords[]`), languages, interests, projects, references, and meta.

**Critical feature for Job360:** The `skills` schema uses `{name, level, keywords[]}` — grouping related technologies under a skill category. Example: `{name: "Web Development", level: "Senior", keywords: ["React", "TypeScript", "Next.js"]}`. This enables hierarchical skill representation.

**Concrete recommendation:** Adopt JSON Resume as Job360's canonical profile output format. This enables interop with the broader ecosystem, provides a proven data model, and its `additionalProperties: true` policy allows custom fields (like `source`, `confidence`, `esco_uri`) without breaking validation. **Maps to:** `profile_schema.py`.

---

### 13. JobFunnel + Levergreen — Limited direct value

**JobFunnel** (archived Dec 2025, 2.1k stars): Job scraper with scikit-learn TF-IDF + cosine similarity for content-based filtering. The `JobStatus` workflow (new → interested → applied → interview → offer) and YAML-based config are clean but standard. **Take:** The content filtering approach using TF-IDF + cosine similarity as a fast pre-filter before LLM-based scoring.

**Levergreen** (36 stars): Pure Scrapy + dbt pipeline for Greenhouse/Lever/Ashby scraping. **Take:** The dbt transformation pattern for normalizing data from multiple ATS platforms into a unified model — useful as architectural reference but no direct code to copy.

---

## Ranked improvements for Pillar 1, ordered by impact

### 1. ESCO-based skill normalization and tiering (HIGH IMPACT)

**What:** Replace the naive position-based thirds split with ESCO taxonomy mapping + multi-signal tiering.

**How:** Download ESCO v1.2.1 dataset. Precompute `all-MiniLM-L6-v2` embeddings for all 13,900 skills. After LLM extracts raw skill strings, map each to the nearest ESCO skill via cosine similarity. Use ESCO's essential/optional labels for the user's target occupation to classify skills as primary/secondary. Layer on recency weighting (from work experience dates), frequency (mentions across sections), and explicit proficiency indicators ("expert in" vs "familiar with"). Supplement with O*NET importance ratings for the target occupation.

**Reference repos:** Nesta `ojd-daps-skills` (extraction pipeline), `esco-skill-extractor` (ESCOX), O*NET database, TechWolf ConTeXT model.

**Complexity:** Medium. Core implementation is embedding precomputation + cosine similarity matching. The tiering logic requires date parsing and frequency counting from the already-extracted profile.

---

### 2. Strict JSON schema LLM extraction with Pydantic validation (HIGH IMPACT)

**What:** Adopt HiringCafe's `strict: true` JSON schema pattern with nullable fields, source-of-truth prioritization, and Pydantic validation on output.

**How:** Define a Pydantic model mirroring the CV extraction schema. Use `response_format={"type": "json_schema", "json_schema": {"strict": true, ...}}` with OpenAI-compatible APIs (Gemini supports this). Add `type: ["string", "null"]` for all non-essential fields. Add retry logic: on validation failure, send the Pydantic error back to the LLM for correction. Add the 16 HiringCafe category enums as `career_domain`.

**Reference repos:** HiringCafe gist (exact schema), Datumo production pipeline (Pydantic validation pattern).

**Complexity:** Low. Mostly prompt and schema changes — no new infrastructure.

---

### 3. GitHub dependency-file parsing for framework detection (HIGH IMPACT)

**What:** Fetch and parse dependency files from top repos to infer specific frameworks and libraries, not just languages.

**How:** For each of the top 30 repos (already fetched by push date), use GitHub API Contents endpoint to fetch `package.json`, `requirements.txt`, `pyproject.toml`, `Cargo.toml`, `Gemfile`, `go.mod`, `composer.json`. Parse each for dependency names. Map dependency names to skill names using a curated mapping (~200 entries: `"react" → "React"`, `"django" → "Django"`, `"tensorflow" → "TensorFlow"`). Add temporal weighting: repos pushed within 12 months get 3× weight.

**Reference repos:** gitparse (dependency extraction), CodeTrace (temporal weighting).

**Complexity:** Low. GitHub API already in use; adding file fetches and a mapping dictionary is straightforward.

---

### 4. NER pre-filtering before LLM extraction (MEDIUM-HIGH IMPACT)

**What:** Add a fast, free NER layer that extracts deterministic skills before sending text to the LLM, reducing token usage by 60-70%.

**How:** Load `TechWolf/ConTeXT-Skill-Extraction-base` (109M params, runs on CPU). Run CV text through it to extract skill spans with ESCO mapping. Pass the pre-extracted skills + remaining unstructured text to the LLM, which only needs to handle experience structuring, education, and ambiguous fields. This is the SkiLLMo hybrid pattern achieving **91% precision**.

**Reference repos:** TechWolf ConTeXT, SkiLLMo pipeline, Nesta Skills Extractor.

**Complexity:** Medium. Requires adding a sentence-transformers dependency and a preprocessing step, but no training.

---

### 5. Expanded LinkedIn parsing with defensive patterns (MEDIUM IMPACT)

**What:** Parse all 12 available CSV files (not just 5), add case-insensitive column matching, fallback column names, and format version tracking.

**How:** Add parsers for Courses.csv, Languages.csv, Projects.csv, PhoneNumbers.csv, Recommendations_Received.csv, Connections.csv. Implement case-insensitive header matching: `header_map = {h.lower().strip(): h for h in row}`. Add fallback names: `get_column(row, ["Started On", "Start Date", "start_date"])`. Store raw headers in profile metadata for format change detection.

**Reference repos:** linkedin-zip-parser (comprehensive field list), linkedin-to-jsonresume (column mappings).

**Complexity:** Low. Straightforward CSV parsing additions.

---

### 6. Local LLM fallback for graceful degradation (MEDIUM IMPACT)

**What:** Add a local fine-tuned model as the final fallback when all cloud LLM providers are unavailable.

**How:** Fine-tune Qwen3-0.6B (or Qwen2.5-1.5B as in OmkarPathak's ResumeParser) on Job360's specific extraction schema using 500-1000 annotated CV examples. Deploy via GGUF format with llama.cpp or Ollama. Add as the fourth step in the fallback chain: Gemini → Groq → Cerebras → Local Qwen.

**Reference repos:** SmartResume (Qwen3-0.6B fine-tuning), ResumeParser (Qwen2.5-1.5B GGUF).

**Complexity:** High. Requires training data annotation, fine-tuning infrastructure, and model deployment.

---

### 7. JSON Resume canonical profile schema with versioning (MEDIUM IMPACT)

**What:** Adopt JSON Resume as the canonical profile format with snapshot versioning for multi-user storage.

**How:** Define a Pydantic model matching JSON Resume schema + custom extensions (`source`, `confidence`, `esco_uri` per skill, `career_domain`). Store profiles as versioned JSONB snapshots in PostgreSQL (pattern from Reactive Resume). Each enrichment action (CV parse, LinkedIn import, GitHub analysis, user edit) creates a new snapshot with source attribution.

**Reference repos:** JSON Resume schema, Reactive Resume (PostgreSQL + Prisma pattern).

**Complexity:** Medium. Schema migration from single JSON file to database, but JSON Resume provides a proven starting point.

---

### 8. Provenance-tracked multi-source conflict resolution (MEDIUM IMPACT)

**What:** Assign confidence scores to every extracted value based on source, enabling intelligent merging.

**How:** Implement a source priority chain with numeric confidence: User override (1.0) > LinkedIn self-reported (0.9) > CV explicit mention (0.85) > GitHub dependency-detected (0.7) > CV inferred (0.6) > GitHub language-detected (0.5) > GitHub topic-inferred (0.4). When sources conflict, highest confidence wins. When tied, most recent source wins. Store all sources for transparency.

**Reference repos:** JobSpy (`salary_source` provenance), career-ops (profile.yml priority model).

**Complexity:** Low-Medium. Requires adding a `source` and `confidence` field to every extracted datum and a merge function.

---

### 9. Layout-aware PDF preprocessing (LOWER IMPACT)

**What:** Add font size/position analysis before LLM extraction to pre-segment CV sections.

**How:** Use pdfplumber's `extract_words()` with `extra_attrs=["fontname", "size"]` to get text with font metadata. Detect section headers via font size changes (headers are typically 2+ points larger). Detect subsections via line gap > 1.4× typical gap (OpenResume's heuristic). Pass pre-segmented sections to LLM with section labels, reducing ambiguity.

**Reference repos:** OpenResume (section detection heuristics), SmartResume (YOLO layout detection).

**Complexity:** Medium. pdfplumber already in use; adding font analysis is incremental.

---

### 10. Archetype classification for contextual matching (LOWER IMPACT)

**What:** Classify both user profiles and jobs into archetype categories that adjust scoring weights.

**How:** Adopt career-ops' community archetype list (15 categories: Technology, Finance, Healthcare, Legal, Creative/Marketing, Operations, Sales/BD, Education, Executive, Trades, Customer Success, People/HR, Government/Nonprofit, Scientific/R&D, Non-Software Engineering). Classify at profile creation time using the career_domain field + skill distribution. Adjust SearchConfig generation to be archetype-aware — different archetypes need different search strategies.

**Reference repos:** career-ops (archetype system + scoring weight adjustment).

**Complexity:** Low. Classification is a simple rule-based or LLM-prompt step; the harder part is tuning per-archetype scoring weights.

---

## Key data downloads and packages to integrate

| Resource | Install / URL | Purpose |
|---|---|---|
| ESCO v1.2.1 dataset | esco.ec.europa.eu/en/use-esco/download | Primary skill taxonomy (CSV) |
| O*NET database | onetcenter.org/database.html | Skill importance ratings (TSV) |
| `sentence-transformers` | `pip install sentence-transformers` | Skill embedding + matching |
| `ojd-daps-skills` | `pip install ojd-daps-skills` | Nesta NER + ESCO mapping pipeline |
| `esco-skill-extractor` | `pip install esco-skill-extractor` | Simple ESCO extraction |
| `skillNer` | `pip install skillNer` | Rule-based skill extraction |
| TechWolf ConTeXT | huggingface.co/TechWolf/ConTeXT-Skill-Extraction-base | SOTA lightweight skill extraction |
| resume-ner-bert-v2 | huggingface.co/yashpwr/resume-ner-bert-v2 | Full resume NER (25 entity types) |
| JSON Resume schema | jsonresume.org/schema | Canonical profile format |
| Lightcast Open Skills API | lightcast.io/open-skills | 33k+ skills, free tier |

## Conclusion

The open-source ecosystem offers Job360 a clear upgrade path from its current naive-but-functional parsing to production-grade profile understanding. **Three changes deliver 80% of the value**: ESCO taxonomy integration replaces the position-based thirds split with evidence-based tiering grounded in 13,900 standardized skills. HiringCafe's strict JSON schema pattern makes LLM extraction deterministic and domain-agnostic via 16 career domain enums. GitHub dependency-file parsing transforms shallow language counting into specific framework detection with temporal weighting.

The remaining improvements — NER pre-filtering, expanded LinkedIn parsing, local LLM fallback, JSON Resume schema adoption, provenance tracking, layout-aware PDF preprocessing, and archetype classification — each address a specific PRD requirement and can be implemented incrementally. The single most underappreciated finding: **ESCO's essential/optional skill-occupation relationships provide free, authoritative skill tiering** that no amount of prompt engineering on the current position-based approach can match. Start there.