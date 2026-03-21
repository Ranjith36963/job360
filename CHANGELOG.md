# Changelog

## v3.0.0 — 2026-03-21 (Search Engine Overhaul)

### Part A — 8 Fixes
- Hard remove foreign-only jobs before scoring (`is_foreign_only()` filter in main.py)
- Search all UK with no location cap (`"{title} UK"` query format)
- Salary: no rejection for missing salary, 5 band filters (£35K+ to £75K+)
- Job type detection and filter (Full-time, Part-time, Contract, Freelance, etc.)
- 5 recency time buckets (24h, 48h, 3d, 5d, 7d) replacing old 3-bucket system
- Smarter two-pass deduplication with description similarity (SequenceMatcher 0.85)
- Domain intelligence: detect 10 professional domains from profile
- Dynamic location filters extracted from job results

### Part B — 9 Phases
- **0A** Structured CV parsing: work experience, education, projects extraction
- **0B** JD section parsing: required/preferred skills, experience years, qualifications
- **0C** ESCO-inspired skill taxonomy: 345 synonym groups for fuzzy skill matching
- **2A** Multi-dimensional scorer: 8 dimensions (Role, Skill, Seniority, Experience, Credentials, Location, Recency, Semantic)
- **2B** Skill gap + match explanation: match_data JSON with matched/missing/transferable skills
- **1A** Semantic embeddings: all-MiniLM-L6-v2 (384-dim) for job-profile similarity
- **1B** Hybrid retrieval: FTS5 + vector search with Reciprocal Rank Fusion
- **3A** Cross-encoder reranking: ms-marco-MiniLM-L-6-v2 on top-50 candidates
- **3B** Feedback loop: liked/rejected signals adjust scores ±5

### Infrastructure
- Database schema v6: added job_type, match_data, embedding columns + jobs_fts FTS5 table
- 446 skill graph relationships (1126 edges), up from ~100
- 345 synonym groups, up from ~65
- 658 tests (655 passed, 3 skipped), up from 435

## v2.0.0 — 2026-03-19

### Tooling Redesign
- Replaced 5 skills with zero-overlap set: `/scaffold`, `/test`, `/audit`, `/status`, `/debug`
- Replaced 3 agents with 2: `builder` (implements), `reviewer` (reviews, read-only)
- Simplified hooks — PostToolUse prompt focuses on MD updates, not rule auditing

### Infrastructure
- Added CI/CD pipeline (`.github/workflows/tests.yml`) — Python 3.9/3.11/3.13
- Added `pyproject.toml` with project metadata and pytest config
- Added custom exception hierarchy (`src/exceptions.py`)
- Added version constant (`src/__version__.py`)
- Added schema versioning to database (`schema_version` table + migrations)
- Added circuit breaker to `BaseJobSource.safe_fetch()` — skips after 3 failures

### Security
- Added `--db-path` validation — rejects paths outside project directory
- Added PII sanitization to logger — redacts emails and API keys from logs
- Added import guards to `cv_summarizer.py` for optional anthropic/openai deps

### Documentation
- Updated README.md: 24→48 sources, 212→435 tests, added Phases 2-3
- Updated `.env.example` with all tunable parameters
- Added CHANGELOG.md

## v1.3.0 — 2026-03-18 (Phase 3: Intelligence Layer)

- Controlled skill inference (`skill_graph.py`, ~100 relationships, threshold ≥ 0.7)
- AI-powered CV summarization (optional Anthropic/OpenAI LLM)
- Skill-to-description matching (~65 synonym groups)
- Job recommendation engine (Like/Apply/Not Interested)
- Interview tracking pipeline with stages and reminders

## v1.2.0 — 2026-03-15 (Phase 2: Profile Enrichment)

- LinkedIn ZIP export parsing
- GitHub API integration
- Interactive profile setup with merged CV + preferences

## v1.1.0 — 2026-03-15 (Phase 1: CV + Preferences)

- CV parsing (PDF/DOCX) with skill extraction
- User preferences system
- SearchConfig generation from profile
- Domain-agnostic keyword system

## v1.0.0 — Initial Release

- 48 job sources (7 keyed, 10 free, 10 ATS, 8 RSS, 7 scrapers, 4 other, 1 intel)
- Scoring 0-100: Title + Skills + Location + Recency − Penalties
- Deduplication by normalized company+title
- Notifications: Email, Slack, Discord
- CLI: run, view, dashboard, status, sources
- Streamlit dashboard
- Async rate limiting, retry logic, SQLite storage
