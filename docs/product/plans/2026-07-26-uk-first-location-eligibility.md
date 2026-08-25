# UK-first feed — location eligibility plan (5-lens review, 2026-07-26)
<!-- doc: PLAN -->

> ## ⚠️ SUPERSEDED — 2026-08-24. Do NOT build from this document.
>
> The problem this plan solves was solved a **different way** and is shipped.
> The live implementation is `backend/src/services/uk_gate.py` (`check_uk`), a
> single chokepoint called once per job before storage, matching against the
> compiled gazetteer in `backend/src/data/uk_gazetteer/`. See root `CLAUDE.md`
> rule #30 and `docs/product/product_design_rules.md`.
>
> **Verified 2026-08-24: the two modules this plan tells you to create do not
> exist and never will** — there is no `backend/src/core/markets.py` and no
> `backend/src/services/location/resolver.py`. A reader following the build
> order below would write new code for a problem that already has an owner.
>
> Kept as the record of the 5-lens review and the reasoning behind the design
> that was chosen instead. Read it as history, not as instructions.

**Original status line (2026-07-26): DESIGNED, NOT BUILT. Awaiting owner approval of build order (F3/F4 = scoring, owner's domain).**
Produced by a 5-agent review (data / code / product / design / adversarial) against live prod + real code.
Full lens transcripts: session e40bad3c workflow `wf_eeddf972-ef0` (journal.jsonl per-agent).

## The core reframe
Job360 models **where the office is** but not **who can legally hold the job**. That missing axis
(eligibility relative to a target country) is why a UK user's feed contains Seoul (51, no penalty),
"Remote – US" (US-only), and 41 country-less "Remote" rows. Fix = classification + gate, not points tuning.

## Proven facts (prod, 2026-07-26)
- Catalog 104 jobs, 26 distinct messy location strings; `jobs` has ONLY `location text` — no country/remote columns.
- Split: 52.9% Remote/Anywhere (41 of 55 carry ZERO country signal — the real mess), 36.5% UK-onsite, 10.6% foreign-onsite (~11 jobs — small).
- Title scorer starved: `skill_matcher.py:480-493` pays 40 only for byte-identical title; user targets ("AI/ML Engineer Intern", "AI Solutions Engineer – R&D Department") can never equal a real posting → best 20/40 via substring, else word-overlap (8/40 for "AI engineer (UK)", 0 for most). Verified against live component data.
- Foreign penalty never fires: `skill_matcher.py:352-368` checks hand-typed `FOREIGN_INDICATORS` (lines 52-140); "seoul"/"ottawa" absent → returns 0 ("unknown → don't penalise"). Same lists imported by `sources/base.py:16` `_is_uk_or_remote()` used by 45/46 sources at fetch.

## Ranked fixes
| # | Fix | Where | Effort | Scoring? |
|---|-----|-------|--------|----------|
| F1 | **LocationResolver + Market config** — parse `location` → `{country, remote_flag, worksite, scope, confidence}`; `TARGET_COUNTRY` env param (default GB); `src/core/markets.py` Market dataclass (country_aliases, eligible_remote_scopes, region_membership, currency); data-driven resolution (pycountry + bundled city→country data, lazy-imported rules #11/#16) — NOT hand-typed lists | new `src/services/location/resolver.py`, `src/core/markets.py` | MED | No |
| F2 | **Eligibility gate, deny-by-default** — buckets: 1 target-onsite/hybrid, 2 target-remote, 3 region-remote (EMEA/Europe incl target), 4 global remote → shown ranked in that order; 5 other-country remote ("Remote – US") + 6 foreign-onsite → HIDDEN by default; 7 ambiguous bare "Remote" → shown, demoted, flagged "eligibility unconfirmed". Gate must run at WRITE time too (before dispatch in main.py) or notifications still leak Seoul jobs (refuter catch #1) | feed read path + `main.py` pre-dispatch + nullable derived cols on `jobs` | MED (dep F1) | Adjacent |
| F3 | **Title scorer → fuzzy token similarity** (RapidFuzz token_set_ratio scaled to TITLE_WEIGHT) — kills the exact-match starvation | `skill_matcher.py:480-493` | LOW-MED | **YES — owner approval** |
| F4 | **Retire hardcoded FOREIGN_INDICATORS penalty** (superseded by F1/F2). Do NOT touch the 45 fetch call-sites in v1 (refuter catch #2: fetch volume + paid-LLM cost explosion) — leave `_is_uk_or_remote` fetch filtering as-is, revisit later measured | `skill_matcher.py:352-368` only | LOW (dep F1) | **YES — owner approval** |
| F5 | **API params** on GET /api/jobs: `worksite[]`, `include_ineligible=false`, `location` — validated inputs (security) | `api/routes/jobs.py` | LOW | No |
| F6 | **Dashboard: 3 controls only** — worksite chips; "Show jobs I can't legally take" switch (default OFF); optional city box | FilterPanel.tsx + JobFilters | MED | No |

## Adversary catches to respect while building
1. Gate at write-time too (notifications path) — not just dashboard read.
2. v1 does NOT relax fetch filters (45 sources, rule #2 surface; cost blow-up risk).
3. City-name ambiguity (London Ontario vs London UK; Cambridge MA): resolver requires explicit country/state token, else `confidence=low` → treated as ambiguous bucket 7.
4. Backfill: nullable ADD COLUMN (fast) + separate batched idempotent script, never inline in migration tx.
5. `job_enrichment.workplace_type/remote_region` (LLM, opt-in, rule #18) must NEVER be read by the deterministic gate — document in resolver module.

## Validation (defines "fixed")
Rescore → re-pull per-user component table (script pattern: `C:/Users/Ranjith/.claude/jobs/e40bad3c/tmp/breakdown.py` via `railway run -s Postgres`). PASS = exact-title jobs top the feed with high title points; no bucket-5/6 job visible by default; ambiguous remote flagged. Also: drive the real dashboard (verify-job360) and screenshot what a user sees.

## Product edge (why bother)
Indeed/LinkedIn don't solve remote-eligibility either (keyword hacks); only Otta approximates it. First-class eligibility filtering is a differentiator, not a patch. Country-as-parameter means .uk → .com is a config row, not a rewrite.
