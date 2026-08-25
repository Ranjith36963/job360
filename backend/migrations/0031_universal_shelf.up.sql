-- 0031_universal_shelf: STEP 1 ("the frame") of the Universal Shelf design
-- (docs/pillars/UNIVERSAL_SHELF.md). Opens the door for four shelves that had
-- NO catalog column at all before this — employment_type, seniority,
-- workplace_mode, category, source_tags — plus salary unit metadata
-- (currency/period/is_estimated + the derived GBP-annual pair), the visa
-- 3-state text column, and shelf_provenance: one JSONB column recording HOW
-- every shelf got its value (source | derived | llm | absent), so "no salary
-- offered" (a fact about the job) is never confused with "nobody looked" (a
-- fact about our pipeline) — see UNIVERSAL_SHELF.md §3/§4.
--
-- Additive only. Every value column is nullable (or list/JSONB-defaulted to
-- an empty shell) so every existing row keeps working with zero backfill.
-- Shared catalog — no user_id/tenant_id anywhere here (CLAUDE.md rule #10).
--
-- shelf_provenance is JSONB, not TEXT: it is the one column in this
-- migration read/written as a whole blob (json.dumps/json.loads in Python,
-- see services/shelf_gate.py), never queried with a JSON operator on any hot
-- path, so translate() (src/repositories/pg.py) needs no new SQL grammar and
-- the schema-per-test Postgres suite keeps working unchanged. Every other
-- new column is a real, indexable, typed value column — see
-- UNIVERSAL_SHELF.md §3 "Values live in typed columns; provenance lives in
-- JSONB."
--
-- WHAT SHIPS WITH THIS MIGRATION (corrected 2026-08-25): steps 1, 2 and 3 now
-- land together, so these columns are LIVE the moment this runs. Source
-- mappers write them (lever.py, recruitee.py, smartrecruiters.py and others)
-- and services/shelf_enrichment.py calls fill_shelves(). The paragraph that
-- stood here said the opposite — written when step 1 was going to ship alone —
-- and it is the paragraph an operator reads before deciding whether the down
-- migration is safe. It is not: see 0031_universal_shelf.down.sql.
-- (CodeRabbit, PR #388.)
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS employment_type TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS workplace_mode TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS seniority TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_tags TEXT DEFAULT '[]';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS visa_status TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS salary_currency TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS salary_period TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS salary_is_estimated BOOLEAN;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS salary_min_gbp_annual REAL;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS salary_max_gbp_annual REAL;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS shelf_provenance JSONB NOT NULL DEFAULT '{}';
