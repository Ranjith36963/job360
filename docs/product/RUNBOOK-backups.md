# RUNBOOK — Database backup & restore
<!-- doc: LIVING -->

> Referenced by `.github/workflows/db-backup.yml`. This is the human procedure for
> restoring the encrypted Postgres backups from Cloudflare R2 during an incident.
> **Read this BEFORE you need it** — an untested restore is not a backup.

## What the backup is (so restore makes sense)

A stored object is a **plain-SQL `pg_dump` (`--no-owner --no-privileges`), gzipped, then AES256-encrypted with gpg**: `job360-<TIMESTAMP>.sql.gz.gpg` in Cloudflare R2. R2 only ever holds ciphertext. It contains user PII (emails, CVs) — handle the decrypted file carefully and delete it when done.

What the nightly job asserts about the restored dump before it encrypts and uploads is the step bodies of `.github/workflows/db-backup.yml`. Read them there. In short, after restoring the dump into a throwaway Postgres it:

- **Compares row counts** for the core tables (`users`, `applications`, `application_events`, `application_artifacts`, `application_receipts`, `profile_edits`). The source counts are taken with `SELECT count(*)` right before `pg_dump`, so the restored copy must hold at least as many rows (writes during the dump can only add). Any table with fewer rows, or missing, turns the job red, and the per-table result is printed in the run's step summary. Logic: `scripts/backup_checks.py compare` (tests: `backend/tests/test_backup_checks.py`). Rare false alarm: an account deleted while the dump runs lowers `users`; re-run the workflow.
- **Times the restore.** The summary reads "Restore took N s (RTO measured)". This is the SQL replay into a local Postgres on a GitHub runner, so it is a floor for your recovery time: add the R2 download, the gpg decrypt, and provisioning a target database.
- Still gates `users >= 1` and `_schema_migrations >= 20`.

## Retention and freshness

- **Nightly:** the newest 30 `job360-<TS>.sql.gz.gpg` objects at the bucket root.
- **Monthly cold copy:** the first successful nightly of each month is also copied (server-side, same ciphertext) to `monthly/job360-<TS>.sql.gz.gpg`. The 30-copy prune never touches `monthly/`; that prefix is pruned separately to the newest 12 (about a year of history). To restore from one, use the procedure below with `s3://${R2_BUCKET}/monthly/` in steps 1 and 2.
- **Freshness alert:** the `freshness` job in `db-backup.yml` runs every 6 hours, lists the bucket and fails (a red scheduled run, so GitHub emails the owner and `scripts/watchdog_check.py` reports it RED) when the newest nightly object is more than 36 hours old or none exists. It is independent of the nightly job, so a nightly that never started or never uploaded is caught. It uses the same R2 secrets as the backup.
- Point-in-time recovery / volume snapshots are a Railway setting the owner turns on (not in this repo).

## What you need to restore
All are GitHub repo secrets (Settings → Secrets → Actions) — copy their values locally:
- `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT`, `R2_BUCKET`
- `BACKUP_PASSPHRASE` (the gpg symmetric passphrase)
- Tools: `awscli`, `gpg`, `gunzip`, `psql`.

## Restore procedure

```bash
# 0. Set credentials (from the GitHub secrets above)
export AWS_ACCESS_KEY_ID=<R2_ACCESS_KEY_ID>
export AWS_SECRET_ACCESS_KEY=<R2_SECRET_ACCESS_KEY>
export R2_ENDPOINT=<R2_ENDPOINT>          # e.g. https://<acct>.r2.cloudflarestorage.com
export R2_BUCKET=<R2_BUCKET>
export BACKUP_PASSPHRASE=<BACKUP_PASSPHRASE>

# 1. List available backups (newest last), pick one
aws s3 ls "s3://${R2_BUCKET}/" --endpoint-url "$R2_ENDPOINT"

# 2. Download the chosen encrypted backup
TS=<TIMESTAMP>   # from the filename job360-<TS>.sql.gz.gpg
aws s3 cp "s3://${R2_BUCKET}/job360-${TS}.sql.gz.gpg" . --endpoint-url "$R2_ENDPOINT"

# 3. Decrypt (AES256 symmetric) → still gzip-compressed
gpg --batch --yes --passphrase "$BACKUP_PASSPHRASE" \
    -o "job360-${TS}.sql.gz" -d "job360-${TS}.sql.gz.gpg"

# 4. Decompress → plain SQL dump
gunzip -f "job360-${TS}.sql.gz"          # → job360-<TS>.sql

# 5. Restore into the TARGET database (verify TARGET_DATABASE_URL first!)
#    ⚠️ This writes into whatever TARGET_DATABASE_URL points at. For a real
#    recovery, restore into a FRESH/empty database first, sanity-check, then cut over.
export TARGET_DATABASE_URL=<postgresql://user:pass@host:port/dbname>
psql "$TARGET_DATABASE_URL" -v ON_ERROR_STOP=1 -f "job360-${TS}.sql"

# 6. Sanity-check the restore
psql "$TARGET_DATABASE_URL" -tAc "SELECT count(*) FROM users;"
psql "$TARGET_DATABASE_URL" -tAc "SELECT count(*) FROM jobs;"
psql "$TARGET_DATABASE_URL" -tAc "SELECT count(*) FROM _schema_migrations;"

# 7. CLEAN UP — the decrypted files contain user PII
shred -u "job360-${TS}.sql" 2>/dev/null || rm -f "job360-${TS}.sql"
rm -f "job360-${TS}.sql.gz.gpg"
```

## Notes & cautions
- **Restore into a fresh DB first**, verify, then repoint the app — never blind-restore over live prod.
- The dump is `--no-owner --no-privileges`, so it restores cleanly into a DB with a different role/owner than prod.
- The pg client must be **≥ the prod server major version** (prod Postgres is 18.x — use psql 18).
- **Retention is ~30 newest nightly** (about 30 days) plus **12 monthly cold copies** under `monthly/`. Older than that is gone. There is still one provider (R2) and no second region, and the recovery point is the last dump (up to 24 h of writes lost) unless the owner turns on Railway point-in-time recovery.
- **Do a restore drill quarterly** into a scratch DB so this procedure stays true and you stay practiced.
