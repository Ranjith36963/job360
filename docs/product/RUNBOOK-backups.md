# RUNBOOK — Database backup & restore
<!-- doc: LIVING -->

> Referenced by `.github/workflows/db-backup.yml`. This is the human procedure for
> restoring the encrypted Postgres backups from Cloudflare R2 during an incident.
> **Read this BEFORE you need it** — an untested restore is not a backup.

## What the backup is (so restore makes sense)
The nightly `db-backup` workflow does, in order:
1. `pg_dump --no-owner --no-privileges "$DATABASE_URL" > dump.sql` (plain SQL).
2. **Verifies** it by restoring into a throwaway Postgres. Only two gates are enforced: `users >= 1` and `count(_schema_migrations) >= 20`. **`jobs` is printed, never asserted** — an empty `jobs` table alone does NOT fail the job, though an otherwise-empty restore still trips the two gates above.
3. `gzip` → `gpg --symmetric --cipher-algo AES256 --passphrase $BACKUP_PASSPHRASE` → `job360-<TIMESTAMP>.sql.gz.gpg`.
4. `aws s3 cp` the encrypted file to `s3://$R2_BUCKET/` (Cloudflare R2, S3-compatible), keeping the newest ~30.

So a stored object is: **gzip-compressed plain-SQL dump, AES256-encrypted**. R2 only ever holds ciphertext. It contains user PII (emails, CVs) — handle the decrypted file carefully and delete it when done.

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
- **Retention is ~30 newest** (nightly ⇒ ~30 days). If you need older, it's already gone — there is no long-term archive, no second region and no monthly cold copy. That gap is accepted, not overlooked.
- **Do a restore drill quarterly** into a scratch DB so this procedure stays true and you stay practiced.
