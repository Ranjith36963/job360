-- 0006_user_profiles: per-user CV / preferences / LinkedIn / GitHub store.
--
-- Replaces the single-file `data/user_profile.json` from the pre-Batch-3.5.2
-- era. One row per user; CASCADE-deletes when the parent `users` row is
-- removed. JSON columns carry dataclass-serialised payloads —
-- `services/profile/storage.py` handles the round-trip.
--
-- Access path is the PK; no additional indexes needed.
-- See docs/plans/batch-3.5.2-plan.md Deliverable A.

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY
        REFERENCES users(id) ON DELETE CASCADE,
    cv_data TEXT,
    preferences TEXT,
    linkedin_data TEXT,
    github_data TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
