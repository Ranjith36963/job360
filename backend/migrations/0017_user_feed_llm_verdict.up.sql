-- Per-user LLM matcher verdict (funnel -> judge). Lives on user_feed because
-- fit is per-(user, job) state -- rules #10/#17 keep jobs/job_enrichment shared.
ALTER TABLE user_feed ADD COLUMN llm_fit_score INTEGER;
ALTER TABLE user_feed ADD COLUMN llm_verdict TEXT;
ALTER TABLE user_feed ADD COLUMN llm_reason TEXT;
ALTER TABLE user_feed ADD COLUMN llm_matched_at TEXT;
