-- 0063_profile_edits_remember: weak spot #6 wave 2. A sensitive answer (equality, salary, visa)
-- an assistant saves is kept ONLY with the user's "remember it" OK. This column is the marker the
-- SERVER stamps (never the caller) when that OK came with the write; NULL = no marker.
-- Written once at INSERT time; never updated. DDL ONLY, nullable, IF NOT EXISTS.
ALTER TABLE profile_edits ADD COLUMN IF NOT EXISTS remember_consent_at TEXT;
