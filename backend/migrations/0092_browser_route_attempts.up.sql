-- 0092_browser_route_attempts: what each visit of Job360's browser met (routes PR C1, record only).
--
-- One row per attempt the gate in api/routes/browser.py books: the site (kind, key and the host it ran on),
-- the task ("read" with no application, "fill" with one), the browser that ran it, and the outcome read off
-- transport facts only (services/browser_agent/outcome.py). Closed values and the host only: never a URL, a
-- path, page text, an application or a contact (security M1). Nothing reads it to route yet; the learner
-- (PR F1) will, after two weeks of real rows. route_book.record prunes a user's rows older than 30 days.
-- Per user: erased with the account and included in its export (JobDatabase._PER_USER_TABLES /
-- _EXPORT_TABLES), registered in scripts/observe.py. The pg shim strips REFERENCES, so erasure is the sweep.
--
-- DDL ONLY. IF NOT EXISTS so a re-run is harmless.

CREATE TABLE IF NOT EXISTS browser_route_attempts (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    site_kind TEXT NOT NULL CHECK (site_kind IN ('linkedin', 'job_board', 'ats', 'company')),
    site_key TEXT NOT NULL CHECK (length(site_key) <= 260),
    tenant_host TEXT NOT NULL CHECK (length(tenant_host) <= 253),
    task TEXT NOT NULL CHECK (task IN ('read', 'fill')),
    browser TEXT NOT NULL CHECK (browser IN ('job360', 'own_browser', 'job360_server')),
    outcome TEXT NOT NULL CHECK (outcome IN (
        'ok', 'confirmed', 'bot_block', 'login_wall', 'captcha', 'rate_limited', 'page_error', 'pending'
    )),
    probe BOOLEAN NOT NULL DEFAULT false,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_bra_user_key
    ON browser_route_attempts (user_id, site_key, task, occurred_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_bra_one_pending_probe
    ON browser_route_attempts (user_id, site_key) WHERE probe AND outcome = 'pending';
CREATE INDEX IF NOT EXISTS ix_bra_rate
    ON browser_route_attempts (site_key, occurred_at) WHERE outcome = 'rate_limited';
