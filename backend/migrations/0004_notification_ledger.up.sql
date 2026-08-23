-- 0004_notification_ledger: durable audit trail + per-channel idempotency.
-- See docs/product/plans/batch-2-plan.md Phase 5 and blueprint §1 "Deduplication".

CREATE TABLE IF NOT EXISTS notification_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    job_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,              -- 'queued','sent','failed','dlq'
    sent_at TEXT,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, job_id, channel)   -- idempotency: never send same (user, job, channel) twice
);

CREATE INDEX IF NOT EXISTS idx_ledger_user_status ON notification_ledger(user_id, status);
CREATE INDEX IF NOT EXISTS idx_ledger_job ON notification_ledger(job_id);
