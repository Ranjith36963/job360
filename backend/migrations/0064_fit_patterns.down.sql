-- 0064 down: reverses the DDL only. No pre-existing `applications` row or
-- column is touched beyond the ones added by the up file.
--
-- WHAT THIS COSTS, STATED PLAINLY: the CURRENT score source, gap kinds, ad
-- requirements, archetype, remote policy, team size and agency/direct of every
-- fit are dropped with the columns. Every value that arrived through save_fit
-- also lives in the `fit_judged` event payload (append-only), so the history
-- survives; only the slot copies go.

ALTER TABLE applications DROP COLUMN IF EXISTS fit_score_source;
ALTER TABLE applications DROP COLUMN IF EXISTS fit_gaps_detail;
ALTER TABLE applications DROP COLUMN IF EXISTS fit_jd_requirements;
ALTER TABLE applications DROP COLUMN IF EXISTS fit_archetype;
ALTER TABLE applications DROP COLUMN IF EXISTS fit_remote_policy;
ALTER TABLE applications DROP COLUMN IF EXISTS fit_team_size;
ALTER TABLE applications DROP COLUMN IF EXISTS fit_via;
