# Job360 Project Status

## Current State: Search Engine Overhaul Complete (v3.0.0)

**Last updated:** 2026-03-21
**Total tests:** 658 (655 passed, 3 skipped on Windows — bash-only tests), 0 failures
**Source files:** ~90 Python modules | **Test files:** 29 test modules
**Job sources:** 48 registered in SOURCE_REGISTRY

---

## End User

Professional job seeker, any domain. One user = one unified profile.

## 4 Inputs (in priority order)

| # | Input | Required? | Purpose |
|---|-------|-----------|---------|
| 1 | **CV** | **Mandatory** | No CV = no profile = no search. Full stop. |
| 2 | **Preferences** | Nice to have | Manual enrichment beyond CV |
| 3 | **LinkedIn** | Nice to have | Career biography — covers tech AND non-tech professionals |
| 4 | **GitHub** | Nice to have | Tech roles only — repos, languages, contributions |

## Core Rules

1. **No hard-coded domain-specific keywords.** All job-specific keywords come from the user's profile.
2. **No CV = no search.** System does not proceed without a CV. No fallback, no defaults.
3. **Only search what the user gave us.** Keywords come from the user's inputs, not from guesses.
4. **Controlled keyword expansion only.** If user says AWS, we can add Azure and GCP (same category, closely related). We do NOT add random or vague keywords.

## Core Matching Philosophy

Match what the **candidate has** (skills from all 4 inputs) against what **job descriptions ask for**. Not title-to-title matching. The user's concentrated skills are matched against job descriptions. This eliminates the need for multi-profile support — one unified search covers all relevant roles automatically.

---

## Phase 1: CV + Preferences — COMPLETE

**Goal:** Build a profile system where the CV is mandatory and preferences provide manual enrichment, so Job360 works for any profession.

### What was built

| Component | File(s) | Status |
|-----------|---------|--------|
| Profile dataclasses | `src/profile/models.py` | Done — CVData, UserPreferences, UserProfile, SearchConfig |
| CV parser (PDF/DOCX) | `src/profile/cv_parser.py` | Done — pdfplumber + python-docx, section detection |
| Preferences validator | `src/profile/preferences.py` | Done — form validation, CV+prefs merge |
| Profile storage | `src/profile/storage.py` | Done — JSON at `data/user_profile.json` |
| Keyword generator | `src/profile/keyword_generator.py` | Done — UserProfile → SearchConfig conversion |
| JobScorer class | `src/filters/skill_matcher.py` | Done — dynamic scoring using SearchConfig |
| BaseJobSource properties | `src/sources/base.py` | Done — `self.relevance_keywords`, `self.job_titles`, `self.search_queries` |
| All 48 sources refactored | `src/sources/*.py` | Done — all use `self.*` properties, no direct keyword imports |
| Orchestrator wiring | `src/main.py` | Done — loads profile, blocks if no CV, creates scorer |
| Dashboard Profile UI | `src/dashboard.py` | Done — sidebar expander with CV upload + form |
| CLI setup-profile | `src/cli.py` | Done — interactive profile wizard |
| Profile tests | `tests/test_profile.py` | Done — 44 tests covering all profile modules |
| Dependencies | `requirements.txt` | Done — added pdfplumber, python-docx |

### Preferences sub-topics (11 fields)

| # | Sub-topic | What the user provides |
|---|-----------|----------------------|
| 1 | Target Job Titles | Roles they're looking for |
| 2 | Additional Skills | Skills not on CV |
| 3 | Excluded Skills | Skills to filter OUT |
| 4 | Preferred Locations | Where they want to work |
| 5 | Industries | Target industries |
| 6 | Salary Min | Minimum salary |
| 7 | Salary Max | Maximum salary |
| 8 | Work Arrangement | remote, hybrid, onsite |
| 9 | Experience Level | junior, mid, senior, etc. |
| 10 | Negative Keywords | Words to avoid in job listings |
| 11 | About Me | Free text |

### How it works

1. User creates profile via CLI (`setup-profile`) or Dashboard (sidebar)
2. CV is mandatory — no CV = no search
3. Profile saved to `data/user_profile.json`
4. On pipeline run, `main.py` loads profile → generates `SearchConfig`
5. SearchConfig passed to all sources and JobScorer
6. No profile = system returns early with error message

### Hard-coded AI/ML keywords removed

- `keywords.py` no longer contains AI/ML-specific defaults (JOB_TITLES, PRIMARY_SKILLS, etc.)
- Module-level `score_job()` and `check_visa_flag()` removed — only `JobScorer` class remains
- `BaseJobSource` properties return empty lists when no config (no fallback)
- `SearchConfig.from_defaults()` removed
- Domain-agnostic data kept: UK LOCATIONS, VISA_KEYWORDS, KNOWN_SKILLS (for CV parsing)

---

## Phase 2: LinkedIn ZIP + GitHub API — COMPLETE

**Goal:** Enrich user profiles with LinkedIn data export and GitHub public repos.

### What was built

- LinkedIn ZIP parser (positions.csv, skills.csv, education.csv, certifications.csv, profile.csv)
- GitHub API integration (fetch repos, languages, topics, infer skills)
- Enrichment functions merge LinkedIn/GitHub data into existing CVData
- Keyword generator uses LinkedIn skills, positions, industry + GitHub inferred skills
- 54 tests in `test_linkedin_github.py`

---

## Phase 3: Intelligence Layer — COMPLETE

| Feature | File(s) | Status |
|---------|---------|--------|
| **Controlled skill inference** | `src/profile/skill_graph.py` | Done — 446 skill relationships (1126 edges), threshold=0.7, inferred → tertiary only |
| **AI-powered CV summarization** | `src/profile/cv_summarizer.py` | Done — optional LLM (anthropic/openai), no key = regex-only |
| **Skill-to-description matching** | `src/filters/description_matcher.py` | Done — 345 synonym groups, `text_contains_with_synonyms()` replaces `_text_contains` in skill scoring |
| **Job recommendation engine** | `src/storage/user_actions.py` | Done — Liked/Applied/Not Interested per job, dashboard buttons + sidebar filter |
| **Interview tracking pipeline** | `src/pipeline/tracker.py`, `reminders.py` | Done — stages from applied → offer/rejected, 7-day outreach reminders, CLI `pipeline` command |
| **Feature 4→5 integration** | `user_actions.py`, `dashboard.py` | Done — "Applied" action auto-creates application entry |

---

## v2.0.0 — Tooling Redesign + Infrastructure (2026-03-19)

### What changed

| Area | Before | After |
|------|--------|-------|
| Skills | 5 overlapping (`/add-source`, `/test`, `/validate-rules`, `/profile-check`, `/source-status`) | 5 zero-overlap (`/scaffold`, `/test`, `/audit`, `/status`, `/debug`) |
| Agents | 3 overlapping (`source-builder`, `test-runner`, `rule-auditor`) | 2 zero-overlap (`builder`, `reviewer`) |
| Hooks | Verbose PostToolUse prompt (repeated audit checks) | Simplified (focus on MD updates) |
| CI/CD | None | GitHub Actions (Python 3.9/3.11/3.13) |
| Security | No db-path validation, no log PII redaction | Path validation + PII sanitizer |
| Resilience | No circuit breaker, no schema versioning | Circuit breaker (3 failures) + schema_version table |
| Code quality | No custom exceptions, hardcoded version | Exception hierarchy + `__version__.py` |
| Docs | README stale (24 sources, 212 tests) | All docs updated to current state |

### New files
- `.github/workflows/tests.yml` — CI pipeline
- `pyproject.toml` — Project metadata + pytest config
- `src/exceptions.py` — Job360Error → SourceError, ScoringError, ProfileError, DatabaseError
- `src/__version__.py` — Version constant
- `CHANGELOG.md` — Full version history

---

## Search Engine Overhaul (v3.0.0) — COMPLETE

### Part A — 8 Fixes

| Fix | Feature | File(s) |
|-----|---------|---------|
| 14 | Hard remove foreign jobs | `is_foreign_only()` in skill_matcher.py, hard filter in main.py |
| 12 | Search all UK, no location cap | `"{title} UK"` in keyword_generator.py |
| 9 | Salary: no rejection, band filters | models.py salary validation, SALARY_BANDS in settings.py |
| 9b | Job type filter | `detect_job_type()` in jd_parser.py, `job_type` on Job model |
| 8 | Recency 7-day buckets | 5 buckets in time_buckets.py (24h, 48h, 3d, 5d, 7d) |
| 3 | Smarter dedup | Two-pass in deduplicator.py with SequenceMatcher (0.85) |
| 5 | Domain intelligence | `detect_domains()` in domain_detector.py (10 domains) |
| 6 | Dynamic location filters | Locations extracted from results in dashboard.py |

### Part B — 9 Phases

| Phase | Feature | File(s) | Tests |
|-------|---------|---------|-------|
| 0A | Structured CV parsing | cv_structured_parser.py | 46 |
| 0B | JD section parsing | jd_parser.py | 32 |
| 0C | ESCO skill taxonomy | description_matcher.py (345 synonym groups) | 34 |
| 2A | Multi-dimensional scorer | score_detailed() in skill_matcher.py (8 dims) | 62 |
| 2B | Skill gap + match explanation | match_data JSON in main.py | implicit |
| 1A | Embeddings | embeddings.py (all-MiniLM-L6-v2, 384-dim) | 24 |
| 1B | Hybrid retrieval | hybrid_retriever.py (FTS5 + vector RRF fusion) | 21 |
| 3A | Cross-encoder reranking | reranker.py (ms-marco-MiniLM-L-6-v2, top-50) | 13 |
| 3B | Feedback loop | feedback.py (liked/rejected → ±5 score adjustment) | 23 |

---

## Known Issues

| Issue | Severity | Notes |
|-------|----------|-------|
| 3 tests skip on Windows | Low | bash-only tests for `setup.sh` and `cron_run.sh` — pass on Linux/Mac |

| CV parser section detection | Low | Regex-based — may miss non-standard CV formats. Works for ~80% of CVs |
| LLM CV parsing requires API key | Low | Optional feature — system works with regex-only parsing without any API key |

---

## Test Coverage by Module

| Test file | Module tested | Tests |
|-----------|--------------|-------|
| `test_sources.py` | All 48 sources | 65 |
| `test_scorer.py` | `skill_matcher.py` scoring (legacy + 8D) | 62 |
| `test_linkedin_github.py` | LinkedIn ZIP + GitHub API | 54 |
| `test_cv_structured_parser.py` | Structured CV parsing (0A) | 46 |
| `test_profile.py` | `src/profile/*`, `JobScorer` | 44 |
| `test_time_buckets.py` | `time_buckets.py` | 34 |
| `test_description_matcher.py` | Synonym matching, word boundaries (0C) | 34 |
| `test_jd_parser.py` | JD section parsing (0B) | 32 |
| `test_skill_graph.py` | Skill inference, bidirectional graph | 29 |
| `test_pipeline.py` | Application tracking, reminders | 24 |
| `test_embeddings.py` | Sentence-transformer embeddings (1A) | 24 |
| `test_feedback.py` | Feedback loop adjustment (3B) | 23 |
| `test_hybrid_retriever.py` | FTS5 + vector hybrid search (1B) | 21 |
| `test_models.py` | `models.py` Job dataclass | 20 |
| `test_notifications.py` | Slack + Discord channels | 19 |
| `test_deduplicator.py` | `deduplicator.py` (two-pass) | 18 |
| `test_user_actions.py` | Liked/Applied/Not Interested actions | 14 |
| `test_cv_summarizer.py` | LLM extraction, merge logic | 14 |
| `test_reranker.py` | Cross-encoder reranking (3A) | 13 |
| `test_main.py` | `main.py` orchestrator | 10 |
| `test_cli.py` | `cli.py` commands | 10 |
| `test_domain_detector.py` | Professional domain detection | 9 |
| `test_notification_base.py` | Channel base + discovery | 7 |
| `test_reports.py` | Report generation | 6 |
| `test_database.py` | SQLite database | 6 |
| `test_setup.py` | setup.sh + requirements | 6 |
| `test_cron.py` | cron_run.sh | 5 |
| `test_cli_view.py` | `cli_view.py` | 5 |
| `test_csv_export.py` | CSV export | 4 |

---

## Quick Verification

```bash
# All tests pass
python -m pytest tests/ -v

# Profile setup works
python -m src.cli setup-profile --cv path/to/cv.pdf

# Pipeline with profile
python -m src.cli run --dry-run --log-level DEBUG
# Log: "Using keywords from user profile"

# Pipeline without profile (returns early)
rm data/user_profile.json
python -m src.cli run --dry-run --log-level DEBUG
# Log: "No user profile found. Upload a CV to start searching."
```
