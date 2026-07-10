# Runbook — Database backups & restore

Job360's production data lives in a single Postgres database (Railway). This
runbook covers how to back it up and — the part people skip — how to actually
restore it.

## Automated backups: self-verifying nightly → Cloudflare R2

`.github/workflows/db-backup.yml` runs **02:17 UTC nightly** (and on demand via
the Actions tab). Trust model — **a backup only counts if it proves it can be
restored:**

1. `pg_dump` prod.
2. **Restore that dump into a throwaway Postgres in the same run** and assert the
   data is real (`users ≥ 1`, `_schema_migrations` head `≥ 25`). A corrupt/empty
   dump fails the job → GitHub emails the owner. A green run = "proven restorable
   tonight". Row counts are written to the run summary.
3. **Encrypt** (`gpg --symmetric AES256`, `BACKUP_PASSPHRASE` secret) — R2 only
   ever stores ciphertext, so the repo can stay public and a leaked R2 key still
   exposes nothing.
4. Upload to a **private R2 bucket**; keep the newest 30.

Why R2 and not a GitHub artifact: a workflow artifact in a *public* repo is
world-downloadable and the dump is user PII. R2 is private + the file is
encrypted anyway — two independent protections.

### One-time setup (secrets the workflow needs)
Create these GitHub repo secrets (Settings → Secrets and variables → Actions):
- `PROD_DATABASE_URL` — the **external** Railway Postgres URL (not `*.railway.internal`).
- `BACKUP_PASSPHRASE` — a long random passphrase. **Store it offline too — without
  it the backups are unrecoverable.**
- `R2_ENDPOINT` — `https://<account-id>.r2.cloudflarestorage.com`
- `R2_BUCKET` — e.g. `job360-backups` (a **private** R2 bucket)
- `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` — an R2 API token scoped to that bucket.

### Alternative: Railway managed backups (paid)
Railway Pro gives daily backups + point-in-time recovery in the dashboard
(Postgres → Backups). Zero code, and PITR is strictly better than nightly dumps.
Move to this when revenue justifies the Pro plan; until then the R2 workflow
above fully protects the data for $0.

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

**Restoring an encrypted R2 backup** (the `.sql.gz.gpg` files):

```bash
# 0. Download the newest backup from R2
aws s3 ls "s3://job360-backups/" --endpoint-url "$R2_ENDPOINT" | sort | tail -1
aws s3 cp "s3://job360-backups/job360-<TS>.sql.gz.gpg" . --endpoint-url "$R2_ENDPOINT"

# 1. Create a fresh target database (never restore over live data)
psql "$ADMIN_DATABASE_URL" -c "CREATE DATABASE job360_restored;"

# 2. Decrypt → gunzip → load (needs BACKUP_PASSPHRASE)
gpg --batch --quiet --decrypt --passphrase "$BACKUP_PASSPHRASE" \
  "job360-<TS>.sql.gz.gpg" | gunzip -c | psql "postgresql://…/job360_restored"

# 3. Sanity-check row counts and the migration head
psql "postgresql://…/job360_restored" -c \
  "SELECT count(*) FROM users; SELECT count(*) FROM jobs; SELECT max(id) FROM _schema_migrations;"

# 4. Only once verified: repoint the app's DATABASE_URL at the restored DB.
```

For a plain unencrypted `.sql.gz` (from `scripts/backup_db.py` run manually), skip
the `gpg` step: `gunzip -c file.sql.gz | psql "postgresql://…/job360_restored"`.

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
