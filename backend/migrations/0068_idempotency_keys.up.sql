-- 0068_idempotency_keys: one store for "the same write, sent twice" (gap roadmap slice 10).
--
-- A write tool that takes a `request_id` keeps one row per (user, tool, key): the
-- sha256 of the canonical request body and the reply it gave. The same key with the
-- same body returns that FIRST reply and writes nothing (a network retry is safe);
-- the same key with a different body is refused (409 idempotency_key_reused).
-- Written in the SAME transaction as the write it guards. record_application is
-- the first user; other write tools join later.
--
-- DDL ONLY. IF NOT EXISTS so a re-run is harmless.

CREATE TABLE IF NOT EXISTS idempotency_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tool TEXT NOT NULL,                      -- e.g. record_application
    key TEXT NOT NULL,                       -- the caller's request_id, 1..128 printable chars
    body_sha256 TEXT NOT NULL,               -- sha256 of the canonical JSON request body
    result_json TEXT NOT NULL,               -- the first reply, returned again on a retry
    created_at TEXT NOT NULL,
    UNIQUE (user_id, tool, key)
);
