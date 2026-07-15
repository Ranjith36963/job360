# 02 — Data & Database

> Source: data-layer sweep (Opus). Read-only, evidence as `file:line`.
> **Headline:** tenancy isolation is clean (rules #10/#17 hold). The real fragility is
> the **SQLite→Postgres shim seam** (`src/repositories/pg.py`). Nothing loses data on the
> happy path, but under concurrency or a failed migration/rollback the safety nets are
> missing. **This doc has the highest-priority fixes in the whole audit.**

## What's already right
- `jobs` / `job_enrichment` / `job_embeddings` carry no `user_id`/`tenant_id` — shared catalog by design; per-user state lives only in `user_feed`/`user_actions`/`applications`. Rules #10/#17 hold.

---

## P1 — The whole app shares ONE psycopg async connection (not concurrency-safe)
- **What I saw:** `database.py:19,25` — `JobDatabase._conn` is a single connection opened once in `init_db()`. `api/dependencies.py:8-28` hands that same singleton to *every* request. Every method does `await self._conn.execute(...)` on it. `pg.py:409-417` even fires a second statement (`SELECT lastval()`) on the same raw connection between awaits.
- **Why it matters:** psycopg3 `AsyncConnection` must not be used by more than one coroutine at a time. FastAPI serves requests concurrently. Two overlapping requests interleave at an `await` → `psycopg.ProgrammingError: another operation is already in progress`, or a cursor returns the *other* request's rows. Survives low traffic; **500s or cross-request data leaks under real load.**
- **Fix (P1, top priority):** use `psycopg_pool.AsyncConnectionPool`, acquire a connection per request/operation. Never share one connection across coroutines. This single change fixes the root of several findings below.

## P1 — Migrations run under autocommit → non-atomic; a half-failed migration bricks the schema on boot
- **What I saw:** `pg.py:321` opens connections `autocommit=True`; `Connection.commit()` is a no-op (`pg.py:431-432`). The runner applies each statement separately (`runner.py:136-148`) and **migrations auto-run on FastAPI boot** (`dependencies.py:21`). Rebuild migrations are multi-statement `CREATE new / INSERT SELECT / DROP / RENAME`.
- **Why it matters:** if statement 3 of 4 fails, the first statements already committed but the migration is NOT recorded → next boot re-runs from a corrupted half-state (`user_actions` already dropped, or `_new` already renamed) with no rollback. SQLite's `executescript` was implicitly transactional; the shim removed that safety.
- **Fix:** wrap each migration body in one explicit `BEGIN…COMMIT` on a non-autocommit connection; record the migration inside the same transaction.

## P1 — Rebuild migrations copy explicit `id` into IDENTITY columns without advancing the sequence
- **What I saw:** `pg.py:243` maps `AUTOINCREMENT` → `IDENTITY`. `0002_multi_tenant.up.sql`, `0010…down.sql`, `0011…down.sql` all `INSERT … SELECT id, …`. Postgres does NOT bump the identity sequence on explicit-value inserts.
- **Why it matters:** run any of these on a *populated* table (e.g. an operator rolls back 0011 on the live `jobs` catalog) → sequence still at 1 → next insert auto-generates id 1, collides → `UniqueViolation`, pipeline inserts die. Hides until the first rollback/re-migration on real data.
- **Fix:** after each copy, `SELECT setval(pg_get_serial_sequence('tbl','id'), MAX(id))` — or don't copy `id`.

## P1 — `ON DELETE CASCADE` is silently stripped by the shim → purge/user-delete leave orphans
- **What I saw:** `pg.py:186-198` strips ALL foreign-key clauses, including `ON DELETE CASCADE`. `job_enrichment`/`job_embeddings`/`user_feed`/`user_actions`/`applications` all declare `REFERENCES jobs(id) ON DELETE CASCADE`, but `purge_old_jobs` only does `DELETE FROM jobs` (`database.py:547-556`). Nothing deletes the children.
- **Why it matters:** every 30-day purge orphans enrichment/embedding/feed/action rows pointing at vanished `job_id`s — they accumulate forever (bloat), and the DB-declared integrity is a lie. **Also a compliance issue:** a user-delete that leaves child rows behind is an incomplete "right to be forgotten" (see `05-COMPLIANCE-AND-LEGAL.md`).
- **Fix:** explicit child deletes in `purge_old_jobs` and every job/user delete — or add real Postgres FKs with cascade and stop stripping them.

---

## P2 — the drift-and-atomicity cluster (all downstream of the shim)
- **Timestamp format drift** (`pg.py:160,242`): `DEFAULT CURRENT_TIMESTAMP` emits `2026-07-11 12:00:00` (space) but app writes ISO `2026-07-11T12:00:00+00:00`. Compared as *text* in `get_notification_ledger` range filters + `ORDER BY created_at` (`database.py:1180-1186`) → space-format always sorts before T-format → **wrong time-range filters and mis-ordered ledgers** when default- and app-inserted rows mix. **Fix:** one canonical ISO format for defaults *and* app writes, or store `timestamptz` and stop string-comparing.
- **Purge keys on `first_seen` not `last_seen_at`** (`database.py:547-556`): a posting still live after 30 days gets deleted then re-inserted as brand-new (resets scores, re-notifies). **Fix:** purge on `last_seen_at < cutoff`.
- **"Best-effort" down migrations don't reverse** (`0014/0017/0018 .down.sql`): they only delete the ledger row, so `down` reports success while columns remain — false reversibility during an incident. **Fix:** implement real reverse, or make `down` loudly refuse and say "restore from backup".
- **`0002` down narrows the unique key with `INSERT OR IGNORE`** → two users who acted on the same job collide → second user's row silently dropped on rollback. **Fix:** fail loudly on collision; mark this down destructive.
- **`normalized_key()` doesn't collapse internal whitespace/punctuation** (`models.py:87-91`): `"Software  Engineer"` vs `"Software Engineer"` → different rows → duplicate catalog entries (the exact rule #1 hazard). **Fix:** `re.sub(r"\s+"," ",…)` + strip punctuation, re-running dedup + UNIQUE tests per rule #1.
- **`lastrowid` via `SELECT lastval()`** (`pg.py:409-417`) is stale after `ON CONFLICT` and can interleave on the shared connection. **Fix:** use `RETURNING id` explicitly; treat `lastrowid`-after-conflict as undefined.
- **Multi-step writes non-atomic** (`advance_application` `database.py:882-902`; `mark_missed_for_source` `422-464`): crash mid-sequence leaves stage advanced with no history row. **Fix:** one explicit transaction per multi-step write.

## P3 — Naive `;` statement splitting
- `runner.py:96-118` + `pg.py:270-284` split on `;` naively (breaks on `;` inside a string literal or a `$$` function body). Safe today; a future migration could sever mid-statement at boot. **Fix:** guard `$$` bodies / use a real splitter; add a test asserting the limits.

---

## Fix order (data) — this is the audit's #1 area
1. **Connection pool** (P1) — fixes the concurrency root cause. Do first.
2. **Transactional migrations** (P1) — wrap each migration in `BEGIN…COMMIT`; stop trusting autocommit.
3. **Cascade/orphan cleanup** (P1) — explicit child deletes in purge + user-delete (also compliance).
4. **Identity `setval` after id-copy** (P1) — before any rollback on populated tables.
5. Timestamp format + `first_seen`→`last_seen_at` purge (P2) — correctness bugs users will feel.
6. Remaining P2/P3 into a "shim hardening" PR.

**Verdict:** No data loss on the happy path today, but the migration-auto-apply-on-boot design has **no atomic safety net**, and one shared connection is a concurrency incident waiting for traffic. Close the pool + transactional-migration gaps before scaling or before the next schema change on populated prod tables.
