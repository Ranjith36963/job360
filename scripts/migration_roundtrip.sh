#!/usr/bin/env bash
#
# scripts/migration_roundtrip.sh
#
# Verifies that the Step-3 migrations (0012, 0013, 0014) survive a
# down→up round-trip against a hermetic temp DB.
#
# Algorithm
# ---------
# 1. Create a fresh temp SQLite file.
# 2. Apply ALL migrations up (baseline 0000–0011 + new 0012–0014).
# 3. Capture the resulting schema.
# 4. Apply `runner down` three times (reverts 0014 → 0013 → 0012).
# 5. Apply `runner up` three times (re-applies 0012 → 0013 → 0014).
# 6. Re-capture the schema and assert it matches step 3.
#
# If Step-3 migration files don't exist yet the script exits 0 (SKIP)
# so the Makefile target can be wired in iteration 1 before Cohort B
# creates the SQL files.
#
# Usage:
#   bash scripts/migration_roundtrip.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

BACKEND_DIR="backend"
MIGRATIONS_DIR="$BACKEND_DIR/migrations"
STEP3_PREFIXES=(0012 0013 0014)

# Detect Python (mirrors review_batch.sh convention)
PYTHON_CMD="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
if [ -z "$PYTHON_CMD" ]; then
    echo "ERROR: neither python3 nor python on PATH" >&2
    exit 2
fi

echo "==> Migration round-trip test (Step-3 migrations: 0012, 0013, 0014)"

# Skip gracefully if Step-3 migrations haven't been created yet
missing=()
for prefix in "${STEP3_PREFIXES[@]}"; do
    found=$(ls "$MIGRATIONS_DIR/${prefix}_"*.up.sql 2>/dev/null | head -1 || true)
    if [ -z "$found" ]; then
        missing+=("$prefix")
    fi
done

if [ ${#missing[@]} -gt 0 ]; then
    echo "  SKIP: Step-3 migration files not yet created: ${missing[*]}"
    echo "        Run Cohort B agents first, then re-run."
    exit 0
fi

# Create a hermetic temp DB (cleaned on exit)
TMPDB="$(mktemp /tmp/job360_roundtrip_XXXXXX.db)"
trap 'rm -f "$TMPDB"' EXIT

_schema() {
    # Dump sorted CREATE statements so comparison is order-independent
    "$PYTHON_CMD" - "$1" <<'PYEOF'
import sys, sqlite3
conn = sqlite3.connect(sys.argv[1])
cur = conn.execute(
    "SELECT sql FROM sqlite_master WHERE type IN ('table','index') AND sql IS NOT NULL ORDER BY name"
)
rows = [row[0] for row in cur if row[0]]
conn.close()
print('\n'.join(rows))
PYEOF
}

# ── Step 0: init_db() to create baseline tables ──────────────────────────────
# migration 0000 is a no-op (SELECT 1); the base tables (jobs, user_actions,
# applications, run_log) are created by JobDatabase.init_db(), not a migration.
# The runner must see those tables before applying 0001+ (which ALTER/rebuild them).
#
# After init_db(), _migrate() has already created notification_rules,
# user_notification_digests, and application_stage_history with its inline SQL.
# We drop those three tables so the migration runner creates them with the
# authoritative SQL from the migration files — making the before/after schema
# comparison valid (both sides use the migration file SQL, not the inline SQL).
echo "  [0/6] Seeding baseline schema via init_db() and resetting Step-3 tables..."
(cd "$BACKEND_DIR" && "$PYTHON_CMD" - "$TMPDB" <<'PYEOF'
import asyncio, sys, aiosqlite
sys.path.insert(0, ".")
from src.repositories.database import JobDatabase

async def main():
    db = JobDatabase(sys.argv[1])
    await db.init_db()
    # Drop Step-3 tables so migration runner creates them with authoritative SQL
    conn = db._conn
    await conn.executescript("""
        DROP TABLE IF EXISTS application_stage_history;
        DROP TABLE IF EXISTS idx_stage_history_job_user;
        DROP INDEX IF EXISTS idx_stage_history_job_user;
        DROP TABLE IF EXISTS user_notification_digests;
        DROP INDEX IF EXISTS idx_digests_user_channel_pending;
        DROP TABLE IF EXISTS notification_rules;
    """)
    await conn.commit()
    await db.close()

asyncio.run(main())
PYEOF
)

# ── Step 1: full up ──────────────────────────────────────────────────────────
echo "  [1/6] Applying all migrations up..."
(cd "$BACKEND_DIR" && python -m migrations.runner up "$TMPDB" 2>&1)

# ── Step 2: capture schema ───────────────────────────────────────────────────
echo "  [2/6] Capturing schema..."
SCHEMA_BEFORE=$(_schema "$TMPDB")

# ── Step 3: roll back three times ───────────────────────────────────────────
echo "  [3/6] Rolling back Step-3 migrations (0014 → 0013 → 0012)..."
for i in 1 2 3; do
    reverted=$(cd "$BACKEND_DIR" && python -m migrations.runner down "$TMPDB" 2>&1)
    echo "    Reverted: ${reverted}"
done

# ── Step 4: re-apply three times ────────────────────────────────────────────
echo "  [4/6] Re-applying Step-3 migrations (0012 → 0013 → 0014)..."
(cd "$BACKEND_DIR" && python -m migrations.runner up "$TMPDB" 2>&1)

# ── Step 5: capture schema after round-trip ──────────────────────────────────
echo "  [5/6] Capturing schema after round-trip..."
SCHEMA_AFTER=$(_schema "$TMPDB")

# ── Step 6: compare ──────────────────────────────────────────────────────────
echo "  [6/6] Comparing schemas..."
if [ "$SCHEMA_BEFORE" = "$SCHEMA_AFTER" ]; then
    echo "  PASS: round-trip schema is identical"
else
    echo "  FAIL: schema diverged after round-trip"
    diff <(echo "$SCHEMA_BEFORE") <(echo "$SCHEMA_AFTER") || true
    exit 1
fi

echo
echo "==> Migration round-trip: PASS"
