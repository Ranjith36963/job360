# Job360 delivery layer: push, scoring, and parity at multi-user scale

**Job360 can ship a production-grade delivery layer — push notifications across Slack, Gmail, Telegram, and Discord with dashboard parity — for under $10/month at 1,000 users.** The architecture rests on three pillars: Apprise as a zero-cost multi-channel sending engine wrapped in a task queue, a pre-filter-first scoring pipeline that reduces compute by 99%, and a single-source-of-truth `user_feed` table that guarantees dashboard and push always show the same data. No existing job search tool offers Slack, Telegram, or webhook-based notifications — this is entirely unserved territory and Job360's genuine differentiator. The critical constraint is honest: LLM-based scoring in the hot path is economically infeasible beyond single-user scale, and SMS delivery costs make it impractical as a default channel.

---

## 1. Push notification architecture

### When to fire: a trigger policy decision tree

The trigger policy must balance notification value against fatigue. Research across LinkedIn, Indeed, Glassdoor, Teal, and every major job platform reveals a universal pattern: **no tool implements score-threshold notifications or tiered urgency**. All offer only binary daily/weekly digests or "as posted" alerts. This gap is Job360's opportunity.

The recommended trigger policy uses a **three-tier decision tree**. Tier 1 fires immediately for jobs scoring **≥80** that enter the 24h bucket — these are rare, high-value matches where latency matters. Tier 2 batches jobs scoring **30–79** into a user-scheduled digest (morning at 8am local, or hourly, per user preference). Tier 3 suppresses entirely — jobs below `MIN_MATCH_SCORE=30` never trigger notifications but remain visible on the dashboard for completeness. This hybrid approach mirrors how feed-based products like Instagram handle priority content (immediate push for high engagement probability, batched for the rest) while respecting the freshness-bucket model from Batch 1.

The decision tree logic:

```
NEW_JOB_SCORED(user, job, score, bucket) →
  IF score ≥ user.instant_threshold (default 80):
    → Queue IMMEDIATE notification to all user channels
  ELIF score ≥ 30 AND bucket IN user.subscribed_buckets:
    → Add to user's next DIGEST batch
  ELSE:
    → Dashboard only (no notification)
    
DIGEST_TIMER(user) fires at user.digest_schedule →
  → Collect all pending digest items since last_notified_at
  → Sort by score DESC, limit to top 10-15
  → Send single digest notification to all user channels
```

Bucket-transition notifications add a refinement: when a job moves from the 24h bucket to the 24–48h bucket, it should not re-trigger. The notification ledger prevents this. When ghost detection marks a job stale, no correction notification is sent — the job simply disappears from the next digest and dashboard. Sending "sorry, that job is gone" notifications would erode trust without adding value.

### Multi-channel architecture: Apprise wins at zero cost

Five notification routing libraries were evaluated against Job360's constraints. **Apprise is the clear winner for zero-cost multi-channel delivery.** It supports 100+ services including Slack, Gmail/SMTP, Telegram, Discord, and generic webhooks through a URL-based configuration system (`tgram://bot_token/chat_id`, `slack://tokenA/tokenB/tokenC`). It is MIT-licensed with no usage limits, no cloud dependency, and native Python support.

The critical caveat: **Apprise is a sending library, not infrastructure.** It provides no queuing, retry logic, deduplication, or delivery tracking. Job360 must wrap it in a task queue for production reliability. The recommended stack is **Apprise + ARQ (async Redis queue) + PostgreSQL notification ledger**.

Novu self-hosted is the alternative if the team wants a full-featured notification workflow engine with built-in digests, user preferences, and retry logic. It requires MongoDB + Redis + S3, costing **$5–10/month** for infrastructure. Its cloud free tier (10,000 workflow runs/month) covers ~330 daily notifications — tight for even 1,000 users getting daily digests. For a bootstrapped product, the Apprise approach provides more control at lower cost.

The paid options (Knock at $250/month for 50K messages, Courier at $99/month, Pingram/NotificationAPI at $20/month for 50K) are all cost-prohibitive for a budget-sensitive project. **None are viable at zero cost beyond testing.**

| Library | Free limit | 1K users viable? | 100K users viable? |
|---------|-----------|-------------------|---------------------|
| Apprise (OSS) | Unlimited | Yes (build queue yourself) | Yes (with custom infra) |
| Novu self-hosted | Unlimited | Yes ($5–10/mo hosting) | Yes ($20–50/mo hosting) |
| Novu Cloud | 10K runs/mo | Tight | No ($250+/mo) |
| Knock | 10K msgs/mo | Testing only | No ($1,500+/mo) |
| Courier | 10K notifs/mo | Testing only | No (premium pricing) |

### Channel rate limits and payload design

Each channel imposes hard constraints that the notification worker must respect:

**Slack** allows ~1 message/second per webhook. For 1,000 users on Slack receiving a digest, that is **~17 minutes of sequential delivery** — acceptable for a morning digest but not for "instant" notifications across all users simultaneously. The solution: use Slack Bot API with per-user OAuth tokens rather than a single webhook, enabling parallel delivery. Block Kit payloads support rich formatting with action buttons (Apply Now, View in Dashboard) within a **16KB payload / 4,000-character** text limit.

**Telegram Bot API** permits **30 messages/second globally** (free tier). Broadcasting to 100,000 users takes ~55 minutes. Paid broadcasts (via @BotFather, 0.1 Telegram Stars/message) scale to 1,000 messages/second but introduce cost. Messages support MarkdownV2 formatting and inline keyboards with URL buttons, limited to **4,096 characters**.

**Gmail SMTP** is the most constrained channel. Free Gmail accounts allow only **100 emails/day via SMTP** (500 via web interface). Google Workspace raises this to 2,000/day. For scale beyond 100 users, Job360 needs a dedicated email service — Amazon SES at **$0.10 per 1,000 emails** is the cheapest production option. SendGrid's free tier offers only 100 emails/day. Every email must include a one-click `List-Unsubscribe` header and visible unsubscribe link (required by Google/Yahoo since February 2024 and UK PECR regulations).

**Discord webhooks** allow 5 requests per 2 seconds (~150/minute). Embed format supports rich payloads within a **6,000-character total** limit across all embed fields.

**SMS is not viable as a default channel.** UK SMS costs ~£0.04/message via Twilio. For 1,000 users receiving one daily SMS, that is **~$1,200/month** — an order of magnitude more than the entire infrastructure budget. SMS should be offered only as a premium channel or for critical alerts (e.g., "dream job" matches scoring 95+).

The recommended per-channel payload format for a single job notification:

- **Slack**: Block Kit with header (job title), section fields (company, salary, location, score), action buttons (Apply, Dashboard)
- **Gmail**: HTML email with inline CSS, 600px width, job card layout, score badge, time-bucket indicator, one-click unsubscribe
- **Telegram**: MarkdownV2 with emoji indicators, inline keyboard URL buttons
- **Discord**: Embed with color-coded border (green for 90+, yellow for 60–89, grey for 30–59)
- **Digest format**: Top 10 jobs grouped by bucket, sorted by score descending, with individual apply links

### Deduplication: the notification ledger

Deduplication operates at three levels. First, a **Redis SET NX with 7-day TTL** provides fast, in-memory dedup checking before any send attempt:

```python
dedup_key = f"notif:{user_id}:{job_id}"
is_new = redis.set(dedup_key, "1", nx=True, ex=604800)  # 7-day window
if not is_new:
    return  # Already notified about this job
```

Second, a **PostgreSQL notification ledger** provides durable audit trail and delivery tracking:

```sql
CREATE TABLE notification_ledger (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL,
    job_id VARCHAR NOT NULL,
    channel VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL,  -- 'queued','sent','delivered','failed','dlq'
    sent_at TIMESTAMPTZ,
    error_message TEXT,
    retry_count SMALLINT DEFAULT 0,
    UNIQUE(user_id, job_id, channel)
);
```

Third, the **channel-aware dedup policy**: send the same job to all user-configured channels. Research and user-experience analysis confirms that job alerts are high-value, time-sensitive content — users configure specific channels because they want to see matches there. Suppressing Gmail because Slack succeeded risks the user missing the match entirely. Only deduplicate within the same channel (never send the same job twice to the same Slack channel).

### Reliability infrastructure

The notification pipeline requires a task queue with retry, dead letter queue, and rate limiting. **ARQ (async Redis queue)** is the recommended starting point for Job360's FastAPI-based architecture. It is async-native, integrates naturally with FastAPI, includes built-in scheduling and retries, and uses only ~30–60 MB RAM. The tradeoff: ARQ lacks built-in fan-out primitives, rate limiting, and GUI monitoring. These must be implemented in application code.

The retry policy follows channel-specific logic:

| Response code | Action |
|---------------|--------|
| 2xx | Mark delivered, log |
| 400 | Do not retry — fix payload |
| 401/403 | Do not retry — credential issue, alert user to re-authenticate |
| 429 | Retry after `Retry-After` header value (Slack, Telegram both provide this) |
| 5xx | Exponential backoff: 1s, 2s, 4s, 8s, 16s, max 5 retries |
| Timeout | Retry with backoff, max 3 retries |

After max retries exhausted, the notification moves to a dead letter queue (a separate Redis list or PostgreSQL table with `status='dlq'`). A circuit breaker pattern pauses delivery to a channel after 5 consecutive failures, resuming with exponential backoff.

### User preferences schema

```sql
CREATE TABLE user_notification_preferences (
    user_id UUID PRIMARY KEY REFERENCES users(id),
    channels JSONB NOT NULL DEFAULT '[]',
    -- e.g. [{"type":"slack","webhook_url":"...","enabled":true},
    --       {"type":"telegram","chat_id":"...","enabled":true}]
    digest_frequency VARCHAR(20) DEFAULT 'daily',  -- 'instant','hourly','daily','weekly'
    digest_time TIME DEFAULT '08:00',
    timezone VARCHAR(50) DEFAULT 'Europe/London',
    quiet_hours_start TIME,  -- null = no quiet hours
    quiet_hours_end TIME,
    instant_threshold SMALLINT DEFAULT 80,  -- score threshold for immediate push
    subscribed_buckets VARCHAR[] DEFAULT ARRAY['last_24h','24_48h'],
    global_enabled BOOLEAN DEFAULT TRUE,
    consent_given_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

**Quiet hours** are a differentiation feature — no job search tool implements them. Telegram and Slack notify at any hour; Job360 respects the user's timezone and defers notifications to the next morning if a match arrives at 2am. **GDPR compliance** requires storing consent timestamps, providing per-channel opt-out, including `List-Unsubscribe` headers in all emails, and supporting full data deletion on request. Notification logs should be retained for **90 days** then anonymized or deleted.

Channel credentials (Slack OAuth tokens, Telegram chat IDs) are stored encrypted using **Fernet (AES-128-CBC + HMAC-SHA256)** in the PostgreSQL `channels` JSONB field, with the encryption key in an environment variable. This satisfies UK GDPR Article 32 requirements for "appropriate technical measures" at zero cost.

---

## 2. Multi-user scoring orchestration

### The fundamental architecture choice: hybrid wins

Three scoring architectures were evaluated against Job360's trajectory from 1 to 100,000 users. LinkedIn's JUDE system (a two-tower architecture with a fine-tuned 7B-parameter LLM for candidate retrieval at 260 million monthly job seekers) and Indeed's Jobs Filter Service (a microservice evaluating ALLOW/VETO decisions using rules + Wide & Deep ML models) both confirm the industry consensus: **exhaustive job×user scoring is infeasible, and two-stage retrieval (cheap filter → expensive rank) is non-negotiable**.

The recommended architecture for Job360 is **hybrid scoring with phased rollout**:

**Phase 1 (1–1,000 users): User-centric batch.** Each user's scheduled run scores recent jobs against their profile. This mirrors Job360's current single-user architecture, simply looped over N users. At 1,000 users with 99% pre-filtering, daily scoring of new jobs takes **50 seconds** of CPU time. A $5/month VPS handles this trivially.

**Phase 2 (1,000–10,000 users): Add job-centric for real-time push.** When a new job is scraped and parsed, it is immediately pre-filtered against all user profiles and scored for matches. Users with instant-notification preferences (score ≥80) receive immediate push. All others receive the match in their next scheduled digest. This adds ~16 minutes of daily compute at 10,000 users — still feasible on a single 4-core VPS.

**Phase 3 (10,000+ users): Embedding-based candidate generation.** Replace keyword-based pre-filtering with sentence-transformer embeddings (all-MiniLM-L6-v2) indexed in FAISS for approximate nearest neighbor search. This reduces candidate generation to sub-millisecond per query and enables 99.5%+ filtering rates. CareerBuilder demonstrated significant CTR improvements with this exact approach (FAISS-indexed two-tower embeddings vs. Apache Solr).

### Pre-filtering is everything

**Pre-filtering is the single most important optimization in the entire system.** Without it, scoring 10,000 users against 100,000 jobs requires 1 billion computations — 11.6 days of CPU time on a single core. With 99% pre-filtering, the same workload takes **16.7 minutes**.

Job360's existing 4-dimension scoring system (skills, location, experience, salary) maps naturally to efficient pre-filters that execute as simple database queries:

1. **Location + work arrangement filter** eliminates ~70% of jobs (a London-based user with hybrid preference instantly excludes all Edinburgh-only, fully-remote-US, etc.)
2. **Experience level filter** eliminates ~50% of the remainder (junior candidates skip senior roles)
3. **Skills overlap filter** (at least 1 matching skill keyword) eliminates ~60–80% of what remains
4. **Combined effect**: typically **95–99% elimination** before the expensive 4-dimension weighted scoring runs

For implementation, **self-hosted Meilisearch** on the same VPS provides sub-millisecond inverted-index lookups on skills, location, and experience level — free, MIT-licensed, ~500MB RAM for 500,000 jobs. Alternatively, PostgreSQL's native `GIN` indexes on `tsvector` columns or `ARRAY` operators achieve similar filtering without additional infrastructure.

### Compute cost projections

These projections assume deterministic scoring at ~1ms per job-user pair (Python weighted sum with penalties) and pre-filtering reducing candidates by the stated percentage. LLM scoring (GPT-4o-mini) at ~$0.000135 per computation is included for reference but **is not recommended in the hot path**.

| Scale | Pre-filter rate | Daily new-job scoring time | Full recompute time | Monthly VPS cost | Monthly LLM cost (if used) |
|-------|----------------|---------------------------|---------------------|------------------|---------------------------|
| 1K users × 50K jobs | 99% | **50 seconds** | 8.3 minutes | $5 | $203 |
| 10K users × 100K jobs | 99% | **16.7 minutes** | 2.8 hours | $20 | $4,050 |
| 100K users × 500K jobs | 99.5% | **1.7 hours** | 17.4 hours | $50–100 | $101,250 |

The verdict is stark: **deterministic scoring scales linearly and cheaply; LLM scoring does not.** At 1,000 users, LLM scoring costs $203/month — more than 40× the compute infrastructure. At 10,000 users, it costs $4,050/month. At 100,000 users, it is economically absurd at over $100,000/month. Job360 should use LLM enrichment only for post-hoc explanation of top-10 matches per user (controllable cost of ~$5–15/month at 1K users), never in the scoring hot path.

### Database schema: top-N storage, not full materialization

Full materialization of all user×job scores produces untenable table sizes: 1K users × 50K jobs = 50M rows (5GB), 10K × 100K = 1B rows (100GB), 100K × 500K = 50B rows (5TB). **Top-N storage is the only viable approach.** Storing only the top 200 matches per user:

| Scale | Rows | Storage |
|-------|------|---------|
| 1K users | 200K | ~16 MB |
| 10K users | 2M | ~160 MB |
| 100K users | 20M | ~1.6 GB |

**20 million rows at 1.6GB is trivial for PostgreSQL** on any modern VPS. The schema:

```sql
CREATE TABLE user_job_matches (
    user_id UUID NOT NULL,
    job_id UUID NOT NULL,
    score SMALLINT NOT NULL,
    bucket VARCHAR(20) NOT NULL,
    computed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, job_id)
);

CREATE INDEX idx_matches_dashboard
    ON user_job_matches (user_id, bucket, score DESC);
CREATE INDEX idx_matches_cascade
    ON user_job_matches (job_id);
```

The `job_id` index enables fast cascade deletion when ghost detection marks a job stale — `DELETE FROM user_job_matches WHERE job_id = $stale_id` removes the job from all user feeds in a single indexed operation.

### Freshness-aware invalidation rules

Three events trigger cascade operations:

**New job scraped**: Pre-filter against all user profiles → score matching users → INSERT into `user_job_matches` (UPSERT if job was rescored) → queue instant notification for users with score ≥ instant_threshold. At 1K users with 99% pre-filter: ~10 users scored per new job = 10ms total.

**Job marked stale** (ghost detection): `DELETE FROM user_job_matches WHERE job_id = $stale_id` → remove from all users' feeds. Both dashboard (next load) and notifications (next digest) automatically exclude the stale job because they query the same table. No correction notification is sent.

**User updates profile**: Delete all that user's matches → recompute against all active jobs. With 50K active jobs and 99% pre-filter: 500 jobs × 1ms = **0.5 seconds**. Debounce profile updates (wait 5 minutes after last change before recomputing) to avoid wasted work during rapid editing.

### Python task queue recommendation

Six task queue options were benchmarked. For Job360's FastAPI architecture:

**Start with ARQ**, then migrate to Celery when complexity demands it. ARQ is async-native (perfect for FastAPI), includes built-in cron scheduling and retries, uses only ~30–60MB RAM, and was created by the author of Pydantic. Its limitations — no built-in fan-out primitives, no rate limiting, no GUI monitoring — are acceptable at ≤10K users where fan-out can be implemented as a simple loop and rate limiting as application-level `asyncio.sleep()`.

**Migrate to Celery at ~30K users** when you need built-in `group()`/`chord()` primitives for fan-out, `rate_limit` decorators for channel compliance, Celery Beat for scheduled tasks across workers, and Flower for monitoring. Celery's complexity overhead (multiple processes, broker configuration, heavyweight memory usage at ~100–200MB per worker) is justified only at this scale.

**Do not use RQ** — it is the slowest option in benchmarks (51 seconds for 20K jobs vs. ARQ's 35 seconds and Dramatiq's 4 seconds) and offers no compensating advantages. **asyncio.Task is insufficient** — no persistence, no retry, no dead letter queue. Scoring and notification tasks must survive worker restarts.

---

## 3. Dashboard-push data parity

### The consistency problem is simpler than it appears

The fear is that dashboard and push notifications diverge — showing different jobs, different scores, different buckets. In practice, **Job360's consistency requirements are solved almost entirely by reading from the same PostgreSQL table.** The dashboard API queries `user_feed WHERE status = 'active' ORDER BY score DESC`. The notification worker queries `user_feed WHERE status = 'active' AND created_at > last_notified_at ORDER BY score DESC LIMIT 10`. Both apply the same `WHERE status = 'active'` filter, so ghost-detected jobs, user-skipped jobs, and stale jobs are excluded from both surfaces automatically.

Event sourcing (append-only event log with state projection) is intellectually elegant but **overkill for Job360's scale and use case**. The feed is not collaborative — there are no multi-writer conflicts, no need for time-travel debugging, and no complex state machines that benefit from event replay. CQRS (separate read models for dashboard and push) adds eventual consistency concerns and maintenance burden without proportional benefit when both surfaces can read the same table.

The recommendation: **SSOT (Single Source of Truth) with a shared service layer**. One `user_feed` table. One Python `FeedService` class called by both FastAPI endpoints and task queue workers. Both surfaces always see identical data because they query the same rows with the same filtering logic.

### The user_feed table: one table to rule both surfaces

```sql
CREATE TABLE user_feed (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL,
    job_id UUID NOT NULL,
    score SMALLINT NOT NULL CHECK (score BETWEEN 0 AND 100),
    bucket VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    notified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, job_id)
);

-- Dashboard query: user's active jobs by bucket and score
CREATE INDEX idx_feed_dashboard
    ON user_feed (user_id, bucket, score DESC)
    WHERE status = 'active';

-- Notification query: unnotified active jobs for digest
CREATE INDEX idx_feed_notify
    ON user_feed (user_id, status, created_at)
    WHERE notified_at IS NULL AND status = 'active';

-- Cascade index: delete stale jobs across all users
CREATE INDEX idx_feed_job ON user_feed (job_id);
```

At 10,000 users × 1,000 jobs each = **10 million rows, ~2GB** of data. PostgreSQL handles this trivially with proper indexing — per-user queries return in under 1ms via the partial indexes. No partitioning needed until 100K+ users.

The notification worker's core query:

```sql
SELECT uf.job_id, uf.score, uf.bucket, j.title, j.company, j.salary_range
FROM user_feed uf
JOIN jobs j ON j.id = uf.job_id
WHERE uf.user_id = :user_id
  AND uf.status = 'active'
  AND uf.notified_at IS NULL
  AND uf.score >= :user_instant_threshold
ORDER BY uf.score DESC
LIMIT 15;
```

After sending the digest, the worker updates: `UPDATE user_feed SET notified_at = NOW() WHERE id IN (:sent_ids)`. This marks those jobs as "notified" so they are excluded from future digests while remaining visible on the dashboard.

### Action-to-notification propagation

When a user marks a job as "not interested" on the dashboard, the status changes to `'skipped'` immediately. Because the notification worker queries `WHERE status = 'active'`, the skipped job is automatically excluded from all future notifications — no separate propagation mechanism needed. This is the SSOT advantage.

| User action | Dashboard effect | Notification effect | Latency |
|-------------|-----------------|---------------------|---------|
| Mark "not interested" | Immediate (optimistic UI) | Excluded from next digest | <1 second (same DB write) |
| Apply to job | Status → 'applied' | Excluded from notifications | <1 second |
| Change notification preferences | Immediate | Before next scheduled digest | <5 minutes |
| Update profile/filters | Loading → re-score → new feed | New results in next digest | <10 minutes |

The key insight from Figma's LiveGraph architecture applies here: **"most traffic is driven by initial reads, not live updates."** Rather than pushing every score change via WebSocket, invalidate the feed and let the next dashboard load fetch fresh data. For notifications, poll for changes on the digest schedule.

### Real-time dashboard updates: polling first, SSE later

**For MVP: polling every 30 seconds.** The dashboard calls `GET /api/feed?since={timestamp}` periodically. At 10,000 concurrent users polling every 30 seconds = ~333 requests/second — FastAPI handles this comfortably. Simple, reliable, zero additional infrastructure.

**For V1.5: Server-Sent Events (SSE).** FastAPI has native SSE support via `EventSourceResponse` with automatic keep-alive pings, cache-control headers, and Pydantic model serialization. SSE provides sub-second server→client updates for new job matches without WebSocket complexity. The dashboard establishes a persistent SSE connection; the server emits `feed_update` events when new jobs are scored for that user.

**WebSockets are not recommended.** Job360's dashboard is read-heavy — users browse and occasionally act on jobs. SSE handles the server→client feed updates; user actions (like, skip, apply) go through normal REST POST endpoints. Bidirectional real-time communication adds complexity (connection management, heartbeats, reconnection logic) without proportional benefit.

### TTL and cleanup policies

The `user_feed` table grows continuously as new jobs are scored. Without cleanup, 10K users × 100 new matches/week = 5.2M new rows/year. Cleanup rules:

- **Jobs leaving all buckets** (older than 21 days): `DELETE FROM user_feed WHERE job_id IN (SELECT id FROM jobs WHERE effective_posted_at < NOW() - INTERVAL '21 days')` — run daily via cron/scheduled task
- **Skipped/stale jobs** older than 30 days: `DELETE FROM user_feed WHERE status IN ('skipped','stale') AND updated_at < NOW() - INTERVAL '30 days'`
- **Applied jobs**: retain indefinitely (user's application history) but move to a separate `applications` table after 90 days
- **Feed events audit log** (if implemented): retain 90 days, then delete per GDPR data minimization principles

---

## 4. Honest verdict: what's achievable, what costs money, and per-user economics

### What works at zero cost

**Apprise for multi-channel sending** — unlimited, MIT-licensed, supports all target channels. **ARQ for task queuing** — async-native, Redis-backed, handles scheduling and retries. **PostgreSQL for everything** — user_feed, notification ledger, credential storage, user preferences. **Fernet encryption for credentials** — stdlib-adjacent (`cryptography` package), zero-cost key management via environment variables. **Meilisearch for pre-filtering** — MIT-licensed, self-hosted, ~500MB RAM for 500K jobs. The entire notification + scoring + parity stack runs on open-source software with no paid service dependencies.

### What requires paid infrastructure

**A server.** There is no free tier that reliably runs a persistent task queue, database, and web server 24/7. Hetzner CX22 at **€4.39/month ($4.80)** is the minimum viable production server — 2 vCPU, 4GB RAM, 40GB SSD, 20TB traffic. This runs the entire stack (FastAPI + ARQ worker + Redis + PostgreSQL + Meilisearch) for up to ~5,000 users.

**Email delivery at scale.** Free Gmail SMTP caps at 100 emails/day. Amazon SES at $0.10/1,000 emails is the cheapest production option. For 1,000 daily digest emails: **$0.10/day = $3/month**.

**SMS is a luxury.** At ~$0.04/message for UK numbers via Twilio, SMS should be offered as a premium-only channel or reserved for 95+ score "dream job" alerts. A daily SMS to 1,000 users costs $1,200/month.

### Per-user infrastructure cost at scale

| Scale | VPS | Redis | PostgreSQL | Email (SES) | Total infra | Per-user/month |
|-------|-----|-------|------------|-------------|-------------|----------------|
| **1,000 users** | $5 (Hetzner CX22) | Included | Included | $3 | **~$8/mo** | **$0.008** |
| **10,000 users** | $15 (Hetzner CX32) | Included | Included | $30 | **~$45/mo** | **$0.0045** |
| **100,000 users** | $60 (3× Hetzner servers) | $0 (self-hosted) | $15 (dedicated server) | $300 | **~$375/mo** | **$0.00375** |

These numbers are for deterministic scoring only. Adding LLM-based scoring or explanation generation increases costs dramatically: **+$203/month at 1K users, +$4,050 at 10K, +$101,250 at 100K** — economically infeasible as a default.

### The minimum viable delivery layer

The fastest path to a working multi-user delivery layer requires five components shipped in order:

1. **User authentication + per-user preferences** — add auth (Supabase Auth free tier, or simple JWT), migrate from single `user_profile.json` to a `users` table with notification preferences
2. **`user_feed` table + FeedService** — create the shared data layer, refactor dashboard API endpoints to read from `user_feed`, refactor scoring to write to `user_feed`  
3. **Notification worker** — ARQ worker that runs on user-defined schedules, queries `user_feed` for unnotified matches, sends via Apprise, updates `notified_at`
4. **Pre-filtering** — add location/skills/experience pre-filter before scoring to prevent quadratic compute explosion
5. **Channel configuration UI** — dashboard page where users add Slack webhooks, Telegram bot connections, set digest frequency and instant-threshold

Components 1–3 are the true minimum. Pre-filtering becomes essential only when the second user arrives. Channel configuration can initially be hardcoded per-user in the database and exposed via UI later.

### What no competitor does (and Job360 should)

Research across 15 job search tools — including LinkedIn, Indeed, Glassdoor, Teal, Huntr, Jobscan, Simplify, RemoteRocketship, career-ops, JobFunnel, and JobSpy — confirms that **no job search tool offers Slack, Telegram, Discord, or webhook-based notifications**. None implement score-threshold notifications. None implement tiered urgency (instant for high-relevance, digest for rest). None offer quiet hours. This is not a crowded feature space — it is genuinely unoccupied territory.

The competitive insight cuts both ways: the absence of these features across the industry could mean they're difficult to execute at scale (unlikely — the infrastructure is straightforward) or that existing players are locked into email/mobile push patterns by legacy architecture and app-store distribution models. Job360's architecture-from-scratch advantage is real, but only if the delivery layer actually ships.

---

## Conclusion

Job360's delivery layer rests on a surprisingly simple technical foundation: **one PostgreSQL table (`user_feed`) serving both dashboard and notifications**, **one open-source library (Apprise) sending across all channels**, and **one task queue (ARQ) orchestrating the pipeline**. The architectural complexity lies not in exotic event-sourcing or CQRS patterns but in disciplined pre-filtering that keeps scoring compute tractable — a 99% pre-filter rate is the difference between 50 seconds and 14 hours of daily CPU time at 1,000 users.

The honest constraint is that LLM scoring in the hot path is economically dead above single-user scale. Job360's deterministic scorer (4-dimension weighted sum at 1ms/pair) is not a limitation — it is an advantage. LinkedIn and Indeed both use cheap rule-based filters before expensive ML ranking. Job360 should do the same: deterministic scoring for matching, LLM enrichment only for top-N explanation generation at controlled cost.

The market gap is real: no job tool pushes matches to Slack or Telegram with score-based urgency tiers. The infrastructure to do so costs under $10/month. The remaining question is execution speed.