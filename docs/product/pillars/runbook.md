# Runbook — Operational Answers

<!-- doc: LIVING | last-verified: 2026-08-24 by the nightly doc-truth routine -->

> **Audience.** Agents and operators answering "I see a problem — what do I do?" Each section below is a question phrased as a verb; the answer is a command, an SQL query, or a code pointer you can act on without first re-reading the pillar docs.
>
> Commands assume `cwd = backend/` and `source venv/bin/activate` unless noted.
>
> **The database is Postgres, not a file.** It has been since 2026-07-02. There is no
> `data/jobs.db` and no sqlite driver in the dependency set (`backend/pyproject.toml`
> declares `psycopg[binary,pool]>=3.2`). Two ways in:
>
> ```bash
> # Local dev — the docker-compose.dev.yml Postgres on port 5433.
> # DATABASE_URL defaults to this (backend/src/core/settings.py:25).
> psql postgresql://job360:job360dev@localhost:5433/job360
>
> # Production — see CLAUDE.md. Use DATABASE_PUBLIC_URL: plain
> # DATABASE_URL only resolves from inside Railway.
> railway run -s Postgres python <script>
> ```
>
> Every `SQL` block below is meant for one of those two, not for a `sqlite3` shell.
> `backend/src/repositories/pg.py` is an *aiosqlite-shaped* async driver over Postgres —
> the shape is why so much of the code still reads like SQLite. The storage is not.

---

## 1. Daily check / "is everything healthy?"

### See the last run

```bash
python -m src.cli status
```

### See the last 20 runs with per-source timing + errors

`run_log` is **per-user** operational metadata — it has a `user_id` column
(migration `0010`, mirrored at `backend/src/repositories/database.py:208`), and
rule #12 applies. Scope by it, or you are reading someone else's runs:

```sql
SELECT timestamp, run_uuid, total_found, new_jobs, total_duration, per_source_errors
FROM run_log WHERE user_id = '<uuid>' ORDER BY timestamp DESC LIMIT 20;
```

That is the query `GET /api/runs/recent` (auth-gated) runs — `get_recent_runs(user_id=…)`
at `backend/src/repositories/database.py:2002-2015`, which also drops legacy rows with a
NULL `user_id`. `GET /api/runs/source-health` sits beside it
(`backend/src/api/routes/runs.py:63,150`). There is no bare `GET /api/runs`.

Drop the `WHERE` only when you deliberately want the operator-wide view across all users.

### List configured sources

```bash
python -m src.cli sources
```

### Browse recent jobs in the terminal

```bash
python -m src.cli view --hours 24 --min-score 50
python -m src.cli view --visa-only
```

---

## 2. Database — inspect, repair, migrate

### Open the DB

```bash
psql postgresql://job360:job360dev@localhost:5433/job360   # local dev
railway run -s Postgres sh -c 'psql "$DATABASE_PUBLIC_URL"'   # production
```

### See what migrations have been applied

```bash
python -m migrations.runner status
```

```sql
-- or straight from psql. The table is (id TEXT PRIMARY KEY, applied_at TEXT)
-- — backend/migrations/runner.py:53-56. There is no `version` column; `id` is
-- the zero-padded migration stem, so it sorts correctly as text.
SELECT * FROM _schema_migrations ORDER BY id;
```

### Apply pending migrations (idempotent, safe to re-run)

```bash
python -m migrations.runner up
```

FastAPI boot auto-runs this — but the CLI doesn't. Run it manually before `python -m src.cli run` after pulling new code.

### Roll a migration back (rare — destructive)

```bash
python -m migrations.runner down   # rolls back the latest only
```

The runner still accepts an optional second argument and still calls it `db_path`
(`backend/migrations/runner.py:399`), but it is **not** a database file. It is passed
to `pg.connect()`, where it selects a *schema* under test and is ignored in production
(`backend/src/repositories/pg.py:732-737`). Migrations always run against `DATABASE_URL`.

### Show all tables

```sql
\dt                      -- psql meta-command
-- or portable:
SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;
```

### Inspect a specific table's schema

```sql
\d user_feed             -- psql meta-command
```

---

## 3. Users — inspect, fix, debug auth

### See all users (use sparingly — PII)

```sql
SELECT id, email, created_at, deleted_at, timezone
FROM users
ORDER BY created_at DESC LIMIT 20;
```

### Find a user by email

```sql
SELECT * FROM users WHERE email = 'alice@example.com';
```

### Revoke all sessions for a user (force re-login)

```sql
DELETE FROM sessions WHERE user_id = '<uuid>';
```

### Soft-delete a user (preserves audit trail)

```sql
UPDATE users SET deleted_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
WHERE email = 'alice@example.com';
```

The cookie-resolver excludes `deleted_at IS NOT NULL`, so the user is logged out immediately.

### Look up a session cookie

The cookie value is `<session_id>.<hmac>`. Strip the `.hmac` part and query:

```sql
SELECT user_id, expires_at, last_seen, ip_hash
FROM sessions WHERE id = '<session_id_before_the_dot>';
```

---

## 4. Profile — inspect, force-rebuild, restore

### Where is a user's profile stored?

Table `user_profiles` (current tip) + `user_profile_versions` (last-10 history).

There is **no `version` column** — the row's `id` *is* the version. `current_profile_version_id()`
is literally `SELECT MAX(id) FROM user_profile_versions WHERE user_id = ?`
(`backend/src/services/profile/storage.py:260-271`), and that id is what `user_feed.profile_version`
stores. Columns are `id, user_id, created_at, source_action, cv_data, preferences`
(`backend/migrations/0007_user_profile_versions.up.sql:17-25`) plus `snapshot_id`
(migration `0030`).

```sql
SELECT id AS version, user_id, source_action, created_at
FROM user_profile_versions WHERE user_id = '<uuid>'
ORDER BY id DESC;
```

### Dump a user's current profile

```sql
SELECT json_extract(profile_json, '$.cv_data.skills'),
       json_extract(profile_json, '$.preferences.target_job_titles')
FROM user_profiles WHERE user_id = '<uuid>';
```

### Restore an older profile version

API: `POST /api/profile/versions/{version_id}/restore` (atomic; creates a new snapshot so history is preserved).

### Force-rebuild from CLI for DEFAULT_TENANT_ID

```bash
python -m src.cli setup-profile --cv path/to/cv.pdf --linkedin linkedin.pdf --github username
```

### Legacy JSON hydration

If `data/user_profile.json` exists but no `user_profiles` row for `DEFAULT_TENANT_ID` does, the next `load_profile(DEFAULT_TENANT_ID)` imports it and then **DELETES the file** (kept only if that raises). Back it up first. Pinned by `test_profile_storage.py::test_legacy_json_hydrates_to_default_tenant_and_deletes_file`.

---

## 5. Feed / user_feed — inspect & cascade

### What's a user seeing today?

```sql
SELECT bucket, COUNT(*) AS n, MIN(score) AS min, AVG(score) AS avg, MAX(score) AS max
FROM user_feed WHERE user_id = '<uuid>' AND status = 'active'
GROUP BY bucket ORDER BY bucket;
```

### Why isn't a specific job in this user's feed?

```sql
-- Is it in the catalog at all?
SELECT id, title, company, match_score, staleness_state FROM jobs WHERE id = <job_id>;

-- Is it in this user's feed?
SELECT score, bucket, status FROM user_feed WHERE user_id = '<uuid>' AND job_id = <job_id>;
```

If catalog-yes / feed-no, the prefilter dropped it for this user. Check user's `preferred_locations`, `experience_level`, `additional_skills` vs the job's location/seniority/skills.

### Mark a job stale across every user (e.g. confirmed dead upstream)

```python
# from a python REPL with FeedService imported
await feed_service.cascade_stale(job_id)
```

Or SQL:

```sql
UPDATE user_feed SET status = 'stale' WHERE job_id = <id> AND status = 'active';
UPDATE jobs SET staleness_state = 'confirmed_expired' WHERE id = <id>;
```

---

## 6. Channels & notifications

### List a user's configured channels

```sql
SELECT id, channel_type, display_name, enabled FROM user_channels
WHERE user_id = '<uuid>' ORDER BY id;
```

(Don't try to read `credential_encrypted` — Fernet ciphertext, needs `CHANNEL_ENCRYPTION_KEY` and `crypto.decrypt()`.)

### Send a test notification

`POST /api/settings/channels/{channel_id}/test` — the dispatcher decrypts, calls Apprise, returns `{ok, error}`.

### See pending digest queue

```sql
SELECT user_id, channel, COUNT(*) AS queued
FROM user_notification_digests WHERE sent = 0
GROUP BY user_id, channel;
```

### Inspect notification history (with failure reasons)

```sql
SELECT created_at, channel, status, retry_count, error_message, job_id
FROM notification_ledger WHERE user_id = '<uuid>'
ORDER BY created_at DESC LIMIT 50;
```

Or via API: `GET /api/notifications?limit=50&status=failed`.

### Reset a failed notification so the worker re-tries

```sql
DELETE FROM notification_ledger WHERE id = <ledger_id>;
```

The next worker pass will see no idempotency row and re-attempt.

---

## 7. Pipeline / applications

### What stage is each application at?

```sql
SELECT stage, COUNT(*) FROM applications WHERE user_id = '<uuid>' GROUP BY stage;
```

### Stage transition history for one application

```sql
SELECT transitioned_at, from_stage, to_stage, notes
FROM application_stage_history
WHERE user_id = '<uuid>' AND job_id = <job_id>
ORDER BY transitioned_at;
```

### Stalled applications (no movement in 7+ days)

`GET /api/pipeline/reminders` — same query the dashboard uses.

---

## 8. Source debugging

### Run *one* source in isolation

```bash
python -m src.cli run --source greenhouse --dry-run --log-level DEBUG
```

`--dry-run` skips DB writes; `--source <name>` runs only that source.

### A source returned 0 jobs — why?

1. Confirm it ran: `grep "source=greenhouse" data/logs/job360.log | tail -20`
2. Check the breaker state — if OPEN, the source was skipped:
   ```python
   # from a REPL
   from src.services.circuit_breaker import default_registry
   print(default_registry().snapshot())
   ```
3. If keyed, confirm the env var is set: `echo $REED_API_KEY`. Keyed sources `return []` silently when the key is empty. They are the contents of `apis_keyed/ (8)` under `backend/src/sources/`: Reed, Adzuna, JSearch, Jooble, Google Jobs, Careerjet, Findwork, **gov_apprenticeships**. Don't trust this list over the folder; `ls` it.
4. For an HTML scraper, the upstream may have changed markup. Open the source file, find the regex, compare against a live response. The scrapers are `scrapers/ (5)` under `backend/src/sources/`, registry keys `linkedin`, `bcs_jobs`, `aijobs_ai`, `climatebase`, `eightykhours`. (**Not** JobTensor — dropped upstream-dead in the 2026-06 M6 rotation, `backend/src/main.py:158`. **Not** Workday either: that is a JSON ATS adapter in `sources/ats/`.)

### Force a circuit breaker back to CLOSED

Breakers are in-memory only — restart the process (CLI or API) and the registry resets. No persistence layer.

### A source returns the same jobs every run

Likely the source's `posted_at` parsing is wrong → `date_confidence='low'` → recency score is low → not promoted. Inspect:

```sql
SELECT date_found, posted_at, date_confidence, date_posted_raw
FROM jobs WHERE source = 'greenhouse' ORDER BY id DESC LIMIT 10;
```

---

## 9. Scoring debugging

### Why did Job#X score Y?

The `jobs` table stores the per-dimension breakdown (migration `0011`):

```sql
SELECT title, company, match_score,
       role, skill, location_score, recency,
       seniority_score, experience, credentials, semantic, penalty
FROM jobs WHERE id = <X>;
```

### Re-score everything against a new user profile

CLI runs always re-score on each pass. The worker (`score_and_ingest`) re-scores when invoked per `(user, job)`. There **is** a "re-score all" admin command:

```bash
python -m src.cli rescore-backfill --batch-size 200 --max-users 50 --throttle 0.5
```

It does no work itself — it enqueues the resumable `rescore_backfill` ARQ task
(`backend/src/cli.py:233`, `backend/src/workers/tasks.py:1494`) and returns. Watch the
worker logs for `rescore_backfill_done`.

### A user updated their profile — when does it take effect?

Automatically, since migration `0018`. `POST /api/profile` compares the last two
`user_profile_versions` snapshots; if the content actually changed it enqueues
`rescore_user_feed_task` on the ARQ queue (`backend/src/api/routes/profile.py:163`).
Only when Redis is unreachable does it fall back to an in-process `asyncio` task that
dies with the web process (`profile.py:179-184`).

What `rescore_user_feed` actually does, precisely:

- It reads the catalog via `repositories/database.Database.get_catalog_jobs_for_rescore`, which has **no date predicate**. The 30-day horizon people quote is a *consequence* of `purge_old_jobs()` capping the catalog, not a filter in this query — and the row limit is deliberately set far above the catalog so a "full re-score" really is one.
- It clears the user's LLM verdicts **only when `ENGINE4_ENABLED or MATCHER_ENABLED`** (`backend/src/services/rescore.py:589,595-598`). With the judge off — the default — no verdict is touched.

And the part that is easy to get wrong:

- **Ordinary searches do re-score.** `src/main.run_search` calls `services/rescore.backfill_feed_from_catalog` on **every** authenticated search — even one that fetched nothing — which scores the whole catalog for that user and upserts feed rows. It is not "newly-fetched jobs only". The call is best-effort: it is wrapped so a failure logs a warning and never fails the run.
- **But an existing row's score still doesn't drift.** `upsert_feed_row` applies a *version-conditional freeze*: on an existing row the score is kept when the incoming `profile_version` **and** `scorer_version` both match what's stored, and overwritten when either differs (`backend/src/services/feed.py:288-303`). So a score moves when the PROFILE changes or `SCORER_VERSION` is bumped — never merely because time passed. `bucket` and both version stamps are always rewritten.
- The mechanism is that freeze, **not** `skip_existing`. `match_batch(..., skip_existing=True)` (`backend/src/services/llm_matcher.py:436,443`) stops the LLM re-judging a job it already judged for this user — it guards verdicts, not keyword scores.
- **Dashboard reads** use whatever's in `user_feed` *now* — so a read landing before the worker drains still shows old scores.
- To force it by hand: `rescore-backfill` above. There is no `db.purge_user_feed()` helper — that name does not exist in `backend/src/`.

---

## 10. Enrichment & embeddings (opt-in surfaces)

### Is enrichment on?

```bash
echo "ENRICHMENT_ENABLED=$ENRICHMENT_ENABLED"
echo "SEMANTIC_ENABLED=$SEMANTIC_ENABLED"
```

Both default `false`. Enabling either changes the code path materially — see Pillar 2 §5.

### How many jobs have enrichment rows?

```sql
SELECT COUNT(*) FROM job_enrichment;
SELECT COUNT(*) FROM jobs;   -- total catalog
```

### Manually enrich one job

```python
# from a REPL
from src.services.job_enrichment import enrich_job
enrichment = await enrich_job(job)  # raises RuntimeError on all-providers-fail
```

### Embeddings look wrong / stale — rebuild

**The vectors are in Postgres, not on disk.** Migration `0027` (2026-08-07) added
`job_embeddings.embedding` (pgvector) and `services/pg_vector_index.py` is the only
store the pipeline and the API use — `backend/src/main.py:580`, `main.py:1298`,
`backend/src/api/routes/jobs.py:379`. `rm -rf data/chroma/` clears a directory the
production pipeline and API never read; it will not fix anything here. (Two
legacy helpers, `backend/scripts/build_job_embeddings.py` and
`eval_v2_pool.py`, do still use that store — deleting it costs them their index.)

**Check the column exists before the first statement.** Migration `0027` is
tolerant: on a Postgres without pgvector it skips the column entirely, and then
`UPDATE … SET embedding = NULL` fails with `column "embedding" does not exist`.

```sql
-- 0 rows here means pgvector is absent and 0027 was a no-op. Install the
-- extension and re-run the migration before the UPDATE below; the DELETE
-- underneath works either way.
--
-- to_regclass, not information_schema.table_name: the UPDATE below is
-- UNQUALIFIED, so it hits whichever job_embeddings the current search_path
-- resolves to. Matching on the bare table name can pass on a same-named table
-- in another schema and still leave the UPDATE erroring.
SELECT 1 FROM pg_attribute
 WHERE attrelid = to_regclass('job_embeddings')
   AND attname = 'embedding' AND NOT attisdropped;

-- Force a re-embed of everything (the vector goes, the audit row stays):
UPDATE job_embeddings SET embedding = NULL;
-- Or drop the bookkeeping too, for a truly fresh start:
DELETE FROM job_embeddings;
```

The next `SEMANTIC_ENABLED` run re-fills them: `_embed_backfill_budget`
(`backend/src/main.py:548`) selects `WHERE e.job_id IS NULL OR e.embedding IS NULL`.

How many jobs actually have a vector:

```sql
SELECT count(*) FROM job_embeddings WHERE embedding IS NOT NULL;
```

If that is 0 with `SEMANTIC_ENABLED=true`, check the two structural causes before
anything else: this Postgres may not have the `vector` extension (migration `0027`
is deliberately tolerant — it skips the column rather than failing boot, and every
`PgVectorIndex` method then degrades to empty), or the process doing the ingest may
not have the `[semantic]` extra installed. `Dockerfile.worker` runs a plain
`pip install .`, and the worker is what runs the scheduled refresh.

---

## 11. Logs

### Where are logs?

`data/logs/job360.log` (rotating file handler) + console (formatted).

### Bump log level for a single run

```bash
python -m src.cli run --log-level DEBUG
```

### Tail per-source activity

```bash
tail -f data/logs/job360.log | grep "source=greenhouse"
```

### Per-run correlation

Every log line emitted during `run_search()` carries the run's `run_uuid` (set in a `contextvar`). Grep by uuid to see one run's entire timeline:

```bash
grep "run_uuid=abc123" data/logs/job360.log
```

---

## 12. Tests

### Run the full suite

```bash
python -m pytest tests/ -v
```

### Live test count

```bash
python -m pytest --collect-only -q | tail -1
```

### Run one file or one test

```bash
python -m pytest tests/test_scorer.py -v
python -m pytest tests/test_scorer.py::test_specific_function -v
```

### Run tests touching a specific source

```bash
python -m pytest tests/test_sources.py -v -k greenhouse
```

### Speed up local iteration

```bash
python -m pytest tests/test_X.py -x --ff   # stop at first fail, run failed first
```

---

## 13. Frontend

### Dev server

```bash
cd frontend && npm run dev    # localhost:3000
```

### Production build

```bash
cd frontend && npm run build && npm start
```

### Lint

```bash
cd frontend && npm run lint
```

### Where's the frontend's notion of "logged in"?

It isn't — the session cookie is `HttpOnly`, so the JS never sees it. The frontend calls `GET /api/auth/me` on each protected page; a 401 redirects to `/(auth)/login?next=<current>`.

---

## 14. Production worker (ARQ + Redis)

> Not required for CLI / read-only API usage. Only needed if you want background scoring and notification fan-out.

### Start the worker

```bash
arq src.workers.settings.WorkerSettings
```

### Required env

- `REDIS_URL` (default `redis://localhost:6379`)
- `DATABASE_URL` — the Postgres DSN
- All the LLM keys you want active. **`OPENAI_API_KEY` first**: OpenAI is the PRIMARY provider and heads the fallback chain (`backend/src/services/profile/llm_provider.py:329-334`). Then `GEMINI_API_KEY`, `GROQ_API_KEY`, `CEREBRAS_API_KEY`.
- `CHANNEL_ENCRYPTION_KEY` (Fernet key) — fail-closed
- `SESSION_SECRET` — fail-closed

### Worker isn't picking up jobs

1. Is Redis up? `redis-cli ping` → `PONG`.
2. Is `REDIS_URL` set correctly in the worker's env? (Different from the API's env.)
3. Check the worker's stdout — exceptions are logged there.

---

## 15. Common error → cause table

| Error / symptom | Most likely cause | Fix |
| --- | --- | --- |
| `RuntimeError: SESSION_SECRET unset` on API boot | Env var missing | Set it in `.env` or shell; fail-closed by design |
| `RuntimeError: CHANNEL_ENCRYPTION_KEY unset` | Same | Set it. Once set, **don't rotate** without a re-encryption migration |
| `argon2.exceptions.InvalidHash` on login | DB password_hash got corrupted | Re-register the user; old hash is unrecoverable |
| All 0-score jobs in `user_feed` | No user profile / empty `SearchConfig` | `setup-profile` or check `keywords.py` (empty defaults since 3ba1342) |
| One source always fails | Likely auth/markup change | See §8 above; in worst case mark `enabled=False` (no such flag yet — comment out of `SOURCE_REGISTRY`) |
| `ModuleNotFoundError: psycopg` | Backend deps not installed in this env | `pip install -e .` from `backend/` (these commands assume `cwd = backend/`, so a `backend/`-prefixed path would resolve to `backend/backend/`). There is **no `requirements.txt`** — `backend/pyproject.toml` is the only dependency manifest. Note: **`aiosqlite` is not a dependency** — every module does `from src.repositories import pg as aiosqlite`, so the name is a local alias for the Postgres shim, never an installed package |
| Notification rule fires but no message arrives | Channel credential decrypted wrong, or Apprise URL malformed | `POST /api/settings/channels/{id}/test` to surface the error |
| Pipeline shows "stalled" too aggressively | Default is 7-day threshold | Hard-coded in `pipeline.py` reminder logic — adjust there |
| Hybrid retrieval returns 0 results | No row carries a vector, or `SEMANTIC_ENABLED=false` | `SELECT count(*) FROM job_embeddings WHERE embedding IS NOT NULL` (that is exactly what `PgVectorIndex.count()` runs); if zero, see §10 "Embeddings look wrong / stale" — it is usually pgvector missing or the worker lacking `[semantic]`, not an empty run |

---

*Originally written 2026-05-28 (HEAD `cb52eb7`), against the pre-Postgres SQLite layout.
Re-verified against code 2026-08-24 by the nightly doc-truth routine: the DB access
commands, the `/api/runs` paths, the keyed/scraper source lists, the re-score answers and
the worker env list were all stale and are corrected above. This file is now a LIVING doc —
if you change one of these facts, change it here too.*
