"""Initialize the full database schema on the ``public`` schema.

Mirrors what the FastAPI lifespan does on first boot: create the JobDatabase
core tables (``JobDatabase.init_db()``) and apply every migration
(``runner.up()``). Both are idempotent — core tables use
``CREATE TABLE IF NOT EXISTS`` and migrations track applied state in
``_schema_migrations`` — so this is safe to run on every invocation.

Why it exists: a fresh Postgres (e.g. a CI service container) has an empty
``public`` schema. Most of the test suite isolates into per-path schemas that
self-bootstrap, but tests that fall back to the default ``DB_PATH`` read the
``public`` schema, which nothing else initializes. On a developer machine that
schema is already populated from prior app boots, so the suite is green locally
but red on a fresh CI database. Running this once before the suite closes that
gap without weakening test isolation.

Usage (from ``backend/``):
    python scripts/init_db.py
"""
import asyncio

from migrations import runner
from src.core.settings import DB_PATH
from src.repositories.database import JobDatabase


async def main() -> None:
    db = JobDatabase(str(DB_PATH))
    await db.init_db()
    await db.close()
    await runner.up(str(DB_PATH))
    print("init_db: public schema initialized (core tables + migrations applied)")


if __name__ == "__main__":
    asyncio.run(main())
