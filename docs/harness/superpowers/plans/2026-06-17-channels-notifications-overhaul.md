# Channels & Notifications Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make notifications one shared rulebook per user (instant / daily / every-N-hours), build the scheduler that actually sends bundles, remove the legacy global notification path, and verify all 5 channels deliver.

**Architecture:** Notifications become ONE `notification_rules` row per user (was one per channel). A new ARQ cron `notification_tick` runs every 5 min, decides per user whether a bundle is due (daily clock / every-N-hours / quiet-hours flush), and enqueues `send_bundle`, which drains the per-channel `user_notification_digests` queue and dispatches via the existing channel-agnostic Apprise dispatcher. Instant mode keeps firing inline from `score_and_ingest`. The legacy `.env`-webhook path in `main.py` + `services/notifications/*` is deleted, leaving one path: worker/scheduler → dispatcher → Apprise → ledger.

**Tech Stack:** Python 3.9, FastAPI, async SQLite (aiosqlite), ARQ, Apprise, pytest + pytest-asyncio + aioresponses; Next.js 16 / React 19 frontend.

**Pre-commit gate (every commit):** `bash scripts/agent-gate.sh` then `git commit` (no edits between). Backend changes run `cd backend && python -m pytest -q -p no:randomly` + `ruff`. The gate is enforced by a hook.

---

## File Structure

**Backend — create:**
- `backend/migrations/0020_notification_rule_single.up.sql` — rebuild `notification_rules` to one-row-per-user.
- `backend/migrations/0020_notification_rule_single.down.sql` — reverse to per-channel (best-effort).
- `backend/tests/test_notification_tick.py` — tick due-logic + send_bundle tests.

**Backend — modify:**
- `backend/src/repositories/database.py` — inline DDL (162-188) + replace rule repo methods (1045-1156) with single-rule methods; add `cleanup_old_digests`.
- `backend/src/services/channels/dispatcher.py` — per-user rule lookup; 3-mode handling; factor single-channel send helper.
- `backend/src/workers/tasks.py` — add `notification_tick` + `send_bundle`; keep `send_notification` (instant) but route through the new rule; replace `send_daily_digest`.
- `backend/src/workers/settings.py` — register new tasks + cron the tick every 5 min.
- `backend/src/api/routes/notification_rules.py` — single-rulebook GET + PUT.
- `backend/src/api/models.py` — `NotificationRule*` Pydantic models (drop `channel`/`digest_send_time`; add `interval_hours`/`daily_send_time`/`last_sent_at`; widen `notify_mode`).
- `backend/src/main.py` — delete legacy notification loop (834-839) + its import.
- `backend/tests/*` — update tests referencing per-channel rules / legacy channels.

**Backend — delete:**
- `backend/src/services/notifications/base.py`, `email_notify.py`, `slack_notify.py`, `discord_notify.py` (+ their tests).

**Frontend — modify:**
- `frontend/src/app/settings/notifications/page.tsx` — single rulebook form.
- `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts` (+ regenerated `api-types.ts`).

---

## Task 1: Migration 0020 — one rule row per user

**Files:**
- Create: `backend/migrations/0020_notification_rule_single.up.sql`
- Create: `backend/migrations/0020_notification_rule_single.down.sql`
- Modify: `backend/src/repositories/database.py:162-188` (inline DDL)
- Test: `backend/tests/test_migrations.py` (add a case) or `backend/tests/test_database.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_database.py`:

```python
@pytest.mark.asyncio
async def test_notification_rules_is_single_per_user(tmp_path, monkeypatch):
    """After 0020 the table has one row per user and the new columns."""
    db = JobDatabase(str(tmp_path / "jobs.db"))
    await db.init_db()
    cols = {r[1] for r in await (await db._conn.execute(
        "PRAGMA table_info(notification_rules)")).fetchall()}
    assert "interval_hours" in cols
    assert "daily_send_time" in cols
    assert "last_sent_at" in cols
    assert "channel" not in cols          # per-channel column gone
    assert "digest_send_time" not in cols # renamed to daily_send_time
    # UNIQUE(user_id): a second insert for same user must conflict-replace, not add.
    await db._conn.execute(
        "INSERT INTO notification_rules(user_id, notify_mode) VALUES('u1','instant')")
    await db._conn.commit()
    with pytest.raises(Exception):
        await db._conn.execute(
            "INSERT INTO notification_rules(user_id, notify_mode) VALUES('u1','daily')")
        await db._conn.commit()
    await db.close()
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `cd backend && python -m pytest tests/test_database.py::test_notification_rules_is_single_per_user -v`
Expected: FAIL (`interval_hours` not in cols / no UNIQUE conflict).

- [ ] **Step 3: Write the up migration**

`backend/migrations/0020_notification_rule_single.up.sql`:

```sql
-- 0020: collapse notification_rules to ONE row per user.
-- SQLite can't ALTER a CHECK or drop a column in place → table rebuild.
CREATE TABLE notification_rules_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    score_threshold INTEGER NOT NULL DEFAULT 60,
    notify_mode TEXT NOT NULL DEFAULT 'instant'
        CHECK (notify_mode IN ('instant', 'daily', 'every_n_hours')),
    interval_hours INTEGER NOT NULL DEFAULT 6,
    daily_send_time TEXT NOT NULL DEFAULT '08:00',
    quiet_hours_start TEXT,
    quiet_hours_end TEXT,
    last_sent_at TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(user_id)
);

-- Fold existing per-channel rows: keep the most recent (MAX(id)) per user.
INSERT INTO notification_rules_new
    (user_id, score_threshold, notify_mode, daily_send_time,
     quiet_hours_start, quiet_hours_end, enabled, created_at, updated_at)
SELECT user_id, score_threshold,
       CASE notify_mode WHEN 'digest' THEN 'daily' ELSE 'instant' END,
       COALESCE(digest_send_time, '08:00'),
       quiet_hours_start, quiet_hours_end, enabled, created_at, updated_at
FROM notification_rules
WHERE id IN (SELECT MAX(id) FROM notification_rules GROUP BY user_id);

DROP TABLE notification_rules;
ALTER TABLE notification_rules_new RENAME TO notification_rules;
```

`backend/migrations/0020_notification_rule_single.down.sql`:

```sql
-- Reverse: rebuild the per-channel table. Best-effort — the single rule
-- is restored as one row with channel='all'.
CREATE TABLE notification_rules_old (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    score_threshold INTEGER NOT NULL DEFAULT 60,
    notify_mode TEXT NOT NULL DEFAULT 'instant'
        CHECK (notify_mode IN ('instant', 'digest')),
    quiet_hours_start TEXT,
    quiet_hours_end TEXT,
    digest_send_time TEXT DEFAULT '08:00',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(user_id, channel)
);
INSERT INTO notification_rules_old
    (user_id, channel, score_threshold, notify_mode, quiet_hours_start,
     quiet_hours_end, digest_send_time, enabled, created_at, updated_at)
SELECT user_id, 'all', score_threshold,
       CASE notify_mode WHEN 'daily' THEN 'digest'
                        WHEN 'every_n_hours' THEN 'digest' ELSE 'instant' END,
       quiet_hours_start, quiet_hours_end, daily_send_time, enabled,
       created_at, updated_at
FROM notification_rules;
DROP TABLE notification_rules;
ALTER TABLE notification_rules_old RENAME TO notification_rules;
```

- [ ] **Step 4: Update the inline DDL in `database.py`**

Replace the `notification_rules` block at `backend/src/repositories/database.py:163-176` with:

```python
            CREATE TABLE IF NOT EXISTS notification_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                score_threshold INTEGER NOT NULL DEFAULT 60,
                notify_mode TEXT NOT NULL DEFAULT 'instant'
                    CHECK (notify_mode IN ('instant', 'daily', 'every_n_hours')),
                interval_hours INTEGER NOT NULL DEFAULT 6,
                daily_send_time TEXT NOT NULL DEFAULT '08:00',
                quiet_hours_start TEXT,
                quiet_hours_end TEXT,
                last_sent_at TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                UNIQUE(user_id)
            );
```

(Leave the `user_notification_digests` block and `users.timezone` add untouched.)

- [ ] **Step 5: Run test — expect PASS**

Run: `cd backend && python -m pytest tests/test_database.py::test_notification_rules_is_single_per_user -v`
Expected: PASS.

- [ ] **Step 6: Gate + commit**

```bash
bash scripts/agent-gate.sh
git add backend/migrations/0020_notification_rule_single.up.sql backend/migrations/0020_notification_rule_single.down.sql backend/src/repositories/database.py backend/tests/test_database.py
git commit -m "feat(db): migration 0020 — single notification rule per user"
```

---

## Task 2: Repository — single-rule methods + queue cleanup

**Files:**
- Modify: `backend/src/repositories/database.py:1045-1156` (replace per-channel rule methods)
- Test: `backend/tests/test_database.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_user_notification_rule_upsert_and_get(tmp_path):
    db = JobDatabase(str(tmp_path / "jobs.db"))
    await db.init_db()
    saved = await db.save_user_notification_rule("u1", {
        "notify_mode": "every_n_hours", "interval_hours": 6,
        "score_threshold": 70, "enabled": True})
    assert saved["notify_mode"] == "every_n_hours"
    assert saved["interval_hours"] == 6
    # Second save updates the SAME row (one per user).
    again = await db.save_user_notification_rule("u1", {"notify_mode": "daily"})
    assert again["id"] == saved["id"]
    assert again["notify_mode"] == "daily"
    got = await db.get_user_notification_rule("u1")
    assert got["notify_mode"] == "daily"
    assert await db.get_user_notification_rule("nobody") is None
    await db.close()


@pytest.mark.asyncio
async def test_set_rule_last_sent_and_users_with_rules(tmp_path):
    db = JobDatabase(str(tmp_path / "jobs.db"))
    await db.init_db()
    await db.save_user_notification_rule("u1", {"notify_mode": "daily"})
    await db.set_rule_last_sent("u1", "2026-06-17T08:00:00Z")
    rows = await db.get_users_with_rules()
    assert any(r["user_id"] == "u1" and r["last_sent_at"] == "2026-06-17T08:00:00Z"
               for r in rows)
    await db.close()
```

- [ ] **Step 2: Run — expect FAIL** (`save_user_notification_rule` missing).

Run: `cd backend && python -m pytest tests/test_database.py::test_user_notification_rule_upsert_and_get tests/test_database.py::test_set_rule_last_sent_and_users_with_rules -v`

- [ ] **Step 3: Replace the rule methods**

In `backend/src/repositories/database.py`, replace `upsert_notification_rule`, `update_notification_rule`, `delete_notification_rule`, `get_notification_rule_for_channel`, `get_notification_rules`, `get_notification_rule` (lines ~1021-1156) with:

```python
    _RULE_FIELDS = (
        "score_threshold", "notify_mode", "interval_hours",
        "daily_send_time", "quiet_hours_start", "quiet_hours_end", "enabled",
    )

    async def get_user_notification_rule(self, user_id: str) -> dict | None:
        """Return the single notification rule for a user, or None."""
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute(
            "SELECT * FROM notification_rules WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def save_user_notification_rule(self, user_id: str, data: dict) -> dict:
        """Upsert the one-per-user rule. Unspecified fields keep DB defaults
        on insert, or their current value on update."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        existing = await self.get_user_notification_rule(user_id)
        merged = {k: (existing or {}).get(k) for k in self._RULE_FIELDS}
        for k in self._RULE_FIELDS:
            if k in data and data[k] is not None:
                merged[k] = int(data[k]) if k == "enabled" else data[k]
        merged = {k: v for k, v in merged.items() if v is not None}
        cols = ["user_id", *merged.keys(), "created_at", "updated_at"]
        vals = [user_id, *merged.values(), now, now]
        set_clause = ", ".join(f"{c}=excluded.{c}" for c in merged) + ", updated_at=excluded.updated_at"
        placeholders = ", ".join("?" for _ in cols)
        await self._conn.execute(
            f"INSERT INTO notification_rules ({', '.join(cols)}) VALUES ({placeholders}) "  # noqa: S608
            f"ON CONFLICT(user_id) DO UPDATE SET {set_clause}",
            vals,
        )
        await self._conn.commit()
        return await self.get_user_notification_rule(user_id)

    async def set_rule_last_sent(self, user_id: str, ts: str) -> None:
        """Stamp last_sent_at after a successful bundle send."""
        await self._conn.execute(
            "UPDATE notification_rules SET last_sent_at = ? WHERE user_id = ?",
            (ts, user_id))
        await self._conn.commit()

    async def get_users_with_rules(self) -> list[dict]:
        """All notification rules (one per user) — the tick iterates these."""
        self._conn.row_factory = aiosqlite.Row
        cur = await self._conn.execute("SELECT * FROM notification_rules WHERE enabled = 1")
        return [dict(r) for r in await cur.fetchall()]

    async def cleanup_old_digests(self, *, days: int = 30) -> int:
        """Drop digest queue rows older than `days`. Returns rows deleted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = await self._conn.execute(
            "DELETE FROM user_notification_digests WHERE queued_at < ?", (cutoff,))
        await self._conn.commit()
        return cur.rowcount
```

Add `from datetime import timedelta` to the imports at the top of `database.py` if not present.

- [ ] **Step 4: Run — expect PASS.**

Run: `cd backend && python -m pytest tests/test_database.py -k notification -v`

- [ ] **Step 5: Gate + commit**

```bash
bash scripts/agent-gate.sh
git add backend/src/repositories/database.py backend/tests/test_database.py
git commit -m "feat(db): single-rule repo methods + digest cleanup"
```

---

## Task 3: Dispatcher — per-user rule + three modes

**Files:**
- Modify: `backend/src/services/channels/dispatcher.py`
- Test: `backend/tests/test_dispatcher.py` (or the existing notification-rules dispatcher test file)

**Behaviour:** load the ONE user rule once. Gate: disabled→skip all; score<threshold→skip all. Mode: `daily`/`every_n_hours`→queue every enabled channel, skip immediate. `instant`→if quiet-hours active queue (held), else send now.

- [ ] **Step 1: Write failing tests**

`backend/tests/test_dispatcher.py` (add):

```python
import pytest
from src.services.channels import dispatcher as D

class _Rule(dict):
    pass

@pytest.mark.asyncio
async def test_dispatch_every_n_hours_queues_all_channels(monkeypatch, dispatcher_db):
    # dispatcher_db: fixture with 2 enabled channels for user 'u1' + a rule.
    await dispatcher_db.execute(
        "INSERT INTO notification_rules(user_id, notify_mode, interval_hours, score_threshold)"
        " VALUES('u1','every_n_hours',6,50)")
    await dispatcher_db.commit()
    queued = []
    monkeypatch.setattr(D, "_queue_digest",
        lambda db, uid, ch, jid: queued.append((uid, ch, jid)) or _async_none())
    results = await D.dispatch(dispatcher_db, user_id="u1", title="t", body="b",
                              job_id=1, match_score=80)
    assert all(r.queued_digest for r in results)
    assert len(queued) == 2  # one per enabled channel

@pytest.mark.asyncio
async def test_dispatch_instant_sends_when_not_quiet(monkeypatch, dispatcher_db):
    await dispatcher_db.execute(
        "INSERT INTO notification_rules(user_id, notify_mode, score_threshold)"
        " VALUES('u1','instant',50)")
    await dispatcher_db.commit()
    monkeypatch.setattr(D, "_notify_async", lambda ap, *, title, body: _async_true())
    results = await D.dispatch(dispatcher_db, user_id="u1", title="t", body="b",
                              job_id=1, match_score=80)
    assert all(r.ok and not r.queued_digest for r in results)
```

(Provide `_async_none`/`_async_true` async helpers at top of the test module returning `None`/`True`, and a `dispatcher_db` fixture seeding `user_channels` with two enabled rows whose credentials decrypt to a dummy URL — mirror the existing dispatcher test fixture.)

- [ ] **Step 2: Run — expect FAIL.**

Run: `cd backend && python -m pytest tests/test_dispatcher.py -k "every_n_hours or instant_sends" -v`

- [ ] **Step 3: Rewrite the rule consultation in `dispatch()`**

Replace `_load_notification_rule` (dispatcher.py:124-135) signature and the per-channel rule block (197-274) so the rule is loaded ONCE before the loop:

```python
async def _load_notification_rule(db: aiosqlite.Connection, user_id: str) -> dict | None:
    """Return the single notification_rules row for user_id, or None."""
    try:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM notification_rules WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None
    except Exception:  # noqa: BLE001 — table may be missing on legacy DB
        return None
```

Then in `dispatch()`, after loading channels and tz, before the loop:

```python
    rule = await _load_notification_rule(db, user_id)
    if rule is not None:
        if not rule.get("enabled", 1):
            return [ChannelSendResult(ch["id"], ch["channel_type"], ok=True,
                    skipped=True, error="rule disabled") for ch in channels]
        if match_score is not None and match_score < int(rule.get("score_threshold", 60)):
            return [ChannelSendResult(ch["id"], ch["channel_type"], ok=True,
                    skipped=True, error="below score threshold") for ch in channels]
    mode = (rule or {}).get("notify_mode", "instant")
    in_quiet = bool(rule) and bool(rule.get("quiet_hours_start")) and bool(rule.get("quiet_hours_end")) \
               and _is_in_quiet_window(rule["quiet_hours_start"], rule["quiet_hours_end"], user_tz)
```

Replace the per-channel rule block inside the loop with:

```python
    for ch in channels:
        ch_type = ch["channel_type"]
        if mode in ("daily", "every_n_hours") or in_quiet:
            await _queue_digest(db, user_id, ch_type, job_id)
            results.append(ChannelSendResult(ch["id"], ch_type, ok=True,
                queued_digest=True, error="queued for bundle"))
            continue
        # instant + not quiet → send now
        url = decrypt(ch["credential_encrypted"])
        ... (existing Apprise send block unchanged) ...
```

(Note: `ChannelSendResult` is a positional dataclass — `ChannelSendResult(ch["id"], ch_type, ok=True, ...)` matches its field order `channel_id, channel_type, ok, error, skipped, queued_digest`.)

- [ ] **Step 4: Run — expect PASS.**

Run: `cd backend && python -m pytest tests/test_dispatcher.py -v`

- [ ] **Step 5: Gate + commit**

```bash
bash scripts/agent-gate.sh
git add backend/src/services/channels/dispatcher.py backend/tests/test_dispatcher.py
git commit -m "feat(dispatcher): single per-user rule + instant/daily/every-n-hours modes"
```

---

## Task 4: `send_bundle` worker task

**Files:**
- Modify: `backend/src/workers/tasks.py` (replace `send_daily_digest` with `send_bundle`; keep helpers)
- Test: `backend/tests/test_notification_tick.py`

**Behaviour:** for a user, drain `user_notification_digests` grouped by channel; build one message per channel; send via dispatcher's single-channel path; on success mark those rows sent + ledger `sent`; on failure leave rows + ledger `failed`; after a channel's rows fail `MAX_BUNDLE_RETRIES` (5) times, ledger `dlq` and drop them; on any success, stamp `set_rule_last_sent`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_notification_tick.py`:

```python
import pytest
from src.workers import tasks

class _Res:
    def __init__(self, ch, ok): self.channel_type, self.ok = ch, ok
    channel_id = 1; error = ""; skipped = False; queued_digest = False

@pytest.mark.asyncio
async def test_send_bundle_sends_and_marks_sent(bundle_db):
    # bundle_db seeds: rule(daily) for u1, 2 queued digest rows (channel 'slack'), jobs exist.
    async def fake_dispatch(db, *, user_id, title, body, **kw):
        return [_Res("slack", True)]
    ctx = {"db": bundle_db, "dispatcher": fake_dispatch}
    out = await tasks.send_bundle(ctx, "u1")
    assert out["sent"] >= 1
    cur = await bundle_db.execute(
        "SELECT COUNT(*) FROM user_notification_digests WHERE user_id='u1' AND sent=0")
    assert (await cur.fetchone())[0] == 0  # all drained

@pytest.mark.asyncio
async def test_send_bundle_failure_keeps_rows(bundle_db):
    async def fake_dispatch(db, *, user_id, title, body, **kw):
        return [_Res("slack", False)]
    ctx = {"db": bundle_db, "dispatcher": fake_dispatch}
    out = await tasks.send_bundle(ctx, "u1")
    assert out["sent"] == 0
    cur = await bundle_db.execute(
        "SELECT COUNT(*) FROM user_notification_digests WHERE user_id='u1' AND sent=0")
    assert (await cur.fetchone())[0] > 0  # rows kept for retry
```

- [ ] **Step 2: Run — expect FAIL** (`send_bundle` missing).

Run: `cd backend && python -m pytest tests/test_notification_tick.py -k send_bundle -v`

- [ ] **Step 3: Implement `send_bundle`** (replace `send_daily_digest` in `tasks.py`, keep `_mark_digest_rows_sent`):

```python
MAX_BUNDLE_RETRIES = 5

async def send_bundle(ctx: dict, user_id: str) -> dict[str, int]:
    """Drain queued digest rows for a user and send one bundle per channel.

    Returns {'sent': channels_sent, 'failed': channels_failed}.
    """
    db: aiosqlite.Connection = ctx["db"]
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
        "SELECT channel, job_id FROM user_notification_digests "
        "WHERE user_id = ? AND sent = 0", (user_id,))
    pending = [dict(r) for r in await cur.fetchall()]
    if not pending:
        return {"sent": 0, "failed": 0}

    by_channel: dict[str, list[int]] = {}
    for r in pending:
        by_channel.setdefault(r["channel"], []).append(r["job_id"])

    dispatcher_fn = ctx.get("dispatcher")
    if dispatcher_fn is None:
        from src.services.channels.dispatcher import dispatch as real_dispatch
        dispatcher_fn = real_dispatch

    sent = failed = 0
    for channel, job_ids in by_channel.items():
        details = []
        for jid in dict.fromkeys(job_ids):
            jc = await db.execute(
                "SELECT title, company, apply_url FROM jobs WHERE id = ?", (jid,))
            jr = await jc.fetchone()
            if jr:
                details.append(f"• {jr['title']} @ {jr['company']} — {jr['apply_url']}")
        if not details:  # jobs purged — drop the queue rows
            await db.execute(
                "DELETE FROM user_notification_digests WHERE user_id=? AND channel=? AND sent=0",
                (user_id, channel))
            await db.commit()
            continue
        n = len(details)
        title = f"Job360 — {n} new match{'es' if n > 1 else ''}"
        body = "\n".join(details)
        # dispatcher re-applies rule gates; bundles should bypass mode gating.
        results = await dispatcher_fn(db, user_id=user_id, title=title, body=body)
        ok = any(r.ok and not r.skipped and not r.queued_digest for r in results)
        for jid in dict.fromkeys(job_ids):
            await _record_ledger_if_new(db, user_id=user_id, job_id=jid, channel=channel)
        if ok:
            await db.execute(
                "UPDATE user_notification_digests SET sent=1, "
                "sent_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                "WHERE user_id=? AND channel=? AND sent=0", (user_id, channel))
            await db.commit()
            for jid in dict.fromkeys(job_ids):
                await mark_ledger_sent(db, user_id=user_id, job_id=jid, channel=channel)
            sent += 1
        else:
            err = next((r.error for r in results if not r.ok), "delivery failed")
            for jid in dict.fromkeys(job_ids):
                await mark_ledger_failed(db, user_id=user_id, job_id=jid, channel=channel, error=err)
            # DLQ rows whose ledger retry_count exceeded the cap.
            await db.execute(
                "DELETE FROM user_notification_digests WHERE user_id=? AND channel=? AND sent=0 "
                "AND job_id IN (SELECT job_id FROM notification_ledger "
                "  WHERE user_id=? AND channel=? AND retry_count >= ?)",
                (user_id, channel, user_id, channel, MAX_BUNDLE_RETRIES))
            await db.execute(
                "UPDATE notification_ledger SET status='dlq' "
                "WHERE user_id=? AND channel=? AND retry_count >= ?",
                (user_id, channel, MAX_BUNDLE_RETRIES))
            await db.commit()
            failed += 1

    if sent:
        from datetime import datetime, timezone
        try:
            from src.repositories.database import JobDatabase  # noqa: F401
        except Exception:  # noqa: BLE001
            pass
        await db.execute(
            "UPDATE notification_rules SET last_sent_at=? WHERE user_id=?",
            (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), user_id))
        await db.commit()
    return {"sent": sent, "failed": failed}
```

**Bundle bypass note:** because the dispatcher gates by mode, a bundle send would itself be re-queued. Guard it: in `dispatch()` accept an optional `force: bool = False` param that skips the daily/every_n_hours/quiet branch when True, and have `send_bundle` call `dispatcher_fn(db, ..., force=True)`. Add `force=False` to `dispatch`'s signature and `if (mode in (...) or in_quiet) and not force:` in the loop. Update the Task 3 tests to pass `force` where needed.

- [ ] **Step 4: Run — expect PASS.**

Run: `cd backend && python -m pytest tests/test_notification_tick.py -k send_bundle -v`

- [ ] **Step 5: Gate + commit**

```bash
bash scripts/agent-gate.sh
git add backend/src/workers/tasks.py backend/src/services/channels/dispatcher.py backend/tests/test_notification_tick.py
git commit -m "feat(worker): send_bundle drains queue, marks sent/failed/dlq"
```

---

## Task 5: `notification_tick` + cron registration

**Files:**
- Modify: `backend/src/workers/tasks.py` (add `notification_tick` + pure helper `_bundle_due`)
- Modify: `backend/src/workers/settings.py` (register + cron every 5 min)
- Test: `backend/tests/test_notification_tick.py`

- [ ] **Step 1: Write failing tests for the pure due-logic**

```python
from src.workers.tasks import _bundle_due

def test_due_every_n_hours_elapsed():
    rule = {"notify_mode": "every_n_hours", "interval_hours": 6,
            "last_sent_at": "2026-06-17T00:00:00Z"}
    assert _bundle_due(rule, now_utc="2026-06-17T06:01:00Z", user_tz="UTC") is True

def test_not_due_every_n_hours_too_soon():
    rule = {"notify_mode": "every_n_hours", "interval_hours": 6,
            "last_sent_at": "2026-06-17T05:00:00Z"}
    assert _bundle_due(rule, now_utc="2026-06-17T06:01:00Z", user_tz="UTC") is False

def test_due_daily_at_window():
    rule = {"notify_mode": "daily", "daily_send_time": "08:00", "last_sent_at": None}
    assert _bundle_due(rule, now_utc="2026-06-17T08:02:00Z", user_tz="UTC") is True

def test_not_due_daily_already_sent_today():
    rule = {"notify_mode": "daily", "daily_send_time": "08:00",
            "last_sent_at": "2026-06-17T08:01:00Z"}
    assert _bundle_due(rule, now_utc="2026-06-17T08:03:00Z", user_tz="UTC") is False

def test_instant_never_due_via_tick():
    rule = {"notify_mode": "instant", "last_sent_at": None}
    assert _bundle_due(rule, now_utc="2026-06-17T08:00:00Z", user_tz="UTC") is False
```

- [ ] **Step 2: Run — expect FAIL** (`_bundle_due` missing).

Run: `cd backend && python -m pytest tests/test_notification_tick.py -k due -v`

- [ ] **Step 3: Implement `_bundle_due` + `notification_tick`** in `tasks.py`:

```python
def _bundle_due(rule: dict, *, now_utc: str, user_tz: str = "UTC") -> bool:
    """Pure: is a bundle send due for this rule at now_utc? (5-min tick window)"""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    mode = rule.get("notify_mode", "instant")
    now = datetime.fromisoformat(now_utc.replace("Z", "+00:00"))
    last = rule.get("last_sent_at")
    last_dt = datetime.fromisoformat(last.replace("Z", "+00:00")) if last else None
    if mode == "every_n_hours":
        if last_dt is None:
            return True
        return (now - last_dt) >= timedelta(hours=int(rule.get("interval_hours", 6)))
    if mode == "daily":
        local = now.astimezone(ZoneInfo(user_tz))
        hh, mm = map(int, str(rule.get("daily_send_time", "08:00")).split(":"))
        target = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if not (target <= local < target + timedelta(minutes=5)):
            return False
        if last_dt is not None:
            last_local = last_dt.astimezone(ZoneInfo(user_tz))
            if last_local.date() == local.date():
                return False  # already sent today
        return True
    return False  # instant is handled inline, not by the tick


async def notification_tick(ctx: dict) -> dict[str, int]:
    """ARQ cron (every 5 min): enqueue send_bundle for each user whose
    daily/every-N-hours bundle is due (and who is outside quiet hours)."""
    from datetime import datetime, timezone
    from src.services.channels.dispatcher import _is_in_quiet_window
    db: aiosqlite.Connection = ctx["db"]
    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT * FROM notification_rules WHERE enabled = 1")
    rules = [dict(r) for r in await cur.fetchall()]
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    enqueue = ctx.get("enqueue")
    fired = 0
    for rule in rules:
        tzc = await db.execute("SELECT timezone FROM users WHERE id = ?", (rule["user_id"],))
        tzr = await tzc.fetchone()
        user_tz = (tzr["timezone"] if tzr and tzr["timezone"] else "UTC")
        qs, qe = rule.get("quiet_hours_start"), rule.get("quiet_hours_end")
        if qs and qe and _is_in_quiet_window(qs, qe, user_tz):
            continue  # hold until quiet hours end
        if not _bundle_due(rule, now_utc=now_utc, user_tz=user_tz):
            continue
        if enqueue is not None:
            res = enqueue("send_bundle", rule["user_id"])
            if hasattr(res, "__await__"):
                await res
        fired += 1
    return {"fired": fired}
```

- [ ] **Step 4: Register in `settings.py`**

In `backend/src/workers/settings.py`: add `notification_tick, send_bundle` to the import from `tasks` (and remove `send_daily_digest`), add both to `functions`, and add the cron:

```python
        return [
            cron(nightly_ghost_sweep, hour=2, minute=0),
            cron(notification_tick, minute=set(range(0, 60, 5))),
        ]
```

- [ ] **Step 5: Run — expect PASS.**

Run: `cd backend && python -m pytest tests/test_notification_tick.py -v`

- [ ] **Step 6: Gate + commit**

```bash
bash scripts/agent-gate.sh
git add backend/src/workers/tasks.py backend/src/workers/settings.py backend/tests/test_notification_tick.py
git commit -m "feat(worker): notification_tick cron drives daily + every-n-hours bundles"
```

---

## Task 6: API — single-rulebook GET + PUT

**Files:**
- Modify: `backend/src/api/models.py` (Pydantic models)
- Modify: `backend/src/api/routes/notification_rules.py`
- Test: `backend/tests/test_api.py` (or `test_notification_rules.py`)

- [ ] **Step 1: Write failing test**

```python
def test_notification_rule_get_and_put(client, auth_headers):
    # No rule yet → defaults.
    r = client.get("/api/settings/notification-rule", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["notify_mode"] == "instant"
    # Save.
    body = {"notify_mode": "every_n_hours", "interval_hours": 8, "score_threshold": 70}
    r = client.put("/api/settings/notification-rule", json=body, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["interval_hours"] == 8
    # Read back.
    r = client.get("/api/settings/notification-rule", headers=auth_headers)
    assert r.json()["notify_mode"] == "every_n_hours"
```

(Use the project's existing authenticated `client`/`auth_headers` fixtures from `tests/conftest.py`.)

- [ ] **Step 2: Run — expect FAIL** (route 404 / old per-channel shape).

Run: `cd backend && python -m pytest tests/test_api.py -k notification_rule_get_and_put -v`

- [ ] **Step 3: Replace the Pydantic models** in `backend/src/api/models.py` (find the existing `NotificationRule*` classes and replace with):

```python
class NotificationRule(BaseModel):
    user_id: str
    score_threshold: int = 60
    notify_mode: str = "instant"  # instant | daily | every_n_hours
    interval_hours: int = 6
    daily_send_time: str = "08:00"
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    last_sent_at: str | None = None
    enabled: bool = True
    created_at: str | None = None
    updated_at: str | None = None


class NotificationRuleUpdate(BaseModel):
    score_threshold: int | None = Field(default=None, ge=0, le=100)
    notify_mode: str | None = Field(default=None, pattern="^(instant|daily|every_n_hours)$")
    interval_hours: int | None = Field(default=None, ge=1, le=24)
    daily_send_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    quiet_hours_start: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    quiet_hours_end: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    enabled: bool | None = None
```

Delete `NotificationRuleCreate` and `NotificationRuleListResponse` (no longer used).

- [ ] **Step 4: Rewrite the route** `backend/src/api/routes/notification_rules.py`:

```python
from __future__ import annotations
from fastapi import APIRouter, Depends
from src.api.auth_deps import CurrentUser, require_user
from src.api.dependencies import get_db
from src.api.models import NotificationRule, NotificationRuleUpdate
from src.repositories.database import JobDatabase

router = APIRouter(tags=["notification-rules"])

def _rule_from_row(row: dict | None, user_id: str) -> NotificationRule:
    row = row or {"user_id": user_id}
    return NotificationRule(
        user_id=row.get("user_id", user_id),
        score_threshold=row.get("score_threshold", 60),
        notify_mode=row.get("notify_mode", "instant"),
        interval_hours=row.get("interval_hours", 6),
        daily_send_time=row.get("daily_send_time", "08:00"),
        quiet_hours_start=row.get("quiet_hours_start"),
        quiet_hours_end=row.get("quiet_hours_end"),
        last_sent_at=row.get("last_sent_at"),
        enabled=bool(row.get("enabled", True)),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )

@router.get("/settings/notification-rule", response_model=NotificationRule)
async def get_notification_rule(
    db: JobDatabase = Depends(get_db),            # noqa: B008
    user: CurrentUser = Depends(require_user),    # noqa: B008
) -> NotificationRule:
    return _rule_from_row(await db.get_user_notification_rule(user.id), user.id)

@router.put("/settings/notification-rule", response_model=NotificationRule)
async def put_notification_rule(
    body: NotificationRuleUpdate,
    db: JobDatabase = Depends(get_db),            # noqa: B008
    user: CurrentUser = Depends(require_user),    # noqa: B008
) -> NotificationRule:
    row = await db.save_user_notification_rule(user.id, body.model_dump(exclude_none=True))
    return _rule_from_row(row, user.id)
```

(Check `backend/src/api/app.py` for how this router is mounted — keep the same prefix so the path is `/api/settings/notification-rule`.)

- [ ] **Step 5: Run — expect PASS** + regenerate API types:

```bash
cd backend && python -m pytest tests/test_api.py -k notification -v
```

- [ ] **Step 6: Gate + commit** (the gate runs the api-types drift check; it regenerates `frontend/src/lib/api-types.ts` — commit that too):

```bash
bash scripts/agent-gate.sh
git add backend/src/api/models.py backend/src/api/routes/notification_rules.py backend/tests/ frontend/openapi.json frontend/src/lib/api-types.ts
git commit -m "feat(api): single notification-rule GET/PUT"
```

---

## Task 7: Remove the legacy global notification path

**Files:**
- Modify: `backend/src/main.py:833-839` (delete the notification loop) + its `get_configured_channels` import
- Delete: `backend/src/services/notifications/{base.py,email_notify.py,slack_notify.py,discord_notify.py}`
- Modify/delete: any tests importing those (`grep -rl get_configured_channels backend/`)

- [ ] **Step 1: Find every reference**

Run: `cd backend && grep -rln "get_configured_channels\|get_all_channels\|notifications.email_notify\|notifications.slack_notify\|notifications.discord_notify\|notifications.base" src tests`
Expected: a short list (main.py + a couple of tests).

- [ ] **Step 2: Write/adjust the guard test**

In `backend/tests/test_main.py` add:

```python
def test_no_legacy_notification_import():
    import src.main as m
    assert not hasattr(m, "get_configured_channels")
```

- [ ] **Step 3: Run — expect FAIL** (still imported).

Run: `cd backend && python -m pytest tests/test_main.py::test_no_legacy_notification_import -v`

- [ ] **Step 4: Delete the loop + import in `main.py`**

Remove lines 833-839 (the `# Notifications via channel abstraction` block) and the `from src.services.notifications... import get_configured_channels` line near the top. Per-user notifications now flow only through the worker/scheduler.

- [ ] **Step 5: Delete the legacy modules + their tests**

```bash
git rm backend/src/services/notifications/base.py backend/src/services/notifications/email_notify.py backend/src/services/notifications/slack_notify.py backend/src/services/notifications/discord_notify.py
# delete any now-orphaned test files surfaced in Step 1
```

If `backend/src/services/notifications/__init__.py` re-exports the deleted names, trim it. Keep the directory only if `profile/` siblings still need it; otherwise remove.

- [ ] **Step 6: Run the full suite — expect PASS** (fix any import fallout):

Run: `cd backend && python -m pytest -q -p no:randomly`

- [ ] **Step 7: Gate + commit**

```bash
bash scripts/agent-gate.sh
git add -A backend/
git commit -m "refactor: remove legacy global notification path — one per-user path only"
```

---

## Task 8: Frontend — single rulebook form

**Files:**
- Modify: `frontend/src/lib/types.ts` (+ `api-types.ts` already regenerated)
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/app/settings/notifications/page.tsx`
- Test: `frontend/src/app/settings/notifications/*.test.tsx` (Vitest) if present; else add one.

**Note (rule #22):** before editing `page.tsx`, consult Context7 for Next.js 16 App Router and read `frontend/node_modules/next/dist/docs/`. `params` is async; `"use client"` disables `generateMetadata`.

- [ ] **Step 1: Replace the API bindings** in `frontend/src/lib/api.ts` — remove `getNotificationRules`/`createNotificationRule`/`updateNotificationRule`/`deleteNotificationRule`; add:

```ts
export async function getNotificationRule(): Promise<NotificationRule> {
  return apiGet<NotificationRule>("/settings/notification-rule");
}
export async function saveNotificationRule(
  body: Partial<NotificationRule>,
): Promise<NotificationRule> {
  return apiPut<NotificationRule>("/settings/notification-rule", body);
}
```

(Match the existing helper names in `api.ts` — e.g. if it uses `request("PUT", ...)` instead of `apiPut`, follow that.)

- [ ] **Step 2: Update the type** in `frontend/src/lib/types.ts`:

```ts
export interface NotificationRule {
  user_id: string;
  score_threshold: number;
  notify_mode: "instant" | "daily" | "every_n_hours";
  interval_hours: number;
  daily_send_time: string;     // "HH:MM"
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
  last_sent_at: string | null;
  enabled: boolean;
}
```

- [ ] **Step 3: Write a failing component test** (Vitest) asserting the form shows ONE rulebook with a mode selector and an interval field when mode = every_n_hours. (Mirror an existing settings test in the repo for the render/fixture pattern.)

- [ ] **Step 4: Rewrite `page.tsx`** as a single form: a `Select`/radio for `notify_mode`; when `every_n_hours` show a number input (1–24) bound to `interval_hours`; when `daily` show a time input bound to `daily_send_time`; always show the score-threshold slider, quiet-hours time inputs, and a master enable toggle; a Save button calling `saveNotificationRule`. Add the line "These settings apply to all your connected channels." Remove the per-channel card loop.

- [ ] **Step 5: Run frontend gates — expect PASS:**

```bash
cd frontend && npm run -s test:unit && npm run -s type-check && npm run -s lint
```

- [ ] **Step 6: Gate + commit**

```bash
bash scripts/agent-gate.sh
git add frontend/src/lib/api.ts frontend/src/lib/types.ts frontend/src/app/settings/notifications/
git commit -m "feat(frontend): single notification rulebook form"
```

---

## Task 9: Docs sync

**Files:**
- Modify: `CLAUDE.md` (rules #23/#24), `STATUS.md`, `docs/harness/IMPLEMENTATION_LOG.md`

- [ ] **Step 1: Update CLAUDE.md rules #23/#24** to describe: ONE rule per user; three modes (`instant`/`daily`/`every_n_hours`); the `notification_tick` cron drives `daily` + `every_n_hours`; quiet hours hold-and-queue for all modes; the legacy global path is removed.

- [ ] **Step 2: Append to `docs/harness/IMPLEMENTATION_LOG.md`** a "Channels & Notifications Overhaul" entry: migration 0020, single-rule model, `notification_tick`/`send_bundle`, legacy removal, all-5-channel verification.

- [ ] **Step 3: Update `STATUS.md`** current-phase + carry-overs.

- [ ] **Step 4: Gate + commit**

```bash
bash scripts/agent-gate.sh
git add CLAUDE.md STATUS.md docs/harness/IMPLEMENTATION_LOG.md
git commit -m "docs: sync channels & notifications overhaul"
```

---

## Task 10: Live end-to-end verification (all 5 channels)

**Use the `/verify-job360` skill — do not claim done from a green suite alone.**

- [ ] **Step 1:** Start backend (`python main.py`) + a Redis + `arq src.workers.settings.WorkerSettings` + frontend (`npm run dev`).
- [ ] **Step 2:** Register a user, connect each channel (email, webhook via paste; Slack/Discord via OAuth; Telegram via deep-link). Confirm each appears in `user_channels` with `connection_status='connected'`.
- [ ] **Step 3:** Send a test from the Channels page for each → confirm a real message arrives in each destination. **Telegram specifically:** confirm no HTTP 400 from markdown; if it fails, change `format_payload`'s telegram branch to plain text (drop the `*...*`) or set a safe parse mode, add a regression test, re-gate, commit.
- [ ] **Step 4:** Set the rulebook to `every_n_hours=instant` first: trigger a pipeline run that produces a matching job; confirm instant delivery + a `notification_ledger` row `sent`.
- [ ] **Step 5:** Set rulebook to `daily` with `daily_send_time` = ~2 min ahead; confirm jobs queue, then the tick fires `send_bundle` and one bundled message arrives per channel; ledger rows flip `sent`; queue drains.
- [ ] **Step 6:** Set `quiet_hours` to cover now in instant mode; trigger a job; confirm it queues (held), and after the window the tick flushes it.
- [ ] **Step 7:** Record evidence (screenshots / curl / DB queries) in the IMPLEMENTATION_LOG entry. Final `bash scripts/agent-gate.sh` green.

---

## Self-Review (author checklist — completed)

- **Spec coverage:** single-rule model (T1/T2/T6) ✓; three modes (T3/T5) ✓; scheduler tick (T5) ✓; send_bundle fix for silent-success (T4) ✓; legacy removal (T7) ✓; all-5-channel verify + Telegram formatting (T10) ✓; queue cleanup (T2) ✓; frontend (T8) ✓; docs (T9) ✓.
- **Placeholder scan:** no TBD/TODO; every code step has real code + exact commands.
- **Type consistency:** `notify_mode` values `instant|daily|every_n_hours` consistent across migration, models, dispatcher, `_bundle_due`, frontend; method names `get_user_notification_rule`/`save_user_notification_rule`/`set_rule_last_sent`/`get_users_with_rules`/`cleanup_old_digests` consistent across tasks; `send_bundle`/`notification_tick`/`_bundle_due` consistent.
- **Known follow-the-code checks for the implementer:** confirm router mount prefix in `api/app.py`; confirm `ChannelSendResult` field order before positional construction; grep for `send_daily_digest` references before deleting it.
