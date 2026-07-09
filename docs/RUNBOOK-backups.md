# Runbook — Database backups & restore

Job360's production data lives in a single Postgres database (Railway). This
runbook covers how to back it up and — the part people skip — how to actually
restore it.

## ⚠️ Why automated CI-artifact backups are NOT wired up

The obvious approach (a nightly GitHub Action that runs `pg_dump` and uploads the
dump as a workflow artifact) is **unsafe for this repo because the repo is
public**. A dump contains every user's email and CV; a workflow artifact in a
public repo is downloadable by anyone. So we deliberately did **not** add that
workflow. Pick one of the safe options below instead.

### Option A (recommended): Railway managed backups
Railway's Postgres plugin offers automated daily backups + point-in-time
restore on its paid tiers. Enable it in the Railway dashboard → Postgres service
→ Backups. Zero code, encrypted at rest, never touches the repo. This is the
right answer for a live product.

### Option B: make the repo private, then enable the artifact workflow
If the repo is made private, a nightly `pg_dump → upload-artifact` job becomes
safe (artifacts inherit the private repo's access control). The script
(`backend/scripts/backup_db.py`) already exists; wiring is a ~40-line workflow.
Do NOT do this while the repo is public.

## Manual backup (any time, from a machine with the Postgres client)

`backend/scripts/backup_db.py` streams `pg_dump` straight into a gzip file with
retention pruning. It needs `pg_dump` on PATH and `DATABASE_URL` set.

```bash
cd backend
export DATABASE_URL="postgresql://…"      # the EXTERNAL Railway connection string
export BACKUP_DIR=./backups               # optional (default ./backups)
export BACKUP_KEEP=14                      # optional (keep newest 14, default 14)
python -m scripts.backup_db
# → backups/job360-2026-07-09T0200Z.sql.gz
```

If you don't have the Postgres client locally, run `pg_dump` through any Postgres
container instead (see the drill below).

## Restore

A dump is only useful if restore works. **Always restore into a NEW database,
never in-place over live data**, then repoint the app.

```bash
# 1. Create a fresh target database
psql "$ADMIN_DATABASE_URL" -c "CREATE DATABASE job360_restored;"

# 2. Load the dump
gunzip -c job360-2026-07-09T0200Z.sql.gz | psql "postgresql://…/job360_restored"

# 3. Sanity-check row counts and the migration head
psql "postgresql://…/job360_restored" -c \
  "SELECT count(*) FROM users; SELECT count(*) FROM jobs; SELECT max(id) FROM _schema_migrations;"

# 4. Only once verified: repoint the app's DATABASE_URL at the restored DB.
```

## Restore drill — logged evidence

Run a restore drill at least quarterly so you find problems before you need the
backup. Most recent drill:

**2026-07-09** — dumped the dev Postgres (`job360-dev-postgres` container), restored
into a scratch `drill` database, and compared row counts. Commands (client run
inside the container, which ships `pg_dump`/`psql`):

```bash
docker exec job360-dev-postgres bash -c \
  "pg_dump --no-owner --no-privileges -U job360 job360 > /tmp/drill.sql && \
   psql -U job360 -d postgres -c 'CREATE DATABASE drill;' && \
   psql -U job360 -d drill -f /tmp/drill.sql"
```

Result — counts matched exactly, restore verified:

| Table | Source | Restored |
|-------|-------:|---------:|
| users |      7 |        7 |
| jobs  |     19 |       19 |

## Gotchas

- **Use the EXTERNAL Railway connection string**, not the `*.railway.internal`
  host — internal hostnames only resolve inside Railway's network.
- **`pg_dump` version ≥ server version.** `pg_dump` refuses to dump a newer
  server. If you hit "server version mismatch", install the matching
  `postgresql-client-NN`.
- The dump includes real user PII (emails, CV text). Treat `.sql.gz` files as
  secret — never commit them, never attach to a public issue/PR/artifact.
