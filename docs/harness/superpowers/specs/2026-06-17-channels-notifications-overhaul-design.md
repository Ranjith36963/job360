# Channels & Notifications Overhaul — Design
<!-- doc: PLAN -->

> **PLAN — not a description of today's code.** Written to be built, possibly never built or since changed. Verify against code before trusting. <!-- banner: auto -->

**Date:** 2026-06-17
**Branch:** `worktree-channels-notifications-overhaul`
**Status:** Approved design — ready for implementation plan

---

## 1. Goal (plain words)

Fix everything in channels and notifications so there is **one clean system**:

- **Channels** = the pipes you connect (Slack, Gmail/email, Telegram, Discord, webhook). Unchanged.
- **Notifications** = **one shared rulebook per user** that governs *all* connected pipes at once. No per-channel duplication, no overlap with "channels."

The user must be able to choose **how often** they are notified: `instant`, `daily at a set time`, or `every N hours`. Today only `instant` works; `digest` queues but never sends because no scheduler runs it.

---

## 2. Current state (verified against code)

| Piece | File | State |
|---|---|---|
| Per-user dispatch (worker → dispatcher → Apprise → ledger) | `backend/src/services/channels/dispatcher.py` | ✅ works |
| Channel connect/test/remove + OAuth | `backend/src/api/routes/channels.py` | ✅ works |
| Telegram delivery | `channels.py:742` stores `tgram://{token}/{chat_id}`; dispatcher decrypts + Apprise sends | ✅ wired (delivery confirmed by reading; formatting risk only) |
| Notification rules CRUD | `backend/src/api/routes/notification_rules.py` | ✅ works, but **per-channel** (one row per user+channel) |
| Daily digest sender | `backend/src/workers/tasks.py:464` `send_daily_digest` | 🔴 exists but **never triggered** — not in cron, nothing enqueues it |
| Cron jobs | `backend/src/workers/settings.py:113` | only `nightly_ghost_sweep` is scheduled |
| Legacy global notifications | `backend/src/main.py:834` + `backend/src/services/notifications/{base,email_notify,slack_notify,discord_notify}.py` | ⚠️ dead-weight parallel system using `.env` webhooks; ignores per-user channels + rules |

**Schemas (verified):**

- `notification_rules` (migration 0012): `id, user_id, channel, score_threshold (def 60), notify_mode CHECK IN ('instant','digest'), quiet_hours_start, quiet_hours_end, digest_send_time (def '08:00'), enabled, created_at, updated_at`, `UNIQUE(user_id, channel)`.
- `user_notification_digests` (migration 0013): `id, user_id, channel, job_id, queued_at, sent, sent_at`, index on `(user_id, channel, sent)`.
- Next free migration number: **0020**.

---

## 3. Target design

### 3.1 Notifications = one rulebook per user

Replace "one rule row per (user, channel)" with **one rule row per user**. The rule applies to every enabled channel.

**Rule fields (the user's single rulebook):**

| Field | Meaning | Default |
|---|---|---|
| `notify_mode` | `instant` · `daily` · `every_n_hours` | `instant` |
| `interval_hours` | only used when mode = `every_n_hours`; integer 1–24 | `6` |
| `daily_send_time` | only used when mode = `daily`; `HH:MM` in user's timezone | `08:00` |
| `score_threshold` | only notify jobs scoring ≥ this | `60` |
| `quiet_hours_start` / `quiet_hours_end` | `HH:MM` window to hold sends | null |
| `enabled` | master on/off for ALL notifications | `1` |

Timezone stays on `users.timezone` (IANA), already present.

**Behaviour by mode:**

- `instant` — the moment the pipeline finds a matching job (score ≥ threshold), push it to every enabled channel. If inside quiet hours, queue it for the bundle and send when quiet hours end. (This is today's working path, minus the per-channel rule lookup.)
- `daily` — matching jobs queue into `user_notification_digests`; once a day at `daily_send_time` (user tz) they are bundled into one message per channel and sent.
- `every_n_hours` — matching jobs queue; every `interval_hours` (measured from the user's last successful send) they are bundled and sent.
- Quiet hours apply to **all** modes: a scheduled send that lands inside the window is held until the window ends.

### 3.2 Channels — unchanged

No change to connect flows, test send, OAuth, encryption, or `user_channels`. Channels remain pure pipes. The dispatcher already sends to any channel type uniformly (decrypt → Apprise).

### 3.3 The missing engine — a scheduler "tick"

Add ONE ARQ cron job, `notification_tick`, running every **5 minutes**:

1. Load every user with `enabled=1`.
2. For each, decide "is it time to send?":
   - `daily`: current local time (user tz) is within the 5-min window starting at `daily_send_time`, and no successful daily send has happened today.
   - `every_n_hours`: now − last successful send ≥ `interval_hours`.
   - `instant`: due only if the user has quiet-hours-held jobs in the queue **and** quiet hours have just ended (the tick flushes the backlog that built up during the quiet window).
3. If due **and** not currently inside quiet hours: enqueue `send_bundle(user_id)` which drains `user_notification_digests` for that user, builds one message per enabled channel, dispatches via the existing dispatcher, records the ledger, and marks the queue rows sent.
4. Track "last successful send" so `every_n_hours` spacing and "once per day" are correct. (New column on the rule row: `last_sent_at`.)

`instant` mode normally fires immediately from `score_and_ingest` → `send_notification` in the worker; it only uses the tick to flush jobs that were held because they arrived during quiet hours.

### 3.4 Remove the legacy global system

- Delete the notification loop at `backend/src/main.py:834-839` (`get_configured_channels()` / `channel.send(...)`).
- Remove `backend/src/services/notifications/{base.py, email_notify.py, slack_notify.py, discord_notify.py}` and their `.env`-webhook senders, plus any imports/tests that reference `get_configured_channels` / `get_all_channels` / the `*Channel` classes.
- After removal there is exactly **one** notification path: worker/scheduler → `dispatcher.dispatch()` → Apprise → `notification_ledger`.
- The `--no-email` / `no_notify` CLI flag stays, now meaning "skip the per-user dispatch step."

### 3.5 Verify all 5 channels end-to-end

With a real run (per `/verify-job360`): connect each of Slack, Gmail, Telegram, Discord, webhook; trigger a matching job; confirm a real message arrives; confirm the ledger row says `sent`. Fix the two known Telegram **formatting** risks if the live test surfaces them:

- bot token contains `:` — confirm `tgram://{token}/{chat_id}` parses in Apprise (URL-encode if needed).
- `format_payload` wraps title in `*...*` markdown — Telegram's API rejects malformed markdown with HTTP 400; switch to a safe parse mode or plain text if it fails.

---

## 4. Data changes (migration 0020)

Because `notify_mode` has a `CHECK IN ('instant','digest')` constraint and SQLite can't `ALTER` a CHECK, this is a **table-rebuild** migration:

1. Create `notification_rules_new` with: same columns **minus** `channel` and `digest_send_time`, **plus** `interval_hours INTEGER DEFAULT 6`, `daily_send_time TEXT DEFAULT '08:00'`, `last_sent_at TEXT`, new `notify_mode CHECK IN ('instant','daily','every_n_hours')`, and `UNIQUE(user_id)`.
2. Fold existing rows into one per user: for each user, take the most-recently-updated rule; map old `notify_mode='digest'` → `'daily'` (carry its `digest_send_time` into `daily_send_time`); keep `score_threshold`, quiet hours, `enabled`.
3. Drop old table, rename new → `notification_rules`.
4. `user_notification_digests` keeps its `channel` column (queue is still per-channel for fan-out), but the queue is now driven by the single per-user rule.
5. Reversible `.down.sql` rebuilds the per-channel table (best-effort: copies the single rule back to one row per existing channel).

`backend/src/api/routes/notification_rules.py` and its Pydantic models change from per-channel CRUD to a single-rulebook GET + PUT (upsert one row per user). The dispatcher's `_load_notification_rule(db, user_id, channel_type)` becomes `_load_notification_rule(db, user_id)` (one lookup, no channel arg).

---

## 5. Frontend changes

- `frontend/src/app/settings/notifications/page.tsx` — change from per-channel cards to **one** rulebook form: mode selector (instant / daily / every-N-hours), conditional `interval_hours` input (1–24) or `daily_send_time` input, score-threshold slider, quiet-hours inputs, master enable toggle. Show "applies to all your channels."
- `frontend/src/lib/api.ts` + `types.ts` — replace `getNotificationRules`/`createNotificationRule`/`updateNotificationRule`/`deleteNotificationRule` (per-channel list) with `getNotificationRule()` / `saveNotificationRule(body)` (single rulebook). Update the `NotificationRule*` types.
- `frontend/src/app/settings/channels/page.tsx` and `frontend/src/app/notifications/page.tsx` (ledger history) — unchanged except any type ripples.

---

## 6. Error handling & edge cases

- **Bundle dispatch fails** (bad credentials): mark the ledger row `failed` with the error; do **not** mark the queue rows `sent` so the next tick retries (bounded — see below). This fixes today's "silently marked sent even on failure" bug.
- **Retry bound:** a queued job that has failed dispatch `N` times (e.g. 5) is moved to ledger status `dlq` and dropped from the queue, so a permanently-bad channel can't wedge the queue forever.
- **No channels connected:** tick does nothing for that user.
- **Quiet hours wraparound** (e.g. 23:00–07:00): reuse existing `_is_in_quiet_window` (already DST-aware via `zoneinfo`).
- **Missing rule row:** default = `instant`, threshold 60, no quiet hours (backwards-compatible).
- **Queue growth:** add an age-based cleanup (drop queue rows older than 30 days, matching the jobs purge window) so digest/interval queues can't grow unbounded.

---

## 7. Testing

- **Unit:** rule upsert (single row per user), mode validation, `notification_tick` due/not-due logic for `daily` and `every_n_hours` (table-driven across timezones + quiet-hours states), `send_bundle` success/failure/dlq paths, migration 0020 up+down round-trip (per-channel → single → per-channel).
- **Dispatcher:** unchanged-behaviour tests still pass with the single-rule lookup; quiet-hours hold-and-queue for all three modes.
- **Regression:** with `enabled=0` no sends happen; instant path byte-identical to today minus per-channel lookup.
- **Live verify (`/verify-job360`):** all 5 channels receive a real message; ledger shows `sent`; Telegram formatting confirmed.
- Canonical gate: `cd backend && python -m pytest -q -p no:randomly` green; `python -m ruff check .` clean.

---

## 8. Out of scope (YAGNI)

- Rich formatting (Slack Block Kit / Discord embeds) — keep the existing plain-text `format_payload`; only touch it if Telegram delivery needs it.
- Per-channel *different* rules — explicitly rejected; one shared rulebook is the chosen model.
- Multi-key Fernet rotation — `key_version` column stays unused.
- Changing how often the **job search** runs — separate concern from notification timing.

---

## 9. Affected files (summary)

**Backend:**
- `migrations/0020_notification_rule_single.{up,down}.sql` (new)
- `src/services/channels/dispatcher.py` (single-rule lookup; mode handling)
- `src/workers/tasks.py` (`notification_tick`, `send_bundle`; fix unconditional mark-sent)
- `src/workers/settings.py` (register + cron the tick)
- `src/api/routes/notification_rules.py` (single-rulebook GET/PUT)
- `src/repositories/database.py` (single-rule repo methods; queue cleanup)
- `src/main.py` (remove legacy notification loop)
- `src/services/notifications/{base,email_notify,slack_notify,discord_notify}.py` (delete)
- tests under `backend/tests/` (update + add)

**Frontend:**
- `src/app/settings/notifications/page.tsx`
- `src/lib/api.ts`, `src/lib/types.ts`

**Docs:** `CLAUDE.md` rules #23/#24 (update to single-rule + three modes), `STATUS.md`, `docs/harness/IMPLEMENTATION_LOG.md`.
